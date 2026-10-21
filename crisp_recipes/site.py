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
from crisp_recipes.render import _EMAIL_CSS, _recipe_html, cooking_time_label

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "site_assets"


def slugify(text: str, max_len: int = 60) -> str:
    """'Tacos with mango-jalapeño salsa' -> 'tacos-with-mango-jalapeno-salsa'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "recipe"


def _recipe_page(recipe: Recipe, week_label: str, pdf_name: Optional[str]) -> str:
    pdf_link = (
        f' · <a href="{html.escape(pdf_name)}">PDF</a>' if pdf_name else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(recipe.title)}</title>
<style>{_EMAIL_CSS}
.nav {{ font-size: 14px; margin: 8px 0; }}
.nav a {{ color: #16324f; }}</style></head>
<body><div class="wrap">
  <div class="nav"><a href="../../index.html">← All recipes</a>{pdf_link} · {html.escape(week_label)}</div>
  {_recipe_html(recipe)}
</div></body></html>"""


def write_week(
    recipes: List[Recipe],
    site_dir: Path,
    week_slug: str,
    week_label: str,
    pdfs: Optional[List[Path]] = None,
) -> Path:
    """Write one week's recipe pages, PDFs and week.json under `site_dir/weeks/`.

    Re-running for the same week overwrites it, so a manual re-run replaces the
    week rather than duplicating it.
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
        (week_dir / page_name).write_text(
            _recipe_page(recipe, week_label, pdf_name), encoding="utf-8"
        )

        per_serving = recipe.nutrition_per_serving()
        kcal = per_serving.get("calories") if per_serving else None
        entries.append({
            "id": f"{week_slug}/{slug}",
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
) -> Path:
    write_week(recipes, site_dir, week_slug, week_label, pdfs)
    return build_index(site_dir, ratings_api_url)
