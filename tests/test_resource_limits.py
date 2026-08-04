"""Runner resource-limit and DoS-containment tests.

These cover the attacks the static scanner CANNOT catch, because they use no
imports and no denylisted calls -- they just try to exhaust the host."""
import os, sys, time, tempfile
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

def test_bytearray_allocation_bomb_is_contained():
    """Same class of attack without building a list -- must also be caught."""
    r = run_benchmark(_w("b = bytearray(10**9)\nprint(len(b))\n"))
    assert r.status in ("FAILURE", "TIMEOUT")

def test_huge_int_cpu_bomb_is_contained():
    """7**7**7 is a single expression with no loop; RLIMIT_CPU/AS must stop it."""
    r = run_benchmark(_w("x = 7**7**7\nprint(len(str(x)))\n"))
    assert r.status in ("FAILURE", "TIMEOUT")

def test_cpu_bomb_is_killed_by_cpu_limit():
    r = run_benchmark(_w("i = 0\nwhile True:\n    i += 1\n"))
    assert r.status in ("FAILURE", "TIMEOUT")

def test_sleep_bomb_is_bounded_by_the_total_sampling_budget():
    """A sleeping script burns ZERO cpu, so RLIMIT_CPU never fires, and each
    individual run stays under TIMEOUT_SECONDS so nothing times out. Before
    the total-budget guard this returned SUCCESS after ~46 seconds (5 runs of
    a 9s sleep). The budget must cut sampling short well before that."""
    start = time.perf_counter()
    r = run_benchmark(_w("import time\ntime.sleep(4)\nprint('done')\n"))
    elapsed = time.perf_counter() - start
    assert elapsed < 30, f"sampling took {elapsed:.1f}s -- total budget not enforced"
    assert r.runs is not None and r.runs < 5, "budget should have truncated sampling"

def test_print_flood_does_not_balloon_parent_memory():
    flood = "s = 'x' * 10000\nfor _ in range(100000):\n    print(s)\n"
    r = run_benchmark(_w(flood))
    assert r.status in ("SUCCESS", "FAILURE", "TIMEOUT")
    assert len(r.stdout or "") < 3_000_000, "output was not capped"

def test_normal_script_still_runs_fine_under_limits():
    r = run_benchmark(_w("print(sum(range(100000)))\n"))
    assert r.status == "SUCCESS"
    assert "4999950000" in (r.stdout or "")

def test_moderate_memory_script_within_limit_succeeds():
    r = run_benchmark(_w("data = [0] * 5_000_000\nprint(sum(data))\n"))
    assert r.status == "SUCCESS"
    assert r.peak_memory_kb is not None and r.peak_memory_kb > 10_000
