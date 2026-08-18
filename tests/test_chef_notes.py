"""Tests for chef-notes parsing and rendering (no API)."""
from crisp_recipes.chef_notes import format_step_reference, parse_notes


def test_parse_notes_from_dict():
    raw = {"notes": ["Prep everything first.", "Cook A boils water while Cook B chops."]}
    assert parse_notes(raw) == [
        "Prep everything first.",
        "Cook A boils water while Cook B chops.",
    ]


def test_format_step_reference():
    assert format_step_reference([3]) == " (step 3)"
    assert format_step_reference([2, 6]) == " (steps 2, 6)"
    assert format_step_reference([6, 2, 2]) == " (steps 2, 6)"   # deduped and sorted
    assert format_step_reference([]) == ""
    assert format_step_reference(None) == ""
    assert format_step_reference(["x", 0, -1]) == ""             # junk ignored


def test_parse_notes_appends_step_reference_inside_sentence():
    raw = {"notes": [
        {"text": "Reuse the shallot pan for the corn.", "steps": [2, 6]},
        {"text": "Slice evenly for even cooking", "steps": [3]},
        {"text": "Taste before serving.", "steps": []},
    ]}
    assert parse_notes(raw) == [
        "Reuse the shallot pan for the corn (steps 2, 6).",
        "Slice evenly for even cooking (step 3)",
        "Taste before serving.",
    ]


def test_parse_notes_from_list_and_strips_bullets():
    raw = ["- Salt the pasta water.", "• Toast the panko last.", "  ", ""]
    assert parse_notes(raw) == ["Salt the pasta water.", "Toast the panko last."]


def test_parse_notes_empty():
    assert parse_notes({}) == []
    assert parse_notes([]) == []


def test_chef_notes_render_html():
    from crisp_recipes.models import Recipe
    from crisp_recipes.render import build_email_html

    recipe = Recipe(
        title="Test",
        steps=["1. Do it."],
        source_portions=2,
        display_portions=2,
        chef_notes=["Cook A boils water while Cook B dices the onion.", "Rest the meat."],
    )
    html = build_email_html([recipe], "Week 1, 2026")
    assert "Chef" in html
    assert "Cook A boils water" in html
    assert "Rest the meat." in html


def test_no_chef_section_when_empty():
    from crisp_recipes.models import Recipe
    from crisp_recipes.render import build_email_html

    recipe = Recipe(title="T", steps=["1. x"], source_portions=2, chef_notes=[])
    html = build_email_html([recipe], "Week 1, 2026")
    assert "Chef's notes" not in html


def test_chef_notes_render_pdf(tmp_path):
    from crisp_recipes.models import Recipe
    from crisp_recipes.render import render_recipe_pdf

    recipe = Recipe(
        title="Test",
        steps=["1. Do it."],
        source_portions=2,
        display_portions=2,
        chef_notes=["Cook A boils water while Cook B dices the onion."],
    )
    out = render_recipe_pdf(recipe, tmp_path / "r.pdf")
    assert out.exists() and out.stat().st_size > 1000
