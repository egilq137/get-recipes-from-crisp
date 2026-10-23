"""Translate recipes from Dutch to English with DeepL.

Improvements over the original:

* The DeepL client is built lazily (on first use) rather than at import time, so
  importing this module — or running the offline tests — never requires a key.
* Title, steps AND ingredient names are translated together in a single batched API
  call per recipe (DeepL accepts a list of strings), instead of joining steps with
  "#" and splitting again — which broke whenever a "#" survived translation.
* Ingredient names get `name_en` filled in, which the nutrition step then uses to
  query USDA FoodData Central in English.

Ingredient names are translated with a disambiguating "(ingrediënt)" suffix appended
to each bare name before sending, then stripped from the result. Bare Dutch food
words are ambiguous without sentence context — DeepL was observed translating
"sjalot" (shallot) as "scarves" and "lente-ui" (spring onion) as "lentils" when sent
as isolated words. The suffix reliably nudges DeepL toward the food sense while
staying easy to strip back off (confirmed against the live API).

Steps can instead be translated by Gemini (`GeminiStepTranslator`), which sees the
whole recipe and an ingredient glossary, so it handles context DeepL can't ("1el bij
2p", homographs). An LLM may also add, drop or merge content, so its output is never
trusted as-is: each step must come back under its own number, contain exactly the same
numbers as the Dutch (amounts, temperatures, times), and stay within a plausible length.
Any step that fails is translated by DeepL instead, so the worst case is today's output.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from crisp_recipes.models import _FRACTION_VALUES, Recipe

log = logging.getLogger(__name__)

_INGREDIENT_SUFFIX_NL = " (ingrediënt)"
# What the suffix reliably comes back as in English; stripped case-insensitively.
_INGREDIENT_SUFFIX_EN_RE = re.compile(r"\s*\(ingredient\)\s*$", re.IGNORECASE)


def _strip_ingredient_suffix(text: str) -> str:
    return _INGREDIENT_SUFFIX_EN_RE.sub("", text).strip()


# --------------------------------------------------------------------------- #
# Guardrails for LLM step translation
# --------------------------------------------------------------------------- #
_GLYPHS = "".join(_FRACTION_VALUES)
# "1/3", "1½" / "½", "0,5" / "400" — each becomes one exact value.
_NUMBER_RE = re.compile(
    rf"(\d+)\s*/\s*(\d+)|(\d+(?:[.,]\d+)?)?([{_GLYPHS}])|(\d+(?:[.,]\d+)?)"
)
_STEP_PREFIX_RE = re.compile(r"^\s*(\d+)\.\s+")

# Translated/Dutch character ratio outside this range means content was added or lost.
# Only checked for steps long enough for the ratio to be meaningful.
_MIN_LENGTH_RATIO, _MAX_LENGTH_RATIO = 0.6, 1.9
_LENGTH_CHECK_MIN_CHARS = 40


def number_tokens(text: str) -> Counter:
    """Every number in `text` as an exact value, with multiplicity.

    Glyph, slash and decimal forms of the same value compare equal ("½" == "1/2",
    "0,5" == "0.5"), since translation may legitimately change the notation.
    """
    out: Counter = Counter()
    for m in _NUMBER_RE.finditer(text):
        num, den, whole, glyph, plain = m.groups()
        if num is not None:
            if int(den) == 0:
                continue
            value = Fraction(int(num), int(den))
        elif glyph is not None:
            value = Fraction(whole.replace(",", ".")) if whole else Fraction(0)
            value += _FRACTION_VALUES[glyph]
        else:
            value = Fraction(plain.replace(",", "."))
        out[value] += 1
    return out


def check_step_translation(dutch: str, english: Optional[str]) -> Optional[str]:
    """Why `english` is not a faithful translation of `dutch`, or None if it passes."""
    if not english or not english.strip():
        return "empty"
    source, target = number_tokens(dutch), number_tokens(english)
    if source != target:
        missing = sorted(str(v) for v in (source - target).elements())
        extra = sorted(str(v) for v in (target - source).elements())
        return f"numbers differ (missing {missing}, extra {extra})"
    if len(dutch) >= _LENGTH_CHECK_MIN_CHARS:
        ratio = len(english) / len(dutch)
        if not _MIN_LENGTH_RATIO <= ratio <= _MAX_LENGTH_RATIO:
            return f"length ratio {ratio:.2f}"
    return None


def split_step_number(step: str) -> Tuple[Optional[str], str]:
    """'3. Schil de ui.' -> ('3', 'Schil de ui.'); no prefix -> (None, step)."""
    m = _STEP_PREFIX_RE.match(step)
    return (m.group(1), step[m.end():]) if m else (None, step)


def accept_step_translations(
    dutch_steps: Sequence[str], raw
) -> Tuple[List[Optional[str]], List[str]]:
    """Match the model's answer back to the steps and keep only the faithful ones.

    `dutch_steps` are the step texts without their "N. " prefix, numbered 1..n in the
    prompt. Returns one entry per step (None = rejected, to be translated by DeepL) and
    a human-readable reason per rejection. A step number answered twice is rejected
    outright, since we can't tell which answer belongs to it.
    """
    items = raw.get("steps", []) if isinstance(raw, dict) else []
    answers: Dict[int, str] = {}
    duplicated = set()
    problems: List[str] = []
    for item in items:
        try:
            n = int(item["n"])
            text = str(item["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if n in answers:
            duplicated.add(n)
        answers[n] = text
    extra = sorted(n for n in answers if not 1 <= n <= len(dutch_steps))
    if extra:
        problems.append(f"ignored extra steps {extra}")

    accepted: List[Optional[str]] = []
    for n, dutch in enumerate(dutch_steps, start=1):
        if n in duplicated:
            reason: Optional[str] = "answered twice"
        elif n not in answers:
            reason = "missing"
        else:
            reason = check_step_translation(dutch, answers[n])
        if reason:
            problems.append(f"step {n}: {reason}")
            accepted.append(None)
        else:
            accepted.append(answers[n])
    return accepted, problems


_STEP_PROMPT = """Translate the numbered cooking steps of this Dutch recipe into {language}.

Rules — these are strict:
- Translate ONLY. Do not add, remove, merge, split, reorder or summarize anything. No
  tips, no extra steps, no "improvements", no explanations.
- Return exactly one entry per input step, with the same number `n`.
- Keep every number exactly as written, as digits: amounts, fractions (½, 1/3),
  temperatures, times, sizes. Do not convert words to digits or digits to words, and do
  not convert units or temperatures.
- Keep amounts attached to their ingredient ("400 g zoete aardappel" -> "400 g sweet
  potato"); Dutch units become el -> tbsp, tl -> tsp, stuk -> piece (or drop it when
  natural, e.g. "1 stuk ui" -> "1 onion").
- "bij 2p" / "voor 2 personen" means "for 2 people".
- Write natural, idiomatic {language}, as a good cookbook would ("bring to room
  temperature", not a word-for-word rendering), while keeping every instruction.
- Ingredient vocabulary — use these words for the foods, written in lower case as part
  of the sentence. Never copy the labels in brackets (like "(for caprese)") or add
  ingredient details the Dutch step doesn't mention:
{glossary}

Recipe: {title}

Steps (JSON):
{steps}
"""

_STEP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "steps": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "n": {"type": "INTEGER"},
                    "text": {"type": "STRING"},
                },
                "required": ["n", "text"],
            },
        },
    },
    "required": ["steps"],
}

_LANGUAGE_NAMES = {"EN-GB": "British English", "EN-US": "American English"}


class GeminiStepTranslator:
    """Translates a recipe's steps with Gemini, returning only steps that pass the
    guardrails (None for the rest)."""

    def __init__(self, api_key: str, target_lang: str = "EN-GB",
                 model: str = "gemini-2.5-flash"):
        self._api_key = api_key
        self.language = _LANGUAGE_NAMES.get(target_lang.upper(), target_lang)
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai  # local import: heavy optional dependency

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _ask(self, prompt: str):
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_json_schema": _STEP_SCHEMA,
            },
        )
        return json.loads(response.text)

    def translate_steps(
        self, title: str, dutch_steps: Sequence[str], glossary: Dict[str, str]
    ) -> Tuple[List[Optional[str]], List[str]]:
        """(translation or None per step, a reason for each rejection)."""
        lines = [f"  {nl} -> {en}" for nl, en in glossary.items()]
        prompt = _STEP_PROMPT.format(
            language=self.language,
            glossary="\n".join(lines) or "  (none)",
            title=title,
            steps=json.dumps(
                [{"n": i, "text": t} for i, t in enumerate(dutch_steps, start=1)],
                ensure_ascii=False, indent=1,
            ),
        )
        return accept_step_translations(dutch_steps, self._ask(prompt))


class DeepLTranslator:
    """Thin wrapper around the DeepL client with lazy initialization."""

    def __init__(
        self,
        api_key: str,
        target_lang: str = "EN-GB",
        model_type: Optional[str] = "prefer_quality_optimized",
    ):
        self._api_key = api_key
        self.target_lang = target_lang
        self.model_type = model_type
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import deepl  # local import: only needed when actually translating

            # DeepLClient is the modern entry point; fall back to Translator.
            factory = getattr(deepl, "DeepLClient", None) or deepl.Translator
            self._client = factory(self._api_key)
        return self._client

    def translate_batch(self, texts: List[str]) -> List[str]:
        """Translate a list of strings, preserving order. Empty input -> empty list."""
        if not texts:
            return []
        results = self.client.translate_text(
            texts,
            target_lang=self.target_lang,
            model_type=self.model_type,
        )
        # DeepL returns a single object for a single input, or a list for many.
        if not isinstance(results, list):
            results = [results]
        return [r.text for r in results]

    def translate_recipe(
        self, recipe: Recipe, step_translator: Optional[GeminiStepTranslator] = None
    ) -> Recipe:
        """Return a new Recipe with title, steps and ingredient names translated.

        Without `step_translator`, everything goes to DeepL in one batch. With it, DeepL
        translates the title and ingredient names, the step translator does the steps,
        and any step it gets wrong (or all of them, if it fails) falls back to DeepL.
        Everything else (amounts, units, cooking time, portions) is preserved.
        """
        # Ingredient names carry a suffix so DeepL reads them as food, not homographs.
        names = [ing.name + _INGREDIENT_SUFFIX_NL for ing in recipe.ingredients]
        deepl_steps = [] if step_translator else list(recipe.steps)
        translated = self.translate_batch([recipe.title, *deepl_steps, *names])

        title = translated[0]
        steps = translated[1 : 1 + len(deepl_steps)]
        name_translations = translated[1 + len(deepl_steps) :]

        ingredients = [
            replace(ing, name_en=_strip_ingredient_suffix(en))
            for ing, en in zip(recipe.ingredients, name_translations)
        ]
        if step_translator:
            glossary = {ing.name: ing.name_en for ing in ingredients if ing.name_en}
            steps = self._steps_with_fallback(recipe, step_translator, glossary)
        return replace(recipe, title=title, steps=steps, ingredients=ingredients)

    def _steps_with_fallback(
        self, recipe: Recipe, step_translator: GeminiStepTranslator,
        glossary: Dict[str, str],
    ) -> List[str]:
        split = [split_step_number(s) for s in recipe.steps]
        bodies = [body for _, body in split]
        try:
            accepted, problems = step_translator.translate_steps(
                recipe.title, bodies, glossary)
        except Exception:
            log.exception("Gemini step translation failed for %r; using DeepL",
                          recipe.title)
            accepted, problems = [None] * len(bodies), []
        for problem in problems:
            log.warning("  %s: Gemini translation rejected, %s", recipe.title, problem)

        rejected = [i for i, text in enumerate(accepted) if text is None]
        if rejected:
            fallback = self.translate_batch([bodies[i] for i in rejected])
            for i, text in zip(rejected, fallback):
                accepted[i] = text
        log.info("  %s: %d/%d steps by Gemini, %d by DeepL", recipe.title,
                 len(bodies) - len(rejected), len(bodies), len(rejected))
        return [
            f"{number}. {text}" if number else text
            for (number, _), text in zip(split, accepted)
        ]


def translate_recipes(recipes: List[Recipe], settings) -> List[Recipe]:
    """Translate every recipe. On failure, log and keep the original (Dutch) recipe
    so one bad translation doesn't sink the whole run."""
    translator = DeepLTranslator(
        api_key=settings.deepl_api_key,
        target_lang=settings.target_lang,
    )
    step_translator = None
    if settings.step_translator == "gemini" and settings.google_api_key:
        step_translator = GeminiStepTranslator(
            api_key=settings.google_api_key, target_lang=settings.target_lang)
    out: List[Recipe] = []
    for recipe in recipes:
        try:
            out.append(translator.translate_recipe(recipe, step_translator))
        except Exception:
            log.exception("Translation failed for %r; keeping original", recipe.title)
            out.append(recipe)
    return out
