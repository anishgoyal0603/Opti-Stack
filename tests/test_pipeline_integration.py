import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import opti_stack.orchestrator as orchestrator

class _AlwaysWrongClient:
    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return "```python\nSCALE = 5\nprint('original')\n```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report: nothing special."
        return "```python\nprint('WRONG OUTPUT')\n```"

class _AlwaysDangerousClient:
    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return "```python\nSCALE = 5\nprint('original')\n```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report."
        return "```python\nimport os\nos.system('echo pwned')\n```"

def test_pipeline_does_not_crash_when_all_attempts_security_rejected():
    result = orchestrator.execute_pipeline("print('original')", client=_AlwaysDangerousClient())
    assert result["verified"] is False
    assert result["all_attempts_security_rejected"] is True
    assert result["optimized_benchmark_summary"] == {}
    assert result.get("rejected_at_input") is not True
    assert len(result["steps"]) == 2 + orchestrator.MAX_OPTIMIZATION_ATTEMPTS

class _OptimizerUsesUndefinedScale:
    def call(self, role_prompt, content, on_status=None):
        if "refactoring" in role_prompt.lower():
            return ("```python\nSCALE = 1000\n"
                    "def process_data(limit):\n"
                    "    return sum(1 for x in range(limit) if 0 <= (limit-1-x) < limit)\n"
                    "print(f'Matches calculated: {process_data(SCALE)}')\n```")
        if "performance engineering" in role_prompt.lower():
            return "Nested loop is O(n^2)."
        return ("```python\n"
                "def process_data(limit):\n"
                "    return sum(1 for x in range(limit) if 0 <= (limit-1-x) < limit)\n"
                "print(f'Matches calculated: {process_data(SCALE)}')\n```")

def test_pipeline_handles_optimizer_code_that_reads_scale():
    original = ("def process_data(limit):\n"
                "    data_list = list(range(limit))\n"
                "    matches = 0\n"
                "    for x in data_list:\n"
                "        for y in data_list:\n"
                "            if x + y == limit - 1:\n"
                "                matches += 1\n"
                "    return matches\n"
                "print(f'Matches calculated: {process_data(1500)}')\n")
    result = orchestrator.execute_pipeline(original, client=_OptimizerUsesUndefinedScale())
    assert result["verified"] is True
    assert result["steps"][-1]["verification"]["passed"] is True
    assert "NameError" not in (result["steps"][-1]["verification"].get("reason") or "")

def test_pipeline_gives_up_gracefully_after_max_attempts():
    result = orchestrator.execute_pipeline("print('original')", client=_AlwaysWrongClient())
    assert result["verified"] is False
    assert result.get("rejected_at_input") is not True
    assert len(result["steps"]) == 2 + orchestrator.MAX_OPTIMIZATION_ATTEMPTS
    assert result["steps"][-1]["code"]
    assert result["scale_sweep"] == []

def test_pipeline_rejects_dangerous_input_with_empty_steps():
    result = orchestrator.execute_pipeline("import os\nos.system('echo hi')", client=_AlwaysWrongClient())
    assert result["rejected_at_input"] is True
    assert result["steps"] == []


class _SlowButValidClient:
    """Produces a verified-correct but deliberately slow script. The scale
    sweep is the most expensive part of a run (two variants at every scale),
    so it must be skipped when the original is already slow -- otherwise a
    slow-but-legal submission multiplies the per-click subprocess fan-out."""
    def call(self, role_prompt, content, on_status=None):
        code = "import time\ntime.sleep(2.5)\nprint('slow')\n"
        if "refactoring" in role_prompt.lower():
            return f"```python\nSCALE = 5\n{code}```"
        if "performance engineering" in role_prompt.lower():
            return "Fake analyst report."
        return f"```python\n{code}```"

def test_scale_sweep_is_skipped_when_original_is_slow():
    result = orchestrator.execute_pipeline(
        "import time\ntime.sleep(2.5)\nprint('slow')\n", client=_SlowButValidClient()
    )
    assert result["verified"] is True
    assert result["scale_sweep"] == []
    assert result["scale_sweep_skipped"] is True
    assert "threshold" in result["scale_sweep_skip_reason"]

def test_scale_sweep_still_runs_for_a_fast_script():
    class _FastClient:
        def call(self, role_prompt, content, on_status=None):
            if "refactoring" in role_prompt.lower():
                return "```python\nSCALE = 5\nprint('A')\n```"
            if "performance engineering" in role_prompt.lower():
                return "Fake analyst report."
            return "```python\nprint('A')\n```"
    result = orchestrator.execute_pipeline("print('A')", client=_FastClient())
    assert result["verified"] is True
    assert result.get("scale_sweep_skipped") is not True
    assert len(result["scale_sweep"]) > 0
