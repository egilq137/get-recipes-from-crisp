"""Tests for cooking-time resolution (scraped value preferred, heuristic fallback)
and the prep-aware realistic estimate (pure compute + JSON parsing, no API)."""
from crisp_recipes.cooking_time import (
    PrepBreakdown,
    compute_realistic_minutes,
    estimate_from_steps,
    parse_prep_breakdown,
    resolve_cooking_minutes,
)


def test_estimate_sums_max_of_ranges():
    steps = [
        "1. Bak 15-20 minuten in de oven.",
        "2. Kook de mais in 5 tot 8 minuten beetgaar.",
        "3. Rooster 8 min.",
    ]
    # 20 + 8 + 8
    assert estimate_from_steps(steps) == 36


def test_estimate_none_when_no_durations():
    assert estimate_from_steps(["1. Snijd de ui.", "2. Meng alles."]) is None


def test_resolve_prefers_scraped_value():
    assert resolve_cooking_minutes(35, ["Bak 15-20 minuten."]) == 35


def test_resolve_falls_back_to_estimate():
    assert resolve_cooking_minutes(None, ["Bak 15-20 minuten."]) == 20


def test_resolve_ignores_zero_scraped():
    assert resolve_cooking_minutes(0, ["Kook 10 min."]) == 10


def test_resolve_returns_none_when_nothing_available():
    assert resolve_cooking_minutes(None, ["Meng alles."]) is None


# --------------------------------------------------------------------------- #
# Prep-aware realistic estimate
# --------------------------------------------------------------------------- #
def test_realistic_adds_prep_to_official():
    # 3 hard-chop (5 each = 15) + 1 grate (3) = 18 prep; no unattended -> no overlap.
    breakdown = PrepBreakdown(
        prep_tasks=[
            {"description": "chop veg", "category": "chop_hard", "count": 3},
            {"description": "grate cheese", "category": "grate", "count": 1},
        ],
        active_cook_minutes=10,
        unattended_minutes=0,
    )
    # official 25 + prep 18 - overlap 0 = 43
    assert compute_realistic_minutes(25, breakdown) == 43


def test_realistic_credits_overlap_with_unattended():
    breakdown = PrepBreakdown(
        prep_tasks=[{"description": "chop", "category": "chop_soft", "count": 4}],  # 12
        active_cook_minutes=5,
        unattended_minutes=20,
    )
    # prep 12; overlap = min(12,20)*0.4 = 4.8; realistic = 35 + 12 - 4.8 = 42.2 -> 42
    assert compute_realistic_minutes(35, breakdown) == 42


def test_realistic_never_below_official():
    breakdown = PrepBreakdown(prep_tasks=[], active_cook_minutes=0, unattended_minutes=30)
    assert compute_realistic_minutes(30, breakdown) == 30


def test_realistic_prep_multiplier_knob():
    breakdown = PrepBreakdown(
        prep_tasks=[{"description": "chop", "category": "chop_soft", "count": 2}],  # 6
        unattended_minutes=0,
    )
    # multiplier 2.0 -> prep 12; realistic = 20 + 12 = 32
    assert compute_realistic_minutes(20, breakdown, prep_multiplier=2.0) == 32


def test_realistic_uses_llm_base_when_no_official():
    breakdown = PrepBreakdown(
        prep_tasks=[{"description": "chop", "category": "chop_soft", "count": 1}],  # 3
        active_cook_minutes=10,
        unattended_minutes=0,
    )
    # no official -> base = active+unattended = 10; + prep 3 = 13
    assert compute_realistic_minutes(None, breakdown) == 13


def test_parse_prep_breakdown_coerces_unknown_category():
    raw = {
        "prep_tasks": [
            {"description": "juggle", "category": "nonsense", "count": 2},
            {"description": "chop", "category": "chop_soft", "count": 1},
        ],
        "active_cook_minutes": 8,
        "unattended_minutes": 15,
    }
    bd = parse_prep_breakdown(raw)
    assert bd.prep_tasks[0]["category"] == "misc"   # coerced
    assert bd.prep_tasks[1]["category"] == "chop_soft"
    assert bd.active_cook_minutes == 8 and bd.unattended_minutes == 15


def test_parse_prep_breakdown_handles_missing_fields():
    bd = parse_prep_breakdown({})
    assert bd.prep_tasks == []
    assert bd.active_cook_minutes == 0 and bd.unattended_minutes == 0
