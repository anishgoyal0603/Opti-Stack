"""
Benchmark runner for the code-benchmarker skill.

Executes a target script in a child process and reports timing and peak
memory as JSON on stdout.

Four things this deliberately does, because earlier versions did not and each
omission was either wrong or exploitable:

1. It does NOT call tracemalloc in *this* process. tracemalloc only traces
   allocations made by the interpreter that started it. The target runs in a
   separate OS process, so a parent-side tracemalloc reports the runner's own
   buffer allocations and is blind to the target. A bootstrap starts
   tracemalloc *inside* the child, around the target, and reports the peak
   back over a sentinel line on stderr.

2. It does NOT report a single wall-clock reading that includes interpreter
   startup. CPython takes ~10-25 ms to boot, which swamps any script faster
   than ~100 ms and is added to both sides of every comparison. We measure
   that startup cost once, subtract it, and report the median of several runs
   to damp scheduler noise.

3. It BOUNDS what a submitted script can consume *inside* the child. Even
   with a static security scan in front of it, code with no imports and no
   denylisted calls can exhaust the host: `[0] * 10**9` or `bytearray(10**9)`
   (memory), `7**7**7` or a tight loop (CPU). The child sets RLIMIT_AS,
   RLIMIT_CPU and RLIMIT_FSIZE on itself before running any user code, so an
   allocation bomb dies with MemoryError and a compute bomb dies with
   SIGXCPU -- inside the child, not by taking the host down. The parent reads
   stdout/stderr through a capped, self-draining reader so a print-flood
   cannot balloon the parent's memory either.

4. It BOUNDS total sampling wall-clock, which the per-run limits above do not
   cover. A script that sleeps consumes no CPU, so RLIMIT_CPU never fires,
   and if each sleep stays under TIMEOUT_SECONDS no run ever times out --
   yet DEFAULT_RUNS repetitions still pin a worker for minutes and the call
   returns SUCCESS. Measured before this guard: `time.sleep(9)` produced a
   SUCCESS after 46 seconds in a single run_profile call. The loop now stops
   taking further samples once TOTAL_BUDGET_SECONDS of wall-clock has been
   spent and reports the median of the samples actually collected, which is
   statistically weaker but bounded. `runs` in the result reflects how many
   samples were really taken, so a consumer can tell a budget-truncated
   measurement from a full one.

None of this makes running untrusted code *safe* -- it makes the easy
denial-of-service attacks ineffective. A real multi-tenant deployment still
needs a container / gVisor boundary.
"""

import sys
import time
import json
import threading
import subprocess
import statistics

SENTINEL = "__OPTISTACK_PEAK_KB__"
DEFAULT_RUNS = 5
TIMEOUT_SECONDS = 10          # wall-clock, per individual run
TOTAL_BUDGET_SECONDS = 15     # wall-clock, across all timed samples combined
CPU_SECONDS = 5               # CPU-time limit enforced inside the child
ADDRESS_SPACE_BYTES = 512 * 1024 * 1024   # ~512 MB virtual memory cap per run
FILE_SIZE_BYTES = 1 * 1024 * 1024         # 1 MB max single file write
MAX_OUTPUT_BYTES = 1_000_000  # cap on captured stdout/stderr, protects parent

# Runs inside the child. It first clamps its own resource limits, then runpy
# executes the target as __main__ so `if __name__ == "__main__":` blocks in
# user code still fire. `resource` is POSIX-only; the try/except keeps this
# working on Windows for local development (where the limits simply don't
# apply -- the deployed Linux host is where they matter).
BOOTSTRAP = (
    "import runpy, sys, tracemalloc\n"
    "try:\n"
    "    import resource\n"
    "    resource.setrlimit(resource.RLIMIT_CPU, (%d, %d))\n"
    "    resource.setrlimit(resource.RLIMIT_AS, (%d, %d))\n"
    "    resource.setrlimit(resource.RLIMIT_FSIZE, (%d, %d))\n"
    "except Exception:\n"
    "    pass\n"
    "tracemalloc.start()\n"
    "try:\n"
    "    runpy.run_path(sys.argv[1], run_name='__main__')\n"
    "finally:\n"
    "    _peak = tracemalloc.get_traced_memory()[1]\n"
    "    tracemalloc.stop()\n"
    "    sys.stderr.write('\\n' + %r + str(round(_peak / 1024, 2)) + '\\n')\n"
) % (
    CPU_SECONDS, CPU_SECONDS,
    ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES,
    FILE_SIZE_BYTES, FILE_SIZE_BYTES,
    SENTINEL,
)


def _capped_reader(stream, cap: int) -> str:
    """Read from a pipe up to `cap` characters, then keep draining it (so the
    child never blocks on a full pipe) while discarding the excess. Returns
    the captured, possibly-truncated text. This is what makes a print-flood
    harmless to the parent: we never accumulate more than `cap` in memory."""
    collected = []
    total = 0
    truncated = False
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        if not truncated:
            collected.append(chunk)
            total += len(chunk)
            if total >= cap:
                truncated = True
                collected.append("\n...[output truncated at cap]\n")
        # once truncated we keep looping to drain, but store nothing more
    return "".join(collected)


def _run_once(script_path: str):
    """Run the target once under the bootstrap, capping captured output and
    enforcing the wall-clock timeout. Raises subprocess.TimeoutExpired on
    timeout (so the caller's existing TIMEOUT handling still applies).
    Returns (returncode, stdout, stderr, elapsed_ms)."""
    start = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, "-c", BOOTSTRAP, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    captured = {}

    def _reader(name, stream):
        captured[name] = _capped_reader(stream, MAX_OUTPUT_BYTES)

    t_out = threading.Thread(target=_reader, args=("stdout", proc.stdout))
    t_err = threading.Thread(target=_reader, args=("stderr", proc.stderr))
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        t_out.join()
        t_err.join()
        raise

    elapsed = (time.perf_counter() - start) * 1000
    t_out.join()
    t_err.join()
    return proc.returncode, captured.get("stdout", ""), captured.get("stderr", ""), elapsed


def _measure_startup_baseline_ms(runs: int = 3) -> float:
    """Cost of booting the interpreter with no user code at all, so it can be
    subtracted from the target's wall-clock time. Median of 3 to damp noise."""
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
    (clean_stderr, peak_kb)."""
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
        returncode = 0
        last_stdout = ""
        last_stderr = ""
        sampling_start = time.perf_counter()

        for _ in range(runs):
            returncode, last_stdout, last_stderr, elapsed = _run_once(script_path)
            durations.append(elapsed)
            if returncode != 0:
                break  # no point timing a script that crashes
            if time.perf_counter() - sampling_start > TOTAL_BUDGET_SECONDS:
                # Budget spent. Stop sampling and report what we have rather
                # than letting a slow-but-legal script pin the worker for
                # runs * TIMEOUT_SECONDS.
                break

        # Report the number of samples actually taken, not the number
        # requested, so a budget-truncated measurement is visible downstream.
        result["runs"] = len(durations)

        clean_stderr, peak_kb = _split_peak(last_stderr)

        elapsed = statistics.median(durations)
        result["duration_ms"] = round(max(elapsed - baseline_ms, 0.0), 3)
        result["peak_memory_kb"] = peak_kb
        result["stdout"] = last_stdout
        result["stderr"] = clean_stderr
        result["status"] = "SUCCESS" if returncode == 0 else "FAILURE"

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
