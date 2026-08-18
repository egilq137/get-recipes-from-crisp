"""Tests for the nutrition pipeline using fake USDA + Gemini (no key, no network).

Covers: per-100 g extraction and unit conversion (g/mg/µg/kcal, IU skipped), scaling
by grams, aggregation across ingredients, grams-response parsing, and graceful
degradation when an ingredient can't be weighed or matched.
"""
from crisp_recipes.models import Ingredient, Recipe
from crisp_recipes.nutrition import (
    FoodMatch,
    GramsEstimate,
    USDAClient,
    aggregate,
    annotate_recipe_nutrition,
    parse_grams_response,
    per_100g_nutrients,
    pick_best_food,
    scale_nutrients,
    score_food,
)


# A trimmed USDA /foods/search food object (values are per 100 g).
OLIVE_OIL = {
    "description": "Oil, olive",
    "foodNutrients": [
        {"nutrientNumber": "208", "unitName": "KCAL", "value": 884},
        {"nutrientNumber": "204", "unitName": "G", "value": 100},   # fat
        {"nutrientNumber": "323", "unitName": "MG", "value": 14.35},  # vit E
        {"nutrientNumber": "307", "unitName": "MG", "value": 2},     # sodium
        {"nutrientNumber": "999", "unitName": "IU", "value": 123},   # unknown -> ignored
    ],
}
SWEET_POTATO = {
    "description": "Sweet potato, raw",
    "foodNutrients": [
        {"nutrientNumber": "208", "unitName": "KCAL", "value": 86},
        {"nutrientNumber": "203", "unitName": "G", "value": 1.6},    # protein
        {"nutrientNumber": "320", "unitName": "UG", "value": 709},   # vit A (µg)
        {"nutrientNumber": "306", "unitName": "MG", "value": 337},   # potassium
    ],
}


def test_per_100g_extraction_and_unit_skip():
    facts = per_100g_nutrients(OLIVE_OIL)
    assert facts["calories"] == 884
    assert facts["fat"] == 100
    assert facts["vit_e"] == 14.35   # mg stays mg
    assert facts["sodium"] == 2
    # IU nutrient (999) isn't tracked anyway, but even a tracked IU would be skipped.
    assert "999" not in facts


def test_microgram_conversion_stays_micrograms():
    facts = per_100g_nutrients(SWEET_POTATO)
    # vit A spec unit is 'ug'; USDA value already µg -> unchanged
    assert facts["vit_a"] == 709
    assert facts["potassium"] == 337


def test_scale_nutrients_by_grams():
    per100 = per_100g_nutrients(OLIVE_OIL)
    facts = scale_nutrients(per100, 15)  # 15 g of olive oil
    assert round(facts.get("calories"), 1) == round(884 * 0.15, 1)  # 132.6
    assert round(facts.get("fat"), 1) == 15.0


def test_aggregate_sums_across_ingredients():
    a = scale_nutrients(per_100g_nutrients(OLIVE_OIL), 15)
    b = scale_nutrients(per_100g_nutrients(SWEET_POTATO), 400)
    total = aggregate([a, b])
    assert round(total.get("calories"), 1) == round(884 * 0.15 + 86 * 4.0, 1)


def test_parse_grams_response_variants():
    raw = [
        {"index": 0, "grams": 400, "usda_query": "sweet potato"},
        {"index": 1, "grams": "15", "usda_query": " olive oil "},
        {"index": 2, "grams": 10},           # missing query -> dropped
    ]
    out = parse_grams_response(raw)
    assert [e.index for e in out] == [0, 1]
    assert out[1].grams == 15.0 and out[1].usda_query == "olive oil"


class _FakeUSDA(USDAClient):
    """USDA client backed by an in-memory table instead of HTTP."""

    def __init__(self, table):
        super().__init__(api_key="x")
        self._table = table

    def lookup(self, query):
        per_100g = self._table.get(query.lower().strip())
        if per_100g is None:
            return None
        return FoodMatch(description=query, per_100g=per_100g)


def _fake_estimator(mapping):
    def estimator(ingredients):
        return [
            GramsEstimate(index=i, grams=g, usda_query=q)
            for i, (g, q) in mapping.items()
        ]
    return estimator


def _food(desc, dtype="SR Legacy", kcal=100):
    nutrients = [] if kcal is None else [{"nutrientNumber": "208", "unitName": "KCAL", "value": kcal}]
    return {"description": desc, "dataType": dtype, "foodNutrients": nutrients}


def test_scorer_prefers_plain_food_over_sauce():
    foods = [
        _food("Garlic sauce", "Survey (FNDDS)", 683),
        _food("Garlic, raw", "Foundation", 143),
    ]
    assert pick_best_food("garlic, raw", foods)["description"] == "Garlic, raw"


def test_scorer_rejects_leaves_for_tuber():
    foods = [
        _food("Sweet potato leaves, raw", "SR Legacy", 42),
        _food("Sweet potatoes, raw", "SR Legacy", 86),  # note plural 'potatoes'
    ]
    # singularization + 'leaves' penalty should pick the tuber
    assert pick_best_food("sweet potato, raw", foods)["description"] == "Sweet potatoes, raw"


def test_scorer_skips_candidate_without_energy():
    foods = [
        _food("Oil, olive, extra virgin", "Foundation", None),  # no energy
        _food("Olive oil", "Survey (FNDDS)", 900),
    ]
    assert pick_best_food("olive oil", foods)["description"] == "Olive oil"


def test_rejects_match_on_modifier_word_only():
    """Regression: USDA offered 'Chicken, ground' for 'cumin, ground' — matching only
    the modifier 'ground' — which silently put meat in a vegan recipe."""
    foods = [
        _food("Chicken, ground", "Survey (FNDDS)", 143),
        _food("Beef, ground", "Survey (FNDDS)", 250),
    ]
    assert pick_best_food("cumin, ground", foods) is None


def test_picks_real_spice_over_meat_for_cumin():
    foods = [
        _food("Chicken, ground", "Survey (FNDDS)", 143),
        _food("Spices, cumin seed", "SR Legacy", 375),
    ]
    assert pick_best_food("cumin, ground", foods)["description"] == "Spices, cumin seed"


def test_head_noun_match_is_accepted():
    # 'Peppers, sweet, red, raw' doesn't contain the word order of the query but does
    # contain the head noun 'pepper'.
    foods = [_food("Peppers, sweet, red, raw", "SR Legacy", 26)]
    assert pick_best_food("red pepper, raw", foods) is not None


def test_content_tokens_strips_modifiers():
    from crisp_recipes.nutrition import content_tokens

    assert content_tokens("cumin, ground") == ["cumin"]
    assert content_tokens("sweet potato, raw") == ["sweet", "potato"]
    # a query of only modifiers falls back to all tokens rather than emptying out
    assert content_tokens("raw") == ["raw"]


def test_no_plausible_match_returns_none_not_wrong_food():
    foods = [_food("Unrelated food item", "SR Legacy", 100)]
    assert pick_best_food("dragonfruit", foods) is None


def test_score_food_all_tokens_present_beats_partial():
    full = _food("Carrots, raw", "SR Legacy", 41)
    partial = _food("Carrot cake", "Survey (FNDDS)", 400)
    assert score_food("carrot, raw", full) > score_food("carrot, raw", partial)


def test_annotate_recipe_end_to_end():
    recipe = Recipe(
        title="Test",
        steps=["1. cook"],
        ingredients=[
            Ingredient(name="zoete aardappel", name_en="sweet potato", amount="400", unit="g"),
            Ingredient(name="olijfolie", name_en="olive oil", amount="1", unit="el", pantry=True),
        ],
        source_portions=2,
    )
    usda = _FakeUSDA({
        "sweet potato": per_100g_nutrients(SWEET_POTATO),
        "olive oil": per_100g_nutrients(OLIVE_OIL),
    })
    estimator = _fake_estimator({0: (400, "sweet potato"), 1: (15, "olive oil")})

    annotate_recipe_nutrition(recipe, usda, estimator)

    assert recipe.ingredients[0].grams == 400
    assert recipe.ingredients[1].grams == 15
    # The matched USDA food is recorded for transparency.
    assert recipe.ingredients[0].usda_description == "sweet potato"
    total = recipe.nutrition_total
    assert total is not None
    # calories = 86*4 + 884*0.15 = 344 + 132.6 = 476.6
    assert round(total.get("calories"), 1) == 476.6
    # per serving (2 portions)
    assert round(recipe.nutrition_per_serving().get("calories"), 1) == 238.3


def test_ingredient_without_match_is_skipped_not_fatal():
    recipe = Recipe(
        title="Test",
        steps=["1."],
        ingredients=[
            Ingredient(name="x", name_en="sweet potato", amount="400", unit="g"),
            Ingredient(name="mystery", name_en="unobtainium", amount="1", unit="stuk"),
        ],
        source_portions=2,
    )
    usda = _FakeUSDA({"sweet potato": per_100g_nutrients(SWEET_POTATO)})
    estimator = _fake_estimator({0: (400, "sweet potato"), 1: (50, "unobtainium")})

    annotate_recipe_nutrition(recipe, usda, estimator)
    # totals reflect only the matched ingredient; no crash
    assert round(recipe.nutrition_total.get("calories"), 1) == round(86 * 4.0, 1)


def test_no_grams_estimate_skips_ingredient():
    recipe = Recipe(
        title="Test",
        steps=["1."],
        ingredients=[Ingredient(name="x", name_en="sweet potato", amount="400", unit="g")],
    )
    usda = _FakeUSDA({"sweet potato": per_100g_nutrients(SWEET_POTATO)})
    estimator = _fake_estimator({})  # returns nothing
    annotate_recipe_nutrition(recipe, usda, estimator)
    assert recipe.nutrition_total is None
