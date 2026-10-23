"""Static recipe website: every week's recipes, plus shared star ratings.

The weekly job adds the new box to a site directory (checked out from the `gh-pages`
branch on CI, so past weeks are kept) and rebuilds the index:

    site/
      index.html, app.js, style.css    the ratings app (copied from site_assets/)
      config.js                         where the ratings API lives
      recipes.json                      every week, newest first
      weeks/2026_Week_44/
        week.json                       that week's recipes
        fried-rice-with-leek.html       phone-friendly recipe page
        fried-rice-with-leek.pdf

Ratings are not stored here: the page reads and writes them through a small Google
Apps Script endpoint backed by a Google Sheet (see `apps_script/Code.gs`), so anyone
with the link can rate without an account. Recipe ids ("2026_Week_44/fried-rice-...")
are the join key between the two, so they must never change once published.
"""
from __future__ import annotations

import html
import json
import logging
import re
import shutil
import unicodedata
from pathlib import Path
from typing import List, Optional

from crisp_recipes.models import Recipe
from crisp_recipes.render import (
    _ingredient_lines,
    _strip_step_number,
    cooking_time_label,
    nutrition_sections,
    steps_portion_note,
)

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "site_assets"


def slugify(text: str, max_len: int = 60) -> str:
    """'Tacos with mango-jalapeño salsa' -> 'tacos-with-mango-jalapeno-salsa'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "recipe"


def _recipe_page(
    recipe: Recipe, recipe_id: str, week_label: str, pdf_name: Optional[str]
) -> str:
    """Phone-first cooking view: big text, one column, tick off ingredients and steps.

    Styling and behaviour live in the shared recipe.css / recipe.js at the site root;
    ticks are remembered per recipe on the device (see recipe.js).
    """
    e = html.escape
    portions = recipe.display_portions or recipe.source_portions
    ingredients = "".join(
        f'<li><label><input type="checkbox"><span>{e(text)}</span></label></li>'
        for text, _ in _ingredient_lines(recipe)
    )
    steps = "".join(
        f'<li><button type="button" class="step" aria-pressed="false">'
        f'<span class="num">Step {i}</span><span class="txt">{e(_strip_step_number(s))}</span>'
        f"</button></li>"
        for i, s in enumerate(recipe.steps, start=1)
    )
    note = steps_portion_note(recipe)
    chef = ""
    if recipe.chef_notes:
        tips = "".join(f"<li>{e(n)}</li>" for n in recipe.chef_notes)
        chef = f'<section><h2>Chef\'s notes &amp; cookware</h2><ul class="chef">{tips}</ul></section>'

    nutrition = ""
    sections = nutrition_sections(recipe)
    if sections:
        rows = "".join(
            f'<tr class="grp"><th colspan="3">{e(title)}</th></tr>'
            + "".join(
                f"<tr><td>{e(label)}</td><td>{e(ps)}</td><td>{e(dv)}</td></tr>"
                for label, ps, dv in section_rows
            )
            for title, section_rows in sections
        )
        nutrition = (
            '<details class="nutrition"><summary>Nutrition per serving</summary>'
            '<table><thead><tr><th>Nutrient</th><th>Amount</th><th>% daily*</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
            '<p class="muted">* Percent of daily needs for a 2000 kcal diet (FDA Daily '
            "Values). Ingredient weights are estimates.</p></details>"
        )

    pdf_link = f'<a href="{e(pdf_name)}">PDF</a>' if pdf_name else ""
    serves = f" · {portions} servings" if portions else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(recipe.title)}</title>
<link rel="stylesheet" href="../../recipe.css"></head>
<body data-recipe-id="{e(recipe_id)}">
<nav class="bar"><a href="../../index.html">← All recipes</a>
  <span class="bar-right"><button type="button" id="wake" hidden>Keep screen on</button>{pdf_link}</span></nav>
<main>
  <h1>{e(recipe.title)}</h1>
  <p class="muted">{e(week_label)} · {e(cooking_time_label(recipe))}{serves}</p>

  <section><h2>Ingredients</h2><ul class="ingredients">{ingredients}</ul></section>

  <section><h2>Preparation</h2>
    {f'<p class="muted">{e(note)}</p>' if note else ""}
    <p class="muted hint">Tap a step when it's done.</p>
    <ol class="steps">{steps}</ol>
  </section>

  {chef}
  {nutrition}
  <p><button type="button" id="reset" class="link">Clear ticks</button></p>
</main>
<script src="../../recipe.js"></script>
</body></html>"""


def write_week(
    recipes: List[Recipe],
    site_dir: Path,
    week_slug: str,
    week_label: str,
    pdfs: Optional[List[Path]] = None,
    site_url: Optional[str] = None,
) -> Path:
    """Write one week's recipe pages, PDFs and week.json under `site_dir/weeks/`.

    Re-running for the same week overwrites it, so a manual re-run replaces the
    week rather than duplicating it. With `site_url` (the site's public root), each
    recipe's `web_url` is set so the email can link to its cooking view.
    """
    week_dir = Path(site_dir) / "weeks" / week_slug
    if week_dir.exists():
        shutil.rmtree(week_dir)
    week_dir.mkdir(parents=True)

    entries = []
    used = set()
    for i, recipe in enumerate(recipes):
        slug = slugify(recipe.title)
        while slug in used:
            slug += "-2"
        used.add(slug)

        pdf_name = None
        if pdfs and i < len(pdfs) and Path(pdfs[i]).exists():
            pdf_name = f"{slug}.pdf"
            shutil.copyfile(pdfs[i], week_dir / pdf_name)
        page_name = f"{slug}.html"
        recipe_id = f"{week_slug}/{slug}"
        (week_dir / page_name).write_text(
            _recipe_page(recipe, recipe_id, week_label, pdf_name), encoding="utf-8"
        )
        if site_url:
            recipe.web_url = f"{site_url.rstrip('/')}/weeks/{week_slug}/{page_name}"

        per_serving = recipe.nutrition_per_serving()
        kcal = per_serving.get("calories") if per_serving else None
        entries.append({
            "id": recipe_id,
            "title": recipe.title,
            "time": cooking_time_label(recipe),
            "servings": recipe.display_portions or recipe.source_portions,
            "kcal": round(kcal) if kcal is not None else None,
            "page": f"weeks/{week_slug}/{page_name}",
            "pdf": f"weeks/{week_slug}/{pdf_name}" if pdf_name else None,
        })

    (week_dir / "week.json").write_text(
        json.dumps({"week": week_slug, "label": week_label, "recipes": entries},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Site: wrote %d recipes to %s", len(entries), week_dir)
    return week_dir


def _week_sort_key(week: dict):
    # "2026_Week_44" -> (2026, 44); anything unparseable sorts last.
    m = re.match(r"(\d{4})_Week_(\d+)$", week.get("week", ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def build_index(site_dir: Path, ratings_api_url: Optional[str]) -> Path:
    """Rebuild recipes.json from every week on disk and refresh the app files."""
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    weeks = []
    for week_json in (site_dir / "weeks").glob("*/week.json"):
        try:
            weeks.append(json.loads(week_json.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            log.warning("Site: skipping unreadable %s", week_json)
    weeks.sort(key=_week_sort_key, reverse=True)

    (site_dir / "recipes.json").write_text(
        json.dumps({"weeks": weeks}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for asset in ASSETS_DIR.iterdir():
        shutil.copyfile(asset, site_dir / asset.name)
    (site_dir / "config.js").write_text(
        f"window.RATINGS_API_URL = {json.dumps(ratings_api_url or '')};\n",
        encoding="utf-8",
    )
    # Serve files as-is; without this GitHub Pages runs Jekyll over the site.
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    log.info("Site: index rebuilt with %d weeks", len(weeks))
    return site_dir / "index.html"


def publish_week(
    recipes: List[Recipe],
    site_dir: Path,
    week_slug: str,
    week_label: str,
    pdfs: Optional[List[Path]],
    ratings_api_url: Optional[str],
    site_url: Optional[str] = None,
) -> Path:
    write_week(recipes, site_dir, week_slug, week_label, pdfs, site_url)
    return build_index(site_dir, ratings_api_url)
