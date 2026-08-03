import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import opti_stack.orchestrator as orchestrator

class _EchoClient:
    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return "```python\nSCALE = 5\nprint('A')\n```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report."
        return "```python\nprint('A')\n```"

def test_trace_has_run_id_and_is_written_to_disk():
    r = orchestrator.execute_pipeline("print('A')", client=_EchoClient())
    assert len(r["run_id"]) == 12
    assert r["trace_path"] is not None and os.path.exists(r["trace_path"])
    with open(r["trace_path"]) as f:
        assert json.load(f)["run_id"] == r["run_id"]
    os.remove(r["trace_path"])

def test_trace_write_failure_does_not_break_the_run(monkeypatch):
    def _boom(*a, **k): raise OSError("simulated disk full")
    monkeypatch.setattr(orchestrator.os, "makedirs", _boom)
    r = orchestrator.execute_pipeline("print('A')", client=_EchoClient())
    assert r["verified"] is True and r["trace_path"] is None

def test_trace_persistence_can_be_disabled(monkeypatch):
    monkeypatch.setenv("OPTISTACK_PERSIST_TRACES", "0")
    r = orchestrator.execute_pipeline("print('A')", client=_EchoClient())
    assert r["verified"] is True
    assert r["trace_path"] is None          # not written to disk
    assert len(r["run_id"]) == 12           # still has an in-memory trace id

def test_trace_directory_is_pruned_to_retention_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(orchestrator, "TRACE_RETENTION", 3)
    for _ in range(6):
        orchestrator.execute_pipeline("print('A')", client=_EchoClient())
    remaining = [f for f in os.listdir(tmp_path) if f.endswith(".json")]
    assert len(remaining) == 3, f"expected 3 kept, found {len(remaining)}"
