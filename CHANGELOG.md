# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2026-08-01

### Fixed
- Peak memory was measured with `tracemalloc` started in the parent process
  while the target script ran in a child subprocess. `tracemalloc` only
  traces the process that started it, so it was blind to the child entirely —
  a 160 MB allocation and a bare `print(0)` reported identical figures.
  Memory is now measured inside the child process itself.
- `duration_ms` was a single wall-clock reading that included roughly 12 ms
  of CPython interpreter startup, added to both sides of every comparison and
  quietly shrinking every reported speedup. Startup cost is now measured
  separately and subtracted, and the median of five runs is reported to damp
  scheduler noise.
- All pipeline runs wrote to fixed paths under a shared `sandbox/` directory,
  so two concurrent users (e.g. on a deployed public app) could overwrite
  each other's generated code mid-run. Each run now gets its own
  `tempfile.mkdtemp()` directory, cleaned up automatically afterward.
- A security-scanner test imported `orchestrator` instead of
  `opti_stack.orchestrator` and failed on every clean clone before a single
  line of application code ever ran.
- The Gemini SDK was imported at module scope in `llm_client.py`, so simply
  importing the orchestrator — and therefore running any unit test that
  touched it — required the SDK installed, even for tests that never call a
  model. The import is now deferred to the point of actual use.
- `st.secrets.get(...)` was called directly in the Streamlit UI. Real
  Streamlit raises `StreamlitSecretNotFoundError` from that call the instant
  no `secrets.toml` file exists anywhere — it does not behave like a safe
  dict lookup with a default — which crashed the app on first load on any
  fresh clone. Wrapped in a helper that catches this and falls back cleanly.
- `pyproject.toml` failed to build entirely with `Multiple top-level packages
  discovered in a flat-layout: ['ui', 'opti_stack']`, since both directories
  sit as siblings at the repo root. Setuptools' package discovery is now
  told explicitly which package is installable.
- `requirements.txt` and `pyproject.toml` each pinned `google-genai<1.0`
  independently, silently resolving to SDK version 0.8.0 on every fresh
  install — including Streamlit Cloud's own build step — which predates
  support for Google's newer `AQ.`-format API keys and produced a confusing
  "API key must be set" error instead of naming the real cause. Both files
  now allow `>=1.0,<3.0`.

### Added
- Security denylist extended to cover network (`urllib`, `requests`,
  `httpx`), dynamic-import (`importlib`, `builtins`), and filesystem
  (`pathlib`, `tempfile`) escape vectors, verified against a regression
  suite of known bypass payloads.
- Exponential backoff with jitter on transient LLM API errors (429, 5xx);
  terminal errors (bad key, permissions) now fail fast with a distinct,
  actionable message instead of walking the entire model-fallback chain.
- A structured JSON trace is persisted to disk per run and downloadable
  from the UI, making "observability" a concrete inspectable artifact.
- CI on Python 3.11 and 3.12, plus a separate job that runs the
  dependency-free unit tests with the Gemini SDK entirely uninstalled, to
  catch any accidental reintroduction of a module-scope SDK import.
- Coverage reporting with a 70% floor enforced on every `pytest` run.
- A sidebar API-key input in the Streamlit UI, so a deployed public app
  lets visitors bring their own key rather than draining the deployer's.
- A live scale-sweep demo GIF and design notes documenting three
  non-obvious architectural decisions (deterministic verification, the
  double security scan, subprocess-isolated benchmarking).

### Changed
- README restructured to lead with a demo GIF and a one-line pitch instead
  of three paragraphs of prose before any evidence the tool works.
- `pyproject.toml`'s deprecated `project.license` table migrated to the
  current SPDX string form.