SCALE = 100
if 'SCALE' not in globals():
    SCALE = 1500

def process_data(limit):
    return max(0, limit)

print(f"Matches calculated: {process_data(SCALE)}")