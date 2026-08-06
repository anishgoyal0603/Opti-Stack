"""Parser-level denial-of-service guard (source size cap).

ast.parse() runs in the CALLER's process -- on a hosted deployment that is the
single shared Streamlit process, where none of runner.py's child resource
limits apply. Parsing costs roughly 400 MB of peak memory per MB of source, so
an oversized paste of entirely benign statements can OOM the host in a single
request, which rate limiting cannot prevent."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opti_stack.security_scanner import scan_code, MAX_SOURCE_CHARS
import opti_stack.orchestrator as orchestrator


def test_oversized_source_is_rejected_before_parsing():
    huge = "x = 1\n" * 400_000          # ~2.4 MB; ~1 GB peak if parsed
    start = time.perf_counter()
    r = scan_code(huge)
    elapsed = time.perf_counter() - start
    assert r.safe is False
    assert "too large" in r.violations[0].lower()
    # The rejection must be a cheap length check, not a parse.
    assert elapsed < 1.0, f"rejection took {elapsed:.2f}s -- did it parse the input?"


def test_source_just_under_the_cap_is_still_scanned_normally():
    body = "x = 1\n" * ((MAX_SOURCE_CHARS - 100) // 6)
    assert len(body) < MAX_SOURCE_CHARS
    r = scan_code(body)
    assert r.safe is True, r.violations


def test_pipeline_rejects_oversized_input_at_the_entry_point():
    """The cap must be enforced inside the pipeline too, so the CLI and any
    other caller are covered, not just the Streamlit text area."""
    huge = "x = 1\n" * 400_000
    result = orchestrator.execute_pipeline(huge)
    assert result["rejected_at_input"] is True
    assert result["verified"] is False
    assert any("too large" in v.lower() for v in result["security_violations"])


def test_non_string_input_is_a_clean_rejection_not_a_crash():
    """ast.parse() raises a bare TypeError on None/int/list, which would
    escape into the pipeline as an unhandled crash. Every real caller passes
    a str, so this is defensive -- but a scanner is the wrong place to have
    an unhandled exception path."""
    for bad in (None, 42, ["print(1)"], {"a": 1}):
        r = scan_code(bad)
        assert r.safe is False, f"{type(bad).__name__} was not rejected"
        assert "must be a string" in r.violations[0]
