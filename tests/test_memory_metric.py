import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from opti_stack.benchmarking import run_benchmark
from tests.fixtures.sample_scripts import MEMORY_HEAVY_SCRIPT, MEMORY_LIGHT_SCRIPT


def _write_temp_script(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def test_memory_metric_distinguishes_heavy_from_light():
    """Regression test for a real bug: tracemalloc was started in the parent
    process while the target ran in a subprocess, so peak_memory_kb was blind
    to the target entirely and returned an identical constant for a 160 MB
    allocation and for print(0)."""
    heavy = run_benchmark(_write_temp_script(MEMORY_HEAVY_SCRIPT))
    light = run_benchmark(_write_temp_script(MEMORY_LIGHT_SCRIPT))

    assert heavy.status == "SUCCESS"
    assert light.status == "SUCCESS"
    assert heavy.peak_memory_kb is not None
    assert light.peak_memory_kb is not None
    # 5M-element list is tens of MB; a bare print is under ~2 MB.
    assert heavy.peak_memory_kb > 10_000
    assert heavy.peak_memory_kb > light.peak_memory_kb * 10


def test_sentinel_does_not_leak_into_stderr():
    """The peak-memory sentinel is written to the child's stderr and must be
    stripped before stderr is shown to the user."""
    result = run_benchmark(_write_temp_script(MEMORY_LIGHT_SCRIPT))
    assert "__OPTISTACK_PEAK_KB__" not in (result.stderr or "")