import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import opti_stack.orchestrator as orchestrator


class _EchoClient:
    """Returns an optimized script that simply echoes back whatever marker the
    original printed, so a cross-contaminated run produces a verifier mismatch."""

    def __init__(self, marker):
        self.marker = marker

    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return f"```python\nSCALE = 5\nprint('{self.marker}')\n```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report."
        return f"```python\nprint('{self.marker}')\n```"


def test_each_run_uses_a_distinct_temp_directory(monkeypatch):
    seen = []
    real = orchestrator._execute_pipeline_in

    def spy(run_dir, script, on_status=None):
        seen.append(run_dir)
        return real(run_dir, script, on_status)

    monkeypatch.setattr(orchestrator, "_execute_pipeline_in", spy)
    monkeypatch.setattr(orchestrator, "GeminiClient", lambda: _EchoClient("A"))

    orchestrator.execute_pipeline("print('A')")
    orchestrator.execute_pipeline("print('A')")

    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_run_directory_is_cleaned_up(monkeypatch):
    captured = {}
    real = orchestrator._execute_pipeline_in

    def spy(run_dir, script, on_status=None):
        captured["dir"] = run_dir
        return real(run_dir, script, on_status)

    monkeypatch.setattr(orchestrator, "_execute_pipeline_in", spy)
    monkeypatch.setattr(orchestrator, "GeminiClient", lambda: _EchoClient("A"))

    orchestrator.execute_pipeline("print('A')")
    assert not os.path.exists(captured["dir"])


def test_concurrent_runs_do_not_contaminate_each_other(monkeypatch):
    """Two pipelines in flight simultaneously must each verify against their
    own script, not the other's."""
    results = {}

    def run(marker):
        monkeypatch.setattr(orchestrator, "GeminiClient", lambda: _EchoClient(marker))
        results[marker] = orchestrator.execute_pipeline(f"print('{marker}')")

    t1 = threading.Thread(target=run, args=("ALPHA",))
    t2 = threading.Thread(target=run, args=("BETA",))
    t1.start(); t2.start(); t1.join(); t2.join()

    for marker, result in results.items():
        for step in result["steps"]:
            if step.get("agent") == "optimizer" and step.get("code"):
                assert marker in step["code"]