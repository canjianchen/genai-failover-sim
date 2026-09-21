def nearest_rank(values, pct):
    if not values:
        return None
    xs = sorted(values)
    import math
    rank = max(1, math.ceil((pct / 100.0) * len(xs)))
    return xs[rank - 1]
