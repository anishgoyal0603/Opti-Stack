import sys
import time
import json
import tracemalloc
import subprocess


def run_profile(script_path: str) -> dict:
    """Executes a target script in a subprocess and captures timing,
    peak memory, and stdout (for correctness comparison upstream).
    Returns a structured dict instead of printing, so callers (agent
    orchestrator, UI, tests) can consume it programmatically."""
    result = {
        "status": "UNKNOWN",
        "duration_ms": None,
        "peak_memory_kb": None,
        "stdout": None,
        "stderr": None,
    }
    try:
        tracemalloc.start()
        start_mark = time.perf_counter()

        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=10,
        )

        end_mark = time.perf_counter()
        _, peak_memory = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        result["duration_ms"] = round((end_mark - start_mark) * 1000, 3)
        result["peak_memory_kb"] = round(peak_memory / 1024, 2)
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr

        if proc.returncode != 0:
            result["status"] = "FAILURE"
        else:
            result["status"] = "SUCCESS"

    except subprocess.TimeoutExpired:
        result["status"] = "TIMEOUT"
        result["stderr"] = "Execution exceeded 10-second threshold."
    except Exception as e:
        result["status"] = "CRITICAL_ERROR"
        result["stderr"] = str(e)

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"status": "CRITICAL_ERROR", "stderr": "No script path provided."}))
        sys.exit(1)

    output = run_profile(sys.argv[1])
    # Machine-readable output on stdout so callers can json.loads() it.
    # (Earlier version printed human-formatted text, which forced callers
    # to regex-parse strings to get numbers back out — fragile.)
    print(json.dumps(output))
    sys.exit(0 if output["status"] == "SUCCESS" else 1)
