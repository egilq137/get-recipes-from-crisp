"""Cooking-time resolution.

Two numbers are produced for each recipe:

* **Official / active** — crisp.nl's own "X min koken", scraped directly. Accurate for
  active cooking but (as observed in practice) it *omits mise en place* — washing,
  peeling, dicing, measuring — so it systematically under-states real time.
* **Realistic** — official time plus a prep estimate, to reflect how long the recipe
  actually takes end to end.

The realistic estimate uses an **"LLM classifies, code computes"** design, which is the
important fix over the original estimator. The old code asked Gemini to do the whole
calculation in its head ("count every task and return one integer") — exactly the
counting/arithmetic LLMs are unreliable at — and `os.abort()`ed on any error. Instead:

1. Gemini reads the (translated) steps + ingredient list and returns a *structured*
   breakdown: prep tasks tagged by category, plus active/unattended cook minutes. It
   only classifies — it does no arithmetic.
2. Python computes the minutes deterministically from `PREP_BENCHMARKS` (below), so the
   numbers are reproducible, debuggable, and tunable: adjust the table or the global
   `PREP_MULTIPLIER` to calibrate realism over time.

If Gemini is unavailable (no key or an API error), we fall back to the official time
only — no realistic estimate is invented.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger(__name__)

# Matches "15-20 minuten", "8 min", "5 tot 8 minuten" -> we take the largest number.
_DURATION_RE = re.compile(
    r"(\d+)\s*(?:-|tot|à)?\s*(\d+)?\s*min(?:uten|uut)?\b",
    re.IGNORECASE,
)


def estimate_from_steps(steps: List[str]) -> Optional[int]:
    """Rough, deterministic fallback: sum the explicit cooking durations found in the
    steps (using the max of any range). Returns None if none are found.

    This is intentionally simple — a lower-bound sanity number, not a precise model.
    Prep work without a stated duration is not counted, so it tends to under-estimate;
    that's acceptable for a fallback that should rarely run.
    """
    total = 0
    found = False
    for step in steps:
        for m in _DURATION_RE.finditer(step):
            found = True
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.group(2) else lo
            total += max(lo, hi)
    return total if found else None


def resolve_cooking_minutes(
    scraped_minutes: Optional[int],
    steps: List[str],
) -> Optional[int]:
    """Return the official scraped time if available, else the heuristic estimate."""
    if scraped_minutes and scraped_minutes > 0:
        return int(scraped_minutes)
    est = estimate_from_steps(steps)
    if est:
        log.warning("No official cooking time found; estimated %d min from steps", est)
    return est


# --------------------------------------------------------------------------- #
# Prep-aware realistic estimate.
# --------------------------------------------------------------------------- #
# Minutes of hands-on time per prep task, by category. THIS TABLE IS THE TUNING KNOB:
# if estimates feel low, raise these (or PREP_MULTIPLIER) after comparing to reality.
PREP_BENCHMARKS = {
    "wash": 2,          # rinsing produce / greens
    "peel": 3,          # peeling (carrot, potato, etc.)
    "chop_soft": 3,     # onion, tomato, herbs, mushrooms
    "chop_hard": 5,     # squash, sweet potato, hard root veg
    "grate": 3,         # grating cheese / zesting
    "measure": 1,       # portioning pantry items, spices, sauces
    "misc": 2,          # anything else hands-on but short
}
# Global realism multiplier applied to total prep. 1.0 = trust the table as-is.
PREP_MULTIPLIER = 1.0
# Fraction of unattended (oven/simmer) time you can realistically use for prep, so it
# isn't double-counted. 0 = prep is fully additive; 1 = prep fully hidden by waiting.
OVERLAP_FRACTION = 0.4

_VALID_CATEGORIES = set(PREP_BENCHMARKS)

# Category enum sent to Gemini so it classifies into exactly our buckets.
_GEMINI_CATEGORIES = sorted(_VALID_CATEGORIES)


@dataclass
class PrepBreakdown:
    """Structured breakdown returned by the LLM (numbers computed in Python)."""

    prep_tasks: List[dict]          # each: {"description", "category", "count"}
    active_cook_minutes: int = 0
    unattended_minutes: int = 0


def compute_realistic_minutes(
    official_minutes: Optional[int],
    breakdown: PrepBreakdown,
    *,
    prep_benchmarks: Optional[dict] = None,
    prep_multiplier: float = PREP_MULTIPLIER,
    overlap_fraction: float = OVERLAP_FRACTION,
) -> Optional[int]:
    """Deterministically combine the official time with the prep breakdown.

        realistic = base + prep_total - overlap_credit

    where `base` is the official active time (or the LLM's active+unattended if the
    site had none), `prep_total` comes from the benchmark table, and `overlap_credit`
    discounts prep that can happen while something cooks unattended.
    """
    benchmarks = prep_benchmarks or PREP_BENCHMARKS

    prep_total = 0.0
    for task in breakdown.prep_tasks:
        category = str(task.get("category", "misc"))
        minutes_each = benchmarks.get(category, benchmarks["misc"])
        count = task.get("count", 1)
        try:
            count = max(1, int(count))
        except (TypeError, ValueError):
            count = 1
        prep_total += minutes_each * count
    prep_total *= prep_multiplier

    if official_minutes and official_minutes > 0:
        base = float(official_minutes)
    else:
        base = float(breakdown.active_cook_minutes + breakdown.unattended_minutes)

    overlap_credit = min(prep_total, breakdown.unattended_minutes) * overlap_fraction
    realistic = base + prep_total - overlap_credit
    if realistic <= 0:
        return None
    # Never report below the official active time.
    if official_minutes:
        realistic = max(realistic, float(official_minutes))
    return int(round(realistic))


def parse_prep_breakdown(raw: dict) -> PrepBreakdown:
    """Validate/normalize the JSON object returned by Gemini into a PrepBreakdown."""
    tasks_in = raw.get("prep_tasks") or []
    tasks: List[dict] = []
    for t in tasks_in:
        category = str(t.get("category", "misc"))
        if category not in _VALID_CATEGORIES:
            category = "misc"
        tasks.append({
            "description": str(t.get("description", "")).strip(),
            "category": category,
            "count": t.get("count", 1),
        })
    return PrepBreakdown(
        prep_tasks=tasks,
        active_cook_minutes=int(raw.get("active_cook_minutes") or 0),
        unattended_minutes=int(raw.get("unattended_minutes") or 0),
    )


_PREP_PROMPT = """You are analysing a cooking recipe to break down its hands-on prep.

Do NOT estimate a total time or do any arithmetic. Only identify and classify the
mise en place and cooking tasks. For every distinct hands-on prep task (washing,
peeling, chopping, grating, measuring, etc.), output an item with:
- description: short text
- category: one of {categories}
- count: how many distinct items that task applies to (e.g. 3 vegetables to chop -> 3)

Also estimate:
- active_cook_minutes: minutes actively tending the stove/pan (stirring, frying)
- unattended_minutes: minutes something cooks unattended (oven bake, simmering)

Ingredients:
{ingredients}

Steps:
{steps}
"""

_PREP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "prep_tasks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "description": {"type": "STRING"},
                    "category": {"type": "STRING"},
                    "count": {"type": "INTEGER"},
                },
                "required": ["category", "count"],
            },
        },
        "active_cook_minutes": {"type": "INTEGER"},
        "unattended_minutes": {"type": "INTEGER"},
    },
    "required": ["prep_tasks", "active_cook_minutes", "unattended_minutes"],
}


def _extract_prep_breakdown_gemini(recipe, api_key: str) -> PrepBreakdown:
    """Call Gemini to classify prep tasks. Raises on any API/parse error."""
    from google import genai  # local import: only needed when estimating

    client = genai.Client(api_key=api_key)
    ingredients = "\n".join(
        f"- {(i.name_en or i.name)}" + (f" ({i.amount} {i.unit})" if i.amount else "")
        for i in recipe.ingredients
    )
    prompt = _PREP_PROMPT.format(
        categories=", ".join(_GEMINI_CATEGORIES),
        ingredients=ingredients or "(none)",
        steps=recipe.combine_steps(),
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
            "response_json_schema": _PREP_SCHEMA,
        },
    )
    return parse_prep_breakdown(json.loads(response.text))


def estimate_realistic_minutes(recipe, api_key: Optional[str]) -> Optional[int]:
    """Best-effort prep-aware estimate. Returns None (official-only) on any problem."""
    if not api_key:
        return None
    try:
        breakdown = _extract_prep_breakdown_gemini(recipe, api_key)
    except Exception:
        log.exception("Prep estimate failed for %r; using official time only", recipe.title)
        return None
    realistic = compute_realistic_minutes(recipe.cooking_time_minutes, breakdown)
    log.info("  %s: official %s min -> realistic %s min",
             recipe.title, recipe.cooking_time_minutes, realistic)
    return realistic


def add_realistic_times(recipes: List, api_key: Optional[str]) -> List:
    """Fill in `realistic_time_minutes` on each recipe in place; returns the list."""
    for recipe in recipes:
        recipe.realistic_time_minutes = estimate_realistic_minutes(recipe, api_key)
    return recipes
