"""Senior-chef notes: how two people should tackle a recipe together.

A separate Gemini call (from the cooking-time estimator) that reads the finished,
translated recipe and returns a short list of practical tips — what to prep ahead, which
tasks two cooks can run in parallel, timing traps, and finishing touches. Best-effort:
if the model is unavailable or errors, the recipe simply carries no notes.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

log = logging.getLogger(__name__)

_PROMPT = """You are a senior chef writing a short briefing for TWO home cooks who will
make this recipe together. Give practical, recipe-specific advice — not generic filler.

Focus on:
- Mise en place worth doing before the heat goes on.
- Which tasks the two cooks can run IN PARALLEL (name who does what), and which must be
  sequential.
- Timing traps: things that overlap, things that can't wait, what to start first.
- Small technique or finishing tips that raise the result.

Write 4-7 concise bullet points. Each bullet one sentence, imperative, specific to THIS
dish. Refer to the two cooks as "Cook A" and "Cook B" where it helps.

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
        text = text.lstrip("-•").strip()
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
