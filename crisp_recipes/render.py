"""Render recipes to a combined HTML email body and to per-recipe PDFs.

Both outputs share the same view-model helpers (`cooking_time_label`,
`nutrition_sections`, value formatting) so the numbers and wording stay identical
across the email and the PDFs. Nutrition is shown per serving and per whole recipe,
grouped into macros / minerals / vitamins; nutrients with no USDA data render as "—"
rather than a fake zero. The matched USDA food is shown next to each ingredient for
transparency, and estimated figures are labelled as such.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from crisp_recipes.models import NutritionFacts, Recipe
from crisp_recipes.nutrients import MACRO, MINERAL, VITAMIN, NUTRIENTS, NutrientSpec

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent.parent / "dejavu-fonts-ttf-2.37" / "ttf"

_GROUP_TITLES = [(MACRO, "Macros"), (MINERAL, "Minerals"), (VITAMIN, "Vitamins")]


# --------------------------------------------------------------------------- #
# Shared view-model helpers
# --------------------------------------------------------------------------- #
def _round_for_unit(value: float, unit: str) -> float:
    if unit in ("kcal", "ug"):
        return round(value)
    return round(value, 1)


def format_amount(value: Optional[float], unit: str) -> str:
    """Format a nutrient value with its unit, or an em dash if there's no data."""
    if value is None:
        return "—"
    rounded = _round_for_unit(value, unit)
    if rounded == int(rounded):
        rounded = int(rounded)
    label = "kcal" if unit == "kcal" else ("µg" if unit == "ug" else unit)
    return f"{rounded} {label}"


def cooking_time_label(recipe: Recipe) -> str:
    """e.g. '35 min active · ~57 min realistic (incl. prep)' or '35 min'."""
    active = recipe.cooking_time_minutes
    realistic = recipe.realistic_time_minutes
    if active and realistic and realistic > active:
        return f"{active} min active · ~{realistic} min realistic (incl. prep)"
    if realistic and not active:
        return f"~{realistic} min (incl. prep)"
    if active:
        return f"{active} min"
    return "time n/a"


def steps_portion_note(recipe: Recipe) -> str:
    """Which portion count the step amounts are for, when it differs from the list.

    Steps keep crisp.nl's own amounts, so when the ingredient list was scaled this
    note (shown under the Preparation heading) says so. Empty when they agree.
    """
    source, display = recipe.source_portions, recipe.display_portions
    if source and display and source != display:
        return f"Amounts in the steps are for {source} servings, as on crisp.nl."
    return ""


def format_daily_value(value: Optional[float], spec) -> str:
    """Percent of the 2000 kcal Daily Value this amount represents, e.g. '54%'.

    '—' when there's no measured value or no Daily Value defined for the nutrient.
    """
    if value is None or not spec.daily_value:
        return "—"
    pct = round(value / spec.daily_value * 100)
    return f"{pct}%"


def nutrition_sections(
    recipe: Recipe,
) -> List[Tuple[str, List[Tuple[str, str, str]]]]:
    """Return [(group_title, [(nutrient_label, per_serving, pct_daily_value), ...]), ...].

    Per-serving figures — the number you actually eat — plus the share of a 2000 kcal
    daily requirement. Empty if the recipe has no nutrition data at all.
    """
    if recipe.nutrition_total is None:
        return []
    per_serving = recipe.nutrition_per_serving() or NutritionFacts()

    sections: List[Tuple[str, List[Tuple[str, str, str]]]] = []
    for group, title in _GROUP_TITLES:
        rows: List[Tuple[str, str, str]] = []
        for spec in NUTRIENTS:
            if spec.group != group:
                continue
            value = per_serving.get(spec.key)
            rows.append((
                spec.label,
                format_amount(value, spec.unit),
                format_daily_value(value, spec),
            ))
        if rows:
            sections.append((title, rows))
    return sections


def _ingredient_lines(recipe: Recipe) -> List[Tuple[str, Optional[str]]]:
    """(display_text, match_note) per ingredient.

    The note names the USDA food used, or says the ingredient was left out of the
    totals — an unmatched ingredient is excluded, so saying so keeps the numbers
    honest rather than silently under-counting.
    """
    out = []
    for ing in recipe.ingredients:
        text = ing.display
        if ing.pantry:
            text += " (pantry)"
        if ing.usda_description:
            note: Optional[str] = ing.usda_description
        elif recipe.nutrition_total is not None:
            note = "not counted — no nutrition match"
        else:
            note = None
        out.append((text, note))
    return out


# --------------------------------------------------------------------------- #
# HTML email
# --------------------------------------------------------------------------- #
_EMAIL_CSS = """
body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       color: #1f2933; margin: 0; padding: 0; background: #f4f5f7; }
.wrap { max-width: 680px; margin: 0 auto; padding: 16px; }
.recipe { background: #fff; border-radius: 10px; padding: 20px 22px; margin: 16px 0;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }
h1 { font-size: 20px; margin: 8px 0 2px; }
h2 { font-size: 19px; margin: 0 0 4px; color: #16324f; }
h3 { font-size: 14px; text-transform: uppercase; letter-spacing: .04em;
     color: #52606d; margin: 18px 0 8px; }
.meta { color: #52606d; font-size: 14px; margin-bottom: 4px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid #eef1f4; }
th:first-child, td:first-child { text-align: left; }
thead th { color: #52606d; font-weight: 600; border-bottom: 2px solid #dfe3e8; }
.grp td { background: #f7f9fb; font-weight: 700; color: #16324f; }
ul { margin: 6px 0; padding-left: 20px; }
li { margin: 3px 0; font-size: 14px; }
.match { color: #9aa5b1; font-size: 12px; }
ol.steps li { margin: 6px 0; }
ul.chef { background: #fbf7ef; border-left: 3px solid #d9a441; border-radius: 4px;
          padding: 10px 10px 10px 28px; }
ul.chef li { margin: 5px 0; font-size: 14px; }
.note { color: #7b8794; font-size: 12px; margin-top: 12px; font-style: italic; }
.stepsnote { color: #7b8794; font-size: 13px; margin: -4px 0 6px; }
.phone { display: inline-block; margin: 8px 0 2px; padding: 6px 12px; border-radius: 6px;
         background: #16324f; color: #fff !important; text-decoration: none; font-size: 14px; }
"""


def _nutrition_table_html(recipe: Recipe) -> str:
    sections = nutrition_sections(recipe)
    if not sections:
        return '<p class="note">Nutrition data unavailable for this recipe.</p>'
    rows = [
        "<thead><tr><th>Nutrient</th><th>Per serving</th><th>% daily*</th></tr></thead>",
        "<tbody>",
    ]
    for title, section_rows in sections:
        rows.append(f'<tr class="grp"><td colspan="3">{html.escape(title)}</td></tr>')
        for label, ps, dv in section_rows:
            rows.append(
                f"<tr><td>{html.escape(label)}</td>"
                f"<td>{html.escape(ps)}</td><td>{html.escape(dv)}</td></tr>"
            )
    rows.append("</tbody>")
    return (
        f"<table>{''.join(rows)}</table>"
        '<p class="note">* Percent of daily needs for a 2000 kcal diet '
        "(FDA Daily Values).</p>"
    )


def _recipe_html(recipe: Recipe, web_url: Optional[str] = None) -> str:
    portions = recipe.display_portions or recipe.source_portions or "?"
    ingredients = "".join(
        f"<li>{html.escape(text)}"
        + (f' <span class="match">→ {html.escape(match)}</span>' if match else "")
        + "</li>"
        for text, match in _ingredient_lines(recipe)
    )
    steps = "".join(f"<li>{html.escape(_strip_step_number(s))}</li>" for s in recipe.steps)
    note = steps_portion_note(recipe)
    steps_note = f'<p class="stepsnote">{html.escape(note)}</p>' if note else ""
    chef = ""
    if recipe.chef_notes:
        tips = "".join(f"<li>{html.escape(n)}</li>" for n in recipe.chef_notes)
        chef = (
            '<h3>👩‍🍳 Chef\'s notes &amp; cookware</h3>'
            f'<ul class="chef">{tips}</ul>'
        )
    phone = (
        f'<a class="phone" href="{html.escape(web_url)}">📱 Open the cooking view</a>'
        if web_url else ""
    )
    # Cooking order first; nutrition is reference material, so it goes last.
    return f"""
    <div class="recipe">
      <h2>{html.escape(recipe.title)}</h2>
      <div class="meta">🕒 {html.escape(cooking_time_label(recipe))} &nbsp;·&nbsp; 🍽️ {portions} servings</div>
      {phone}
      <h3>Ingredients (for {portions} servings)</h3>
      <ul>{ingredients}</ul>
      <h3>Preparation</h3>{steps_note}
      <ol class="steps">{steps}</ol>
      {chef}
      <h3>Nutrition (per serving)</h3>
      {_nutrition_table_html(recipe)}
    </div>
    """


def _strip_step_number(step: str) -> str:
    # steps come as "1. text"; the <ol> re-numbers, so drop the leading "N. ".
    parts = step.split(". ", 1)
    return parts[1] if len(parts) == 2 and parts[0].isdigit() else step


def build_email_html(recipes: List[Recipe], week_label: str) -> str:
    body = "".join(_recipe_html(r, r.web_url) for r in recipes)
    note = (
        "Nutrition from USDA FoodData Central. Ingredient weights and the realistic "
        "cooking time are estimates; nutrient values are real but depend on the matched "
        "food shown after each ingredient."
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_EMAIL_CSS}</style></head>
<body><div class="wrap">
  <h1>🥗 Crisp recipes — {html.escape(week_label)}</h1>
  {body}
  <p class="note">{html.escape(note)}</p>
</div></body></html>"""


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
# Phone-shaped page (roughly a 9:16 screen), so the PDF opens fit-to-width on a phone
# and reads top to bottom with no sideways scrolling. Text is sized for that zoom.
PDF_PAGE_MM = (80, 142)
_PDF_MARGIN = 5
_BODY_PT, _BODY_LINE = 11, 5.4
_SMALL_PT, _SMALL_LINE = 8.5, 4.2
_TEXT = (31, 41, 51)
_MUTED = (110, 120, 133)


class RecipePdf:
    """Builds a single-recipe, phone-sized PDF with the same content as the email card.

    Cooking order first (ingredients, steps, chef's notes); nutrition last, since it's
    reference material nobody needs mid-recipe.
    """

    def __init__(self):
        from fpdf import FPDF

        self.pdf = FPDF(format=PDF_PAGE_MM)
        self.pdf.set_margins(_PDF_MARGIN, _PDF_MARGIN, _PDF_MARGIN)
        self.pdf.set_auto_page_break(auto=True, margin=_PDF_MARGIN + 1)
        self.pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))

    def render(self, recipe: Recipe, out_path: Path) -> Path:
        pdf = self.pdf
        pdf.add_page()

        pdf.set_font("DejaVu", "B", 14)
        self._text(recipe.title, 6.5)
        portions = recipe.display_portions or recipe.source_portions or "?"
        pdf.set_font("DejaVu", "", _SMALL_PT + 0.5)
        pdf.set_text_color(*_MUTED)
        self._text(f"{cooking_time_label(recipe)}  ·  {portions} servings", 4.8)
        pdf.set_text_color(*_TEXT)

        self._ingredients(recipe)
        self._steps(recipe)
        self._chef_notes(recipe)
        self._nutrition(recipe)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(out_path))
        return out_path

    def _text(self, text: str, line_height: float) -> None:
        from fpdf.enums import XPos, YPos

        # Left-aligned: justified text leaves big gaps in a column this narrow.
        self.pdf.multi_cell(0, line_height, text, align="L",
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def _heading(self, text: str) -> None:
        # Never leave a heading stranded at the bottom of a page.
        if self.pdf.get_y() > self.pdf.h - self.pdf.b_margin - 22:
            self.pdf.add_page()
        else:
            self.pdf.ln(3)
        self.pdf.set_font("DejaVu", "B", 12)
        self.pdf.set_text_color(22, 50, 79)
        self._text(text, 5.8)
        self.pdf.set_text_color(*_TEXT)
        self.pdf.ln(0.5)

    def _ingredients(self, recipe: Recipe) -> None:
        portions = recipe.display_portions or recipe.source_portions
        self._heading(f"Ingredients (for {portions} servings)" if portions else "Ingredients")
        pdf = self.pdf
        for text, match in _ingredient_lines(recipe):
            pdf.set_font("DejaVu", "", _BODY_PT)
            self._text(f"•  {text}", _BODY_LINE)
            if match:
                pdf.set_font("DejaVu", "", _SMALL_PT - 1)
                pdf.set_text_color(154, 165, 177)
                self._text(f"     → {match}", 3.8)
                pdf.set_text_color(*_TEXT)

    def _steps(self, recipe: Recipe) -> None:
        self._heading("Preparation")
        note = steps_portion_note(recipe)
        if note:
            self.pdf.set_font("DejaVu", "", _SMALL_PT)
            self.pdf.set_text_color(*_MUTED)
            self._text(note, _SMALL_LINE)
            self.pdf.set_text_color(*_TEXT)
            self.pdf.ln(1.5)
        for step in recipe.steps:
            number, _, body = step.partition(". ")
            if number.isdigit():
                self.pdf.set_font("DejaVu", "B", _BODY_PT)
                self._text(f"Step {number}", _BODY_LINE)
                step = body
            self.pdf.set_font("DejaVu", "", _BODY_PT)
            self._text(step, _BODY_LINE)
            self.pdf.ln(2)

    def _chef_notes(self, recipe: Recipe) -> None:
        if not recipe.chef_notes:
            return
        self._heading("Chef's notes & cookware")
        self.pdf.set_font("DejaVu", "", _BODY_PT)
        for note in recipe.chef_notes:
            self._text(f"•  {note}", _BODY_LINE)
            self.pdf.ln(1)

    def _nutrition(self, recipe: Recipe) -> None:
        from fpdf.enums import XPos, YPos

        self._heading("Nutrition (per serving)")
        sections = nutrition_sections(recipe)
        pdf = self.pdf
        if not sections:
            pdf.set_font("DejaVu", "", _SMALL_PT)
            self._text("Nutrition data unavailable.", _SMALL_LINE)
            return
        epw = pdf.epw
        w = (epw * 0.50, epw * 0.28, epw * 0.22)
        pdf.set_font("DejaVu", "B", _SMALL_PT)
        pdf.cell(w[0], 5, "Nutrient", border="B")
        pdf.cell(w[1], 5, "Amount", border="B", align="R")
        pdf.cell(w[2], 5, "% daily*", border="B", align="R",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for title, rows in sections:
            pdf.set_font("DejaVu", "B", _SMALL_PT)
            pdf.set_fill_color(247, 249, 251)
            pdf.cell(epw, 5, title, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("DejaVu", "", _SMALL_PT)
            for label, ps, dv in rows:
                pdf.cell(w[0], _SMALL_LINE + 0.4, label)
                pdf.cell(w[1], _SMALL_LINE + 0.4, ps, align="R")
                pdf.cell(w[2], _SMALL_LINE + 0.4, dv, align="R",
                         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)
        pdf.set_font("DejaVu", "", _SMALL_PT - 1.5)
        pdf.set_text_color(*_MUTED)
        self._text("* Percent of daily needs for a 2000 kcal diet (FDA Daily Values).", 3.5)
        pdf.set_text_color(*_TEXT)


def render_recipe_pdf(recipe: Recipe, out_path: Path) -> Path:
    return RecipePdf().render(recipe, Path(out_path))


def render_all_pdfs(recipes: List[Recipe], out_dir: Path) -> List[Path]:
    out_dir = Path(out_dir)
    paths = []
    for recipe in recipes:
        safe = "".join(c for c in recipe.title if c.isalnum() or c in " -_").strip()[:60]
        paths.append(render_recipe_pdf(recipe, out_dir / f"{safe}.pdf"))
    return paths
