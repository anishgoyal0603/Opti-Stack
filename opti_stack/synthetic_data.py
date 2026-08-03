"""
Synthetic stress-test data generator.
"""

import re
from dataclasses import dataclass
from typing import List


# Reduced from [100, 1_000, 5_000] to shrink the amount of work a single
# public-demo click can trigger. The scale sweep runs two script variants at
# every scale, each spawning several benchmark subprocesses, so trimming one
# scale meaningfully cuts the per-request subprocess fan-out (part of the
# rate-limiting / DoS-surface hardening). Override without a code change via
# OPTISTACK_SCALES="100,1000,5000" if you want the wider sweep locally.
import os as _os

def _default_scales() -> List[int]:
    raw = _os.environ.get("OPTISTACK_SCALES")
    if raw:
        try:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            pass
    return [100, 1_000]

DEFAULT_SCALES = _default_scales()


@dataclass
class ScaledVariant:
    scale: int
    code: str


def inject_scale(script: str, scale: int) -> str:
    injected = f"SCALE = {scale}\n"
    cleaned = re.sub(r"^SCALE\s*=\s*\d+\s*$", "", script, flags=re.MULTILINE)
    return injected + cleaned


def extract_scale_value(script: str, default: int = 1000) -> int:
    match = re.search(r"^SCALE\s*=\s*(\d+)\s*$", script, flags=re.MULTILINE)
    return int(match.group(1)) if match else default


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
