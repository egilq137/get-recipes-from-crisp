"""Tests for settings loading and validation."""
import pytest

from crisp_recipes.config import ConfigError, Settings

REQUIRED = {
    "GOOGLE_API_KEY": "g",
    "DEEPL_API_KEY": "d",
    "USDA_FDC_API_KEY": "u",
    "GMAIL_APP_PASSWORD": "p",
    "SENDER_EMAIL": "me@example.com",
    "RECIPIENTS": "a@example.com, b@example.com",
}


def _set_env(monkeypatch, **overrides):
    for key, value in {**REQUIRED, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_loads_required_and_defaults(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.delenv("CRISP_BOX", raising=False)
    monkeypatch.delenv("TARGET_PORTIONS", raising=False)
    s = Settings.load()
    assert s.google_api_key == "g"
    assert s.recipients == ["a@example.com", "b@example.com"]
    assert s.crisp_box == "vegan"
    assert s.target_portions == 4


def test_missing_required_raises_with_name(monkeypatch):
    _set_env(monkeypatch, USDA_FDC_API_KEY=None)
    with pytest.raises(ConfigError, match="USDA_FDC_API_KEY"):
        Settings.load()


def test_blank_required_is_treated_as_missing(monkeypatch):
    _set_env(monkeypatch, DEEPL_API_KEY="   ")
    with pytest.raises(ConfigError, match="DEEPL_API_KEY"):
        Settings.load()


def test_empty_optional_falls_back_to_default(monkeypatch):
    """CI passes unset repository variables as empty strings; int('') would crash."""
    _set_env(monkeypatch, CRISP_BOX="", TARGET_PORTIONS="")
    s = Settings.load()
    assert s.crisp_box == "vegan"
    assert s.target_portions == 4


def test_optional_overrides_apply(monkeypatch):
    _set_env(monkeypatch, CRISP_BOX="vega", TARGET_PORTIONS="2")
    s = Settings.load()
    assert s.crisp_box == "vega"
    assert s.target_portions == 2


def test_recipients_accept_semicolons(monkeypatch):
    _set_env(monkeypatch, RECIPIENTS="a@x.com; b@y.com")
    assert Settings.load().recipients == ["a@x.com", "b@y.com"]
