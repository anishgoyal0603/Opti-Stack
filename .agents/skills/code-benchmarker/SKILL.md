---
name: code-benchmarker
description: Executes a target Python script in an isolated temporary directory to capture precise millisecond timing, peak memory, and stdout, returned as structured JSON for downstream agent consumption.
---

# Code Benchmarker Skill

## Objective
Provides performance instrumentation capability to the agent ecosystem,
and exposes the executed script's stdout so a downstream Verifier agent
can check output equivalence between an original and an optimized script.

## Operational Constraints
- Target scripts run inside a per-invocation `tempfile.mkdtemp()` directory,
  not a shared fixed folder -- this ensures two concurrent pipeline runs
  (e.g. two users on a deployed instance) can never read or overwrite each
  other's generated code.
- Hard timeout capped at 10 seconds per individual run, applied 5 times
  (median reported) plus 3 baseline-measurement runs, to defend against
  accidental infinite loops while damping measurement noise.
- Output is a single JSON object on stdout:
  `{"status", "duration_ms", "peak_memory_kb", "stdout", "stderr", "runs", "baseline_ms"}`
  where `status` is one of `SUCCESS`, `FAILURE`, `TIMEOUT`, `CRITICAL_ERROR`,
  `runs` is how many timed samples were taken, and `baseline_ms` is the
  measured interpreter-startup cost already subtracted from `duration_ms`.
- Process exit code is `0` on `SUCCESS`, non-zero otherwise, so callers can
  branch on exit code alone if they don't need the full payload.