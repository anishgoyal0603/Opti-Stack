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

st.set_page_config(page_title="Opti-Stack: Autonomous Algorithmic Auditor", layout="wide")

st.title("⚡ Opti-Stack: Autonomous Algorithmic Auditor")
st.caption(
    "A multi-agent system (Analyst → Optimizer → Benchmarker → Verifier) "
    "that analyzes, rewrites, and verifies the correctness of optimized Python code."
)

# --- Public-demo disclaimer / lightweight terms of use (Fix 6). A public app
# that runs strangers' code and asks for an API key should say plainly what it
# does and does not promise, so this is stated up front rather than buried. ---
st.info(
    "**Demonstration tool.** Opti-Stack runs submitted Python in a sandboxed "
    "subprocess (static security scan + memory/CPU limits + a short timeout) "
    "for demo purposes only — this is not a hardened multi-tenant sandbox. "
    "Do not submit malicious code, secrets, or personal data. Submitted code "
    "may be temporarily logged for debugging. Provided as-is, without warranty.",
    icon="ℹ️",
)

user_code = st.text_area(
    "Paste your unoptimized Python code here:",
    height=200,
    value="""def process_data(limit):
    data_list = list(range(limit))
    matches = 0
    for x in data_list:
        for y in data_list:
            if x + y == limit - 1:
                matches += 1
    return matches
print(f"Matches calculated: {process_data(1500)}")""",
)

with st.sidebar:
    st.header("Configuration")
    user_key = st.text_input(
        "Gemini API key",
        type="password",
        help="Used only for this browser session and never stored. "
             "Get a free one at aistudio.google.com/app/apikey",
    )
    st.caption(
        "Opti-Stack runs your code in a sandboxed subprocess with a static "
        "security scan, memory/CPU limits, and a 10-second timeout. Do not "
        "paste secrets you wouldn't want to type into any web form."
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

# NOTE (Fix 3): we deliberately do NOT write api_key into os.environ. On a
# hosted multi-session deployment the whole app is one shared OS process, so
# os.environ is global state -- writing one visitor's key there would leak it
# into another visitor's concurrent run. The key is passed explicitly into a
# per-session GeminiClient below instead.


# --- Per-session rate limiting (Fix 4). A single click fans out to several
# LLM calls and dozens of subprocess spawns, so an unthrottled button is a
# cheap way to burn CPU (and any fallback key's quota). This is a per-session
# limit, which stops casual hammering; true per-IP limiting needs a reverse
# proxy in front and is out of scope for free Streamlit Cloud. ---
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
            st.error(
                "🛑 Your submitted code was rejected by the static security scanner "
                "before any agent touched it, for these reasons:"
            )
            for v in result.get("security_violations", []):
                st.write(f"- {v}")
            st.info(
                "Opti-Stack will not execute code that imports os/subprocess/socket, "
                "calls eval/exec/open, references __builtins__, or uses dunder "
                "attributes (e.g. .__class__). Remove these and try again."
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
