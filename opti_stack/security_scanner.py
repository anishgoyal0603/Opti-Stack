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
reaches runner.py's subprocess. It runs on every agent-generated rewrite,
every attempt, with no opt-out.

Three classes of thing are rejected:
  1. Denylisted imports (os, subprocess, socket, ...).
  2. Denylisted bare builtins (eval, exec, open, getattr, ...).
  3. Attribute access that is either rooted at a denylisted module name
     (os.system(...)) OR uses ANY dunder attribute (.__class__,
     .__subclasses__, .__globals__, .__builtins__, ...). The dunder rule
     is what closes the classic "no-import" sandbox escapes, which never
     touch an import or a denylisted builtin name and instead walk Python's
     object graph -- e.g. ().__class__.__bases__[0].__subclasses__() to
     reach Popen. Legitimate numeric/algorithmic optimization code has no
     reason to touch dunder attributes, so blocking them wholesale is a
     cheap, high-value rule rather than a real usability cost.

This is still a denylist, NOT a sandbox replacement -- it catches the known
and common dangerous patterns, not a fully adversarial bypass. It is one
layer; the subprocess resource limits (RLIMIT_AS / RLIMIT_CPU in runner.py),
the wall-clock timeout, and the per-run temp directory are the others. Do
not expose this to untrusted multi-tenant traffic without a real container
or gVisor boundary underneath it.
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

# Bare *names* that must never be referenced at all, even without a call --
# reaching __builtins__ (which can be a dict or a module) is the first step
# of several escapes (__builtins__['eval'], __builtins__.__import__, ...),
# so we reject the name on sight rather than trying to catch every way it
# can then be indexed or called.
DENYLISTED_NAMES = {"__builtins__", "__builtin__"}

# os.system, os.remove, etc. -- checked via attribute access on names that
# resolve back to a denylisted import. __builtins__ is included so that
# __builtins__.eval(...) is caught here too, belt-and-braces with the name
# rule above.
DENYLISTED_ATTR_ROOTS = DENYLISTED_IMPORTS | {"__builtins__"}


@dataclass
class ScanResult:
    safe: bool
    violations: List[str] = field(default_factory=list)


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def scan_code(source: str) -> ScanResult:
    """Parses source with ast.parse (no execution) and walks the tree
    looking for denylisted imports, denylisted bare calls, denylisted bare
    names, dunder attribute access, and attribute access rooted at a
    denylisted module name (e.g. os.system(...))."""
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

        elif isinstance(node, ast.Name):
            if node.id in DENYLISTED_NAMES:
                violations.append(f"Disallowed reference to '{node.id}' (line {node.lineno})")

        elif isinstance(node, ast.Attribute):
            # Any dunder attribute access is a red flag: this is how the
            # no-import object-graph escapes reach dangerous internals
            # (e.g. ().__class__.__bases__[0].__subclasses__()).
            if _is_dunder(node.attr):
                violations.append(
                    f"Disallowed dunder attribute access: '.{node.attr}' (line {node.lineno})"
                )
            else:
                root = _resolve_attribute_root(node)
                if root in DENYLISTED_ATTR_ROOTS:
                    violations.append(
                        f"Disallowed attribute on module '{root}': '{root}.{node.attr}' (line {node.lineno})"
                    )

        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in DENYLISTED_CALLS:
                violations.append(f"Disallowed call: '{func.id}(...)' (line {node.lineno})")
            # Attribute-based calls (os.system(...), obj.__class__(...)) are
            # already covered by the ast.Attribute branch above, which every
            # such node also passes through during ast.walk.

    return ScanResult(safe=(len(violations) == 0), violations=violations)


def _resolve_attribute_root(node: ast.Attribute) -> str:
    """For something like os.path.join(...), walk down to the leftmost Name
    node ('os') so attribute chains are still caught. Returns "" when the
    chain bottoms out on a non-Name (e.g. a literal like ().__class__),
    which is fine because that case is caught by the dunder rule instead."""
    current = node.value
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    return ""
