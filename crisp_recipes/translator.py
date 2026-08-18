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
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import List, Optional

from crisp_recipes.models import Recipe

log = logging.getLogger(__name__)

_INGREDIENT_SUFFIX_NL = " (ingrediënt)"
# What the suffix reliably comes back as in English; stripped case-insensitively.
_INGREDIENT_SUFFIX_EN_RE = re.compile(r"\s*\(ingredient\)\s*$", re.IGNORECASE)


def _strip_ingredient_suffix(text: str) -> str:
    return _INGREDIENT_SUFFIX_EN_RE.sub("", text).strip()


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

    def translate_recipe(self, recipe: Recipe) -> Recipe:
        """Return a new Recipe with title, steps and ingredient names translated.

        Everything else (amounts, units, cooking time, portions) is preserved.
        """
        # Ingredient names carry a suffix so DeepL reads them as food, not homographs.
        names = [ing.name + _INGREDIENT_SUFFIX_NL for ing in recipe.ingredients]
        payload = [recipe.title, *recipe.steps, *names]
        translated = self.translate_batch(payload)

        title = translated[0]
        steps = translated[1 : 1 + len(recipe.steps)]
        name_translations = translated[1 + len(recipe.steps) :]

        ingredients = [
            replace(ing, name_en=_strip_ingredient_suffix(en))
            for ing, en in zip(recipe.ingredients, name_translations)
        ]
        return replace(recipe, title=title, steps=steps, ingredients=ingredients)


def translate_recipes(recipes: List[Recipe], settings) -> List[Recipe]:
    """Translate every recipe. On failure, log and keep the original (Dutch) recipe
    so one bad translation doesn't sink the whole run."""
    translator = DeepLTranslator(
        api_key=settings.deepl_api_key,
        target_lang=settings.target_lang,
    )
    out: List[Recipe] = []
    for recipe in recipes:
        try:
            out.append(translator.translate_recipe(recipe))
        except Exception:
            log.exception("Translation failed for %r; keeping original", recipe.title)
            out.append(recipe)
    return out
