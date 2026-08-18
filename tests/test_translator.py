"""Tests for the translation mapping logic, using a fake DeepL client so no API
key or network is needed. The important behavior is that a single batched call's
results are mapped back onto title / steps / ingredient names correctly."""
from crisp_recipes.models import Ingredient, Recipe
from crisp_recipes.translator import DeepLTranslator


class _FakeResult:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    """Records the batch it received and echoes each string with an 'EN:' prefix.

    Mimics real DeepL by also translating the Dutch "(ingrediënt)" disambiguation
    suffix into "(ingredient)", so the stripping logic is genuinely exercised.
    """

    def __init__(self):
        self.last_batch = None

    def translate_text(self, texts, target_lang=None, model_type=None):
        self.last_batch = list(texts)
        return [
            _FakeResult(f"EN:{t}".replace("(ingrediënt)", "(ingredient)"))
            for t in texts
        ]


def _translator_with_fake():
    t = DeepLTranslator(api_key="x")
    t._client = _FakeClient()  # inject fake, bypass lazy import of deepl
    return t


def test_translate_recipe_maps_fields_back():
    recipe = Recipe(
        title="Zomerse pasta",
        steps=["1. Kook de pasta.", "2. Meng alles."],
        ingredients=[
            Ingredient(name="tomaten pomodori", amount="1½"),
            Ingredient(name="olijfolie", amount="2", unit="el", pantry=True),
        ],
        cooking_time_minutes=25,
        source_portions=2,
    )
    t = _translator_with_fake()
    out = t.translate_recipe(recipe)

    assert out.title == "EN:Zomerse pasta"
    assert out.steps == ["EN:1. Kook de pasta.", "EN:2. Meng alles."]
    # the disambiguating suffix is sent to DeepL but stripped from the result
    assert out.ingredients[0].name_en == "EN:tomaten pomodori"
    assert out.ingredients[1].name_en == "EN:olijfolie"

    # Non-translated data is preserved.
    assert out.ingredients[0].amount == "1½"
    assert out.ingredients[1].unit == "el" and out.ingredients[1].pantry is True
    assert out.cooking_time_minutes == 25
    assert out.source_portions == 2


def test_single_batched_call_contains_everything():
    recipe = Recipe(
        title="T",
        steps=["s1", "s2"],
        ingredients=[Ingredient(name="ui"), Ingredient(name="knoflook")],
    )
    t = _translator_with_fake()
    t.translate_recipe(recipe)
    # One call, in order: title, steps..., then ingredient names with the suffix that
    # disambiguates bare food words (sjalot -> shallot, not "scarves").
    assert t._client.last_batch == [
        "T", "s1", "s2", "ui (ingrediënt)", "knoflook (ingrediënt)",
    ]


def test_ingredient_suffix_stripped_case_insensitively():
    from crisp_recipes.translator import _strip_ingredient_suffix

    assert _strip_ingredient_suffix("shallot (ingredient)") == "shallot"
    assert _strip_ingredient_suffix("spring onion (Ingredient)") == "spring onion"
    assert _strip_ingredient_suffix("olive oil") == "olive oil"  # nothing to strip


def test_empty_batch_returns_empty():
    t = _translator_with_fake()
    assert t.translate_batch([]) == []
