import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
 
from opti_stack.benchmarking import run_benchmark 

from tests.fixtures.sample_scripts import (
    CORRECT_SLOW_SCRIPT,
    CRASHING_SCRIPT,
    INFINITE_LOOP_SCRIPT,
)


def _write_temp_script(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def test_runner_success():
    path = _write_temp_script(CORRECT_SLOW_SCRIPT)
    result = run_benchmark(path)
    assert result.status == "SUCCESS"
    assert result.duration_ms is not None and result.duration_ms > 0
    assert "Matches:" in (result.stdout or "")


def test_runner_handles_crash():
    path = _write_temp_script(CRASHING_SCRIPT)
    result = run_benchmark(path)
    assert result.status == "FAILURE"
    assert "ValueError" in (result.stderr or "")


def test_runner_handles_timeout():
    path = _write_temp_script(INFINITE_LOOP_SCRIPT)
    result = run_benchmark(path)
    assert result.status == "TIMEOUT"
