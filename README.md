# ⚡ Opti-Stack: Autonomous Algorithmic Auditor
[![CI](https://github.com/anishgoyal0603/Opti-Stack/actions/workflows/ci.yml/badge.svg)](https://github.com/anishgoyal0603/Opti-Stack/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A multi-agent system that rewrites slow Python — then **proves the rewrite is
correct before accepting it.**

![Opti-Stack finding an O(N squared) bottleneck and verifying the O(N) rewrite](docs/demo.gif)

**[▶ Live demo](https://opti-stack.streamlit.app)** · [Design notes](docs/design.md)

> The hosted demo sleeps after a period of inactivity — the first load may take
> ~30 seconds to wake. Bring your own free Gemini API key (sidebar); it is used
> only for your session. The public demo runs curated example scripts rather
> than accepting arbitrary pasted code — see "Known limitations" below.

A multi-agent system that acts as an automated Senior Staff Engineer:
feed it a slow or naive Python script, and it diagnoses the algorithmic
bottleneck, rewrites the code, **verifies the rewrite is behaviorally
identical to the original**, and benchmarks both versions across multiple
synthetic input scales.

## Contents

- [The problem this solves](#the-problem-this-solves)
- [Features](#features)
- [Architecture](#architecture)
- [Course concepts demonstrated](#course-concepts-demonstrated)
- [Project structure](#project-structure)
- [Setup](#setup-free--local)
- [Run](#run)
- [Test](#test)
- [Known limitations](#known-limitations-stated-honestly-not-hidden)
- [License](#license)

## Features

- **Multi-agent pipeline** — Analyst, Normalizer, Optimizer, and a deterministic Verifier, each with a single responsibility
- **Correctness-first** — rejects a faster rewrite unless it produces byte-identical stdout to the original, with a bounded retry loop on failure
- **Static security gate** — AST-based scan runs on both the user's input and every LLM-generated rewrite, before any code executes
- **Layered sandboxing** — the static gate is backed by per-run OS resource limits (memory, CPU, file size), wall-clock and sampling budgets, and a capped output reader, so a submission that passes the scan still cannot exhaust the host
- **Examples-only public deployment** — the hosted demo runs a fixed, vetted set of example scripts rather than accepting arbitrary pasted code, removing the free-text code-execution surface entirely; full arbitrary-code mode is available for local/cloned use
- **Scale-sweep benchmarking** — both versions are re-run across synthetic input sizes to show how complexity actually diverges, not just a single-point timing
- **Structured observability** — every agent step, security verdict, and benchmark result is captured in a JSON trace, downloadable from the UI
- **Fault-tolerant LLM calls** — exponential backoff on transient errors, a configurable model-fallback chain, and a distinct error path for unrecoverable auth failures

**Tech stack:** Python 3.11+ · Google Gemini (`google-genai`) · Streamlit · pytest + pytest-cov · GitHub Actions

## The problem this solves

Engineers regularly write or inherit correct-but-slow code under time
pressure — nested loops that should be hash lookups, O(N²) scans that
should be O(N log N). Manually profiling, diagnosing, and safely
rewriting that code takes real senior-engineer judgment. Opti-Stack
automates the full loop, with a critical safety property most "AI code
optimizer" demos skip: **it refuses to accept a faster rewrite unless it
proves the rewrite produces identical output to the original.**

## Architecture

```
  user script
      │
      ▼
┌───────────────────────────┐
│ Security Scanner          │   rejects unsafe input before any agent runs
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Analyst  (LLM)            │   diagnoses the algorithmic bottleneck
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Normalizer  (LLM)         │   extracts a SCALE variable for fair scale-sweeps
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Benchmarker skill         │   profiles the ORIGINAL script (sandboxed subprocess)
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Optimizer  (LLM)          │   rewrites the code -- up to 3 attempts, see note below
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Security Scanner          │   on the generated code this time
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Benchmarker skill         │   profiles the OPTIMIZED script
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Verifier  (deterministic) │   checks stdout match against the original
└───────────────────────────┘
              │
              ▼
┌───────────────────────────┐
│ Stress-Tester             │   scale sweep across synthetic input sizes
└───────────────────────────┘
```

The diagram above shows the happy path top to bottom, but the
**Coordinator** (`opti_stack/orchestrator.py`) doesn't run a fixed script — it
branches: the four steps from Optimizer through Verifier form a bounded
retry loop (up to 3 attempts). If the Verifier rejects an optimization
attempt, the Coordinator routes the failure reason back to the Optimizer
agent for another try, rather than presenting an unverified rewrite as a
success; if all 3 attempts fail, the Coordinator gives up and surfaces
the failure honestly to the user. Note that the Security Scanner runs
twice per pipeline: once on the user's original input, and again on
every piece of code the Optimizer generates -- a prompt-injected input
could otherwise steer the Optimizer into producing unsafe code, so
scanning only the input isn't enough. The final Stress-Tester step is
skipped when the original script is already slow, since sweeping it would
multiply cost for little demonstrative value. Where "user's original
input" comes from depends on the deployment mode: see "Known limitations"
below for how the public deployment sources it from a fixed, vetted list
rather than free-text submission.

## Course concepts demonstrated

| Concept | Where |
|---|---|
| Multi-agent system | `opti_stack/orchestrator.py` — Analyst, Normalizer, Optimizer, Verifier as distinct roles coordinated by one orchestrator, with branching control flow based on agent output |
| Agent skills | `.agents/skills/code-benchmarker/` — a documented, sandboxed, resource-bounded capability (`SKILL.md` + `runner.py`) callable by the agent system |
| Guardrails / security | `opti_stack/security_scanner.py` — AST-based static scan rejects dangerous imports/calls, object-graph escapes via dunder attributes, and oversized input, before any code executes; applied to both user input and every LLM-generated rewrite. Layered with per-run OS resource limits in `.agents/skills/code-benchmarker/scripts/runner.py`, and with `opti_stack/examples.py` removing the free-text attack surface on the public deployment entirely |
| Correctness verification | The Verifier's deterministic stdout-diff gate (rejects fast-but-wrong rewrites instead of accepting them, with bounded retry) |
| Fault tolerance | `GeminiClient` model-fallback chain handles quota limits, 503s, and deprecated model IDs without crashing the pipeline |
| Observability | Every agent step (input, output, verification verdict) is captured in a structured trace, surfaced in the UI's "Full agent trace" panel |

## Project structure

```
opti-stack-project/
├── .github/workflows/
│   └── ci.yml                         # CI: pytest on 3.11/3.12, plus a no-SDK unit-test job
├── .agents/skills/code-benchmarker/   # the benchmarking skill
│   ├── SKILL.md
│   └── scripts/runner.py
├── opti_stack/                        # core package -- all business logic lives here
│   ├── orchestrator.py                # coordination/branching logic only
│   ├── llm_client.py                  # Gemini API wrapper + retry/model fallback
│   ├── prompts.py                     # all agent role prompts
│   ├── benchmarking.py                # subprocess benchmark runner + scale sweep
│   ├── verification.py                # deterministic correctness checker
│   ├── security_scanner.py            # AST-based static security gate
│   ├── synthetic_data.py              # SCALE-variable injection for stress tests
│   ├── examples.py                    # vetted example scripts for the public deployment
│   └── cli.py                         # CLI entry point
├── ui/
│   └── app.py                         # Streamlit front-end
├── docs/
│   ├── demo.gif                       # scale-sweep demo, shown at the top of this README
│   └── design.md                      # why the Verifier is deterministic, why dunders are blocked, etc.
├── tests/                             # pytest suite
├── pyproject.toml                     # pytest + coverage config (70% floor)
├── requirements.txt
├── env.example                        # copy to .env and add your own Gemini API key
├── .gitignore
├── LICENSE
└── README.md                          # this file
```

Each module in `opti_stack/` has exactly one job: `orchestrator.py` only
decides *what order things happen in and how to branch* -- it doesn't
know how the LLM API works (`llm_client.py`), what to say to it
(`prompts.py`), how to measure performance (`benchmarking.py`), or how to
judge correctness (`verification.py`). This means, for example, swapping
Gemini for another model provider only touches `llm_client.py`.

## Setup (free / local)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp env.example .env                # then paste your free Gemini API key into .env
```

Get a free Gemini API key at [aistudio.google.com](https://aistudio.google.com) — the free tier is sufficient for demo purposes.

`.env` is loaded automatically via `python-dotenv` the moment the app
starts -- no need to manually export the variable into your shell.

### Deployment settings

For a public deployment, set these in your host's environment or secrets panel:

| Variable | Recommended | Why |
|---|---|---|
| `OPTISTACK_ALLOW_ARBITRARY_CODE` | unset (`0`) | Keeps the public deployment locked to the vetted example scripts in `opti_stack/examples.py`, with no free-text code-execution surface. Set to `1` **only** for local/cloned use to unlock pasting your own scripts. Unset, empty, or any other value defaults to the safe examples-only mode. |
| `OPTISTACK_PERSIST_TRACES` | `0` | Traces contain submitted code; `0` keeps them in memory only |
| `OPTISTACK_TRACE_RETENTION` | `50` (default) | Bounds the trace directory if persistence is on |
| `OPTISTACK_SCALES` | unset | Defaults to `100,1000`; widen only for local runs |

Leave the deployer's own `GEMINI_API_KEY` **unset** on a public deploy so
visitors must supply their own key rather than drawing on your quota.

## Run

```bash
# Web UI, examples-only mode (the safe default -- run from the repo root)
python3 -m streamlit run ui/app.py

# Web UI, arbitrary-code mode (local/cloned use only)
OPTISTACK_ALLOW_ARBITRARY_CODE=1 python3 -m streamlit run ui/app.py

# CLI (also run from the repo root, as a module; always accepts arbitrary code,
# since it has no public-internet exposure to begin with)
python -m opti_stack.cli --inline "print('hello world')"
python -m opti_stack.cli path/to/script.py
```

## Test

```bash
pytest tests/ -v
```

## Known limitations (stated honestly, not hidden)

- **The public deployment does not accept arbitrary pasted code at all.**
  Across eight adversarial review rounds, the AST-scanner denylist below was
  patched for eight distinct escape families and each round found a new one
  — a pattern that shows a denylist in front of `exec()` cannot be made
  exhaustive against an arbitrary stranger, not that it was nearly complete.
  Rather than a ninth patch, the public deployment (the default,
  `OPTISTACK_ALLOW_ARBITRARY_CODE` unset) offers only five vetted example
  scripts in `opti_stack/examples.py`, each checked into the repo and
  verified in CI against the scanner and a real execution. There is no
  adversarial input for the scanner to fail against, because there is no
  free-text input at all. Clone the repo and run locally with
  `OPTISTACK_ALLOW_ARBITRARY_CODE=1` to submit your own scripts against the
  full pipeline below — at that point you are running code against your own
  machine, a different and much smaller trust boundary than a public URL.
  The scanner, resource limits and budgets described below still apply in
  that mode, as defense-in-depth for your own machine:
  It blocks process, filesystem, network and dynamic-import escapes by name;
  dunder attribute access outside a small allowlist of protocol methods
  (which is what closes the no-import object-graph escapes such as
  `().__class__.__bases__[0].__subclasses__()`); non-dunder introspection
  attributes and methods that reach the same internals by another route
  (`gi_frame`/`f_globals`/`co_consts` on generators and frames,
  `type(x).mro()` to `object.__subclasses__()`); the `operator` and `types`
  modules, whose `attrgetter`/`methodcaller` fetch attributes by string
  (bypassing the attribute rules) and whose `FunctionType`/`CodeType`
  construct executable objects — blocked both as imports and as attribute
  names, so a re-export through a safe module is caught too; the `string`
  module (`Formatter().get_field` traverses attributes by string); dangerous
  builtins (`eval`, `exec`, `open`, `getattr`, `__import__`, `help`) whether
  called directly *or* passed as bare values such as `map(eval, xs)`; the
  `codecs`/`linecache`/`traceback`/`pydoc` modules (file-read and
  frame-introspection primitives that bypass the `open` block); references to
  `__builtins__`; and dunder traversal smuggled through a `str.format`
  template. It is verified against a regression suite of known bypass
  payloads. It does not defeat a determined adversary — obfuscated bytecode
  tricks and format templates assembled at runtime from concatenated fragments
  are out of scope. (Unicode homoglyph payloads are covered incidentally:
  CPython NFKC-normalises identifiers at parse time, so a homoglyph spelling
  of `__class__` arrives at the scanner already normalised and is caught by
  the dunder rule.) (That last gap is accepted knowingly: format-template
  traversal can *read* attributes but cannot call them, so it is information
  disclosure rather than code execution, and the child runs each script in a
  fresh namespace holding no credentials.)
- The Normalizer agent's SCALE-extraction is itself an LLM call and can
  occasionally fail on unusual code shapes; the scale-sweep chart should
  be read as a best-effort demo enhancement, not a guarantee. The sweep is
  also skipped entirely when the original script already takes over ~2 s,
  because sweeping a slow script multiplies cost for little demonstrative
  value.
- Verification compares stdout only. Scripts that don't print their result,
  or that have side effects beyond stdout (file writes, network calls), are
  not currently verified for those side effects.
- **Isolation is process-level with resource limits, not a container or VM.**
  Each run gets its own temp directory; the child process clamps `RLIMIT_AS`
  (512 MB), `RLIMIT_CPU` (5 s) and `RLIMIT_FSIZE` (1 MB) on itself before
  executing user code; the parent enforces a 10 s per-run wall-clock timeout,
  a 15 s total sampling budget, and a 1 MB cap on captured output. Together
  these make the easy denial-of-service attacks (allocation bombs, compute
  bombs, print floods, sleep-based wall-clock burn) ineffective in arbitrary-
  code mode. They do not make executing untrusted code safe — this is why
  the public deployment does not use arbitrary-code mode at all.
- **Submitted source is capped at 50,000 characters, checked before parsing.**
  `ast.parse()` runs in the shared web process where none of the child limits
  apply, and costs roughly 400 MB of peak memory per MB of source — an
  oversized paste of entirely benign statements could exhaust the instance in
  a single request. Only relevant in arbitrary-code mode, since the examples
  used in the public deployment are fixed, short, checked-in constants.
- **A whole pipeline run is capped at 30 seconds of wall-clock.** Per-call
  limits bound one benchmark, not a run that makes up to six of them. A
  submission tuned to sit just under the sweep threshold (a ~1.9 s sleep)
  passes every per-call limit and measured ~61 s of worker time per click;
  the pipeline budget brings that to ~40 s by stopping retries and truncating
  the sweep. It reduces the ceiling, it does not remove it.
- **Rate limiting is per-session and is not a real control.** The UI limits
  5 runs per 60 s using `st.session_state`, which an incognito window or a
  scripted client resets for free. It stops casual button-mashing and nothing
  more; genuine per-IP limiting needs a reverse proxy in front of the app,
  which is out of scope on free Streamlit Cloud. This matters far less in
  examples-only mode, since there's no free-text execution surface to abuse —
  it mainly bounds LLM-quota usage per session.
- **Do not enable `OPTISTACK_ALLOW_ARBITRARY_CODE=1` on untrusted multi-tenant
  traffic without a container or gVisor boundary underneath it.** The hosted
  demo is a demonstration, not a service, and defaults to examples-only mode
  specifically so this limitation doesn't apply to it.

## License

This project is licensed under the MIT License — see [`LICENSE`](LICENSE) for the full text.
