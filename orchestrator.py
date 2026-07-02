"""
Opti-Stack orchestrator.

This replaces the old "two sequential prompts" pipeline with an actual
multi-agent system:

  Coordinator (this module)
    -> Analyst agent      : diagnoses complexity bottlenecks
    -> Optimizer agent     : proposes a rewrite
    -> Benchmarker skill    : measures speed/memory (existing runner.py)
    -> Verifier agent      : checks output equivalence (NEW)
    -> Coordinator decides : accept, or send back to Optimizer with the
                              verifier's failure reason (retry loop, bounded)

The key difference from the old code: the Coordinator makes a branching
decision based on a structured result from the Verifier, instead of
always running the same three steps in a fixed order regardless of
outcome. That branching is what makes this an agent system rather than
a script with two LLM calls in it.
"""

import os
import re
import sys
import json
import subprocess
import dataclasses
from typing import Optional

from google import genai
from google.genai import errors

from app.synthetic_data import build_scaled_variants, DEFAULT_SCALES, SCALE_AWARE_OPTIMIZER_SUFFIX
from app.security_scanner import scan_code

MAX_OPTIMIZATION_ATTEMPTS = 3
RUNNER_PATH = os.path.join(
    os.path.dirname(__file__), ".agents", "skills", "code-benchmarker", "scripts", "runner.py"
)

FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
]

ANALYST_ROLE = """You are a Performance Engineering Agent.
Analyze the input code for algorithmic bottlenecks. Identify time complexity O(N).
Provide a sharp, factual breakdown detailing exactly where the loops degrade."""

NORMALIZER_ROLE = """You are a refactoring assistant. Your ONLY job is to take the
given script and rewrite it so its main input-size parameter is read from a
top-level variable named SCALE, which you must define at the very top of the
file (e.g. SCALE = 1000) using the same value the script currently hardcodes.
Do NOT change any algorithm, logic, or behavior -- this is a mechanical
extraction, not an optimization. Return strictly the rewritten script in a
single ```python code block."""

OPTIMIZER_ROLE = (
    """You are a Principal Software Engineer.
Rewrite the code using optimal algorithms (e.g., hash mapping or math properties).
The rewrite MUST preserve identical observable behavior: same stdout output for
the same input, same function names and signature if the script is called by name.
Return strictly the optimized Python code wrapped cleanly in a single ```python code block."""
    + SCALE_AWARE_OPTIMIZER_SUFFIX
)

OPTIMIZER_RETRY_ROLE = (
    """You are a Principal Software Engineer.
Your previous optimization attempt FAILED correctness verification: its output
did not match the original script's output. Fix this while keeping the
performance improvement. Return strictly the corrected code in a single
```python code block."""
    + SCALE_AWARE_OPTIMIZER_SUFFIX
)


@dataclasses.dataclass
class AgentResult:
    status: str  # SUCCESS | FAILURE | TIMEOUT | CRITICAL_ERROR
    duration_ms: Optional[float] = None
    peak_memory_kb: Optional[float] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None


class GeminiClient:
    """Thin wrapper so both the CLI and the Streamlit UI share one
    implementation of model fallback/retry, instead of two drifting copies."""

    def __init__(self):
        self.client = genai.Client()

    def call(self, role_prompt: str, content: str, on_status=None) -> str:
        last_error = None
        for model_name in FALLBACK_MODELS:
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=[role_prompt, content],
                )
                return response.text
            except errors.APIError as e:
                last_error = e
                if on_status:
                    on_status(f"Model {model_name} unavailable ({e.code}). Trying next fallback...")
                continue
        raise RuntimeError(
            f"All fallback models exhausted. Last error: {last_error}"
        )


def extract_code(text: str) -> str:
    match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def run_benchmark(script_path: str) -> AgentResult:
    """Calls the code-benchmarker skill as a subprocess and parses its
    JSON output into a structured result."""
    try:
        proc = subprocess.run(
            [sys.executable, RUNNER_PATH, script_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(proc.stdout.strip())
        return AgentResult(**data)
    except Exception as e:
        return AgentResult(status="CRITICAL_ERROR", stderr=str(e))


def _truncate(text: str, limit: int = 2000) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


def verify_equivalence(original: AgentResult, optimized: AgentResult) -> dict:
    """The Verifier agent's job, expressed as deterministic logic rather
    than an LLM call: optimization is only valid if it ran successfully
    AND produced the same stdout as the original. This is the check that
    was completely missing from the original implementation -- without
    it, a faster-but-wrong rewrite would have been silently accepted."""
    if optimized.status != "SUCCESS":
        return {"passed": False, "reason": f"Optimized script did not run successfully: {optimized.status}. {_truncate(optimized.stderr)}"}
    if original.status != "SUCCESS":
        return {"passed": False, "reason": "Original script itself failed to run; cannot verify against it."}
    if (original.stdout or "").strip() != (optimized.stdout or "").strip():
        return {
            "passed": False,
            "reason": (
                f"Output mismatch.\nOriginal stdout:\n{_truncate(original.stdout)}\n"
                f"Optimized stdout:\n{_truncate(optimized.stdout)}"
            ),
        }
    return {"passed": True, "reason": "Outputs match."}


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
    sandbox_dir = os.path.join(os.path.dirname(__file__), "sandbox")
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
    verification = None

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


def run_scale_sweep(original_script: str, optimized_script: str, sandbox_dir: str, status) -> list:
    """Runs both the original and optimized scripts at multiple synthetic
    input sizes and returns a list of {scale, original_ms, optimized_ms}
    rows. This is what turns a single-point benchmark into an actual
    stress test, and produces the data for the 'original explodes,
    optimized stays flat' chart in the demo."""
    rows = []
    original_variants = build_scaled_variants(original_script, DEFAULT_SCALES)
    optimized_variants = build_scaled_variants(optimized_script, DEFAULT_SCALES)

    for orig_variant, opt_variant in zip(original_variants, optimized_variants):
        orig_path = os.path.join(sandbox_dir, f"original_scale_{orig_variant.scale}.py")
        opt_path = os.path.join(sandbox_dir, f"optimized_scale_{opt_variant.scale}.py")

        with open(orig_path, "w") as f:
            f.write(orig_variant.code)
        with open(opt_path, "w") as f:
            f.write(opt_variant.code)

        status(f"[Stress-Tester] Scale {orig_variant.scale}: benchmarking both variants...")
        orig_result = run_benchmark(orig_path)
        opt_result = run_benchmark(opt_path)

        rows.append({
            "scale": orig_variant.scale,
            "original_ms": orig_result.duration_ms,
            "original_status": orig_result.status,
            "optimized_ms": opt_result.duration_ms,
            "optimized_status": opt_result.status,
        })

    return rows


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
