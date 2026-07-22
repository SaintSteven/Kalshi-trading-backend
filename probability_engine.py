import math

def poisson_probability_at_least(mean: float, threshold: int) -> float:
    if mean < 0: raise ValueError("Mean cannot be negative.")
    if threshold <= 0: return 1.0
    cumulative=sum(math.exp(-mean)*(mean**k)/math.factorial(k) for k in range(threshold))
    return max(0.0,min(1.0,1.0-cumulative))
