import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import opti_stack.orchestrator as orchestrator


class _EchoClient:
    """Returns valid, verifiable code so the pipeline completes successfully
    and we can inspect the persisted trace afterward."""

    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return "```python\nSCALE = 5\nprint('A')\n```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report."
        return "```python\nprint('A')\n```"


def test_trace_has_run_id_and_is_written_to_disk():
    result = orchestrator.execute_pipeline("print('A')", client=_EchoClient())

    assert len(result["run_id"]) == 12
    assert result["trace_path"] is not None
    assert os.path.exists(result["trace_path"])

    with open(result["trace_path"]) as f:
        on_disk = json.load(f)
    assert on_disk["run_id"] == result["run_id"]

    os.remove(result["trace_path"])  # don't leave test artifacts behind


def test_trace_write_failure_does_not_break_the_run(monkeypatch):
    """If the traces/ directory can't be created (disk full, permissions),
    the pipeline must still return a result -- telemetry failing should never
    take down the actual feature."""
    def _boom(*a, **k):
        raise OSError("simulated disk full")

    monkeypatch.setattr(orchestrator.os, "makedirs", _boom)

    result = orchestrator.execute_pipeline("print('A')", client=_EchoClient())

    assert result["verified"] is True          # pipeline still succeeded
    assert result["trace_path"] is None         # telemetry degraded gracefully