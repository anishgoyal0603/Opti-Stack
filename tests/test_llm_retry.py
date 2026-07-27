import os
import sys
import types
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from opti_stack.llm_client import (
    GeminiClient, TerminalAPIError, MAX_ATTEMPTS_PER_MODEL,
)


# A single shared APIError class that both the fake SDK module and our
# test-raised errors use, so `except errors.APIError` in call() catches them.
class APIError(Exception):
    def __init__(self, code):
        super().__init__(f"fake API error {code}")
        self.code = code


@pytest.fixture(autouse=True)
def _stub_google_sdk(monkeypatch):
    """call() does `from google.genai import errors` at call time. Inject a
    fake google.genai.errors module exposing our APIError, so no real SDK is
    needed and our raised errors are caught by the real except clause."""
    fake_errors = types.ModuleType("google.genai.errors")
    fake_errors.APIError = APIError

    genai_mod = types.ModuleType("google.genai")
    genai_mod.errors = fake_errors

    google_mod = types.ModuleType("google")
    google_mod.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.errors", fake_errors)
    yield


def _make_client(responses):
    """Build a GeminiClient without running __init__ (which needs the real
    SDK). `responses` is consumed one entry per generate_content call:
    an int raises APIError(code); a str is returned as .text."""
    calls = {"n": 0, "models": [], "slept": []}

    class _Models:
        def generate_content(self, model, contents):
            calls["n"] += 1
            calls["models"].append(model)
            outcome = responses[min(calls["n"] - 1, len(responses) - 1)]
            if isinstance(outcome, int):
                raise APIError(outcome)
            return type("R", (), {"text": outcome})()

    client = GeminiClient.__new__(GeminiClient)  # bypass __init__, no SDK
    client.client = type("C", (), {"models": _Models()})()
    client.models = ["model-a", "model-b"]
    client._sleep = lambda s: calls["slept"].append(s)
    return client, calls


def test_terminal_error_fails_fast_without_trying_other_models():
    client, calls = _make_client([400])
    with pytest.raises(TerminalAPIError):
        client.call("role", "content")
    assert calls["n"] == 1                    # one call, no retries, no fallback
    assert calls["models"] == ["model-a"]


def test_transient_error_retries_same_model_with_backoff():
    client, calls = _make_client([503, 503, "recovered"])
    assert client.call("role", "content") == "recovered"
    assert calls["models"] == ["model-a", "model-a", "model-a"]   # same model
    assert len(calls["slept"]) == 2
    assert calls["slept"][1] > calls["slept"][0]                  # exponential


def test_falls_through_to_next_model_after_exhausting_retries():
    client, calls = _make_client([503] * MAX_ATTEMPTS_PER_MODEL + ["ok"])
    assert client.call("role", "content") == "ok"
    assert "model-b" in calls["models"]       # moved on after model-a exhausted


def test_all_models_exhausted_raises_runtime_error():
    client, calls = _make_client([503] * 99)
    with pytest.raises(RuntimeError):
        client.call("role", "content")