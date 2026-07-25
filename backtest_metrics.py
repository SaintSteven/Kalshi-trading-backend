import math
from collections import defaultdict
from statistics import mean

EPSILON = 1e-9

def mae(actual, predicted):
    return sum(abs(a-p) for a,p in zip(actual,predicted))/len(actual) if actual else None

def rmse(actual, predicted):
    return math.sqrt(sum((a-p)**2 for a,p in zip(actual,predicted))/len(actual)) if actual else None

def mean_error(actual, predicted):
    return sum(p-a for a,p in zip(actual,predicted))/len(actual) if actual else None

def brier_score(probabilities, outcomes):
    return sum((p-y)**2 for p,y in zip(probabilities,outcomes))/len(probabilities) if probabilities else None

def log_loss(probabilities, outcomes):
    if not probabilities:
        return None
    total = 0.0
    for p,y in zip(probabilities,outcomes):
        p = max(EPSILON, min(1-EPSILON, p))
        total += -(y*math.log(p) + (1-y)*math.log(1-p))
    return total/len(probabilities)

def calibration_buckets(probabilities, outcomes, bucket_size=0.10):
    buckets = defaultdict(list)
    for p,y in zip(probabilities,outcomes):
        index = min(int(p/bucket_size), int(1/bucket_size)-1)
        buckets[index].append((p,y))
    result = []
    for index in range(int(1/bucket_size)):
        rows = buckets.get(index, [])
        low = index*bucket_size
        high = low+bucket_size
        if rows:
            avg_p = mean(r[0] for r in rows)
            actual = mean(r[1] for r in rows)
            error = abs(avg_p-actual)
        else:
            avg_p = actual = error = None
        result.append({
            "bucket_low": round(low,2),
            "bucket_high": round(high,2),
            "observations": len(rows),
            "average_predicted_probability": round(avg_p,4) if avg_p is not None else None,
            "actual_win_rate": round(actual,4) if actual is not None else None,
            "calibration_error": round(error,4) if error is not None else None,
        })
    return result

def expected_calibration_error(probabilities, outcomes):
    if not probabilities:
        return None
    total = len(probabilities)
    return sum(
        bucket["observations"]/total*bucket["calibration_error"]
        for bucket in calibration_buckets(probabilities,outcomes)
        if bucket["observations"] and bucket["calibration_error"] is not None
    )
