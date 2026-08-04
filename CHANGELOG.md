# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.0] — 2026-08-04

### Security
- **Capped source size before parsing.** `ast.parse()` runs in the caller's
  process — on a hosted deployment, the single shared Streamlit process where
  none of the child's resource limits apply. Parsing costs roughly 400 MB of
  peak memory per MB of source, so a ~2.4 MB paste of entirely benign
  statements consumed ~1 GB and ~20 s of CPU and would OOM a typical 1 GB
  instance **in a single request**, which no rate limit can prevent. The
  scanner now rejects input over 50,000 characters with a length check that
  runs before the parse (0.1 ms instead of 21.7 s), and the UI enforces the
  same cap at the text area.
- **Bounded total sampling wall-clock.** Per-run limits did not compose into a
  total: a sleeping script burns zero CPU (so `RLIMIT_CPU` never fires) and
  can stay under the per-run timeout while still repeating `DEFAULT_RUNS`
  times. `time.sleep(9)` returned `SUCCESS` after 46 seconds from one
  benchmark call. A 15-second total budget now truncates sampling (measured:
  18 s, 2 samples), and `runs` reports how many samples were actually taken.
- **Closed format-template dunder traversal.** `"{0.__class__.__bases__[0]}".format(())`
  performs the same object-graph walk as the blocked attribute chain, but the
  dunders live inside a string constant so the AST contains no `Attribute`
  node and the dunder rule never saw them. The scanner now pattern-matches
  replacement fields reaching for a dunder. A template assembled at runtime
  from concatenated fragments still evades this; that gap is accepted and
  documented, since format traversal can read but not call.
- **Skip the scale sweep for already-slow scripts.** The sweep benchmarks two
  variants at every scale and is the largest multiplier of per-click subprocess
  cost — the lever an abuser pulls with a slow-but-legal submission. It is now
  skipped above a 2,000 ms original-script threshold, and the result says so.

### Fixed
- **Ordinary object-oriented code is no longer rejected.** The blanket dunder
  ban introduced in 1.1.0 also refused `super().__init__()`, `__len__`,
  `__enter__` and every other protocol method. A conservative `SAFE_DUNDERS`
  allowlist now permits protocol methods that cannot traverse the object
  graph, while the escape-critical names (`__class__`, `__bases__`, `__mro__`,
  `__subclasses__`, `__globals__`, `__code__`, `__dict__`, `__reduce__`,
  `__getattribute__`) stay blocked.
- The scanner no longer lets `ValueError`/`MemoryError`/`RecursionError` from
  `ast.parse()` escape into the pipeline; deeply nested literals are now a
  clean rejection.

### Added
- Regression tests for every issue above: oversized-input rejection (and that
  it is a length check, not a parse), the sleep-based wall-clock bomb, the
  bytearray and huge-int variants of the allocation bomb, format-template
  payloads, escape-critical dunders, and the OOP false-positive cases.
- `scale_sweep_skipped` / `scale_sweep_skip_reason` in the run trace, surfaced
  in the UI.

### Changed
- README, `SKILL.md` and `docs/design.md` updated to describe the defenses
  that actually exist, including an honest note that per-session rate limiting
  is defeated by a new browser session and is not a real control.

## [1.1.0] — 2026-08-03

### Security
- **Closed no-import sandbox-escape holes in the static scanner.** The scanner
  previously only inspected imports, bare builtin names, and attribute calls
  rooted at a `Name`, so object-graph escapes that use none of those
  (`().__class__.__bases__[0].__subclasses__()` to reach `Popen`) and
  `__builtins__` indirection (`__builtins__['eval'](...)`) passed cleanly and
  were then executed. The scanner now rejects dunder attribute access, any
  reference to `__builtins__`/`__builtin__`, and treats `__builtins__` as a
  denylisted attribute root.
- **Bounded resource use of executed scripts.** Even code that passes the
  scanner could exhaust the host: an allocation bomb (`[0]*10**9`), a tight CPU
  loop under the wall-clock limit, or a stdout print-flood that fills the
  parent's pipe. The benchmark child now sets `RLIMIT_AS` (~512 MB),
  `RLIMIT_CPU` (5 s), and `RLIMIT_FSIZE` (1 MB) on itself before running user
  code, and the parent reads stdout/stderr through a capped, self-draining
  reader (1 MB).
- **Stopped cross-session API-key leakage.** The UI wrote the visitor's key
  into `os.environ`, which is process-global and therefore shared across all
  concurrent sessions on a hosted deployment. The key is now passed explicitly
  into a per-session `GeminiClient(api_key=...)`.

### Added
- Per-session rate limiting in the UI (default 5 runs / 60 s).
- A public-demo disclaimer / lightweight terms-of-use notice in the UI.
- `OPTISTACK_PERSIST_TRACES=0` to disable on-disk trace persistence, and
  `OPTISTACK_TRACE_RETENTION` (default 50) to bound the trace directory; old
  traces are pruned after each run.
- `OPTISTACK_SCALES` env override for the scale-sweep sizes.

### Changed
- Default scale-sweep sizes trimmed from `[100, 1000, 5000]` to `[100, 1000]`
  to reduce per-request subprocess fan-out on the public demo.
- `LICENSE` copyright line: removed the leftover `[ ]` template brackets.

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
