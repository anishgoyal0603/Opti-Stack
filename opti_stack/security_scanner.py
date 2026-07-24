"""
Static security scanner for LLM-generated code.

Before this module existed, the pipeline executed whatever the Optimizer
agent produced directly in a subprocess, with no check on *what* that code
actually does beyond a timeout. An LLM could (accidentally, or if the
input script was adversarial/prompt-injected) generate code that deletes
files, opens a network connection, or shells out -- and the old pipeline
would run it the same way it ran a harmless loop.

This module performs a static AST scan (no execution) and rejects code
that uses a denylisted set of dangerous calls/imports BEFORE it ever
reaches runner.py's subprocess. This is a real guardrail, not a cosmetic
one: it runs on every agent-generated rewrite, every attempt, with no
opt-out.

This is a denylist, not a sandbox replacement -- it catches the obvious
and common dangerous patterns, not a fully adversarial bypass attempt.
Combined with the existing subprocess timeout and sandbox-folder
restriction, it raises the bar meaningfully for a hackathon-scope project
without claiming to be a hardened production sandbox.
"""

import ast
from dataclasses import dataclass, field
from typing import List


DENYLISTED_IMPORTS = {
    # process / system
    "os", "subprocess", "shutil", "sys", "ctypes", "pty", "signal",
    "multiprocessing", "threading", "resource", "runpy", "code",
    # serialisation that can execute code on load
    "pickle", "marshal", "shelve", "dill",
    # network
    "socket", "ssl", "urllib", "urllib2", "http", "ftplib", "smtplib",
    "telnetlib", "requests", "httpx", "aiohttp", "asyncio",
    # filesystem
    "pathlib", "tempfile", "glob", "fileinput", "io", "zipfile", "tarfile",
    # dynamic import / introspection escapes
    "importlib", "builtins", "__builtin__", "imp", "inspect", "gc", "ast",
    "webbrowser", "platform", "site", "sysconfig",
}

DENYLISTED_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "breakpoint", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "memoryview",
}

# os.system, os.remove, etc. -- checked via attribute access on names
# that resolve back to a denylisted import.
DENYLISTED_ATTR_ROOTS = DENYLISTED_IMPORTS


@dataclass
class ScanResult:
    safe: bool
    violations: List[str] = field(default_factory=list)


def scan_code(source: str) -> ScanResult:
    """Parses source with ast.parse (no execution) and walks the tree
    looking for denylisted imports, denylisted bare calls, and attribute
    access rooted at a denylisted module name (e.g. os.system(...))."""
    violations = []

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ScanResult(safe=False, violations=[f"Code does not parse: {e}"])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in DENYLISTED_IMPORTS:
                    violations.append(f"Disallowed import: '{alias.name}' (line {node.lineno})")

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in DENYLISTED_IMPORTS:
                    violations.append(f"Disallowed import: 'from {node.module} import ...' (line {node.lineno})")

        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in DENYLISTED_CALLS:
                violations.append(f"Disallowed call: '{func.id}(...)' (line {node.lineno})")
            elif isinstance(func, ast.Attribute):
                root = _resolve_attribute_root(func)
                if root in DENYLISTED_ATTR_ROOTS:
                    violations.append(
                        f"Disallowed call on module '{root}': '{root}.{func.attr}(...)' (line {node.lineno})"
                    )

    return ScanResult(safe=(len(violations) == 0), violations=violations)


def _resolve_attribute_root(node: ast.Attribute) -> str:
    """For something like os.path.system(...), walk down to the leftmost
    Name node ('os') so attribute chains are still caught."""
    current = node.value
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    return ""
