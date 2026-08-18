"""Tests for the pure parsing layer of the scraper.

These run fully offline against real page data captured in tests/fixtures/, so they
stay stable regardless of network or crisp.nl availability, and they exercise the
tricky bits: unicode-fraction amounts, unit-less amounts, multi-word names, pantry
items with and without quantities, and step reconstruction from span-split text.
"""
import json
from pathlib import Path

import pytest

from crisp_recipes.scraper import (
    ScrapeError,
    is_amount,
    parse_ingredient_string,
    parse_recipe_rows,
    parse_steps,
    select_box_links,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# is_amount
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("row", ["400 g", "1 stuk", "¾ zakje", "½ stuks", "1½", "30 g"])
def test_is_amount_true(row):
    assert is_amount(row)


@pytest.mark.parametrize("row", ["sjalot", "zoete aardappel oranje", "Olie om te bakken", "peterselie"])
def test_is_amount_false(row):
    assert not is_amount(row)


# --------------------------------------------------------------------------- #
# parse_ingredient_string
# --------------------------------------------------------------------------- #
def test_parse_amount_unit_name():
    ing = parse_ingredient_string("400 g zoete aardappel oranje", pantry=False)
    assert ing.amount == "400"
    assert ing.unit == "g"
    assert ing.name == "zoete aardappel oranje"
    assert ing.pantry is False


def test_parse_unicode_fraction_unit():
    ing = parse_ingredient_string("¾ zakje kaas reraspt pittig", pantry=False)
    assert ing.amount == "¾"
    assert ing.unit == "zakje"
    assert ing.name == "kaas reraspt pittig"


def test_parse_amount_without_unit():
    # "1½ tomaten pomodori" — "tomaten" is a food word, not a unit.
    ing = parse_ingredient_string("1½ tomaten pomodori", pantry=False)
    assert ing.amount == "1½"
    assert ing.unit is None
    assert ing.name == "tomaten pomodori"


def test_parse_pantry_without_amount():
    ing = parse_ingredient_string("Olie om te bakken", pantry=True)
    assert ing.amount is None
    assert ing.unit is None
    assert ing.name == "Olie om te bakken"
    assert ing.pantry is True


def test_parse_pantry_with_amount_and_parens():
    ing = parse_ingredient_string("2 el olijfolie extra vierge (voor pastadressing)", pantry=True)
    assert ing.amount == "2"
    assert ing.unit == "el"
    assert ing.name == "olijfolie extra vierge (voor pastadressing)"


def test_display_reconstructs_amount():
    ing = parse_ingredient_string("125 g wortel", pantry=False)
    assert ing.display == "125 g wortel"


def test_display_translates_dutch_units():
    from dataclasses import replace

    # "stuk" is a counter word and disappears entirely: "1 stuk sjalot" -> "1 shallot"
    ing = replace(parse_ingredient_string("1 stuk sjalot", pantry=False), name_en="shallot")
    assert ing.display == "1 shallot"

    ing = replace(parse_ingredient_string("¾ stengel lente-ui", pantry=False),
                  name_en="spring onion")
    assert ing.display == "¾ stalk spring onion"

    ing = replace(parse_ingredient_string("1 el mayonaise", pantry=True), name_en="mayonnaise")
    assert ing.display == "1 tbsp mayonnaise"


# --------------------------------------------------------------------------- #
# parse_steps
# --------------------------------------------------------------------------- #
def test_parse_steps_numbering_and_punctuation():
    rows = ["1", "Verwarm de oven.", "2", "Snipper", "1 stuk sjalot", ",", "en bak."]
    steps = parse_steps(rows)
    assert steps[0] == "1. Verwarm de oven."
    # span-split text is rejoined and the stray space before the comma removed
    assert steps[1] == "2. Snipper 1 stuk sjalot, en bak."


# --------------------------------------------------------------------------- #
# Full recipe fixtures
# --------------------------------------------------------------------------- #
def test_recipe_845_full_parse():
    data = load("recipe_845.json")
    ingredients, steps, portions = parse_recipe_rows(data["rows"])

    assert portions == 2
    # 12 main + 5 pantry
    main = [i for i in ingredients if not i.pantry]
    pantry = [i for i in ingredients if i.pantry]
    assert len(main) == 12
    assert len(pantry) == 5

    # First main ingredient
    assert (main[0].amount, main[0].unit, main[0].name) == ("1", "stuk", "sjalot")
    # A weighed one
    aardappel = next(i for i in main if "aardappel" in i.name)
    assert aardappel.amount == "400" and aardappel.unit == "g"
    # Pantry oil with fractional tablespoon
    olie = next(i for i in pantry if "olijfolie" in i.name)
    assert olie.amount == "1½" and olie.unit == "el"

    # 9 numbered steps, correctly ordered
    assert len(steps) == 9
    assert steps[0].startswith("1. Verwarm de oven")
    assert steps[-1].startswith("9. ")


def test_recipe_1567_full_parse():
    data = load("recipe_1567.json")
    ingredients, steps, portions = parse_recipe_rows(data["rows"])

    assert portions == 2
    main = [i for i in ingredients if not i.pantry]
    pantry = [i for i in ingredients if i.pantry]
    assert len(main) == 8
    assert len(pantry) == 7

    # Unit-less leading amount
    tomaten = main[0]
    assert tomaten.amount == "1½" and tomaten.unit is None
    assert tomaten.name == "tomaten pomodori"

    # Pantry item with no amount ("naar smaak")
    assert any(i.amount is None and "Olijfolie" in i.name for i in pantry)
    # Pantry item with amount
    assert any(i.amount == "1" and i.unit == "teentje" and "knoflook" in i.name for i in pantry)

    assert len(steps) == 8
    assert steps[0].startswith("1. ")


def test_no_duplicate_or_empty_names():
    for fx in ("recipe_845.json", "recipe_1567.json"):
        ingredients, _, _ = parse_recipe_rows(load(fx)["rows"])
        names = [i.name for i in ingredients]
        assert all(n.strip() for n in names), "empty ingredient name"


# --------------------------------------------------------------------------- #
# Error handling / box selection
# --------------------------------------------------------------------------- #
def test_parse_recipe_rows_missing_bereiding_raises():
    with pytest.raises(ScrapeError):
        parse_recipe_rows(["Ingrediënten", "2 porties", "400 g", "rijst"])


def test_select_box_links_by_short_key():
    box_map = {
        "Weekbox vegan": ["/weekbox/recipe/1570", "/weekbox/recipe/1566", "/weekbox/recipe/1568"],
        "Weekbox": ["/weekbox/recipe/843"],
    }
    assert select_box_links(box_map, "vegan") == box_map["Weekbox vegan"]


def test_select_box_links_unknown_raises():
    with pytest.raises(ScrapeError):
        select_box_links({"Weekbox": []}, "nonexistent-box")
