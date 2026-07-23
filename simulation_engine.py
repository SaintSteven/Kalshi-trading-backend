import random
from collections import Counter

def run_strikeout_simulation(rate,expected_bf,floor,ceiling,sd,simulations=10000,seed=42):
    rng=random.Random(seed);out=Counter()
    for _ in range(simulations):
        bf=max(floor,min(ceiling,round(rng.gauss(expected_bf,sd))));ks=sum(rng.random()<rate for _ in range(bf));out[ks]+=1
    probs={f'{t}+':round(sum(c for k,c in out.items() if k>=t)/simulations,4) for t in range(1,max(out)+2)}
    mean=sum(k*c for k,c in out.items())/simulations
    return {'projected_strikeouts':round(mean,2),'ladder_probabilities':probs}
