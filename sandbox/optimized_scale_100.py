SCALE = 100
def process_data(limit):
    """
    Optimized version of process_data.
    
    Mathematical Analysis:
    The original nested loop iterates through all x in [0, limit-1] and y in [0, limit-1].
    The condition is x + y = limit - 1.
    For any x where 0 <= x < limit, y is determined as (limit - 1 - x).
    Since 0 <= x < limit:
    - If x = 0, y = limit - 1 (Valid: 0 <= limit-1 < limit)
    - If x = limit - 1, y = 0 (Valid: 0 <= 0 < limit)
    For every integer x in the range [0, limit-1], there is exactly one y 
    that satisfies the condition and is within the bounds [0, limit-1].
    There are 'limit' such integers.
    
    Time Complexity: O(1)
    Space Complexity: O(1)
    """
    if limit <= 0:
        return 0
    return limit

# The harness provides the SCALE variable. 
# We use globals() to access it safely or rely on the provided environment.
try:
    current_scale = SCALE
except NameError:
    current_scale = 1500

print(f"Matches calculated: {process_data(current_scale)}")