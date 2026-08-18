"""Opt-in live test against the real USDA FoodData Central API.

Verifies your USDA_FDC_API_KEY works and returns sane numbers. Skipped automatically
unless the key is present in the environment (or your .env). Run it with:

    pytest tests/test_nutrition_live.py -s
"""
import os

import pytest

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_KEY = os.getenv("USDA_FDC_API_KEY")
pytestmark = pytest.mark.skipif(not API_KEY, reason="set USDA_FDC_API_KEY (or .env) to run")


def test_usda_key_and_lookup():
    from crisp_recipes.nutrition import USDAClient

    usda = USDAClient(API_KEY)
    match = usda.lookup("sweet potato, raw")
    assert match, "no USDA match returned — check the API key"
    facts = match.per_100g
    # Sweet potato tuber is ~86 kcal / 100 g; allow a band across datasets.
    assert 60 <= facts.get("calories", 0) <= 130, (match.description, facts.get("calories"))
    # Should carry a healthy dose of vitamin A and NOT be the leaves (~42 kcal).
    assert facts.get("vit_a", 0) > 100
    assert "leaves" not in match.description.lower()


def test_matching_picks_sensible_foods():
    from crisp_recipes.nutrition import USDAClient

    usda = USDAClient(API_KEY)
    # (query, kcal low, kcal high)
    cases = [
        ("carrot, raw", 30, 55),
        ("olive oil", 800, 950),
        ("tomato, raw", 12, 30),
        ("garlic, raw", 120, 160),
    ]
    for query, lo, hi in cases:
        match = usda.lookup(query)
        assert match, f"no match for {query}"
        cal = match.per_100g.get("calories", 0)
        assert lo <= cal <= hi, f"{query} -> {match.description} {cal} kcal"


def test_cache_avoids_second_call():
    from crisp_recipes.nutrition import USDAClient

    usda = USDAClient(API_KEY)
    first = usda.lookup("carrot, raw")
    second = usda.lookup("Carrot, Raw")  # different case -> same cache key
    assert first is second  # cached object, not just equal
