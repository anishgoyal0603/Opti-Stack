"""
Synthetic stress-test data generator.

The original pitch claimed the system "injects thousands of rows of
synthetic data to stress-test" submitted code. Nothing in the original
implementation did this -- it only ran the benchmark once, against
whatever single example was in the textbox.

This module makes that claim true. It rewrites the user's script to run
at multiple input scales (e.g. 100, 1000, 10000) and returns a comparison
table, so the demo can show the original algorithm's runtime exploding
quadratically while the optimized version stays roughly linear -- this is
the single most convincing visual for the "wow factor" judges call out.

Approach: rather than trying to magically guess the user's function
signature, we ask the user's script to expose one integer "scale" via a
simple, explicit convention: a top-level variable named SCALE. We inject
different values of SCALE before execution. This is simple, predictable,
and avoids fragile AST surgery on arbitrary user code.
"""

import re
from dataclasses import dataclass
from typing import List


DEFAULT_SCALES = [100, 2_000]


@dataclass
class ScaledVariant:
    scale: int
    code: str


def inject_scale(script: str, scale: int) -> str:
    """Injects or overrides a top-level SCALE = <int> assignment at the
    top of the script. If the LLM-generated code already references
    SCALE, this lets us drive it. If it doesn't, this is a no-op the
    benchmark simply ignores (the script runs once at whatever the
    LLM hardcoded -- still useful, just not scale-swept)."""
    injected = f"SCALE = {scale}\n"
    # Strip any existing top-level SCALE assignment to avoid duplicate
    # definitions confusing readers of the generated sandbox file.
    cleaned = re.sub(r"^SCALE\s*=\s*\d+\s*$", "", script, flags=re.MULTILINE)
    return injected + cleaned


def build_scaled_variants(script: str, scales: List[int] = None) -> List[ScaledVariant]:
    scales = scales or DEFAULT_SCALES
    return [ScaledVariant(scale=s, code=inject_scale(script, s)) for s in scales]


SCALE_AWARE_OPTIMIZER_SUFFIX = """
IMPORTANT: Your rewritten code MUST read a top-level integer variable
named SCALE (it will already be defined above your code when executed)
and use it as the primary input size, instead of hardcoding a fixed
number. This allows the benchmarking harness to stress-test your
solution at multiple scales automatically.
"""
