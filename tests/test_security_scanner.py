import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from opti_stack.security_scanner import scan_code


def test_scanner_allows_safe_code():
    safe = "def f(n):\n    return sum(range(n))\nprint(f(10))"
    result = scan_code(safe)
    assert result.safe is True
    assert result.violations == []


def test_scanner_blocks_os_system():
    dangerous = "import os\nos.system('rm -rf /')"
    result = scan_code(dangerous)
    assert result.safe is False
    assert any("os" in v for v in result.violations)


def test_scanner_blocks_eval():
    dangerous = "result = eval('1+1')\nprint(result)"
    result = scan_code(dangerous)
    assert result.safe is False
    assert any("eval" in v for v in result.violations)


def test_scanner_blocks_subprocess_import():
    dangerous = "import subprocess\nsubprocess.run(['ls'])"
    result = scan_code(dangerous)
    assert result.safe is False


def test_scanner_blocks_socket():
    dangerous = "import socket\ns = socket.socket()"
    result = scan_code(dangerous)
    assert result.safe is False


def test_scanner_handles_syntax_error_gracefully():
    broken = "def f(:\n    pass"
    result = scan_code(broken)
    assert result.safe is False
    assert "does not parse" in result.violations[0].lower()


def test_orchestrator_rejects_dangerous_input_before_any_agent_call():
    """Proves the scanner is actually wired into execute_pipeline's entry
    point, not just sitting unused as a standalone module -- this directly
    addresses the bug report that flagged a missing security check."""
    from opti_stack.orchestrator import execute_pipeline

    dangerous_script = "import os\nos.system('echo pwned')"
    result = execute_pipeline(dangerous_script)

    assert result["rejected_at_input"] is True
    assert result["verified"] is False
    assert len(result["security_violations"]) > 0
