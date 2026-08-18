"""The canonical set of nutrients we track.

Pure data, no dependencies. Both the nutrition lookup (`nutrition.py`) and the
renderers (`render.py`) import this so the columns, order, units and labels stay
in one place. Each nutrient is matched from USDA FoodData Central by its stable
numeric `usda_number` (more reliable than the free-text name).

USDA reports every nutrient per 100 g of food; amounts here are absolute (already
scaled by the ingredient's weight).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

MACRO = "macro"
MINERAL = "mineral"
VITAMIN = "vitamin"


@dataclass(frozen=True)
class NutrientSpec:
    key: str           # canonical short key used everywhere in code
    label: str         # human label for tables
    unit: str          # unit the amount is expressed in (kcal, g, mg, ug)
    group: str         # MACRO | MINERAL | VITAMIN
    usda_number: str   # USDA FoodData Central nutrient number
    # Reference Daily Value for a 2000 kcal diet, in this nutrient's `unit`. Used for
    # the "% of daily needs" column. None where no conventional %DV exists (energy,
    # total sugars) — those show "—". Sources: FDA Daily Values (21 CFR 101.9),
    # 2016 update, which harmonise closely with EU NRVs.
    daily_value: Optional[float] = None


# Order here is the order shown in tables. Daily Values from the FDA nutrition-label
# reference amounts (2000 kcal reference adult).
NUTRIENTS: List[NutrientSpec] = [
    # --- Macros / energy ---
    NutrientSpec("calories", "Energy", "kcal", MACRO, "208", 2000),
    NutrientSpec("protein", "Protein", "g", MACRO, "203", 50),
    NutrientSpec("carbs", "Carbohydrate", "g", MACRO, "205", 275),
    NutrientSpec("sugars", "Sugars", "g", MACRO, "269", None),   # no DV for total sugars
    NutrientSpec("fiber", "Fiber", "g", MACRO, "291", 28),
    NutrientSpec("fat", "Fat", "g", MACRO, "204", 78),
    NutrientSpec("sat_fat", "Saturated fat", "g", MACRO, "606", 20),
    # --- Minerals ---
    NutrientSpec("calcium", "Calcium", "mg", MINERAL, "301", 1300),
    NutrientSpec("iron", "Iron", "mg", MINERAL, "303", 18),
    NutrientSpec("magnesium", "Magnesium", "mg", MINERAL, "304", 420),
    NutrientSpec("phosphorus", "Phosphorus", "mg", MINERAL, "305", 1250),
    NutrientSpec("potassium", "Potassium", "mg", MINERAL, "306", 4700),
    NutrientSpec("sodium", "Sodium", "mg", MINERAL, "307", 2300),
    NutrientSpec("zinc", "Zinc", "mg", MINERAL, "309", 11),
    NutrientSpec("copper", "Copper", "mg", MINERAL, "312", 0.9),
    NutrientSpec("manganese", "Manganese", "mg", MINERAL, "315", 2.3),
    NutrientSpec("selenium", "Selenium", "ug", MINERAL, "317", 55),
    # --- Vitamins ---
    NutrientSpec("vit_a", "Vitamin A (RAE)", "ug", VITAMIN, "320", 900),
    NutrientSpec("vit_c", "Vitamin C", "mg", VITAMIN, "401", 90),
    NutrientSpec("vit_d", "Vitamin D", "ug", VITAMIN, "328", 20),
    NutrientSpec("vit_e", "Vitamin E", "mg", VITAMIN, "323", 15),
    NutrientSpec("vit_k", "Vitamin K", "ug", VITAMIN, "430", 120),
    NutrientSpec("thiamin", "Thiamin (B1)", "mg", VITAMIN, "404", 1.2),
    NutrientSpec("riboflavin", "Riboflavin (B2)", "mg", VITAMIN, "405", 1.3),
    NutrientSpec("niacin", "Niacin (B3)", "mg", VITAMIN, "406", 16),
    NutrientSpec("vit_b6", "Vitamin B6", "mg", VITAMIN, "415", 1.7),
    NutrientSpec("folate", "Folate (DFE)", "ug", VITAMIN, "435", 400),
    NutrientSpec("vit_b12", "Vitamin B12", "ug", VITAMIN, "418", 2.4),
]

# Fast lookups.
BY_KEY: Dict[str, NutrientSpec] = {n.key: n for n in NUTRIENTS}
BY_USDA_NUMBER: Dict[str, NutrientSpec] = {n.usda_number: n for n in NUTRIENTS}


def keys_for_group(group: str) -> List[str]:
    return [n.key for n in NUTRIENTS if n.group == group]
