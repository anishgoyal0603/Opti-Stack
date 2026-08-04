---
name: code-benchmarker
description: Executes a target Python script in an isolated temporary directory under hard memory, CPU, output and wall-clock limits, capturing millisecond timing, peak memory, and stdout as structured JSON for downstream agent consumption.
---

# Code Benchmarker Skill

## Objective
Provides performance instrumentation capability to the agent ecosystem,
and exposes the executed script's stdout so a downstream Verifier agent
can check output equivalence between an original and an optimized script.

## Operational Constraints

### Isolation
- Target scripts run inside a per-invocation `tempfile.mkdtemp()` directory,
  not a shared fixed folder -- this ensures two concurrent pipeline runs
  (e.g. two users on a deployed instance) can never read or overwrite each
  other's generated code.

### Resource limits (enforced inside the child process)
The child clamps its own limits before executing any user code, so a
submitted script that passes the static scan still cannot exhaust the host:

| Limit | Value | Stops |
|---|---|---|
| `RLIMIT_AS` | 512 MB | allocation bombs (`[0]*10**9`, `bytearray(10**9)`) |
| `RLIMIT_CPU` | 5 s | compute bombs (tight loops, `7**7**7`) |
| `RLIMIT_FSIZE` | 1 MB | runaway file writes |

`resource` is POSIX-only; on Windows the limits are skipped (local
development only — the deployed Linux host is where they apply).

### Time and output limits (enforced in the parent)
- Wall-clock timeout of 10 seconds per individual run.
- A **total sampling budget of 15 seconds** across all timed runs combined.
  Per-run limits alone do not bound total cost: a sleeping script burns no
  CPU (so `RLIMIT_CPU` never fires) and can stay under the per-run timeout
  while still repeating `DEFAULT_RUNS` times. The budget stops sampling early
  and reports the median of the samples actually collected.
- Captured stdout/stderr is capped at 1 MB via a self-draining reader, so a
  print-flood cannot balloon the parent process's memory. The pipe keeps
  being drained after the cap so the child never blocks.

### Output contract
- A single JSON object on stdout:
  `{"status", "duration_ms", "peak_memory_kb", "stdout", "stderr", "runs", "baseline_ms"}`
  where `status` is one of `SUCCESS`, `FAILURE`, `TIMEOUT`, `CRITICAL_ERROR`,
  `runs` is how many timed samples were **actually taken** (fewer than
  `DEFAULT_RUNS` when the sampling budget truncated the loop), and
  `baseline_ms` is the measured interpreter-startup cost already subtracted
  from `duration_ms`.
- Process exit code is `0` on `SUCCESS`, non-zero otherwise.

### What this is not
Process-level isolation with resource limits, not a container or VM. It makes
the easy denial-of-service attacks ineffective; it does not make executing
untrusted code safe. See the README's "Known limitations".
