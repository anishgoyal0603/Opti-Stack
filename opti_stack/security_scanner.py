"""
Static security scanner for LLM-generated code.

The pipeline executes whatever the Optimizer agent produces. This module
performs a static AST scan (no execution) and rejects code that uses a
denylisted set of dangerous constructs BEFORE it ever reaches runner.py's
subprocess. It runs on the user's original input and on every agent-generated
rewrite, every attempt, with no opt-out.

What gets rejected, and why each rule exists:

  1. Source larger than MAX_SOURCE_CHARS, checked *before* ast.parse().
     This is a parser-level denial-of-service guard, not a code-safety rule.
     ast.parse() runs in the caller's process -- on a hosted deployment that
     is the single shared Streamlit process, where none of runner.py's child
     resource limits apply. Parsing is roughly linear in input size at about
     400 MB of peak memory per MB of source, so a ~2.4 MB paste of entirely
     benign statements ("x = 1" repeated) consumes ~1 GB and ~20 s of CPU and
     will OOM a typical 1 GB instance in a single request. Rate limiting does
     not help when one request suffices, so the size check must come first.

  2. Denylisted imports (os, subprocess, socket, ...).

  3. Denylisted bare builtins (eval, exec, open, getattr, ...).

  4. Attribute access rooted at a denylisted module name (os.system(...)),
     and dunder attribute access outside a small SAFE_DUNDERS allowlist.
     The dunder rule closes the classic "no-import" object-graph escapes,
     which touch neither an import nor a denylisted builtin -- e.g.
     ().__class__.__bases__[0].__subclasses__() to reach Popen. The allowlist
     exists because a blanket ban also rejected ordinary object-oriented code
     (super().__init__() being the obvious casualty). Every name on the
     allowlist is a protocol method that cannot traverse the object graph;
     the escape-critical ones (__class__, __bases__, __mro__, __subclasses__,
     __globals__, __code__, __closure__, __dict__, __reduce__,
     __getattribute__, ...) all remain blocked.

  5. Dunder attribute traversal hidden inside a str.format template, e.g.
     "{0.__class__.__bases__[0]}".format(()). Here the dunders live in a
     string constant, so the AST contains no Attribute node at all and rule 4
     never sees them. Note the honest limits of this rule: it pattern-matches
     the template literal, so a template assembled at runtime from
     concatenated fragments still evades it. That residual gap is accepted
     because format-template traversal can *read* attributes but cannot call
     them -- it is an information-disclosure primitive, not code execution --
     and the child process runs each script in a fresh runpy namespace that
     holds no credentials.

This is a denylist, NOT a sandbox replacement. It is one layer; the child
process's RLIMIT_AS / RLIMIT_CPU / RLIMIT_FSIZE, the wall-clock timeout and
sampling budget, the captured-output cap, and the per-run temp directory are
the others. Do not expose this to untrusted multi-tenant traffic without a
real container or gVisor boundary underneath it.
"""

import ast
import re
from dataclasses import dataclass, field
from typing import List


# Parser-level DoS guard. 50k characters is far more than any realistic
# submitted script and keeps ast.parse()'s peak well under ~25 MB.
MAX_SOURCE_CHARS = 50_000

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
    # string-based attribute access and code-object construction. `operator`
    # is the important one: operator.attrgetter("gi_frame") / methodcaller
    # fetch an attribute BY STRING, which bypasses the entire .attr denylist
    # (the AST never contains the attribute name). Confirmed at runtime to
    # reach __builtins__.__import__("os") through a generator frame. `types`
    # (FunctionType/CodeType) constructs executable objects from parts.
    # functools/itertools/collections are deliberately NOT here -- they are
    # common in real optimization code and offer no string-attr or code
    # construction primitive on their own.
    "operator", "types", "copyreg",
}

DENYLISTED_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "breakpoint", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "memoryview",
}

# Bare names that must never be referenced at all, even without a call.
DENYLISTED_NAMES = {"__builtins__", "__builtin__"}

DENYLISTED_ATTR_ROOTS = DENYLISTED_IMPORTS | {"__builtins__"}

# Dunder attributes that ordinary object-oriented code legitimately touches
# and that cannot be used to walk toward dangerous internals. Anything not on
# this list is rejected. Keep this list conservative: adding a traversal
# primitive here (__class__, __bases__, __globals__, __dict__, ...) would
# reopen the object-graph escape the dunder rule exists to close.
SAFE_DUNDERS = {
    "__init__", "__new__", "__del__",
    "__str__", "__repr__", "__format__",
    "__len__", "__iter__", "__next__", "__contains__", "__reversed__",
    "__getitem__", "__setitem__", "__delitem__",
    "__enter__", "__exit__",
    "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__hash__",
    "__add__", "__sub__", "__mul__", "__truediv__", "__floordiv__",
    "__mod__", "__pow__", "__neg__", "__abs__", "__round__",
    "__radd__", "__rsub__", "__rmul__",
    "__bool__", "__int__", "__float__", "__index__",
    "__call__", "__copy__", "__deepcopy__",
}

# Non-dunder attribute and method names that reach frame/code/type internals.
# The dunder rule cannot see these because they are ordinary names -- yet each
# is a documented path back to f_globals -> __builtins__ -> __import__, or to
# object.__subclasses__() via type().mro(). Confirmed reachable at runtime:
#   (g := (lambda: (yield)))().gi_frame.f_globals['__builtins__'].__import__('os')
#   type(()).mro()[-1].__subclasses__()
# None of these appear in ordinary optimization code.
DENYLISTED_INTROSPECTION_ATTRS = {
    # generator / coroutine / async-generator internals
    "gi_frame", "gi_code", "gi_yieldfrom",
    "cr_frame", "cr_code", "cr_await",
    "ag_frame", "ag_code", "ag_await",
    # frame object
    "f_globals", "f_builtins", "f_locals", "f_back", "f_code", "f_trace",
    "f_lasti", "f_lineno",
    # code object
    "co_consts", "co_names", "co_code", "co_varnames", "co_freevars",
    "co_cellvars", "co_globals",
    # traceback (e.__traceback__ is a blocked dunder, but tb_* are not)
    "tb_frame", "tb_next",
    # legacy py2-style function attributes, blocked defensively
    "func_globals", "func_code", "func_dict", "func_closure",
}

# Non-dunder methods that leak the type graph when *called*.
DENYLISTED_INTROSPECTION_METHODS = {
    # type(x).mro() returns the MRO list, whose last element is object, whose
    # __subclasses__() walks to Popen -- the classic escape without a dunder.
    "mro",
    # __init_subclass__ / __set_name__ etc. are dunders and already blocked;
    # this set is for the non-dunder callable leaks only.
    #
    # Executable-object constructors and string-based attribute getters,
    # blocked by attribute NAME so that a re-export through an otherwise-safe
    # module (e.g. dataclasses.FunctionType, which really is types.FunctionType)
    # does not become a bypass. Blocking the import of `operator`/`types` above
    # handles the direct route; this handles the re-export route.
    "FunctionType", "CodeType", "FrameType", "TracebackType",
    "attrgetter", "methodcaller", "itemgetter",
}

# Matches a str.format replacement field that performs attribute access on a
# dunder, e.g. "{0.__class__}" or "{obj.__globals__[KEY]}".
_FORMAT_DUNDER_RE = re.compile(r"\{[^{}]*\.__\w")


@dataclass
class ScanResult:
    safe: bool
    violations: List[str] = field(default_factory=list)


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def scan_code(source: str) -> ScanResult:
    """Parses source with ast.parse (no execution) and walks the tree looking
    for the constructs documented in this module's docstring. The size check
    runs first, because ast.parse() itself is the resource risk for oversized
    input and it executes in the caller's (shared) process."""
    if not isinstance(source, str):
        # Defensive: every real caller passes a str, but ast.parse() raises a
        # bare TypeError on None/int/list and that would escape into the
        # pipeline as an unhandled crash rather than a clean rejection.
        return ScanResult(
            safe=False,
            violations=[f"Source must be a string, got {type(source).__name__}."],
        )

    if len(source) > MAX_SOURCE_CHARS:
        return ScanResult(
            safe=False,
            violations=[
                f"Source too large: {len(source):,} characters "
                f"(limit {MAX_SOURCE_CHARS:,}). Parsing input this large can "
                f"exhaust the host process before any sandbox applies."
            ],
        )

    violations = []

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ScanResult(safe=False, violations=[f"Code does not parse: {e}"])
    except (ValueError, MemoryError, RecursionError) as e:
        # Deeply nested literals can blow the parser's recursion limit or
        # memory even under the size cap; treat that as a rejection rather
        # than letting the exception escape into the pipeline.
        return ScanResult(safe=False, violations=[f"Code could not be parsed safely: {e}"])

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
            # Dunder attribute access outside the safe allowlist: this is how
            # the no-import object-graph escapes reach dangerous internals
            # (e.g. ().__class__.__bases__[0].__subclasses__()).
            if _is_dunder(node.attr) and node.attr not in SAFE_DUNDERS:
                violations.append(
                    f"Disallowed dunder attribute access: '.{node.attr}' (line {node.lineno})"
                )
            elif node.attr in DENYLISTED_INTROSPECTION_ATTRS:
                violations.append(
                    f"Disallowed introspection attribute: '.{node.attr}' "
                    f"(reaches frame/code internals) (line {node.lineno})"
                )
            elif node.attr in DENYLISTED_INTROSPECTION_METHODS:
                violations.append(
                    f"Disallowed introspection method: '.{node.attr}(...)' "
                    f"(leaks the type graph) (line {node.lineno})"
                )
            else:
                root = _resolve_attribute_root(node)
                if root in DENYLISTED_ATTR_ROOTS:
                    violations.append(
                        f"Disallowed attribute on module '{root}': '{root}.{node.attr}' (line {node.lineno})"
                    )

        elif isinstance(node, ast.Constant):
            # Dunder traversal smuggled through a str.format template, which
            # produces no Attribute node for the rule above to catch.
            if isinstance(node.value, str) and _FORMAT_DUNDER_RE.search(node.value):
                snippet = node.value[:60]
                violations.append(
                    f"Disallowed dunder attribute access inside a format template: "
                    f"{snippet!r} (line {node.lineno})"
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
    chain bottoms out on a non-Name (e.g. a literal like ().__class__), which
    is fine because that case is caught by the dunder rule instead."""
    current = node.value
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    return ""
