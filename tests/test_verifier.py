import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from opti_stack.benchmarking import run_benchmark
from opti_stack.verification import verify_equivalence

from tests.fixtures.sample_scripts import (
    CORRECT_SLOW_SCRIPT,
    CORRECT_FAST_EQUIVALENT,
    INCORRECT_FAST_VARIANT,
)
import tempfile


def _write_temp_script(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def test_verifier_accepts_equivalent_output():
    orig_path = _write_temp_script(CORRECT_SLOW_SCRIPT)
    fast_path = _write_temp_script(CORRECT_FAST_EQUIVALENT)

    original_result = run_benchmark(orig_path)
    fast_result = run_benchmark(fast_path)

    verdict = verify_equivalence(original_result, fast_result)
    assert verdict["passed"] is True


def test_verifier_rejects_incorrect_output():
    """This is the test that proves the core bug fix: a faster script
    that produces the WRONG answer must be rejected, not silently
    accepted as a successful optimization."""
    orig_path = _write_temp_script(CORRECT_SLOW_SCRIPT)
    wrong_path = _write_temp_script(INCORRECT_FAST_VARIANT)

    original_result = run_benchmark(orig_path)
    wrong_result = run_benchmark(wrong_path)

    verdict = verify_equivalence(original_result, wrong_result)
    assert verdict["passed"] is False
    assert "mismatch" in verdict["reason"].lower()
