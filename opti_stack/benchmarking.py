"""
Runs the code-benchmarker skill as a subprocess and parses its results.
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
    status: str
    duration_ms: Optional[float] = None
    peak_memory_kb: Optional[float] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    runs: Optional[int] = None
    baseline_ms: Optional[float] = None


def run_benchmark(script_path: str) -> AgentResult:
    try:
        proc = subprocess.run(
            [sys.executable, RUNNER_PATH, script_path],
            capture_output=True,
            text=True,
            timeout=90,
        )
        data = json.loads(proc.stdout.strip())
        return AgentResult(**data)
    except Exception as e:
        return AgentResult(status="CRITICAL_ERROR", stderr=str(e))


def run_scale_sweep(original_script: str, optimized_script: str, sandbox_dir: str,
                    status, should_stop=None) -> list:
    rows = []
    original_variants = build_scaled_variants(original_script, DEFAULT_SCALES)
    optimized_variants = build_scaled_variants(optimized_script, DEFAULT_SCALES)

    for orig_variant, opt_variant in zip(original_variants, optimized_variants):
        # The sweep is where most of a run's wall-clock goes: two benchmark
        # calls per scale. Checking the caller's time budget only *before* the
        # sweep is useless, because at that point the sweep hasn't spent
        # anything yet. Check between scales instead, so a run that is already
        # over budget returns the scales it managed rather than all of them.
        if should_stop is not None and should_stop():
            status("[Stress-Tester] Time budget spent; returning partial sweep.")
            break

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
