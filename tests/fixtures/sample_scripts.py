"""Small, deterministic scripts used as fixtures across tests.
Kept separate from test files so they're easy to reuse and easy to read."""

CORRECT_SLOW_SCRIPT = """
def process_data(limit):
    data_list = list(range(limit))
    matches = 0
    for x in data_list:
        for y in data_list:
            if x + y == limit - 1:
                matches += 1
    return matches
print(f"Matches: {process_data(200)}")
"""

CORRECT_FAST_EQUIVALENT = """
def process_data(limit):
    # O(N) instead of O(N^2): for each x, the only y that matches is
    # (limit - 1 - x), so just check it's in range.
    matches = sum(1 for x in range(limit) if 0 <= (limit - 1 - x) < limit)
    return matches
print(f"Matches: {process_data(200)}")
"""

INCORRECT_FAST_VARIANT = """
print("Matches: 999999")
"""

INFINITE_LOOP_SCRIPT = """
while True:
    pass
"""

CRASHING_SCRIPT = """
raise ValueError("deliberate failure for testing")
"""

MEMORY_HEAVY_SCRIPT = """
data = [0] * 5_000_000
print(sum(data))
"""

MEMORY_LIGHT_SCRIPT = """
print(0)
"""