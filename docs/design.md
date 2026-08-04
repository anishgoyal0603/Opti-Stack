# Design notes

## Why the Verifier is deterministic, not another LLM

It would be simpler to ask a second LLM "does this rewrite behave the same
as the original?" and trust its answer. That's deliberately not how this
works: judging one model's output with another model correlates their
errors. A plain stdout diff has no opinions to be talked into — it either
matches or it doesn't.

## Why the security scanner runs twice

Once on the user's original input, and once again on every piece of code
the Optimizer generates. A cleverly-crafted input script could steer the
Optimizer into writing something unsafe, so scanning only the input isn't
enough.

## Why benchmarking happens in a separate subprocess

Isolation, an enforceable timeout, and honest measurement. An early version
of this project measured memory using tracemalloc started in the parent
process while the target ran in a child subprocess — tracemalloc only sees
its own process, so it was blind to the child entirely. Running the target
as its own process, and measuring memory inside that process, was the fix.

## Why dunder attribute access is blocked, but not all of it

The dangerous sandbox escapes in Python need no imports and no denylisted
builtins. They walk the object graph instead:
`().__class__.__bases__[0].__subclasses__()` reaches `Popen` using nothing
but attribute access on an empty tuple. A denylist of module names cannot
see this, so the scanner rejects dunder attribute access outright.

A blanket ban was too broad: it also rejected `super().__init__()`, `__len__`,
`__enter__` and every other ordinary protocol method, so legitimate
object-oriented submissions were refused. The scanner therefore keeps a
conservative `SAFE_DUNDERS` allowlist of protocol methods that cannot
traverse the object graph, and blocks everything else. The escape-critical
names (`__class__`, `__bases__`, `__mro__`, `__subclasses__`, `__globals__`,
`__code__`, `__dict__`, `__reduce__`, `__getattribute__`) stay blocked.

## Why format templates are scanned as well

`"{0.__class__.__bases__[0]}".format(())` performs the same traversal, but
the dunders live inside a *string constant* — the AST contains no `Attribute`
node at all, so the rule above never sees them. The scanner therefore also
pattern-matches replacement fields that reach for a dunder.

The limits of that rule are worth stating: a template assembled at runtime
from concatenated fragments still evades it. This residual gap is accepted
because format-template traversal can *read* attributes but cannot call
them — it is an information-disclosure primitive, not code execution — and
the child runs each script in a fresh `runpy` namespace holding no
credentials.

## Why the source size is capped before parsing

`ast.parse()` runs in the *caller's* process. On a hosted deployment that is
the single shared Streamlit process, where none of the child's resource
limits apply. Parsing costs roughly 400 MB of peak memory per MB of source,
so a ~2.4 MB paste of entirely benign statements (`x = 1` repeated) consumed
~1 GB and ~20 seconds of CPU and would OOM a typical 1 GB instance in a
single request. Rate limiting cannot help when one request suffices, so the
length check runs before the parse.

## Why there is a total sampling budget, not just a per-run timeout

Per-run limits don't compose into a total. A script that sleeps burns zero
CPU, so `RLIMIT_CPU` never fires, and if each sleep stays under the per-run
wall-clock timeout, nothing ever times out — yet the runner still repeats it
`DEFAULT_RUNS` times. Measured before the fix: `time.sleep(9)` returned
`SUCCESS` after 46 seconds from a single benchmark call. The budget caps
total sampling wall-clock and reports how many samples were actually taken.
For the same reason the Coordinator skips the scale sweep when the original
script is already slow: the sweep is the largest multiplier of per-click cost.
