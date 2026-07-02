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
"""

import os
import re
import dataclasses

from dotenv import load_dotenv

from .llm_client import GeminiClient
from .prompts import ANALYST_ROLE, NORMALIZER_ROLE, OPTIMIZER_ROLE, OPTIMIZER_RETRY_ROLE
from .benchmarking import run_benchmark, run_scale_sweep, _REPO_ROOT
from .verification import verify_equivalence
from .security_scanner import scan_code

# Load .env from the repo root (one directory up from this package) so
# GEMINI_API_KEY is available as soon as this module is imported --
# no need to manually export it in the shell every session.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

MAX_OPTIMIZATION_ATTEMPTS = 3


def extract_code(text: str) -> str:
    match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def execute_pipeline(raw_user_script: str, on_status=None) -> dict:
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
        return trace

    client = GeminiClient()
    sandbox_dir = os.path.join(_REPO_ROOT, "sandbox")
    os.makedirs(sandbox_dir, exist_ok=True)

    original_path = os.path.join(sandbox_dir, "original.py")
    optimized_path = os.path.join(sandbox_dir, "optimized.py")

    with open(original_path, "w") as f:
        f.write(raw_user_script)

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
            f.write(optimized_code)

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
    # If every attempt was rejected by the security scanner (no attempt ever
    # reached the verifier), surface that distinction explicitly so the UI
    # can tell "logic was wrong" apart from "code was unsafe to even run".
    trace["all_attempts_security_rejected"] = bool(trace["steps"]) and all(
        not step.get("security_scan", {}).get("safe", True) for step in trace["steps"] if step["agent"] == "optimizer"
    )

    if not verified:
        status("[Coordinator] Could not produce a verified-correct optimization within attempt budget.")
        trace["scale_sweep"] = []
        return trace

    status("[Stress-Tester] Running scale sweep across synthetic input sizes...")
    trace["scale_sweep"] = run_scale_sweep(normalized_original, optimized_code, sandbox_dir, status)

    return trace


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
