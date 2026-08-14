"""
Curated example scripts for the public, examples-only deployment mode.

Why this module exists: the security scanner in security_scanner.py is a
denylist over an AST scan, and a public demo that lets strangers submit
arbitrary Python for execution asks that denylist to be complete against an
adversarial user forever. It has been patched eight times across as many
review rounds (frame introspection, operator.attrgetter, bare-value
builtins, codecs, ...) and each round found a new escape family. That
pattern doesn't converge -- it demonstrates that a denylist in front of
exec() cannot be made exhaustive.

The examples in this module are the actual fix, not another patch: they are
fixed, reviewed, checked-in strings that never come from a stranger's
browser, so there is no adversarial input for the scanner to fail against.
The public deployment (OPTISTACK_ALLOW_ARBITRARY_CODE unset or "0") offers
only these. Arbitrary-code mode remains fully available for local/cloned use
(OPTISTACK_ALLOW_ARBITRARY_CODE=1), where the user is running code against
their own machine, which is an entirely different trust boundary.

Each example is deliberately slow-but-correct, so the pipeline has a real
bottleneck to diagnose, rewrite, and verify.
"""

from dataclasses import dataclass


@dataclass
class Example:
    key: str
    label: str
    description: str
    code: str


EXAMPLES = [
    Example(
        key="pair_sum",
        label="Pair-sum counter (O(N²) → O(N))",
        description=(
            "Counts index pairs whose values sum to a target using a nested "
            "loop. The classic case for a hash-map rewrite."
        ),
        code="""def process_data(limit):
    data_list = list(range(limit))
    matches = 0
    for x in data_list:
        for y in data_list:
            if x + y == limit - 1:
                matches += 1
    return matches
print(f"Matches calculated: {process_data(1500)}")""",
    ),
    Example(
        key="duplicate_finder",
        label="Duplicate finder (O(N²) → O(N))",
        description=(
            "Finds duplicate values in a list by comparing every pair. "
            "A set-based rewrite drops this to a single pass."
        ),
        code="""def find_duplicates(items):
    duplicates = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j] and items[i] not in duplicates:
                duplicates.append(items[i])
    return duplicates

data = list(range(400)) + list(range(200))
print(f"Duplicates found: {len(find_duplicates(data))}")""",
    ),
    Example(
        key="fibonacci",
        label="Naive recursive Fibonacci (exponential → linear)",
        description=(
            "Textbook exponential-time recursion with no memoization. "
            "A strong demo of the scale-sweep chart, since the original "
            "explodes fast."
        ),
        # n=20 (21,891 recursive calls), not n=24 (150,049 calls). The
        # deployed public app runs on Streamlit Community Cloud's shared free
        # tier, measured at roughly 35-40x slower wall-clock than a typical
        # dev machine for this workload -- fib(24) took ~55ms locally but
        # ~2006ms on the live host, landing right on top of the
        # orchestrator's 2000ms SWEEP_MAX_ORIGINAL_MS threshold and silently
        # skipping the scale-sweep chart (the pipeline's best visual) on an
        # otherwise-successful run. fib(20) has ~15% of fib(24)'s call count,
        # projecting to roughly 300ms on the same host: a wide, deliberate
        # safety margin under the threshold rather than a coin flip against
        # it, while the exponential blowup is still dramatic enough to be a
        # good demo on its own.
        code="""def fib(n):
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

print(f"fib(20) = {fib(20)}")""",
    ),
    Example(
        key="string_concat",
        label="String concatenation in a loop (quadratic → linear)",
        description=(
            "Builds a string with += inside a loop, which is quadratic in "
            "CPython because each += copies the whole string so far. "
            "''.join(...) is the fix."
        ),
        code="""def build_report(n):
    report = ""
    for i in range(n):
        report += f"line {i}\\n"
    return report

result = build_report(6000)
print(f"Report length: {len(result)}")""",
    ),
    Example(
        key="membership_check",
        label="Repeated list membership checks (O(N²) → O(N))",
        description=(
            "Checks membership against a growing list with `in`, which is "
            "linear per check. Switching the lookup structure to a set "
            "makes each check constant time."
        ),
        code="""def unique_preserving_order(items):
    seen = []
    result = []
    for item in items:
        if item not in seen:
            seen.append(item)
            result.append(item)
    return result

data = list(range(3000)) + list(range(1500))
print(f"Unique count: {len(unique_preserving_order(data))}")""",
    ),
]


def get_example(key: str) -> Example:
    for ex in EXAMPLES:
        if ex.key == key:
            return ex
    raise KeyError(f"Unknown example key: {key!r}")
