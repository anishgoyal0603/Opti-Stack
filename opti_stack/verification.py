"""
Deterministic correctness verification: compares the original script's
stdout against a candidate optimized script's stdout. No LLM call here on
purpose -- correctness should not be judged by the same kind of model
that produced the code being judged.
"""

from .benchmarking import AgentResult


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
