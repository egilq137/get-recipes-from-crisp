"""Senior-chef notes: practical tips for cooking a recipe in *our* kitchen.

A separate Gemini call (from the cooking-time estimator) that reads the finished,
translated recipe and returns a short list of practical tips — which of our cookware
each step needs, what the spare cookware lets us run in parallel, timing traps, and
finishing touches. Best-effort: if the model is unavailable or errors, the recipe
simply carries no notes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

log = logging.getLogger(__name__)

# The cookware we actually have. The notes plan the recipe around exactly this.
KITCHEN_EQUIPMENT = [
    "1 big pot",
    "1 medium pot",
    "1 frying pan",
    "1 oven",
    "1 air fryer",
]

_PROMPT = """You are a senior chef writing a short briefing for a home cook making this
recipe. Give practical, recipe-specific advice — not generic filler.

The kitchen has exactly this cookware, and nothing else for cooking:
{equipment}
Bowls, colanders, baking trays, ovenproof dishes and a hand blender are available too;
don't plan around them or repurpose the cookware above for their jobs.

Focus on:
- Cookware plan (always the FIRST bullet): which of the cookware above the recipe needs,
  mapped to what it's used for. Where the recipe asks for something not in the list
  (a wok, a small saucepan, a second pan), say which item above to use instead.
- Parallel cooking: where a spare item from the list (e.g. the air fryer or the oven
  while the frying pan is busy, the second pot) lets two things cook at the same time
  instead of one after the other, say which item, for what, and from which step. Only
  suggest swaps that keep the result as good (e.g. roasting veg in the air fryer
  instead of frying them in batches). Never assume more than one of any item.
- Mise en place worth doing before the heat goes on.
- Timing traps: things that can't wait, what to start first.
- Small technique or finishing tips that raise the result.

Write 4-7 concise bullet points. Each bullet one or two sentences, imperative, specific
to THIS dish.

For every bullet, list the step numbers it refers to in `steps` (the numbers shown in
the recipe below), so the cook can find them quickly. Use an empty list only for advice
that genuinely belongs to no particular step. Do NOT write the step numbers inside the
text itself — put them only in the `steps` field.

Recipe: {title}
Serves: {portions}
Ingredients:
{ingredients}

Steps:
{steps}
"""

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "notes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "steps": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                },
                "required": ["text", "steps"],
            },
        },
    },
    "required": ["notes"],
}


# "(Step 6)", "(steps 2, 3 and 5)" — the model writes these inline despite being told
# not to; the `steps` field already carries them, so they'd show up twice.
_INLINE_STEP_REF_RE = re.compile(r"\s*\(\s*steps?\s+[\d,\s&and-]+\)", re.IGNORECASE)


def format_step_reference(steps) -> str:
    """'(step 3)' / '(steps 2, 6)' / '' — appended so a tip is easy to locate."""
    numbers = []
    for value in steps or []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in numbers:
            numbers.append(n)
    if not numbers:
        return ""
    numbers.sort()
    label = "step" if len(numbers) == 1 else "steps"
    return f" ({label} {', '.join(str(n) for n in numbers)})"


def parse_notes(raw) -> List[str]:
    """Normalize Gemini's JSON into display-ready bullets with step references.

    Accepts either the structured form ({"text": ..., "steps": [...]}) or a plain list
    of strings, so an older/degraded response still renders.
    """
    if isinstance(raw, dict):
        raw = raw.get("notes") or []
    notes = []
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            reference = format_step_reference(item.get("steps"))
        else:
            text, reference = str(item).strip(), ""
        text = _INLINE_STEP_REF_RE.sub("", text.lstrip("-•")).strip()
        if not text:
            continue
        # Keep the sentence's full stop after the reference: "... pan (step 6)."
        if reference and text.endswith("."):
            text = f"{text[:-1]}{reference}."
        else:
            text = f"{text}{reference}"
        notes.append(text)
    return notes


def _generate_gemini(recipe, api_key: str) -> List[str]:
    from google import genai  # local import: heavy optional dependency

    client = genai.Client(api_key=api_key)
    ingredients = "\n".join(f"- {i.display}" for i in recipe.ingredients)
    portions = recipe.display_portions or recipe.source_portions or 2
    prompt = _PROMPT.format(
        equipment="\n".join(f"- {item}" for item in KITCHEN_EQUIPMENT),
        title=recipe.title,
        portions=portions,
        ingredients=ingredients or "(none)",
        steps=recipe.combine_steps(),
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={
            "temperature": 0.6,
            "response_mime_type": "application/json",
            "response_json_schema": _SCHEMA,
        },
    )
    return parse_notes(json.loads(response.text))


def generate_chef_notes(recipe, api_key: Optional[str]) -> List[str]:
    """Best-effort chef notes for one recipe. Empty list on any problem."""
    if not api_key:
        return []
    try:
        notes = _generate_gemini(recipe, api_key)
        log.info("  %s: %d chef notes", recipe.title, len(notes))
        return notes
    except Exception:
        log.exception("Chef notes failed for %r; leaving none", recipe.title)
        return []


def add_chef_notes(recipes: List, api_key: Optional[str]) -> List:
    """Fill in `chef_notes` on each recipe in place; returns the list."""
    for recipe in recipes:
        recipe.chef_notes = generate_chef_notes(recipe, api_key)
    return recipes
