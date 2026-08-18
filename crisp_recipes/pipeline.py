"""End-to-end weekly pipeline: scrape → translate → nutrition → times → render → email.

Each stage logs its progress and the enrichment stages (translation, nutrition, prep
times) degrade gracefully per recipe, so one failure doesn't sink the batch. This
replaces the old top-level `main.py` / `get_recipes_crisp.py` split.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import List, Optional

from crisp_recipes.chef_notes import add_chef_notes
from crisp_recipes.config import Settings
from crisp_recipes.cooking_time import add_realistic_times
from crisp_recipes.email_sender import send_email
from crisp_recipes.models import Recipe
from crisp_recipes.nutrition import add_nutrition
from crisp_recipes.render import render_all_pdfs
from crisp_recipes.scraper import scrape_current_recipes
from crisp_recipes.translator import translate_recipes

log = logging.getLogger(__name__)


def week_label(today: Optional[date] = None) -> str:
    """Label for the *coming* week's box (crisp delivers next week's recipes)."""
    today = today or date.today()
    iso = today.isocalendar()
    return f"Week {iso.week + 1}, {iso.year}"


def output_dir(today: Optional[date] = None) -> Path:
    today = today or date.today()
    iso = today.isocalendar()
    return Path("recipes") / f"{iso.year}_Week_{iso.week + 1}"


def run(settings: Optional[Settings] = None) -> List[Recipe]:
    """Run the full weekly job. Returns the enriched recipes (for tests/inspection)."""
    settings = settings or Settings.load()
    label = week_label()
    log.info("=== Crisp recipes: %s (box: %s) ===", label, settings.crisp_box)

    recipes = scrape_current_recipes(settings)
    if not recipes:
        log.error("No recipes scraped; aborting")
        return []

    # Scale amounts while the text is still Dutch — the unit allowlist and word order
    # only hold pre-translation. A no-op if the site's portion selector already
    # returned the target size.
    for r in recipes:
        current = r.portions_for_amounts
        if current and current != settings.target_portions:
            log.info("Scaling %r amounts from %d to %d portions",
                     r.title, current, settings.target_portions)
    recipes = [r.scaled_to_portions(settings.target_portions) for r in recipes]

    log.info("Translating %d recipes...", len(recipes))
    recipes = translate_recipes(recipes, settings)

    # Nutrition is derived from the (scaled) amounts and divided by the same portion
    # count when displayed, so per-serving values stay correct either way.
    log.info("Fetching nutrition from USDA...")
    add_nutrition(recipes, settings)

    log.info("Estimating realistic cooking times...")
    add_realistic_times(recipes, settings.google_api_key)

    log.info("Writing chef's notes...")
    add_chef_notes(recipes, settings.google_api_key)

    log.info("Rendering PDFs...")
    pdfs = render_all_pdfs(recipes, output_dir())

    log.info("Sending email...")
    send_email(recipes, settings, label, pdfs)

    log.info("Done: %d recipes delivered", len(recipes))
    return recipes
