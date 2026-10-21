"""Tests for the static recipe website builder (no network)."""
import json

from crisp_recipes.models import NutritionFacts, Recipe
from crisp_recipes.render import render_recipe_pdf
from crisp_recipes.site import build_index, publish_week, slugify, write_week


def _recipe(title="Tacos with mango-jalapeño salsa"):
    total = NutritionFacts()
    total.add_amount("calories", 1300)
    return Recipe(title=title, steps=["1. Cook."], source_portions=2,
                  display_portions=4, cooking_time_minutes=30, nutrition_total=total)


def test_slugify():
    assert slugify("Tacos with mango-jalapeño salsa") == "tacos-with-mango-jalapeno-salsa"
    assert slugify("  !!  ") == "recipe"
    assert len(slugify("x" * 200)) == 60


def test_write_week_writes_pages_pdfs_and_week_json(tmp_path):
    pdf = render_recipe_pdf(_recipe(), tmp_path / "src" / "Tacos.pdf")
    week_dir = write_week([_recipe()], tmp_path / "site", "2026_Week_44", "Week 44, 2026", [pdf])

    week = json.loads((week_dir / "week.json").read_text(encoding="utf-8"))
    [entry] = week["recipes"]
    assert entry["id"] == "2026_Week_44/tacos-with-mango-jalapeno-salsa"
    assert entry["kcal"] == 325                      # per serving: 1300 / 4
    assert entry["pdf"] == "weeks/2026_Week_44/tacos-with-mango-jalapeno-salsa.pdf"
    assert (tmp_path / "site" / entry["pdf"]).exists()
    page = (tmp_path / "site" / entry["page"]).read_text(encoding="utf-8")
    assert "Tacos with mango-jalapeño salsa" in page and "../../index.html" in page


def test_duplicate_titles_get_distinct_ids(tmp_path):
    week_dir = write_week([_recipe("Soup"), _recipe("Soup")], tmp_path, "2026_Week_1", "W1")
    ids = [r["id"] for r in json.loads((week_dir / "week.json").read_text())["recipes"]]
    assert len(set(ids)) == 2


def test_index_lists_weeks_newest_first_and_rerun_replaces_week(tmp_path):
    write_week([_recipe("Old")], tmp_path, "2025_Week_52", "Week 52, 2025")
    write_week([_recipe("New")], tmp_path, "2026_Week_3", "Week 3, 2026")
    publish_week([_recipe("Newer")], tmp_path, "2026_Week_3", "Week 3, 2026", None,
                 "https://script.google.com/macros/s/abc/exec")

    weeks = json.loads((tmp_path / "recipes.json").read_text(encoding="utf-8"))["weeks"]
    assert [w["week"] for w in weeks] == ["2026_Week_3", "2025_Week_52"]
    assert [r["title"] for r in weeks[0]["recipes"]] == ["Newer"]
    assert (tmp_path / "index.html").exists() and (tmp_path / "app.js").exists()
    assert "script.google.com/macros/s/abc/exec" in (tmp_path / "config.js").read_text()


def test_index_without_ratings_api(tmp_path):
    build_index(tmp_path, None)
    assert (tmp_path / "config.js").read_text() == 'window.RATINGS_API_URL = "";\n'
