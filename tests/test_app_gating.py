"""Real Streamlit smoke test proving the examples-only gate actually renders
the right UI, not just that the underlying boolean computes correctly."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from streamlit.testing.v1 import AppTest

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "ui", "app.py")


def test_default_mode_shows_example_picker_not_free_text(monkeypatch):
    monkeypatch.delenv("OPTISTACK_ALLOW_ARBITRARY_CODE", raising=False)
    at = AppTest.from_file(APP_PATH)
    at.run()
    assert at.exception == []
    # In the safe default, there must be a selectbox (example picker) and
    # NO text_area (free-text paste surface).
    assert len(at.selectbox) >= 1
    assert len(at.text_area) == 0


def test_arbitrary_mode_shows_free_text_area(monkeypatch):
    monkeypatch.setenv("OPTISTACK_ALLOW_ARBITRARY_CODE", "1")
    at = AppTest.from_file(APP_PATH)
    at.run()
    assert at.exception == []
    assert len(at.text_area) >= 1


def test_unset_env_defaults_to_safe_mode(monkeypatch):
    """No config at all -- a fresh clone, a forgotten env var on a new
    deploy target -- must still land in the safe mode, not the open one."""
    monkeypatch.delenv("OPTISTACK_ALLOW_ARBITRARY_CODE", raising=False)
    at = AppTest.from_file(APP_PATH)
    at.run()
    assert len(at.text_area) == 0


def test_garbage_env_value_defaults_to_safe_mode(monkeypatch):
    monkeypatch.setenv("OPTISTACK_ALLOW_ARBITRARY_CODE", "yes_please")
    at = AppTest.from_file(APP_PATH)
    at.run()
    assert len(at.text_area) == 0
