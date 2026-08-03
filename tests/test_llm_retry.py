import os, sys, types, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opti_stack.llm_client import GeminiClient, TerminalAPIError, MAX_ATTEMPTS_PER_MODEL

class APIError(Exception):
    def __init__(self, code):
        super().__init__(f"fake API error {code}"); self.code = code

@pytest.fixture(autouse=True)
def _stub_google_sdk(monkeypatch):
    fake_errors = types.ModuleType("google.genai.errors"); fake_errors.APIError = APIError
    genai_mod = types.ModuleType("google.genai"); genai_mod.errors = fake_errors
    captured = {}
    class _Client:
        def __init__(self, api_key=None): captured["api_key"] = api_key
    genai_mod.Client = _Client
    google_mod = types.ModuleType("google"); google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.errors", fake_errors)
    _stub_google_sdk.captured = captured
    yield

def _make_client(responses):
    calls = {"n": 0, "models": [], "slept": []}
    class _Models:
        def generate_content(self, model, contents):
            calls["n"] += 1; calls["models"].append(model)
            outcome = responses[min(calls["n"] - 1, len(responses) - 1)]
            if isinstance(outcome, int): raise APIError(outcome)
            return type("R", (), {"text": outcome})()
    client = GeminiClient.__new__(GeminiClient)
    client.client = type("C", (), {"models": _Models()})()
    client.models = ["model-a", "model-b"]
    client._sleep = lambda s: calls["slept"].append(s)
    return client, calls

def test_terminal_error_fails_fast_without_trying_other_models():
    client, calls = _make_client([400])
    with pytest.raises(TerminalAPIError): client.call("role", "content")
    assert calls["n"] == 1 and calls["models"] == ["model-a"]

def test_transient_error_retries_same_model_with_backoff():
    client, calls = _make_client([503, 503, "recovered"])
    assert client.call("role", "content") == "recovered"
    assert calls["models"] == ["model-a", "model-a", "model-a"]
    assert len(calls["slept"]) == 2 and calls["slept"][1] > calls["slept"][0]

def test_falls_through_to_next_model_after_exhausting_retries():
    client, calls = _make_client([503] * MAX_ATTEMPTS_PER_MODEL + ["ok"])
    assert client.call("role", "content") == "ok"
    assert "model-b" in calls["models"]

def test_all_models_exhausted_raises_runtime_error():
    client, calls = _make_client([503] * 99)
    with pytest.raises(RuntimeError): client.call("role", "content")

def test_api_key_is_passed_to_sdk_client_not_env():
    """The session's key must reach genai.Client(api_key=...) and must NOT be
    written into os.environ (which is shared across all sessions)."""
    before = os.environ.get("GEMINI_API_KEY")
    GeminiClient(api_key="sk-session-123", models=["m"])
    assert _stub_google_sdk.captured["api_key"] == "sk-session-123"
    assert os.environ.get("GEMINI_API_KEY") == before  # unchanged
