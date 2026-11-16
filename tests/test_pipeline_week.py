"""Tests for picking which crisp.nl week a run labels and files. No network."""
from datetime import date
from pathlib import Path

import pytest

from crisp_recipes.pipeline import output_dir, week_label
from crisp_recipes.scraper import WEEK_TABS


def test_default_is_coming_week():
    # Wed 11 Nov 2026 is ISO week 46; the scheduled run sends week 47's box.
    assert week_label(date(2026, 11, 11)) == "Week 47, 2026"
    assert output_dir(date(2026, 11, 11)) == Path("recipes") / "2026_Week_47"


def test_last_and_after_tabs():
    # Mon 16 Nov 2026 is ISO week 47: "last" catches up week 47, "after" is week 49.
    today = date(2026, 11, 16)
    assert week_label(today, "last") == "Week 47, 2026"
    assert week_label(today, "coming") == "Week 48, 2026"
    assert week_label(today, "after") == "Week 49, 2026"
    assert output_dir(today, "last") == Path("recipes") / "2026_Week_47"


def test_year_rollover():
    # Wed 30 Dec 2026 is ISO week 53; the coming week is week 1 of 2027, not "week 54".
    assert week_label(date(2026, 12, 30)) == "Week 1, 2027"


def test_every_week_tab_has_an_offset():
    for week in WEEK_TABS:
        week_label(date(2026, 11, 16), week)
    with pytest.raises(KeyError):
        week_label(date(2026, 11, 16), "bogus")
