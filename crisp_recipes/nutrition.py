"""Nutrition facts from USDA FoodData Central.

Pipeline per recipe:

1. **Gemini → grams + query.** Ingredient amounts on crisp.nl are human units
   ("1 teentje", "¾ zakje", "naar smaak"), not weights. Gemini converts each to an
   estimated weight in grams and proposes a concise English search term. This is the
   only estimated step; the nutrient *values* themselves are real. One batched call
   per recipe.
2. **USDA FoodData Central lookup.** Each search term is looked up in the free USDA
   database; we read the food's per-100 g nutrient panel, matched by stable USDA
   nutrient numbers (see `crisp_recipes.nutrients`).
3. **Scale + aggregate.** Per-100 g values are scaled by the ingredient's grams and
   summed into the recipe's `nutrition_total` across all 28 tracked nutrients.

Everything that talks to the network is injectable, so the logic is unit tested with
fake USDA/Gemini clients (no key, no network). Missing data degrades gracefully: an
ingredient we can't weigh or match is skipped (logged), not fatal.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import requests

from crisp_recipes.models import Ingredient, NutritionFacts, Recipe
from crisp_recipes.nutrients import BY_USDA_NUMBER

log = logging.getLogger(__name__)

FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
# Prefer curated, per-100 g datasets over noisy branded items.
DEFAULT_DATA_TYPES = ("SR Legacy", "Foundation", "Survey (FNDDS)")

# Description words that signal a processed/derivative form we usually don't want when
# matching a plain recipe ingredient (heavily penalized in scoring).
_BAD_WORDS = {
    "leaves", "leaf", "sauce", "chips", "fries", "fried", "frozen", "pie", "powder",
    "juice", "dried", "dehydrated", "tots", "paste", "snack", "snacks", "roll", "rolls",
    "soup", "bread", "cake", "candied", "baby", "flour", "puree", "muffin", "pickled",
    "canned", "cooked", "roasted", "with", "substitute",
}

# Convert a USDA mass unit to grams, so we can re-express in each nutrient's spec unit.
_MASS_TO_GRAMS = {"G": 1.0, "MG": 1e-3, "UG": 1e-6, "µG": 1e-6, "MCG": 1e-6}
_SPEC_UNIT_FROM_GRAMS = {"g": 1.0, "mg": 1e3, "ug": 1e6}


# --------------------------------------------------------------------------- #
# Pure helpers (no network) — unit tested.
# --------------------------------------------------------------------------- #
def per_100g_nutrients(food: dict) -> Dict[str, float]:
    """Extract a {nutrient_key: value_in_spec_unit} map (per 100 g) from a USDA food."""
    out: Dict[str, float] = {}
    for fn in food.get("foodNutrients", []):
        number = str(fn.get("nutrientNumber") or fn.get("number") or "")
        spec = BY_USDA_NUMBER.get(number)
        if not spec:
            continue
        value = fn.get("value")
        if value is None:
            continue
        unit = str(fn.get("unitName") or fn.get("unit") or "").upper()
        converted = _convert_to_spec_unit(float(value), unit, spec.unit)
        if converted is not None:
            out[spec.key] = converted
    return out


def _convert_to_spec_unit(value: float, usda_unit: str, spec_unit: str) -> Optional[float]:
    """Convert a USDA value to our spec's unit. Energy passes through (kcal)."""
    if spec_unit == "kcal":
        return value  # energy is already in kcal (nutrient 208)
    grams = _MASS_TO_GRAMS.get(usda_unit)
    if grams is None:
        # Unknown/incompatible unit (e.g. IU for some vitamins) — skip rather than guess.
        return None
    return value * grams * _SPEC_UNIT_FROM_GRAMS[spec_unit]


def scale_nutrients(per_100g: Dict[str, float], grams: float) -> NutritionFacts:
    """Scale a per-100 g nutrient map to an absolute amount for `grams` grams."""
    factor = grams / 100.0
    facts = NutritionFacts()
    for key, value in per_100g.items():
        facts.add_amount(key, value * factor)
    return facts


def aggregate(items: List[NutritionFacts]) -> NutritionFacts:
    total = NutritionFacts()
    for item in items:
        total = total + item
    return total


# --------------------------------------------------------------------------- #
# Candidate scoring — pick the best USDA food for a query (not blindly foods[0]).
# --------------------------------------------------------------------------- #
_DATATYPE_BONUS = {"SR Legacy": 3, "Foundation": 2, "Survey (FNDDS)": 1}

# Words that describe *how* a food is prepared rather than *what* it is. They carry no
# identity, so a candidate must never qualify on these alone — "cumin, ground" matching
# "Chicken, ground" purely via "ground" is exactly the failure this prevents.
_MODIFIER_WORDS = {
    "raw", "ground", "canned", "cooked", "fresh", "frozen", "dried", "whole",
    "chopped", "sliced", "shredded", "grated", "unprepared", "prepared", "plain",
    "extra", "virgin", "light", "low", "fat", "free", "reduced", "salted", "unsalted",
    "seed", "seeds", "powder", "leaf", "leaves",
}


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z]+", text.lower())


def _singular(token: str) -> str:
    """Naive singularizer good enough for food words: potatoes->potato,
    tomatoes->tomato, berries->berry, carrots->carrot."""
    if len(token) > 4 and token.endswith("oes"):
        return token[:-2]          # potatoes -> potato, tomatoes -> tomato
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"    # berries -> berry
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]          # carrots -> carrot, limes -> lime
    return token


def score_food(query: str, food: dict) -> float:
    """Higher is better. Rewards matching all query words and having energy data;
    penalizes processed-form words and extra descriptors."""
    desc_tokens = _tokens(food.get("description", ""))
    q_tokens = _tokens(query)
    desc_norm = {_singular(t) for t in desc_tokens}
    q_norm = {_singular(t) for t in q_tokens}

    all_present = q_norm.issubset(desc_norm)
    extra = len(desc_norm - q_norm - {"raw"})
    # Penalize processed-form words unless the query itself asked for them.
    bad = sum(1 for t in desc_tokens if t in _BAD_WORDS and t not in q_tokens)
    raw_bonus = 20 if "raw" in desc_tokens else 0
    has_energy = any(
        str(n.get("nutrientNumber")) == "208" and n.get("value") is not None
        for n in food.get("foodNutrients", [])
    )
    dt_bonus = _DATATYPE_BONUS.get(food.get("dataType"), 0)

    return (
        (1000 if all_present else 0)
        + (200 if has_energy else -500)
        - 8 * extra
        - 100 * bad
        + raw_bonus
        + dt_bonus
    )


def content_tokens(query: str) -> List[str]:
    """The identity-bearing words of a query (modifiers like 'ground'/'raw' removed).

    'cumin, ground' -> ['cumin'];  'sweet potato, raw' -> ['sweet', 'potato'].
    Falls back to all tokens if the query is nothing but modifiers.
    """
    tokens = [_singular(t) for t in _tokens(query)]
    content = [t for t in tokens if t not in _MODIFIER_WORDS]
    return content or tokens


def is_plausible_match(query: str, food: dict) -> bool:
    """True only if the food's description contains the query's identity words.

    This is a hard gate, not a score: without it USDA's search happily offers
    'Chicken, ground' for 'cumin, ground' (matching only the modifier), which would
    silently put meat into a vegan recipe's nutrition. We accept a full match, or a
    match on the head noun (last content word, e.g. 'potato' in 'sweet potato').
    """
    desc = {_singular(t) for t in _tokens(food.get("description", ""))}
    content = content_tokens(query)
    if not content:
        return False
    if set(content).issubset(desc):
        return True
    return content[-1] in desc  # head noun alone is acceptable


def pick_best_food(query: str, foods: List[dict]) -> Optional[dict]:
    """Choose the best plausible food that has energy data, or None.

    Returning None is deliberate: an ingredient omitted from the totals is far less
    harmful than a wrong food silently inflating them.
    """
    if not foods:
        return None
    plausible = [f for f in foods if is_plausible_match(query, f)]
    if not plausible:
        log.warning("No plausible USDA match for %r (best was %r); skipping",
                    query, foods[0].get("description", "")[:60])
        return None
    ranked = sorted(plausible, key=lambda f: score_food(query, f), reverse=True)
    for food in ranked:
        if "calories" in per_100g_nutrients(food):
            return food
    return ranked[0]  # plausible but no energy data; caller handles empty nutrients


@dataclass
class FoodMatch:
    description: str
    per_100g: Dict[str, float]


@dataclass
class GramsEstimate:
    index: int
    grams: float
    usda_query: str


def parse_grams_response(raw) -> List[GramsEstimate]:
    """Normalize Gemini's JSON (list of {index, grams, usda_query}) into estimates."""
    if isinstance(raw, dict):
        raw = raw.get("ingredients") or raw.get("items") or []
    estimates: List[GramsEstimate] = []
    for item in raw:
        try:
            estimates.append(
                GramsEstimate(
                    index=int(item["index"]),
                    grams=float(item["grams"]),
                    usda_query=str(item["usda_query"]).strip(),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return estimates


# --------------------------------------------------------------------------- #
# USDA client
# --------------------------------------------------------------------------- #
class USDAClient:
    """FoodData Central search client with retry/backoff, candidate scoring and an
    in-run cache."""

    def __init__(
        self,
        api_key: str,
        session: Optional[requests.Session] = None,
        data_types=DEFAULT_DATA_TYPES,
        timeout: int = 25,
        max_retries: int = 6,
    ):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.data_types = list(data_types)
        self.timeout = timeout
        self.max_retries = max_retries
        self._cache: Dict[str, Optional[FoodMatch]] = {}

    def lookup(self, query: str) -> Optional[FoodMatch]:
        """Search USDA for `query` and return the best-matching food, or None."""
        key = query.lower().strip()
        if key in self._cache:
            return self._cache[key]
        result = self._search(query)
        self._cache[key] = result
        return result

    def _request(self, query: str) -> Optional[List[dict]]:
        """GET the search endpoint with retry/backoff. USDA's nginx intermittently
        returns 400/429 even for valid requests, so we retry before giving up."""
        params = {
            "query": query,
            "pageSize": 25,
            "dataType": self.data_types,
            "api_key": self.api_key,
        }
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(FDC_SEARCH_URL, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json().get("foods", [])
                log.warning("USDA %s for %r (attempt %d)", resp.status_code, query, attempt + 1)
            except Exception:
                log.warning("USDA request error for %r (attempt %d)", query, attempt + 1)
            time.sleep(0.8 * (attempt + 1))
        log.error("USDA search gave up for %r", query)
        return None

    def _search(self, query: str) -> Optional[FoodMatch]:
        foods = self._request(query)
        if not foods:
            return None
        best = pick_best_food(query, foods)
        if not best:
            log.warning("No usable USDA match for %r", query)
            return None
        return FoodMatch(
            description=best.get("description", ""),
            per_100g=per_100g_nutrients(best),
        )


# --------------------------------------------------------------------------- #
# Gemini grams estimator
# --------------------------------------------------------------------------- #
_GRAMS_PROMPT = """You are matching Dutch recipe ingredients to the USDA FoodData
Central database. For each ingredient return an estimated weight in grams and a search
term that will find it in USDA.

Rules for usda_query:
- Use AMERICAN English as USDA spells it: zucchini (not courgette), arugula (not
  rocket/rucola), eggplant (not aubergine), cilantro (not coriander leaf), scallion or
  green onion (not spring onion), garbanzo/chickpea, bell pepper.
- Prefer the plain raw whole food, and append ", raw" for fresh produce
  (e.g. "sweet potato, raw", "carrot, raw", "tomato, raw"). For clearly processed
  items keep their form ("olive oil", "black beans, canned", "panko bread crumbs").
- No brand names, no quantities.

Rules for grams:
- The weight in grams for the stated amount. If the amount is vague ("naar smaak"/to
  taste, "olie om te bakken"/for frying, or missing), estimate a typical amount
  actually used in the dish (e.g. oil for frying ~10 g).

Return one object per ingredient, preserving the given index.

Ingredients:
{ingredients}
"""

_GRAMS_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "index": {"type": "INTEGER"},
            "grams": {"type": "NUMBER"},
            "usda_query": {"type": "STRING"},
        },
        "required": ["index", "grams", "usda_query"],
    },
}


def _ingredient_line(index: int, ing: Ingredient) -> str:
    name = ing.name_en or ing.name
    amount = " ".join(p for p in (ing.amount, ing.unit) if p)
    suffix = " [pantry, adjust to taste]" if ing.pantry and not ing.amount else ""
    return f"{index}. {amount + ' ' if amount else ''}{name}{suffix}"


def estimate_grams_gemini(ingredients: List[Ingredient], api_key: str) -> List[GramsEstimate]:
    """Ask Gemini for grams + USDA query per ingredient. Raises on API error."""
    from google import genai  # local import: heavy optional dependency

    client = genai.Client(api_key=api_key)
    listing = "\n".join(_ingredient_line(i, ing) for i, ing in enumerate(ingredients))
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=_GRAMS_PROMPT.format(ingredients=listing),
        config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
            "response_json_schema": _GRAMS_SCHEMA,
        },
    )
    return parse_grams_response(json.loads(response.text))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def annotate_recipe_nutrition(
    recipe: Recipe,
    usda: USDAClient,
    grams_estimator: Callable[[List[Ingredient]], List[GramsEstimate]],
) -> Recipe:
    """Fill in ingredient grams and the recipe's `nutrition_total`. Deps are injected
    so this is fully testable with fakes."""
    if not recipe.ingredients:
        return recipe

    estimates = {e.index: e for e in grams_estimator(recipe.ingredients)}
    per_ingredient: List[NutritionFacts] = []

    for i, ing in enumerate(recipe.ingredients):
        est = estimates.get(i)
        if not est or est.grams <= 0:
            log.warning("No grams estimate for %r; skipping", ing.name)
            continue
        ing.grams = est.grams
        match = usda.lookup(est.usda_query)
        if not match or not match.per_100g:
            continue
        ing.usda_description = match.description
        per_ingredient.append(scale_nutrients(match.per_100g, est.grams))

    if per_ingredient:
        recipe.nutrition_total = aggregate(per_ingredient).rounded(1)
    return recipe


def add_nutrition(
    recipes: List[Recipe],
    settings,
    *,
    usda: Optional[USDAClient] = None,
    grams_estimator: Optional[Callable[[List[Ingredient]], List[GramsEstimate]]] = None,
) -> List[Recipe]:
    """Annotate every recipe with nutrition. On per-recipe failure, log and continue."""
    usda = usda or USDAClient(settings.usda_fdc_api_key)
    if grams_estimator is None:
        def grams_estimator(ings: List[Ingredient]) -> List[GramsEstimate]:
            return estimate_grams_gemini(ings, settings.google_api_key)

    for recipe in recipes:
        try:
            annotate_recipe_nutrition(recipe, usda, grams_estimator)
        except Exception:
            log.exception("Nutrition failed for %r; leaving it without facts", recipe.title)
    return recipes
