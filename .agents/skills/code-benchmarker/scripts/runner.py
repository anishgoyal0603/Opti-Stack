"""
Benchmark runner for the code-benchmarker skill.

Executes a target script in a child process and reports timing and peak
memory as JSON on stdout.

One thing this deliberately does NOT do, because an earlier version did and it
was wrong: it does not call tracemalloc in *this* process. tracemalloc only
traces allocations made by the interpreter that started it. The target runs in a
separate OS process, so a parent-side tracemalloc reports the runner's own buffer
allocations and is completely blind to the target -- a script allocating 160 MB
and `print(0)` produced byte-identical readings. Instead a bootstrap starts
tracemalloc inside the child, around the target, and reports the peak back over a
sentinel line on stderr.
"""

import sys
import time
import json
import subprocess

SENTINEL = "__OPTISTACK_PEAK_KB__"
TIMEOUT_SECONDS = 10

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


def _split_peak(stderr: str):
    """Pull the sentinel line back out of stderr and return
    (clean_stderr, peak_kb). stderr is surfaced to the user on failure, so the
    sentinel must not leak into it."""
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


def run_profile(script_path: str) -> dict:
    result = {
        "status": "UNKNOWN",
        "duration_ms": None,
        "peak_memory_kb": None,
        "stdout": None,
        "stderr": None,
    }
    try:
        start = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-c", BOOTSTRAP, script_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        clean_stderr, peak_kb = _split_peak(proc.stderr)

        result["duration_ms"] = round(elapsed_ms, 3)
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