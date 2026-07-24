"""
Benchmark runner for the code-benchmarker skill.

Executes a target script in a child process and reports timing and peak
memory as JSON on stdout.

Two things this deliberately does NOT do, because earlier versions did and
both were wrong:

1. It does not call tracemalloc in *this* process. tracemalloc only traces
   allocations made by the interpreter that started it. The target runs in a
   separate OS process, so a parent-side tracemalloc reports the runner's own
   buffer allocations and is completely blind to the target -- a script
   allocating 160 MB and `print(0)` produced byte-identical readings.
   Instead a bootstrap starts tracemalloc *inside* the child, around the
   target, and reports the peak back over a sentinel line on stderr.

2. It does not report a single wall-clock reading that includes interpreter
   startup. CPython takes ~10-25 ms to boot, which swamps any script faster
   than about 100 ms and is added to both sides of every comparison, shrinking
   every speedup the tool reports. We measure that startup cost once, subtract
   it, and report the median of several runs to damp scheduler noise.
"""

import sys
import time
import json
import subprocess
import statistics

SENTINEL = "__OPTISTACK_PEAK_KB__"
DEFAULT_RUNS = 5
TIMEOUT_SECONDS = 10   # per individual run, not for the whole profile

# Runs inside the child. runpy executes the target as __main__ so that
# `if __name__ == "__main__":` blocks in user code still fire.
BOOTSTRAP = (
    "import runpy, sys, tracemalloc\n"
    "tracemalloc.start()\n"
    "try:\n"
    "    runpy.run_path(sys.argv[1], run_name='__main__')\n"
    "finally:\n"
    "    _peak = tracemalloc.get_traced_memory()[1]\n"
    "    tracemalloc.stop()\n"
    "    sys.stderr.write('\\n' + %r + str(round(_peak / 1024, 2)) + '\\n')\n"
) % SENTINEL


def _measure_startup_baseline_ms(runs: int = 3) -> float:
    """Cost of booting the interpreter with no user code at all, so it can be
    subtracted from the target's wall-clock time.

    `python -c "pass"` starts the interpreter and immediately exits, so
    whatever it costs is pure startup overhead. Median of 3 because this is
    itself subject to scheduler noise."""
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "pass"],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def _split_peak(stderr: str):
    """Pull the sentinel line back out of stderr and return
    (clean_stderr, peak_kb). Verification compares stdout, but stderr is
    surfaced to the user on failure, so the sentinel must not leak into it."""
    peak_kb = None
    kept = []
    for line in (stderr or "").splitlines():
        if line.startswith(SENTINEL):
            try:
                peak_kb = float(line[len(SENTINEL):])
            except ValueError:
                pass
        else:
            kept.append(line)
    return "\n".join(kept), peak_kb


def run_profile(script_path: str, runs: int = DEFAULT_RUNS) -> dict:
    result = {
        "status": "UNKNOWN",
        "duration_ms": None,
        "peak_memory_kb": None,
        "stdout": None,
        "stderr": None,
        "runs": runs,
        "baseline_ms": None,
    }
    try:
        baseline_ms = _measure_startup_baseline_ms()
        result["baseline_ms"] = round(baseline_ms, 3)

        durations = []
        proc = None
        for _ in range(runs):
            start = time.perf_counter()
            proc = subprocess.run(
                [sys.executable, "-c", BOOTSTRAP, script_path],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
            )
            durations.append((time.perf_counter() - start) * 1000)
            if proc.returncode != 0:
                break  # no point timing a script that crashes

        clean_stderr, peak_kb = _split_peak(proc.stderr)

        # Median, not mean: one unlucky scheduler pause would drag a mean
        # upward, whereas the median simply ignores an outlier.
        elapsed = statistics.median(durations)

        # Subtract interpreter startup, but never report a negative figure:
        # for trivial scripts the target and the baseline are within noise of
        # each other and the subtraction can go slightly below zero.
        result["duration_ms"] = round(max(elapsed - baseline_ms, 0.0), 3)
        result["peak_memory_kb"] = peak_kb
        result["stdout"] = proc.stdout
        result["stderr"] = clean_stderr
        result["status"] = "SUCCESS" if proc.returncode == 0 else "FAILURE"

    except subprocess.TimeoutExpired:
        result["status"] = "TIMEOUT"
        result["stderr"] = f"Execution exceeded {TIMEOUT_SECONDS}-second threshold."
    except Exception as e:
        result["status"] = "CRITICAL_ERROR"
        result["stderr"] = str(e)

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"status": "CRITICAL_ERROR", "stderr": "No script path provided."}))
        sys.exit(1)
    output = run_profile(sys.argv[1])
    print(json.dumps(output))
    sys.exit(0 if output["status"] == "SUCCESS" else 1)
ENDOFFILE