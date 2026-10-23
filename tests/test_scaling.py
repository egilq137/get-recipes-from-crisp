"""Tests for scaling displayed amounts to a different portion count.

Only the ingredient list scales; the steps keep crisp.nl's original text and amounts.
Nutrition must stay pinned to the official portion count so per-serving figures never
move.
"""
from fractions import Fraction

import pytest

from crisp_recipes.models import (
    Ingredient,
    NutritionFacts,
    Recipe,
    format_amount_value,
    parse_amount,
)

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


def test_scaled_to_portions_doubles_ingredients_but_not_steps():
    original = _recipe()
    out = original.scaled_to_portions(4)
    assert out.ingredients[0].amount == "800"
    assert out.steps == original.steps          # steps keep the site's 2-portion text
    assert out.display_portions == 4
    assert out.source_portions == 2


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
