"""Tests for the curated example scripts used by the public examples-only
deployment mode. Every example must pass the security scanner cleanly --
if one didn't, the public UI would show a security rejection for a script
the deployer wrote, which would be a bug in the example, not in the
scanner. Each example must also actually run and finish."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from opti_stack.examples import EXAMPLES, get_example
from opti_stack.security_scanner import scan_code
from opti_stack.benchmarking import run_benchmark


def _write_temp_script(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def test_at_least_a_few_examples_exist():
    assert len(EXAMPLES) >= 3


def test_example_keys_are_unique():
    keys = [ex.key for ex in EXAMPLES]
    assert len(keys) == len(set(keys))


def test_get_example_returns_the_right_one():
    for ex in EXAMPLES:
        assert get_example(ex.key) is ex


def test_get_example_raises_on_unknown_key():
    try:
        get_example("does-not-exist")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_every_example_passes_the_security_scanner():
    """This is the load-bearing test: the whole point of examples-only mode
    is that nothing shown to a public visitor was ever adversarial input,
    but that only holds if what's checked in here is actually clean."""
    for ex in EXAMPLES:
        result = scan_code(ex.code)
        assert result.safe, f"{ex.key} failed the scanner: {result.violations}"


def test_every_example_actually_runs_successfully():
    for ex in EXAMPLES:
        result = run_benchmark(_write_temp_script(ex.code))
        assert result.status == "SUCCESS", (
            f"{ex.key} did not run successfully: {result.status} / {result.stderr}"
        )
        assert result.stdout and result.stdout.strip(), f"{ex.key} produced no output"


def test_every_example_has_label_and_description():
    for ex in EXAMPLES:
        assert ex.label.strip()
        assert ex.description.strip()
