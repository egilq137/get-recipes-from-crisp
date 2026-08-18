"""Opt-in live smoke test against the real crisp.nl site.

Skipped by default so the normal (offline) test run stays fast and deterministic.
Enable it to confirm the scraper still matches the live page structure:

    CRISP_LIVE=1 pytest tests/test_scraper_live.py -s

Requires Playwright + Firefox (`playwright install firefox`). Run it after any
crisp.nl redesign, or on a schedule, as an early-warning that selectors drifted.
"""
import os

import pytest

RUN_LIVE = os.getenv("CRISP_LIVE") == "1"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="set CRISP_LIVE=1 to run live scrape")


@pytest.fixture(scope="module")
def recipes():
    playwright = pytest.importorskip("playwright.sync_api")  # noqa: F841
    from crisp_recipes.scraper import scrape_current_recipes

    box = os.getenv("CRISP_BOX", "vegan")
    return scrape_current_recipes(box_key=box, headless=True)


def test_three_recipes(recipes):
    assert len(recipes) == 3, f"expected 3 recipes, got {len(recipes)}"


def test_each_recipe_is_populated(recipes):
    for r in recipes:
        assert r.title and r.title != "Untitled recipe", "missing title"
        assert len(r.ingredients) >= 4, f"too few ingredients in {r.title!r}"
        assert len(r.steps) >= 3, f"too few steps in {r.title!r}"
        assert r.source_portions and r.source_portions > 0, "portions not detected"
        assert r.cooking_time_minutes and r.cooking_time_minutes > 0, "cooking time not detected"


def test_ingredients_have_names_and_some_amounts(recipes):
    for r in recipes:
        assert all(i.name.strip() for i in r.ingredients), "empty ingredient name"
        # At least some non-pantry ingredients should carry a numeric amount.
        weighed = [i for i in r.ingredients if not i.pantry and i.amount]
        assert weighed, f"no amounts parsed for {r.title!r}"
