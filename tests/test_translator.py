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


# --------------------------------------------------------------------------- #
# Gemini step translation: guardrails and DeepL fallback
# --------------------------------------------------------------------------- #
from fractions import Fraction  # noqa: E402

from crisp_recipes.translator import (  # noqa: E402
    GeminiStepTranslator,
    accept_step_translations,
    check_step_translation,
    number_tokens,
    split_step_number,
)


def test_number_tokens_treats_notations_alike():
    assert number_tokens("½ tl en 0,5 cm") == number_tokens("1/2 tsp and 0.5 cm")
    assert number_tokens("1½ el") == {Fraction(3, 2): 1}
    assert number_tokens("1/3de van de kappertjes") == {Fraction(1, 3): 1}
    assert number_tokens("Bak 15-20 minuten op 200 graden") == {
        Fraction(15): 1, Fraction(20): 1, Fraction(200): 1}


def test_check_accepts_faithful_translation():
    assert check_step_translation(
        "Schil 400 g zoete aardappel en snij in plakjes van 0.5 cm dik.",
        "Peel 400 g sweet potato and cut into slices 0.5 cm thick.",
    ) is None


def test_check_rejects_changed_dropped_or_invented_numbers():
    dutch = "Bak 15-20 minuten in de oven op 200 graden."
    assert "numbers differ" in check_step_translation(
        dutch, "Bake for 15-20 minutes at 180 degrees.")            # changed
    assert "numbers differ" in check_step_translation(
        dutch, "Bake in the oven at 200 degrees.")                   # dropped
    assert "numbers differ" in check_step_translation(
        "Voeg zout toe.", "Add 1 tsp salt.")                         # invented


def test_check_rejects_added_content_by_length():
    dutch = "Snij de lente-ui in ringetjes en zet apart voor later."
    padded = dutch + " Meanwhile, toast some sesame seeds in a dry pan until golden and fragrant."
    assert "length ratio" in check_step_translation(dutch, padded)
    assert check_step_translation(dutch, "") == "empty"


def test_split_step_number():
    assert split_step_number("3. Schil de ui.") == ("3", "Schil de ui.")
    assert split_step_number("Schil de ui.") == (None, "Schil de ui.")


def test_accept_rejects_missing_duplicated_and_ignores_extra_steps():
    dutch = ["Kook 150 g pasta.", "Snij de ui.", "Meng alles."]
    raw = {"steps": [
        {"n": 1, "text": "Cook 150 g pasta."},
        {"n": 2, "text": "Slice the onion."},
        {"n": 2, "text": "Dice the onion."},       # answered twice -> rejected
        {"n": 4, "text": "Serve with a salad."},   # invented step -> ignored
    ]}                                              # step 3 missing -> rejected
    accepted, problems = accept_step_translations(dutch, raw)
    assert accepted == ["Cook 150 g pasta.", None, None]
    assert "ignored extra steps [4]" in problems
    assert "step 2: answered twice" in problems and "step 3: missing" in problems


class _FakeGemini(GeminiStepTranslator):
    def __init__(self, answer=None, error=None):
        super().__init__(api_key="x")
        self.answer, self.error, self.prompt = answer, error, None

    def _ask(self, prompt):
        self.prompt = prompt
        if self.error:
            raise self.error
        return self.answer


def _two_step_recipe():
    return Recipe(
        title="Pasta",
        steps=["1. Kook 150 g pasta.", "2. Bak de ui 5 minuten."],
        ingredients=[Ingredient(name="ui", amount="1")],
    )


def test_gemini_steps_used_and_bad_step_falls_back_to_deepl():
    gemini = _FakeGemini({"steps": [
        {"n": 1, "text": "Cook 150 g pasta."},
        {"n": 2, "text": "Fry the onion for 8 minutes."},   # wrong number -> DeepL
    ]})
    t = _translator_with_fake()
    out = t.translate_recipe(_two_step_recipe(), gemini)

    assert out.steps == ["1. Cook 150 g pasta.", "2. EN:Bak de ui 5 minuten."]
    # The glossary carries DeepL's ingredient names into the Gemini prompt.
    assert "ui -> EN:ui" in gemini.prompt
    # Only the rejected step went to DeepL for steps; the first batch had no steps.
    assert t._client.last_batch == ["Bak de ui 5 minuten."]


def test_gemini_failure_falls_back_to_deepl_for_all_steps():
    t = _translator_with_fake()
    out = t.translate_recipe(_two_step_recipe(), _FakeGemini(error=RuntimeError("quota")))
    assert out.steps == ["1. EN:Kook 150 g pasta.", "2. EN:Bak de ui 5 minuten."]
    assert out.title == "EN:Pasta"
