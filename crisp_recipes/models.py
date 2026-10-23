"""Pure data structures. No I/O, no API clients, no side effects.

Scraping, translation, cooking-time estimation, nutrition lookup, rendering and
email all live in their own modules and operate on these objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Dict, List, Optional

from crisp_recipes.nutrients import BY_KEY

# crisp.nl writes amounts with unicode vulgar fractions ("¾ zakje", "1½ el").
_FRACTION_VALUES = {
    "¼": Fraction(1, 4), "½": Fraction(1, 2), "¾": Fraction(3, 4),
    "⅐": Fraction(1, 7), "⅑": Fraction(1, 9), "⅒": Fraction(1, 10),
    "⅓": Fraction(1, 3), "⅔": Fraction(2, 3), "⅕": Fraction(1, 5),
    "⅖": Fraction(2, 5), "⅗": Fraction(3, 5), "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6), "⅚": Fraction(5, 6), "⅛": Fraction(1, 8),
    "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8),
}
_VALUE_FRACTIONS = {v: k for k, v in _FRACTION_VALUES.items()}


def parse_amount(text: Optional[str]) -> Optional[Fraction]:
    """Parse '400', '¾', '1½' into an exact Fraction. None if unparseable."""
    if not text:
        return None
    text = text.strip()
    whole = ""
    frac = Fraction(0)
    for ch in text:
        if ch in _FRACTION_VALUES:
            frac += _FRACTION_VALUES[ch]
        elif ch.isdigit() or ch in ".,":
            whole += "." if ch == "," else ch
        else:
            return None
    try:
        base = Fraction(whole) if whole else Fraction(0)
    except (ValueError, ZeroDivisionError):
        return None
    total = base + frac
    return total if total > 0 else None


def format_amount_value(value: Fraction) -> str:
    """Render a Fraction back the way crisp writes it: '800', '1½', '⅔'."""
    whole = int(value)
    remainder = value - whole
    if remainder == 0:
        return str(whole)
    glyph = _VALUE_FRACTIONS.get(remainder)
    if glyph:
        return f"{whole}{glyph}" if whole else glyph
    # No neat glyph (e.g. 2/7) — fall back to a short decimal.
    as_float = float(value)
    return f"{as_float:.2f}".rstrip("0").rstrip(".")

# Dutch measure words -> English, for display only. Units are never sent to DeepL
# (only food names are), so we map the handful crisp.nl uses.
UNIT_LABELS_EN = {
    "stuk": "", "stuks": "", "st": "",           # "1 stuk sjalot" -> "1 shallot"
    "zakje": "bag", "zak": "bag",
    "bakje": "tub", "bak": "tub",
    "pakje": "pack", "pak": "pack",
    "blik": "tin", "blikje": "tin",
    "bol": "ball",
    "bosje": "bunch", "bos": "bunch",
    "stengel": "stalk", "stengels": "stalks",
    "kropje": "head", "krop": "head",
    "teen": "clove", "teentje": "clove", "tenen": "cloves",
    "snuf": "pinch", "snufje": "pinch",
    "handje": "handful", "hand": "handful",
    "plak": "slice", "plakje": "slice", "plakken": "slices",
    "mespunt": "pinch", "mespuntje": "pinch",
    "druppel": "drop", "drupje": "drop",
    "scheutje": "splash", "scheut": "splash",
    "pot": "jar", "potje": "jar",
    "fles": "bottle",
    "blaadje": "leaf",
    "el": "tbsp", "eetlepel": "tbsp", "eetlepels": "tbsp",
    "tl": "tsp", "theelepel": "tsp", "theelepels": "tsp",
}


@dataclass
class Ingredient:
    """A single recipe ingredient with its amount.

    `name` is the original (Dutch) label as scraped. `name_en` is filled in after
    translation. `grams` is filled in by the nutrition step, which converts the
    human amount (e.g. "1 ui", "2 el") into an estimated weight in grams.
    """

    name: str
    amount: Optional[str] = None          # raw amount as shown, e.g. "200", "1", "1½"
    unit: Optional[str] = None            # raw unit as shown, e.g. "g", "stuk", "el"
    name_en: Optional[str] = None
    grams: Optional[float] = None
    # The USDA food description this ingredient was matched to (for transparency).
    usda_description: Optional[str] = None
    # Pantry ("Zelf toe te voegen") items — oil, spices, etc. you add yourself.
    # Often unquantified ("naar smaak"); flagged so the nutrition/render steps can
    # treat their weights as best-effort estimates.
    pantry: bool = False

    def scaled(self, factor: Fraction) -> "Ingredient":
        """Return a copy with the displayed amount multiplied by `factor`.

        Unquantified pantry items ("Olie om te bakken", "naar smaak") have nothing to
        scale and are returned unchanged. `grams` is intentionally NOT scaled: it feeds
        the nutrition totals, which stay pinned to the official portion count so that
        per-serving figures remain correct regardless of batch size.
        """
        if factor == 1:
            return self
        value = parse_amount(self.amount)
        if value is None:
            return self
        return replace(self, amount=format_amount_value(value * factor))

    @property
    def display(self) -> str:
        """Human-readable amount + name, e.g. '200 g tomato' or '1 onion'.

        Units are kept as scraped (Dutch) but shown in English when we have a mapping,
        since they are never sent to the translator — only the food name is.
        """
        label = self.name_en or self.name
        unit = UNIT_LABELS_EN.get((self.unit or "").lower(), self.unit) if self.unit else None
        qty = " ".join(part for part in (self.amount, unit) if part)
        return f"{qty} {label}".strip()


@dataclass
class NutritionFacts:
    """Aggregated nutrients (macros, vitamins and minerals).

    Values are absolute (already scaled by weight, not per 100 g) and keyed by the
    canonical nutrient keys defined in `crisp_recipes.nutrients`. Units are implied
    by each nutrient's spec, so we never mix them up. Missing nutrients are simply
    absent from the map rather than stored as zero, so `get()` can distinguish
    "no data" (None) from a real zero.
    """

    values: Dict[str, float] = field(default_factory=dict)

    def get(self, key: str) -> Optional[float]:
        return self.values.get(key)

    def add_amount(self, key: str, amount: float) -> None:
        """Accumulate `amount` (in the nutrient's canonical unit) under `key`."""
        if key not in BY_KEY:
            raise KeyError(f"Unknown nutrient key: {key}")
        self.values[key] = self.values.get(key, 0.0) + amount

    def __add__(self, other: "NutritionFacts") -> "NutritionFacts":
        combined = dict(self.values)
        for key, amount in other.values.items():
            combined[key] = combined.get(key, 0.0) + amount
        return NutritionFacts(values=combined)

    def scaled(self, factor: float) -> "NutritionFacts":
        """Return a copy with every value multiplied by `factor`."""
        return NutritionFacts(values={k: v * factor for k, v in self.values.items()})

    def rounded(self, digits: int = 1) -> "NutritionFacts":
        return NutritionFacts(
            values={k: round(v, digits) for k, v in self.values.items()}
        )


@dataclass
class Recipe:
    """A recipe scraped from crisp.nl, enriched as it moves through the pipeline."""

    title: str
    steps: List[str]
    ingredients: List[Ingredient] = field(default_factory=list)
    # Official "X min koken" from crisp.nl — active cooking only, excludes prep.
    cooking_time_minutes: Optional[int] = None
    # Prep-aware estimate (official + mise en place). None if the estimator couldn't run.
    realistic_time_minutes: Optional[int] = None
    nutrition_total: Optional[NutritionFacts] = None
    # How many portions `nutrition_total` corresponds to — always the portion count the
    # site published the amounts for (2). Nutrition is never rescaled: per-serving is
    # invariant, so this stays pinned to the official figures.
    source_portions: Optional[int] = None
    # How many portions the *displayed* ingredient/step amounts are written for. Equals
    # source_portions unless the amounts were scaled up for cooking.
    display_portions: Optional[int] = None
    # Senior-chef tips on cooking this dish with two people (parallelizable tasks, etc.).
    chef_notes: List[str] = field(default_factory=list)
    # Public URL of this recipe's phone-friendly page on the recipe site, once published.
    web_url: Optional[str] = None

    def combine_steps(self) -> str:
        return "\n".join(self.steps)

    @property
    def portions_for_amounts(self) -> Optional[int]:
        """How many portions the current ingredient amounts represent."""
        return self.display_portions or self.source_portions

    def nutrition_per_serving(
        self, servings: Optional[int] = None
    ) -> Optional[NutritionFacts]:
        """Per-serving nutrition.

        Divides by the portion count the *amounts* represent, so the result is the same
        whether the recipe was scaled up or not — per serving is invariant.
        """
        n = servings if servings is not None else self.portions_for_amounts
        if self.nutrition_total is None or not n or n <= 0:
            return None
        return self.nutrition_total.scaled(1 / n)

    def scaled_to_portions(self, target_portions: int) -> "Recipe":
        """Return a copy with the ingredient amounts scaled to `target_portions`.

        Only the ingredient list is scaled. The steps keep crisp.nl's own wording and
        amounts (for `source_portions`): rewriting numbers inside free text proved
        error-prone ("1el bij 2p" became "2el bij 2p"), and a step that silently
        disagrees with what's in the box causes more mistakes than it prevents.

        Nutrition is computed afterwards from these amounts and divided by
        `portions_for_amounts`, so per-serving figures are unaffected by the batch size.
        """
        current = self.display_portions or self.source_portions
        if not current or not target_portions or current == target_portions:
            return self
        factor = Fraction(target_portions, current)
        return replace(
            self,
            ingredients=[i.scaled(factor) for i in self.ingredients],
            display_portions=target_portions,
        )

    def with_translation(self, title: str, steps: List[str]) -> "Recipe":
        """Return a copy carrying translated title/steps but the same everything else."""
        return replace(self, title=title, steps=steps)
