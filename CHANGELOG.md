# Changelog

## [1.0.0] — 2026-08-01

### Fixed
- Peak memory was measured with tracemalloc in the parent process, which is
  blind to the target subprocess; a 160 MB allocation and `print(0)` reported
  identical figures. Now measured inside the child.
- `duration_ms` included ~12 ms of interpreter startup, which dominated any
  script faster than ~100 ms. Startup is now measured and subtracted, and the
  median of five runs is reported.
- All runs wrote to fixed paths under `sandbox/`, so concurrent users
  overwrote each other's generated scripts.
- A security scanner test imported `orchestrator` rather than
  `opti_stack.orchestrator` and failed on a clean clone.

### Added
- Security denylist extended to cover network (`urllib`, `requests`, `httpx`),
  dynamic import (`importlib`, `builtins`) and filesystem (`pathlib`,
  `tempfile`) escape vectors, with a regression suite of bypass payloads.
- Exponential backoff with jitter on transient API errors; terminal errors now
  fail fast instead of walking the fallback chain.
- Structured JSON trace persisted per run, downloadable from the UI.
- CI on Python 3.11 and 3.12, with a dependency-free unit-test job.
- Coverage reporting with a 70% floor.