import os
import sys
import time
import json
import streamlit as st

# ui/app.py lives in a sibling folder to opti_stack/, so add the repo root
# to sys.path before importing it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opti_stack.orchestrator import execute_pipeline
from opti_stack.llm_client import GeminiClient, TerminalAPIError
from opti_stack.security_scanner import MAX_SOURCE_CHARS
from opti_stack.examples import EXAMPLES, get_example

st.set_page_config(page_title="Opti-Stack: Autonomous Algorithmic Auditor", layout="wide")

# --- Arbitrary-code mode gate -------------------------------------------
# The security scanner is a denylist over an AST scan. Across repeated
# adversarial review it was patched eight times for distinct escape families
# (frame introspection, operator.attrgetter, bare-value builtins, codecs,
# ...) -- a pattern that demonstrates a denylist in front of exec() cannot be
# made exhaustive against a determined, arbitrary stranger. The fix is not a
# ninth patch: it's removing the free-text execution surface from the public
# deployment entirely.
#
# ALLOW_ARBITRARY_CODE is OFF unless explicitly enabled, so a fresh deploy
# (Streamlit Cloud, a forgotten env var, whatever) defaults to the safe mode.
# Enable it locally with:  OPTISTACK_ALLOW_ARBITRARY_CODE=1 streamlit run ui/app.py
# The full pipeline -- scanner, resource limits, wall-clock budgets -- still
# runs underneath either mode; this only changes where the source code comes
# from (a vetted constant vs. a stranger's browser).
ALLOW_ARBITRARY_CODE = os.environ.get("OPTISTACK_ALLOW_ARBITRARY_CODE", "0") == "1"

st.title("⚡ Opti-Stack: Autonomous Algorithmic Auditor")
st.caption(
    "A multi-agent system (Analyst → Optimizer → Benchmarker → Verifier) "
    "that analyzes, rewrites, and verifies the correctness of optimized Python code."
)

if ALLOW_ARBITRARY_CODE:
    st.info(
        "**Demonstration tool — arbitrary-code mode.** Opti-Stack runs submitted "
        "Python in a sandboxed subprocess (static security scan + memory/CPU "
        "limits + a short timeout) for demo purposes only — this is not a "
        "hardened multi-tenant sandbox. Do not submit malicious code, secrets, "
        "or personal data. Submitted code may be temporarily logged for "
        "debugging. Provided as-is, without warranty.",
        icon="ℹ️",
    )
else:
    st.info(
        "**Public demo — curated examples only.** This deployment runs a fixed "
        "set of vetted example scripts rather than accepting arbitrary pasted "
        "code, so there is no free-text code-execution surface exposed to the "
        "internet. Clone the repo and run locally with "
        "`OPTISTACK_ALLOW_ARBITRARY_CODE=1` to try your own scripts.",
        icon="🔒",
    )

# --- Source selection -----------------------------------------------------
if ALLOW_ARBITRARY_CODE:
    user_code = st.text_area(
        "Paste your unoptimized Python code here:",
        height=200,
        max_chars=MAX_SOURCE_CHARS,
        value=EXAMPLES[0].code,
    )
else:
    labels = [ex.label for ex in EXAMPLES]
    chosen_label = st.selectbox("Pick an example to optimize:", labels)
    chosen = next(ex for ex in EXAMPLES if ex.label == chosen_label)
    st.caption(chosen.description)
    user_code = chosen.code
    st.code(user_code, language="python")

with st.sidebar:
    st.header("Configuration")
    user_key = st.text_input(
        "Gemini API key",
        type="password",
        help="Used only for this browser session and never stored. "
             "Get a free one at aistudio.google.com/app/apikey",
    )
    st.caption(
        "Opti-Stack runs code in a sandboxed subprocess with a static "
        "security scan, memory/CPU limits, and a 10-second timeout. Do not "
        "paste secrets you wouldn't want to type into any web form."
    )
    if not ALLOW_ARBITRARY_CODE:
        st.divider()
        st.caption(
            "Want to run your own code? Clone the repo and start it with "
            "`OPTISTACK_ALLOW_ARBITRARY_CODE=1` locally — see the README."
        )

# Precedence: a key typed into the sidebar (a visitor using the deployed app)
# beats st.secrets (the deployer's own fallback key) beats a plain environment
# variable (local development via .env).

def _safe_secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

api_key = user_key or _safe_secret("GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")

# NOTE: we deliberately do NOT write api_key into os.environ. On a hosted
# multi-session deployment the whole app is one shared OS process, so
# os.environ is global state -- writing one visitor's key there would leak it
# into another visitor's concurrent run. The key is passed explicitly into a
# per-session GeminiClient below instead.


# --- Per-session rate limiting. A single click fans out to several LLM calls
# and many subprocess spawns, so an unthrottled button is a cheap way to burn
# CPU (and any fallback key's quota).
#
# Be clear about what this is worth: st.session_state is per browser session,
# so an incognito window or a scripted client gets a fresh budget. This stops
# casual button-mashing and nothing more. Real per-IP limiting needs a reverse
# proxy in front of the app, which is out of scope on free Streamlit Cloud.
# In examples-only mode this matters much less, since there's no free-text
# execution surface to abuse -- it's kept mainly to bound LLM-quota usage. ---
RUN_WINDOW_SECONDS = 60
MAX_RUNS_PER_WINDOW = 5


def _rate_limited() -> bool:
    now = time.time()
    runs = st.session_state.setdefault("run_times", [])
    runs[:] = [t for t in runs if now - t < RUN_WINDOW_SECONDS]
    if len(runs) >= MAX_RUNS_PER_WINDOW:
        return True
    runs.append(now)
    return False


def _redact(text: str) -> str:
    """Strip the live API key out of any string before it's shown on screen.
    Defense-in-depth: a low-level transport error can echo the request URL,
    which for API-key auth includes the key as a query parameter."""
    if not text or not api_key or len(api_key) < 8:
        return text
    return text.replace(api_key, "[REDACTED]")


if st.button("Audit & Optimize Code", type="primary"):
    if not api_key:
        st.error(
            "Enter a Gemini API key in the sidebar before running "
            "(or set GEMINI_API_KEY in your local .env for development)."
        )
    elif ALLOW_ARBITRARY_CODE and len(user_code) > MAX_SOURCE_CHARS:
        # Belt-and-braces with the text area's max_chars: parsing oversized
        # input happens in this shared process, before any subprocess limit
        # can apply, so it must be refused here as well as in the scanner.
        # Not reachable in examples-only mode, since user_code is a fixed,
        # already-short constant.
        st.error(
            f"Script too long ({len(user_code):,} characters). The limit is "
            f"{MAX_SOURCE_CHARS:,}. Parsing input this large can exhaust the "
            f"server process before the sandbox applies."
        )
    elif not user_code.strip():
        st.warning("Paste some Python code first.")
    elif _rate_limited():
        st.warning(
            f"Rate limit reached ({MAX_RUNS_PER_WINDOW} runs per "
            f"{RUN_WINDOW_SECONDS}s). Please wait a moment and try again."
        )
    else:
        status_box = st.empty()
        status_log = []

        def on_status(msg: str):
            status_log.append(msg)
            status_box.info("\n\n".join(status_log[-6:]))

        with st.spinner("Running multi-agent pipeline..."):
            try:
                # Session-scoped client carrying this session's key only.
                client = GeminiClient(api_key=api_key)
                result = execute_pipeline(user_code, on_status=on_status, client=client)
            except TerminalAPIError as e:
                st.error(
                    "🔑 The Gemini API rejected the request outright (bad key, "
                    "quota, or permissions). Retrying other models will not help.\n\n"
                    f"Details: {_redact(str(e))}"
                )
                st.stop()
            except Exception as e:
                st.error(f"Pipeline failed: {_redact(str(e))}")
                st.stop()

        status_box.empty()

        if result.get("rejected_at_input"):
            # In examples-only mode this should never fire, since every
            # example is pre-verified against the scanner (see
            # opti_stack/examples.py) -- surfaced anyway so a future example
            # that regresses fails loudly instead of silently.
            st.error(
                "🛑 Your submitted code was rejected by the static security scanner "
                "before any agent touched it, for these reasons:"
            )
            for v in result.get("security_violations", []):
                st.write(f"- {v}")
            if ALLOW_ARBITRARY_CODE:
                st.info(
                    "Opti-Stack will not execute code that imports os/subprocess/socket, "
                    "calls eval/exec/open, references __builtins__, or reaches internals "
                    "through dunder attributes (e.g. .__class__) — including dunders "
                    "hidden inside a format template. Ordinary methods like __init__ "
                    "and __len__ are fine."
                )
            st.stop()

        # --- Agent trace (the "observability" course concept) ---
        with st.expander("🔍 Full agent trace", expanded=False):
            for step in result["steps"]:
                st.markdown(f"**Agent: `{step['agent']}`**" + (f" (attempt {step.get('attempt')})" if "attempt" in step else ""))
                st.write(step.get("output") or "")
                if "security_scan" in step:
                    if step["security_scan"]["safe"]:
                        st.write("🛡️ Security scan: passed")
                    else:
                        st.write(f"🛡️ Security scan: REJECTED — {'; '.join(step['security_scan']['violations'])}")
                if "code" in step:
                    st.code(step["code"], language="python")
                if "verification" in step:
                    icon = "✅" if step["verification"]["passed"] else "❌"
                    st.write(f"{icon} Verifier: {step['verification']['reason']}")
                st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("1. Analyst Assessment")
            analyst_step = next((s for s in result["steps"] if s["agent"] == "analyst"), None)
            st.info(analyst_step["output"] if analyst_step else "No analyst output recorded.")

        with col2:
            st.subheader("2. Final Optimized Code")
            if result["verified"]:
                st.success("✅ Verified: output matches the original script.")
                st.code(result["final_optimized_code"], language="python")
            elif result.get("all_attempts_security_rejected"):
                st.error(
                    "🛑 Every optimization attempt was rejected by the security "
                    "scanner before it was ever executed — the model kept proposing "
                    "unsafe code. No benchmark is available because nothing was run."
                )
                st.code(result["steps"][-1]["code"], language="python")
            else:
                st.error(
                    "⚠️ Could not produce a verified-correct optimization within "
                    "the attempt budget. Showing the last attempt below — review "
                    "it manually before trusting it."
                )
                st.code(result["steps"][-1]["code"], language="python")

        st.markdown("---")
        st.subheader("3. Benchmark Comparison")

        orig_b = result.get("original_benchmark_summary") or {}
        opt_b = result.get("optimized_benchmark_summary") or {}

        if not opt_b:
            st.info("No optimized-code benchmark is available for this run (see message above).")
        else:
            b1, b2, b3 = st.columns(3)
            with b1:
                st.metric("Original time (ms)", orig_b.get("duration_ms"))
                st.metric("Optimized time (ms)", opt_b.get("duration_ms"))
            with b2:
                st.metric("Original memory (KB)", orig_b.get("peak_memory_kb"))
                st.metric("Optimized memory (KB)", opt_b.get("peak_memory_kb"))
            with b3:
                if orig_b.get("duration_ms") and opt_b.get("duration_ms"):
                    speedup = orig_b["duration_ms"] / max(opt_b["duration_ms"], 0.001)
                    st.metric("Speedup", f"{speedup:.1f}x")
                st.metric("Correctness verified", "Yes" if result["verified"] else "No")

        sweep = result.get("scale_sweep", [])
        if sweep:
            st.markdown("---")
            st.subheader("4. Stress Test: Performance Across Input Scales")
            st.caption(
                "Both versions are re-run at increasing synthetic input sizes "
                "to show how the original's complexity degrades versus the optimized version."
            )
            sweep_table = [
                {
                    "Scale": row["scale"],
                    "Original (ms)": row["original_ms"],
                    "Original status": row["original_status"],
                    "Optimized (ms)": row["optimized_ms"],
                    "Optimized status": row["optimized_status"],
                }
                for row in sweep
            ]
            st.dataframe(sweep_table, use_container_width=True)

            chart_data = {
                "Scale": [row["scale"] for row in sweep],
                "Original (ms)": [row["original_ms"] for row in sweep],
                "Optimized (ms)": [row["optimized_ms"] for row in sweep],
            }
            try:
                import pandas as pd
                df = pd.DataFrame(chart_data).set_index("Scale")
                st.line_chart(df)
            except ImportError:
                st.info("Install pandas to see the scale-sweep line chart (table above still shows the raw numbers).")
        elif result.get("scale_sweep_skipped"):
            st.info(
                "Scale sweep skipped for this run — "
                + result.get("scale_sweep_skip_reason", "the original script was too slow to sweep.")
                + " The single-point benchmark above is still valid."
            )
        elif result["verified"]:
            st.info(
                "Scale sweep produced no data for this run — this can happen if the "
                "Normalizer agent's SCALE extraction didn't apply cleanly to this "
                "particular script. The single-point benchmark above is still valid."
            )

        st.divider()
        st.download_button(
            "Download full agent trace (JSON)",
            data=json.dumps(result, indent=2, default=str),
            file_name=f"optistack_trace_{result.get('run_id', 'run')}.json",
            mime="application/json",
        )
