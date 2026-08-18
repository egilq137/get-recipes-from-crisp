"""Tests for scaling displayed amounts to a different portion count.

The critical property: only food quantities scale. Oven temperatures, cooking times
and physical sizes inside the step text must survive untouched — doubling "200 graden"
or "15-20 minuten" would ruin the recipe. Nutrition must stay pinned to the official
portion count so per-serving figures never move.
"""
from fractions import Fraction

import pytest

from crisp_recipes.models import (
    Ingredient,
    NutritionFacts,
    Recipe,
    format_amount_value,
    parse_amount,
    scale_amounts_in_text,
)

NAMES = ["zoete aardappel", "wortel", "kaas", "yoghurt", "tomaten", "sjalot"]


# --------------------------------------------------------------------------- #
# Amount parsing / formatting
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("400", Fraction(400)),
    ("¾", Fraction(3, 4)),
    ("1½", Fraction(3, 2)),
    ("0.5", Fraction(1, 2)),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_parse_amount_rejects_junk():
    assert parse_amount("naar smaak") is None
    assert parse_amount("") is None
    assert parse_amount(None) is None


@pytest.mark.parametrize("value,expected", [
    (Fraction(800), "800"),
    (Fraction(3, 2), "1½"),
    (Fraction(3, 4), "¾"),
    (Fraction(1), "1"),
])
def test_format_amount_value(value, expected):
    assert format_amount_value(value) == expected


# --------------------------------------------------------------------------- #
# Step-text scaling: what MUST change
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("Schil 400 g zoete aardappel", "Schil 800 g zoete aardappel"),
    ("Voeg ½ tl komijnpoeder toe", "Voeg 1 tl komijnpoeder toe"),
    ("bestrooi met ¾ zakje kaas", "bestrooi met 1½ zakje kaas"),
    ("Meng 1 el mayonaise", "Meng 2 el mayonaise"),
    ("Snij 1½ tomaten in plakjes", "Snij 3 tomaten in plakjes"),
])
def test_scales_food_quantities(text, expected):
    assert scale_amounts_in_text(text, Fraction(2), NAMES) == expected


# --------------------------------------------------------------------------- #
# Step-text scaling: what MUST NOT change (safety)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "Verwarm de oven voor op 200 graden.",          # temperature
    "Bak 15-20 minuten in de oven.",                # time range
    "bak de mais in 5 tot 8 minuten beetgaar",      # time range, words
    "snij in plakjes van 0.5 cm dik",               # thickness
    "Kook 9-11 minuten al dente.",                  # time
])
def test_never_scales_non_food_numbers(text):
    assert scale_amounts_in_text(text, Fraction(2), NAMES) == text


def test_mixed_line_scales_only_the_food():
    text = "Verwarm 10 minuten op 180 graden en voeg 250 g wortel toe"
    out = scale_amounts_in_text(text, Fraction(2), NAMES)
    assert out == "Verwarm 10 minuten op 180 graden en voeg 500 g wortel toe"


def test_factor_one_is_noop():
    text = "Schil 400 g zoete aardappel"
    assert scale_amounts_in_text(text, Fraction(1), NAMES) == text


# --------------------------------------------------------------------------- #
# Recipe-level scaling
# --------------------------------------------------------------------------- #
def _recipe():
    total = NutritionFacts()
    total.add_amount("calories", 1200)
    return Recipe(
        title="T",
        steps=["1. Verwarm de oven op 200 graden.", "2. Schil 400 g zoete aardappel."],
        ingredients=[
            Ingredient(name="zoete aardappel", amount="400", unit="g", grams=400),
            Ingredient(name="Olie om te bakken", pantry=True),  # unquantified
        ],
        nutrition_total=total,
        source_portions=2,
    )


def test_scaled_to_portions_doubles_ingredients_and_steps():
    out = _recipe().scaled_to_portions(4)
    assert out.ingredients[0].amount == "800"
    assert out.steps[1] == "2. Schil 800 g zoete aardappel."
    assert out.steps[0] == "1. Verwarm de oven op 200 graden."   # temperature intact
    assert out.display_portions == 4


def test_unquantified_pantry_item_survives_scaling():
    out = _recipe().scaled_to_portions(4)
    assert out.ingredients[1].amount is None
    assert out.ingredients[1].name == "Olie om te bakken"


def test_per_serving_is_invariant_to_batch_size():
    """The whole point: cooking 4 portions instead of 2 must not change per-serving
    nutrition. Totals scale with the batch; the per-serving figure does not."""
    unscaled = _recipe()                       # amounts for 2, total 1200 kcal
    assert unscaled.nutrition_per_serving().get("calories") == 600

    # After scaling, nutrition is recomputed from the doubled amounts in the real
    # pipeline; simulate that here.
    scaled = _recipe().scaled_to_portions(4)
    doubled = NutritionFacts()
    doubled.add_amount("calories", 2400)
    scaled.nutrition_total = doubled

    assert scaled.portions_for_amounts == 4
    assert scaled.nutrition_per_serving().get("calories") == 600   # unchanged


def test_scaling_to_same_portions_is_noop():
    r = _recipe()
    assert r.scaled_to_portions(2) is r
