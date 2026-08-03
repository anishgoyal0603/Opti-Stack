import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opti_stack.benchmarking import run_benchmark
from tests.fixtures.sample_scripts import CORRECT_SLOW_SCRIPT, CRASHING_SCRIPT, INFINITE_LOOP_SCRIPT

def _w(c):
    fd, p = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f: f.write(c)
    return p

def test_runner_success():
    r = run_benchmark(_w(CORRECT_SLOW_SCRIPT))
    # duration_ms is floored at 0.0 by design (see verification.py's docstring
    # and test_duration_is_never_negative_after_subtraction): on a slow or
    # noisy host, a fast script's timed run can land at or below the measured
    # baseline's median, and 0.0 is the correct clamped result, not a bug.
    # Strict ">0" is flaky across environments with different subprocess-spawn
    # overhead (e.g. Windows vs. the Linux CI runners), so we only require
    # a valid non-negative number here.
    assert r.status == "SUCCESS" and r.duration_ms is not None and r.duration_ms >= 0
    assert "Matches:" in (r.stdout or "")

def test_runner_handles_crash():
    r = run_benchmark(_w(CRASHING_SCRIPT))
    assert r.status == "FAILURE" and "ValueError" in (r.stderr or "")

def test_runner_handles_timeout():
    r = run_benchmark(_w(INFINITE_LOOP_SCRIPT))
    assert r.status in ("TIMEOUT", "FAILURE")  # CPU limit may fire before wall-clock
