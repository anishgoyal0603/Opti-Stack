def process_data(limit):
    """
    Optimized version of process_data.
    The original O(N^2) loop counts pairs (x, y) where 0 <= x, y < limit
    and x + y = limit - 1.
    For every x in [0, limit - 1], y = limit - 1 - x is always 
    within [0, limit - 1].
    Thus, there are exactly 'limit' solutions.
    """
    if limit <= 0:
        return 0
    return limit

# The variable SCALE is provided by the harness as per instructions.
print(f"Matches calculated: {process_data(SCALE)}")