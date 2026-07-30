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