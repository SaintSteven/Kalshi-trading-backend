import math
from collections import defaultdict
from statistics import mean

EPSILON=1e-9

def mae(a,p): return sum(abs(x-y) for x,y in zip(a,p))/len(a) if a else None
def rmse(a,p): return math.sqrt(sum((x-y)**2 for x,y in zip(a,p))/len(a)) if a else None
def mean_error(a,p): return sum(y-x for x,y in zip(a,p))/len(a) if a else None
def brier_score(p,y): return sum((a-b)**2 for a,b in zip(p,y))/len(p) if p else None

def log_loss(p,y):
    if not p:return None
    total=0.0
    for prob,outcome in zip(p,y):
        prob=max(EPSILON,min(1-EPSILON,prob))
        total += -(outcome*math.log(prob)+(1-outcome)*math.log(1-prob))
    return total/len(p)

def calibration_buckets(p,y,bucket_size=0.10):
    buckets=defaultdict(list)
    for prob,outcome in zip(p,y):
        idx=min(int(prob/bucket_size),int(1/bucket_size)-1)
        buckets[idx].append((prob,outcome))
    out=[]
    for idx in range(int(1/bucket_size)):
        rows=buckets.get(idx,[])
        low=idx*bucket_size; high=low+bucket_size
        if rows:
            avg=mean(r[0] for r in rows); actual=mean(r[1] for r in rows); err=abs(avg-actual)
        else:
            avg=actual=err=None
        out.append({"bucket_low":round(low,2),"bucket_high":round(high,2),"observations":len(rows),"average_predicted_probability":round(avg,4) if avg is not None else None,"actual_win_rate":round(actual,4) if actual is not None else None,"calibration_error":round(err,4) if err is not None else None})
    return out

def expected_calibration_error(p,y):
    if not p:return None
    total=len(p); weighted=0.0
    for b in calibration_buckets(p,y):
        if b["observations"] and b["calibration_error"] is not None:
            weighted += b["observations"]/total*b["calibration_error"]
    return weighted
