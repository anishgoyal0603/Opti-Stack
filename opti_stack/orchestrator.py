"""
Opti-Stack orchestrator.

This is the Coordinator: it decides what order agents run in and how to
branch based on their output. It intentionally contains no LLM-calling
mechanics (see llm_client.py), no prompt text (see prompts.py), no
benchmarking mechanics (see benchmarking.py), and no correctness-checking
logic (see verification.py) -- those are each a separate, focused module
so this file stays readable as "the flow", not "the flow plus everything
each step does internally".

  Coordinator (this module)
    -> Security Scanner  : rejects unsafe code before anything else runs
    -> Analyst agent      : diagnoses complexity bottlenecks
    -> Normalizer agent    : extracts a SCALE variable for fair stress-testing
    -> Optimizer agent     : proposes a rewrite
    -> Security Scanner   : rejects unsafe rewrites before they ever execute
    -> Benchmarker skill    : measures speed/memory (existing runner.py)
    -> Verifier            : checks output equivalence, deterministically
    -> Coordinator decides : accept, or send back to Optimizer with the
                              verifier's failure reason (retry loop, bounded)

Every completed run is also persisted to disk as a JSON trace (see
_persist_trace below) -- this is what makes "observability" a concrete,
inspectable artifact rather than just a word on a resume.
"""

import os
import re
import json
import uuid
import tempfile
import shutil
import dataclasses
from datetime import datetime, timezone

from dotenv import load_dotenv

from .llm_client import GeminiClient
from .prompts import ANALYST_ROLE, NORMALIZER_ROLE, OPTIMIZER_ROLE, OPTIMIZER_RETRY_ROLE
from .benchmarking import run_benchmark, run_scale_sweep
from .verification import verify_equivalence
from .security_scanner import scan_code
from .synthetic_data import extract_scale_value, inject_scale

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), override=True)

MAX_OPTIMIZATION_ATTEMPTS = 3

TRACE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "traces"
)


def _persist_trace(trace: dict) -> dict:
    """Writes a structured JSON trace per run to disk. This is what makes the
    'observability' claim concrete: every agent input, output and verdict is
    recoverable after the fact, not just rendered once in the UI. Never lets
    a disk/write problem break the actual pipeline run -- telemetry failing
    silently degrades to trace_path=None rather than crashing the request."""
    trace["run_id"] = uuid.uuid4().hex[:12]
    trace["completed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        os.makedirs(TRACE_DIR, exist_ok=True)
        path = os.path.join(TRACE_DIR, f"{trace['run_id']}.json")
        with open(path, "w") as f:
            json.dump(trace, f, indent=2, default=str)
        trace["trace_path"] = path
    except OSError:
        trace["trace_path"] = None
    return trace


def extract_code(text: str) -> str:
    match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def execute_pipeline(raw_user_script: str, on_status=None, client=None) -> dict:
    """Public entry point. Each invocation gets its own temp directory so
    concurrent runs (e.g. two Streamlit users) can never read or overwrite
    each other's generated scripts. `client` is accepted here so tests can
    inject a fake LLM client instead of monkeypatching the module global."""
    run_dir = tempfile.mkdtemp(prefix="optistack_run_")
    try:
        return _execute_pipeline_in(run_dir, raw_user_script, on_status, client)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _execute_pipeline_in(run_dir: str, raw_user_script: str, on_status=None, client=None) -> dict:
    """Coordinator entry point. Returns a structured dict containing every
    intermediate artifact, so both the CLI and the Streamlit UI can render
    the full agent trace (this also gives you the 'observability' course
    concept almost for free)."""

    def status(msg):
        if on_status:
            on_status(msg)
        else:
            print(msg)

    trace = {"steps": []}

    status("[Security Scanner] Statically scanning user-submitted code...")
    user_scan = scan_code(raw_user_script)
    if not user_scan.safe:
        status(f"[Security Scanner] REJECTED user input: {'; '.join(user_scan.violations)}")
        trace["verified"] = False
        trace["rejected_at_input"] = True
        trace["security_violations"] = user_scan.violations
        trace["scale_sweep"] = []
        return _persist_trace(trace)

    if client is None:
        client = GeminiClient()

    original_path = os.path.join(run_dir, "original.py")
    optimized_path = os.path.join(run_dir, "optimized.py")

    status("[Analyst] Diagnosing bottlenecks...")
    analyst_output = client.call(ANALYST_ROLE, f"Target Script:\n{raw_user_script}", on_status=status)
    trace["steps"].append({"agent": "analyst", "output": analyst_output})

    status("[Normalizer] Extracting input size into a SCALE variable for fair stress-testing...")
    normalizer_output = client.call(NORMALIZER_ROLE, raw_user_script, on_status=status)
    normalized_original = extract_code(normalizer_output)

    normalizer_scan = scan_code(normalized_original)
    if not normalizer_scan.safe:
        status("[Security Scanner] Normalizer output failed scan; falling back to raw script for the sweep base.")
        normalized_original = raw_user_script

    trace["steps"].append({"agent": "normalizer", "output": normalizer_output, "code": normalized_original})

    scale_value = extract_scale_value(normalized_original)
    original_runnable = inject_scale(normalized_original, scale_value)

    with open(original_path, "w") as f:
        f.write(original_runnable)

    status("[Benchmarker] Profiling original script...")
    original_result = run_benchmark(original_path)
    trace["original_benchmark"] = dataclasses.asdict(original_result)

    optimizer_role = OPTIMIZER_ROLE
    optimizer_context = f"Original Code:\n{raw_user_script}\n\nAnalyst Report:\n{analyst_output}"
    verified = False
    optimized_code = None

    for attempt in range(1, MAX_OPTIMIZATION_ATTEMPTS + 1):
        status(f"[Optimizer] Attempt {attempt}/{MAX_OPTIMIZATION_ATTEMPTS}: rewriting code...")
        optimizer_output = client.call(optimizer_role, optimizer_context, on_status=status)
        optimized_code = extract_code(optimizer_output)

        status("[Security Scanner] Statically scanning generated code before execution...")
        scan = scan_code(optimized_code)
        if not scan.safe:
            status(f"[Security Scanner] REJECTED: {'; '.join(scan.violations)}")
            trace["steps"].append({
                "agent": "optimizer",
                "attempt": attempt,
                "code": optimized_code,
                "security_scan": {"safe": False, "violations": scan.violations},
                "verification": {"passed": False, "reason": "Rejected by security scanner before execution."},
            })
            optimizer_role = OPTIMIZER_RETRY_ROLE
            optimizer_context = (
                f"Original Code:\n{raw_user_script}\n\n"
                f"Your last attempt:\n{optimized_code}\n\n"
                f"Security scanner rejected this code before it was even run, for these reasons:\n"
                f"{chr(10).join(scan.violations)}\n"
                f"Rewrite using only safe, standard computational constructs -- no file, "
                f"process, network, or system-level operations are permitted."
            )
            continue

        with open(optimized_path, "w") as f:
            f.write(inject_scale(optimized_code, scale_value))

        status("[Benchmarker] Profiling optimized script...")
        optimized_result = run_benchmark(optimized_path)

        status("[Verifier] Checking output equivalence against original...")
        verification = verify_equivalence(original_result, optimized_result)

        trace["steps"].append({
            "agent": "optimizer",
            "attempt": attempt,
            "code": optimized_code,
            "security_scan": {"safe": True, "violations": []},
            "benchmark": dataclasses.asdict(optimized_result),
            "verification": verification,
        })

        if verification["passed"]:
            verified = True
            break

        status(f"[Verifier] FAILED: {verification['reason'][:200]}... retrying with feedback.")
        optimizer_role = OPTIMIZER_RETRY_ROLE
        optimizer_context = (
            f"Original Code:\n{raw_user_script}\n\n"
            f"Your last attempt:\n{optimized_code}\n\n"
            f"Verifier failure reason:\n{verification['reason']}"
        )

    trace["verified"] = verified
    trace["final_optimized_code"] = optimized_code if verified else None
    trace["original_benchmark_summary"] = dataclasses.asdict(original_result)
    trace["optimized_benchmark_summary"] = trace["steps"][-1].get("benchmark", {})
    trace["all_attempts_security_rejected"] = bool(trace["steps"]) and all(
        not step.get("security_scan", {}).get("safe", True) for step in trace["steps"] if step["agent"] == "optimizer"
    )

    if not verified:
        status("[Coordinator] Could not produce a verified-correct optimization within attempt budget.")
        trace["scale_sweep"] = []
        return _persist_trace(trace)

    status("[Stress-Tester] Running scale sweep across synthetic input sizes...")
    trace["scale_sweep"] = run_scale_sweep(normalized_original, optimized_code, run_dir, status)

    return _persist_trace(trace)


if __name__ == "__main__":
    test_script = """
def process_data(limit):
    data_list = list(range(limit))
    matches = 0
    for x in data_list:
        for y in data_list:
            if x + y == limit - 1:
                matches += 1
    return matches
print(f"Matches: {process_data(1000)}")
"""
    result = execute_pipeline(test_script)
    print("\n=== FINAL RESULT ===")
    print(f"Verified: {result['verified']}")
    if result["verified"]:
        print(f"Original:  {result['original_benchmark_summary']}")
        print(f"Optimized: {result['optimized_benchmark_summary']}")