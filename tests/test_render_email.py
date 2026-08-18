"""Tests for rendering (HTML/PDF view-model) and email assembly. No network."""
from pathlib import Path

from crisp_recipes.email_sender import build_message
from crisp_recipes.models import Ingredient, NutritionFacts, Recipe
from crisp_recipes.render import (
    build_email_html,
    cooking_time_label,
    format_amount,
    nutrition_sections,
    render_recipe_pdf,
)


def _recipe_with_nutrition():
    total = NutritionFacts()
    total.add_amount("calories", 1200)
    total.add_amount("protein", 40)
    total.add_amount("iron", 6)
    total.add_amount("vit_c", 90)
    return Recipe(
        title="Test bowl",
        steps=["1. Chop.", "2. Cook."],
        ingredients=[
            Ingredient(name="ui", name_en="onion", amount="1", unit="stuk",
                       grams=100, usda_description="Onions, raw"),
            Ingredient(name="olie", name_en="oil", amount="1", unit="el",
                       pantry=True, grams=10, usda_description="Olive oil"),
        ],
        cooking_time_minutes=25,
        realistic_time_minutes=40,
        nutrition_total=total,
        source_portions=2,
    )


def test_format_amount_units_and_missing():
    assert format_amount(None, "g") == "—"
    assert format_amount(1200.0, "kcal") == "1200 kcal"
    assert format_amount(2.5, "g") == "2.5 g"
    assert format_amount(90.0, "mg") == "90 mg"
    assert format_amount(709.4, "ug") == "709 µg"


def test_cooking_time_label_variants():
    assert cooking_time_label(Recipe("t", [], cooking_time_minutes=25,
                                     realistic_time_minutes=40)) == \
        "25 min active · ~40 min realistic (incl. prep)"
    assert cooking_time_label(Recipe("t", [], cooking_time_minutes=25)) == "25 min"
    assert cooking_time_label(Recipe("t", [], realistic_time_minutes=30)) == \
        "~30 min (incl. prep)"
    assert cooking_time_label(Recipe("t", [])) == "time n/a"


def test_nutrition_sections_grouping_and_per_serving():
    sections = nutrition_sections(_recipe_with_nutrition())
    titles = [t for t, _ in sections]
    assert titles == ["Macros", "Minerals", "Vitamins"]
    macros = {label: (ps, dv) for label, ps, dv in sections[0][1]}
    # only per-serving is reported: total / 2 portions
    assert macros["Energy"][0] == "600 kcal"
    assert macros["Protein"][0] == "20 g"
    # a nutrient with no data shows an em dash
    assert macros["Sugars"] == ("—", "—")


def test_daily_value_percentages():
    sections = nutrition_sections(_recipe_with_nutrition())
    macros = {label: dv for label, _ps, dv in sections[0][1]}
    minerals = {label: dv for label, _ps, dv in sections[1][1]}
    vitamins = {label: dv for label, _ps, dv in sections[2][1]}
    # protein 20 g / 50 g DV = 40%
    assert macros["Protein"] == "40%"
    # energy has a DV (2000) but per-serving 600 kcal -> 30%
    assert macros["Energy"] == "30%"
    # iron 3 mg (6 total / 2) / 18 mg DV = 17%
    assert minerals["Iron"] == "17%"
    # vitamin C 45 mg (90/2) / 90 mg DV = 50%
    assert vitamins["Vitamin C"] == "50%"
    # sugars has no DV, and no data -> em dash
    assert macros["Sugars"] == "—"


def test_daily_value_missing_when_no_data():
    from crisp_recipes.nutrients import BY_KEY
    from crisp_recipes.render import format_daily_value

    assert format_daily_value(None, BY_KEY["iron"]) == "—"
    assert format_daily_value(9.0, BY_KEY["iron"]) == "50%"
    # sugars has daily_value=None -> always em dash even with a value
    assert format_daily_value(10.0, BY_KEY["sugars"]) == "—"


def test_nutrition_sections_empty_when_no_data():
    assert nutrition_sections(Recipe("t", ["1."])) == []


def test_build_email_html_contains_key_content():
    html = build_email_html([_recipe_with_nutrition()], "Week 35, 2026")
    assert "Test bowl" in html
    assert "Week 35, 2026" in html
    assert "Onions, raw" in html          # matched USDA food shown
    assert "realistic" in html            # dual cooking time
    assert "USDA FoodData Central" in html  # disclosure note


def test_build_message_multipart_with_attachment(tmp_path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    msg = build_message(
        [_recipe_with_nutrition()], "Week 35, 2026",
        sender="me@example.com", recipients=["a@example.com", "b@example.com"],
        pdf_paths=[pdf],
    )
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Subject"] == "Crisp recipes — Week 35, 2026"
    # has plain + html alternatives and a pdf attachment
    assert msg.is_multipart()
    attachments = [p for p in msg.iter_attachments()]
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "r.pdf"


def test_render_pdf_writes_file(tmp_path):
    out = render_recipe_pdf(_recipe_with_nutrition(), tmp_path / "out.pdf")
    assert out.exists() and out.stat().st_size > 1000  # a real PDF got written
