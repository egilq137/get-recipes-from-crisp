"""Scrape the weekly crisp.nl box: recipe links, ingredients (with amounts) and steps.

Design notes
------------
crisp.nl is a React app whose CSS class names are auto-generated and unstable
(``mealkitRecipe18`` etc.) and are even reused between the ingredient list and the
step list. So instead of hanging selectors on those classes, we:

* select boxes by their **heading text** ("Weekbox vegan"), which is stable, and
* extract each recipe as a flat, ordered list of leaf-text "rows", then parse that
  list in pure Python.

The parsing is deliberately split from the browser code: `parse_recipe_rows` and its
helpers take a plain ``list[str]`` and need no network or browser, so they are unit
tested against saved fixtures in ``tests/fixtures``.

Ingredient rows look like alternating amount / name spans::

    "400 g", "zoete aardappel oranje", "¾ zakje", "kaas reraspt pittig", ...

Amounts always begin with a digit or a unicode fraction; names never do. That is the
discriminator we rely on. A "Zelf toe te voegen" block lists pantry items (oil,
spices) as single strings, some without an amount ("Olie om te bakken").
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from crisp_recipes.config import Settings
from crisp_recipes.models import Ingredient, Recipe

log = logging.getLogger(__name__)

BASE_URL = "https://crisp.nl"
IN_DE_BOXEN_URL = f"{BASE_URL}/weekbox/in-de-boxen"

# Section markers on a recipe page (Dutch).
_PORTIONS_RE = re.compile(r"^(\d+)\s*porties$", re.IGNORECASE)
_ZELF_MARKER = "Zelf toe te voegen"
_BEREIDING_MARKER = "Bereiding"
_INGREDIENTS_MARKER = "Ingrediënten"

# A row is an "amount" if it starts with a digit or a unicode vulgar fraction.
_FRACTIONS = "¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
_AMOUNT_START_RE = re.compile(rf"^\s*[\d{_FRACTIONS}]")

# Known Dutch cooking units, so we can split "400 g rijst" into amount/unit/name
# without mistaking a food word ("tomaten") for a unit.
UNITS = {
    "g", "gr", "gram", "kg", "mg", "ml", "cl", "dl", "l", "liter",
    "el", "eetlepel", "eetlepels", "tl", "theelepel", "theelepels",
    "stuk", "stuks", "st", "zakje", "zak", "bakje", "bak", "pakje", "pak",
    "blik", "blikje", "bol", "bosje", "bos", "stengel", "stengels",
    "kropje", "krop", "teen", "teentje", "tenen", "snuf", "snufje",
    "handje", "hand", "plak", "plakken", "plakje", "mespunt", "mespuntje",
    "druppel", "drupje", "scheutje", "scheut", "pot", "potje", "fles", "blaadje",
}

# Map a short box key (config) to the exact heading text on the site.
_BOX_HEADINGS = {
    "vegan": "Weekbox vegan",
    "vega": "Weekbox vega",
    "weekbox": "Weekbox",
    "snelle": "Snelle weekbox",
    "snelle-vega": "Snelle weekbox vega",
}


class ScrapeError(RuntimeError):
    """Raised when the page structure isn't what we expect."""


# --------------------------------------------------------------------------- #
# Pure parsing (no browser) — unit tested.
# --------------------------------------------------------------------------- #
def is_amount(row: str) -> bool:
    return bool(_AMOUNT_START_RE.match(row))


def parse_ingredient_string(text: str, *, pantry: bool) -> Ingredient:
    """Parse one full ingredient string like '400 g zoete aardappel' or
    '1½ el olijfolie extra vierge' or 'Olie om te bakken' into an Ingredient."""
    text = text.strip()
    if not is_amount(text):
        return Ingredient(name=text, pantry=pantry)

    tokens = text.split()
    amount = tokens[0]
    rest = tokens[1:]
    unit: Optional[str] = None
    if rest and rest[0].lower().strip(".") in UNITS:
        unit = rest[0]
        rest = rest[1:]
    name = " ".join(rest).strip()
    return Ingredient(name=name, amount=amount, unit=unit, pantry=pantry)


def _group_main_ingredients(rows: List[str]) -> List[Ingredient]:
    """Rows alternate amount/name; a name may span several rows. Group each amount
    with the name rows that follow it."""
    ingredients: List[Ingredient] = []
    current: List[str] = []
    for row in rows:
        if is_amount(row):
            if current:
                ingredients.append(parse_ingredient_string(" ".join(current), pantry=False))
            current = [row]
        elif current:
            current.append(row)
        else:
            # A name with no preceding amount (unusual) — keep it as its own item.
            current = [row]
    if current:
        ingredients.append(parse_ingredient_string(" ".join(current), pantry=False))
    return ingredients


def _clean_step_text(parts: List[str]) -> str:
    text = " ".join(p for p in parts if p)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)   # no space before punctuation
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def parse_steps(step_rows: List[str]) -> List[str]:
    """Reconstruct numbered steps. Step numbers are rows that are just digits;
    everything between two numbers is that step's (possibly span-split) text."""
    steps: List[str] = []
    number: Optional[str] = None
    parts: List[str] = []
    for row in step_rows:
        if re.fullmatch(r"\d+", row):
            if number is not None:
                steps.append(f"{number}. {_clean_step_text(parts)}")
            number = row
            parts = []
        elif number is not None:
            parts.append(row)
        # rows before the first number are section noise; skip.
    if number is not None:
        steps.append(f"{number}. {_clean_step_text(parts)}")
    return steps


def _find(rows: List[str], marker: str, start: int = 0) -> int:
    for i in range(start, len(rows)):
        if rows[i].strip() == marker:
            return i
    return -1


def parse_recipe_rows(rows: List[str]) -> Tuple[List[Ingredient], List[str], Optional[int]]:
    """Split the flat row list into ingredients, steps and portions count.

    Returns ``(ingredients, steps, portions)``. Raises ScrapeError if the essential
    ingredient/preparation markers are missing.
    """
    rows = [r.strip() for r in rows]

    # The main ingredient list starts right after the "N porties" row.
    portions: Optional[int] = None
    start = -1
    for i, row in enumerate(rows):
        m = _PORTIONS_RE.match(row)
        if m:
            portions = int(m.group(1))
            start = i + 1
            break
    if start == -1:
        # Fallback: begin after the (last) Ingrediënten marker.
        idx = _find(rows, _INGREDIENTS_MARKER)
        if idx == -1:
            raise ScrapeError("Could not locate the ingredients section")
        start = idx + 1

    zelf_idx = _find(rows, _ZELF_MARKER, start)
    bereiding_idx = _find(rows, _BEREIDING_MARKER, zelf_idx if zelf_idx != -1 else start)
    if bereiding_idx == -1:
        raise ScrapeError("Could not locate the preparation (Bereiding) section")

    main_end = zelf_idx if zelf_idx != -1 else bereiding_idx
    main_rows = [r for r in rows[start:main_end] if r]
    pantry_rows = (
        [r for r in rows[zelf_idx + 1:bereiding_idx] if r] if zelf_idx != -1 else []
    )
    step_rows = [r for r in rows[bereiding_idx + 1:] if r]

    ingredients = _group_main_ingredients(main_rows)
    ingredients += [parse_ingredient_string(r, pantry=True) for r in pantry_rows]
    steps = parse_steps(step_rows)

    if not ingredients:
        raise ScrapeError("No ingredients parsed")
    if not steps:
        raise ScrapeError("No steps parsed")
    return ingredients, steps, portions


def select_box_links(box_map: Dict[str, List[str]], box_key: str) -> List[str]:
    """Pick the recipe hrefs for the requested box from a {heading: [hrefs]} map."""
    heading = _BOX_HEADINGS.get(box_key.lower(), box_key)
    # exact match first, then case-insensitive
    if heading in box_map:
        return box_map[heading]
    for name, links in box_map.items():
        if name.lower() == heading.lower():
            return links
    raise ScrapeError(
        f"Box '{box_key}' (heading '{heading}') not found. "
        f"Available: {sorted(box_map)}"
    )


# --------------------------------------------------------------------------- #
# Browser extraction (Playwright). These small JS snippets were validated live.
# --------------------------------------------------------------------------- #
# Returns {heading: [recipe hrefs]} by walking the box-listing page in DOM order.
_BOX_MAP_JS = r"""
() => {
  const BOX_RE = /^(Snelle weekbox|Weekbox)( vega| vegan)?$/;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  const map = {}; let current = null; let node;
  while ((node = walker.nextNode())) {
    const tag = node.tagName.toLowerCase();
    if (/^h[1-4]$/.test(tag)) {
      const t = node.textContent.trim();
      if (BOX_RE.test(t)) { current = t; if (!map[current]) map[current] = []; }
    } else if (tag === 'a') {
      const href = node.getAttribute('href') || '';
      if (href.includes('/weekbox/recipe/') && current && !map[current].includes(href)) {
        map[current].push(href);
      }
    }
  }
  return map;
}
"""

# Returns {title, portions, rows} for a single recipe page.
_RECIPE_JS = r"""
() => {
  const title = document.querySelector('span.mealkitRecipe12.mealkitRecipe13')?.innerText?.trim()
    || document.title.split('|')[0].trim();
  const bodyText = document.body.innerText;
  const pm = bodyText.match(/(\d+)\s*porties/);
  const portions = pm ? parseInt(pm[1], 10) : null;
  // Official cooking time published by the site, e.g. "35 min koken".
  const km = bodyText.match(/(\d+)\s*min\s*koken/i);
  const cookingMinutes = km ? parseInt(km[1], 10) : null;
  const h3 = [...document.querySelectorAll('h1,h2,h3,h4')]
    .find(h => h.textContent.trim() === 'Ingrediënten');
  if (!h3) return { title, portions, rows: [] };
  let sec = h3;
  for (let i = 0; i < 8; i++) {
    sec = sec.parentElement;
    if (sec && sec.textContent.includes('Zelf toe te voegen') && sec.textContent.includes('Bereiding')) break;
  }
  const walker = document.createTreeWalker(sec, NodeFilter.SHOW_ELEMENT, {
    acceptNode: (n) => n.children.length === 0 && n.textContent.trim()
      ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP
  });
  const rows = []; let node;
  while ((node = walker.nextNode())) rows.push(node.textContent.trim());
  return { title, portions, cookingMinutes, rows };
}
"""


# Try to switch the page's ingredient amounts to N portions using the site's own
# selector. Returns true if it changed something. Handles both a <select> and a
# custom dropdown/button UI, since the exact widget isn't guaranteed.
_SET_PORTIONS_JS = r"""
(target) => {
  const wanted = String(target);
  // 1) A real <select> whose options mention "porties".
  for (const sel of document.querySelectorAll('select')) {
    const opts = [...sel.options];
    if (!opts.some(o => /porties/i.test(o.textContent))) continue;
    const match = opts.find(o => new RegExp('\\b' + wanted + '\\b').test(o.textContent));
    if (match) {
      sel.value = match.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
  }
  // 2) A clickable element labelled "N porties".
  const re = new RegExp('^\\s*' + wanted + '\\s*porties\\s*$', 'i');
  const clickable = [...document.querySelectorAll('button,li,div,span,a,label')]
    .find(el => el.children.length === 0 && re.test(el.textContent));
  if (clickable) { clickable.click(); return true; }
  return false;
}
"""


def _set_portions(page, target_portions: int) -> bool:
    """Best-effort: ask the site for `target_portions`. May need the selector opened
    first, so we try once, then again after clicking the current value."""
    try:
        if page.evaluate(_SET_PORTIONS_JS, target_portions):
            page.wait_for_timeout(700)
            return True
        # The options may be hidden until the current value is clicked.
        page.get_by_text("porties", exact=False).first.click(timeout=3000)
        page.wait_for_timeout(400)
        if page.evaluate(_SET_PORTIONS_JS, target_portions):
            page.wait_for_timeout(700)
            return True
    except Exception:
        pass
    return False


def _accept_cookies(page) -> None:
    """Best-effort cookie acceptance; the banner may or may not appear."""
    try:
        page.get_by_text("accepteren", exact=False).first.click(timeout=4000)
    except Exception:
        pass


def _goto(page, url: str) -> None:
    page.goto(url, wait_until="networkidle", timeout=45000)


def scrape_current_recipes(
    settings: Optional[Settings] = None,
    *,
    box_key: Optional[str] = None,
    headless: bool = True,
) -> List[Recipe]:
    """Scrape the three recipes of the configured box from crisp.nl.

    Requires Playwright + Firefox. Falls back to `settings.crisp_box` for the box.
    """
    from playwright.sync_api import sync_playwright  # local import: heavy dependency

    if box_key is None:
        box_key = settings.crisp_box if settings else "vegan"

    recipes: List[Recipe] = []
    with sync_playwright() as pw:
        browser = pw.firefox.launch(headless=headless)
        context = browser.new_context(locale="nl-NL")
        try:
            page = context.new_page()
            log.info("Loading box listing (%s)...", box_key)
            _goto(page, IN_DE_BOXEN_URL)
            _accept_cookies(page)
            page.wait_for_timeout(1000)
            box_map = page.evaluate(_BOX_MAP_JS)
            hrefs = select_box_links(box_map, box_key)
            log.info("Box '%s' -> %d recipes", box_key, len(hrefs))
            if len(hrefs) != 3:
                log.warning("Expected 3 recipes, found %d: %s", len(hrefs), hrefs)

            target_portions = settings.target_portions if settings else None
            for href in hrefs:
                url = urljoin(BASE_URL, href)
                recipes.append(_scrape_one(context, url, target_portions))
        finally:
            browser.close()
    return recipes


def _scrape_one(context, url: str, target_portions: Optional[int] = None) -> Recipe:
    page = context.new_page()
    try:
        log.info("Scraping %s", url)
        _goto(page, url)
        _accept_cookies(page)
        page.wait_for_timeout(800)
        if target_portions:
            if _set_portions(page, target_portions):
                log.info("  set portions to %d via the site selector", target_portions)
            else:
                log.info("  portion selector unavailable; will scale amounts instead")
        data = page.evaluate(_RECIPE_JS)
        if not data or not data.get("rows"):
            raise ScrapeError(f"No recipe rows extracted from {url}")
        ingredients, steps, portions = parse_recipe_rows(data["rows"])
        title = (data.get("title") or "").strip() or "Untitled recipe"

        # Prefer the site's official cooking time; fall back to a heuristic.
        from crisp_recipes.cooking_time import resolve_cooking_minutes
        cooking = resolve_cooking_minutes(data.get("cookingMinutes"), steps)

        log.info("  %s: %d ingredients, %d steps, %s min (%s portions)",
                 title, len(ingredients), len(steps), cooking, portions)
        return Recipe(
            title=title,
            steps=steps,
            ingredients=ingredients,
            cooking_time_minutes=cooking,
            source_portions=portions or (data.get("portions")),
        )
    finally:
        page.close()
