import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from opti_stack.security_scanner import scan_code, MAX_SOURCE_CHARS
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
    # no-import object-graph escapes
    "subclass_walk": "print(().__class__.__bases__[0].__subclasses__())",
    "dunder_class_chain": "x = ''.__class__.__mro__",
    "dunder_globals": "def f():\n    pass\nprint(f.__globals__)",
    "builtins_name_ref": "print(__builtins__)",
    "builtins_subscript": "__builtins__['eval']('1+1')",
    "builtins_attr_import": "__builtins__.__import__('os')",
    # format-template traversal: dunders hidden inside a string constant, so
    # the AST contains no Attribute node for the dunder rule to catch
    "format_string_class": 'print("{0.__class__}".format(()))',
    "format_string_deep": 'print("{0.__class__.__bases__[0]}".format(()))',
    "format_globals_leak": 'def f(): pass\nprint("{0.__globals__}".format(f))',
    "format_map_variant": 'print("{a.__class__}".format_map({"a": ()}))',
    # dangerous dunders that must stay blocked despite the SAFE_DUNDERS list
    "dunder_dict": "class A: pass\nprint(A.__dict__)",
    "dunder_code": "def f(): pass\nprint(f.__code__)",
    "dunder_reduce": "print(().__reduce__)",
    "dunder_getattribute": "print(''.__getattribute__)",
}

LEGITIMATE_OPTIMIZATIONS = {
    "nested_loop": "SCALE = 100\nt = 0\nfor i in range(SCALE):\n    t += i\nprint(t)",
    "hashmap": "from collections import defaultdict\nd = defaultdict(int)\nprint(len(d))",
    "math_and_itertools": "import math, itertools, heapq, functools\nprint(math.isqrt(16))",
    "name_main_guard": "def main():\n    print(sum(range(10)))\nif __name__ == '__main__':\n    main()",
    "comprehension": "data = [i*i for i in range(1000)]\nprint(sum(data))",
    # ordinary OOP: a blanket dunder ban wrongly rejected all of these
    "super_init": "class A:\n    def __init__(self):\n        super().__init__()\nprint(1)",
    "dunder_len_protocol": "class B:\n    def __len__(self):\n        return 3\nprint(len(B()))",
    "context_manager": "class C:\n    def __enter__(self):\n        return self\n    def __exit__(self, *a):\n        return False\nwith C():\n    print(1)",
    "plain_format_call": 'print("{} and {}".format(1, 2))',
    "fstring_normal": "n = 5\nprint(f'value is {n}')",
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
