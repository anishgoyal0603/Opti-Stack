"""
Runs the code-benchmarker skill as a subprocess and parses its results.
Separated from orchestrator.py because "how we measure performance" is a
distinct concern from "what order agents run in and how we branch on
their output".
"""

import os
import sys
import json
import subprocess
import dataclasses
from typing import Optional

from .synthetic_data import build_scaled_variants, DEFAULT_SCALES

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_PATH = os.path.join(
    _REPO_ROOT, ".agents", "skills", "code-benchmarker", "scripts", "runner.py"
)


@dataclasses.dataclass
class AgentResult:
    status: str  # SUCCESS | FAILURE | TIMEOUT | CRITICAL_ERROR
    duration_ms: Optional[float] = None
    peak_memory_kb: Optional[float] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None


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
