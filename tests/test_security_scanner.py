import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opti_stack.security_scanner import scan_code
import pytest

def test_scanner_allows_safe_code():
    r = scan_code("def f(n):\n    return sum(range(n))\nprint(f(10))")
    assert r.safe is True and r.violations == []

def test_scanner_blocks_os_system():
    r = scan_code("import os\nos.system('rm -rf /')")
    assert r.safe is False and any("os" in v for v in r.violations)

def test_scanner_blocks_eval():
    r = scan_code("result = eval('1+1')\nprint(result)")
    assert r.safe is False and any("eval" in v for v in r.violations)

def test_scanner_blocks_subprocess_import():
    assert scan_code("import subprocess\nsubprocess.run(['ls'])").safe is False

def test_scanner_blocks_socket():
    assert scan_code("import socket\ns = socket.socket()").safe is False

def test_scanner_handles_syntax_error_gracefully():
    r = scan_code("def f(:\n    pass")
    assert r.safe is False and "does not parse" in r.violations[0].lower()

def test_orchestrator_rejects_dangerous_input_before_any_agent_call():
    from opti_stack.orchestrator import execute_pipeline
    r = execute_pipeline("import os\nos.system('echo pwned')")
    assert r["rejected_at_input"] is True and r["verified"] is False
    assert len(r["security_violations"]) > 0

BYPASS_PAYLOADS = {
    "urllib_network": "import urllib.request\nurllib.request.urlopen('http://evil.com')",
    "requests_network": "import requests\nrequests.get('http://evil.com')",
    "importlib_indirection": "import importlib\nimportlib.import_module('os').system('x')",
    "builtins_eval": "import builtins\nbuiltins.eval('1+1')",
    "pathlib_read": "import pathlib\nprint(pathlib.Path('/etc/passwd').read_text())",
    "getattr_gadget": "f = getattr(str, 'upper')",
    "from_import_escape": "from importlib import import_module",
    "aliased_os": "import os as o\no.system('x')",
    # --- NEW: no-import object-graph escapes the old scanner let through ---
    "subclass_walk": "print(().__class__.__bases__[0].__subclasses__())",
    "dunder_class_chain": "x = ''.__class__.__mro__",
    "dunder_globals": "def f():\n    pass\nprint(f.__globals__)",
    "builtins_name_ref": "print(__builtins__)",
    "builtins_subscript": "__builtins__['eval']('1+1')",
    "builtins_attr_import": "__builtins__.__import__('os')",
}

LEGITIMATE_OPTIMIZATIONS = {
    "nested_loop": "SCALE = 100\nt = 0\nfor i in range(SCALE):\n    t += i\nprint(t)",
    "hashmap": "from collections import defaultdict\nd = defaultdict(int)\nprint(len(d))",
    "math_and_itertools": "import math, itertools, heapq, functools\nprint(math.isqrt(16))",
    "name_main_guard": "def main():\n    print(sum(range(10)))\nif __name__ == '__main__':\n    main()",
    "comprehension": "data = [i*i for i in range(1000)]\nprint(sum(data))",
}

@pytest.mark.parametrize("name,source", sorted(BYPASS_PAYLOADS.items()))
def test_scanner_blocks_known_bypass(name, source):
    r = scan_code(source)
    assert r.safe is False, f"{name} was not blocked"
    assert r.violations

@pytest.mark.parametrize("name,source", sorted(LEGITIMATE_OPTIMIZATIONS.items()))
def test_scanner_allows_legitimate_optimization(name, source):
    r = scan_code(source)
    assert r.safe is True, f"{name} was falsely rejected: {r.violations}"
