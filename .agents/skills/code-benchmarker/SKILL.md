---
name: code-benchmarker
description: Executes a target Python script within the sandbox folder to capture precise millisecond timing, peak memory, and stdout, returned as structured JSON for downstream agent consumption.
---

# Code Benchmarker Skill

## Objective
Provides performance instrumentation capability to the agent ecosystem,
and exposes the executed script's stdout so a downstream Verifier agent
can check output equivalence between an original and an optimized script.

## Operational Constraints
- Only execute target scripts located strictly inside the local `sandbox/` directory.
- Hard timeout capped at 10 seconds to defend against accidental infinite loops.
- Output is a single JSON object on stdout:
  `{"status", "duration_ms", "peak_memory_kb", "stdout", "stderr"}`
  where `status` is one of `SUCCESS`, `FAILURE`, `TIMEOUT`, `CRITICAL_ERROR`.
- Process exit code is `0` on `SUCCESS`, non-zero otherwise, so callers can
  branch on exit code alone if they don't need the full payload.
