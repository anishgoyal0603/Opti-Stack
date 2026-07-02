import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import opti_stack.orchestrator as orchestrator


class _AlwaysWrongClient:
    """Mocked LLM client that always returns code producing different
    output than the original, so we can test the bounded-retry-then-give-up
    path deterministically, without needing a real Gemini API key."""

    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return "```python\nSCALE = 5\nprint('original')\n```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report: nothing special."
        return "```python\nprint('WRONG OUTPUT')\n```"


class _AlwaysDangerousClient:
    """Mocked LLM client that always returns code the security scanner
    will reject (an os.system call), so we can test the case where every
    optimizer attempt is rejected before ever reaching the verifier --
    this previously crashed with KeyError('benchmark') because the last
    recorded step in that path has no 'benchmark' key."""

    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return "```python\nSCALE = 5\nprint('original')\n```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report."
        return "```python\nimport os\nos.system('echo pwned')\n```"


def test_pipeline_does_not_crash_when_all_attempts_security_rejected(monkeypatch):
    """Regression test: previously raised KeyError('benchmark') because
    trace["steps"][-1]["benchmark"] assumed the last step always has a
    benchmark, which is false when every attempt was security-rejected
    before execution. Fixed via trace["steps"][-1].get("benchmark", {})."""
    monkeypatch.setattr(orchestrator, "GeminiClient", lambda: _AlwaysDangerousClient())

    result = orchestrator.execute_pipeline("print('original')")

    assert result["verified"] is False
    assert result["all_attempts_security_rejected"] is True
    assert result["optimized_benchmark_summary"] == {}
    assert result.get("rejected_at_input") is not True  # input itself was safe
    assert len(result["steps"]) == 2 + orchestrator.MAX_OPTIMIZATION_ATTEMPTS


def test_pipeline_gives_up_gracefully_after_max_attempts(monkeypatch):
    monkeypatch.setattr(orchestrator, "GeminiClient", lambda: _AlwaysWrongClient())

    result = orchestrator.execute_pipeline("print('original')")

    assert result["verified"] is False
    assert result.get("rejected_at_input") is not True
    # Must have recorded: analyst + normalizer + one step per attempt
    assert len(result["steps"]) == 2 + orchestrator.MAX_OPTIMIZATION_ATTEMPTS
    # The CLI/UI both index steps[-1]["code"] on failure -- must not be empty.
    assert result["steps"][-1]["code"]
    assert result["scale_sweep"] == []


def test_pipeline_rejects_dangerous_input_with_empty_steps(monkeypatch):
    """Companion to the cli.py crash fix: confirms steps is an empty list
    (not missing) on the rejected_at_input path, which is exactly the
    shape app/cli.py and app_ui.py must handle without an IndexError."""
    monkeypatch.setattr(orchestrator, "GeminiClient", lambda: _AlwaysWrongClient())

    result = orchestrator.execute_pipeline("import os\nos.system('echo hi')")

    assert result["rejected_at_input"] is True
    assert result["steps"] == []
