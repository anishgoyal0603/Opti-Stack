"""Tests for the runner's resource-limit and output-cap DoS defenses (Fix 2).
These are the attacks the static scanner CANNOT catch, because they use no
imports and no denylisted calls -- they just try to exhaust the host."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opti_stack.benchmarking import run_benchmark

def _w(c):
    fd, p = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f: f.write(c)
    return p

def test_allocation_bomb_is_contained_not_crashing_host():
    """[0]*10**9 wants ~8GB; RLIMIT_AS must turn this into a contained
    MemoryError (FAILURE), not an OOM of the runner/host."""
    r = run_benchmark(_w("x = [0] * (10**9)\nprint(len(x))\n"))
    assert r.status in ("FAILURE", "TIMEOUT")
    assert "MemoryError" in (r.stderr or "") or r.status == "TIMEOUT"

def test_cpu_bomb_is_killed_by_cpu_limit():
    """A tight compute loop that would run far past the CPU limit must be
    killed (FAILURE via SIGXCPU) or time out -- never hang the harness."""
    r = run_benchmark(_w("i = 0\nwhile True:\n    i += 1\n"))
    assert r.status in ("FAILURE", "TIMEOUT")

def test_print_flood_does_not_balloon_parent_memory():
    """A huge amount of stdout must be truncated at the cap, so the parent
    never accumulates unbounded output. We assert the captured stdout stays
    within a small multiple of the 1MB cap rather than the full flood."""
    flood = "s = 'x' * 10000\nfor _ in range(100000):\n    print(s)\n"
    r = run_benchmark(_w(flood))
    # status may be SUCCESS (cap hit, drained) or FAILURE/TIMEOUT (cpu/time)
    assert r.status in ("SUCCESS", "FAILURE", "TIMEOUT")
    assert len(r.stdout or "") < 3_000_000, "output was not capped"

def test_normal_script_still_runs_fine_under_limits():
    r = run_benchmark(_w("print(sum(range(100000)))\n"))
    assert r.status == "SUCCESS"
    assert "4999950000" in (r.stdout or "")

def test_moderate_memory_script_within_limit_succeeds():
    """~40MB list is well under the 512MB cap and must still succeed, proving
    the limit isn't so tight it breaks legitimate memory use."""
    r = run_benchmark(_w("data = [0] * 5_000_000\nprint(sum(data))\n"))
    assert r.status == "SUCCESS"
    assert r.peak_memory_kb is not None and r.peak_memory_kb > 10_000
