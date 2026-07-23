from baseline_skill_model import build_regressed_k_rate
from lineup_model import build_lineup_adjustment
from workload_model import build_workload
from recent_change_model import build_recent_change_multiplier
from simulation_engine import run_strikeout_simulation
from confidence_engine import build_confidence

def build_full_projection(d):
    base,weights,sr=build_regressed_k_rate(d);lm,lr=build_lineup_adjustment(d);rm,rr=build_recent_change_multiplier(d);bf,floor,ceil,sd,wr=build_workload(d)
    adj=max(.05,min(.55,base*lm*rm));sim=run_strikeout_simulation(adj,bf,floor,ceil,sd);conf=build_confidence(d)
    status='READY' if d.starter_confirmed else 'INSUFFICIENT_DATA';warnings=[] if d.starter_confirmed else ['Starter is not confirmed.']
    return {'player':d.player,'status':status,'baseline_k_pct':round(base*100,2),'adjusted_k_pct':round(adj*100,2),'skill_weights':weights,'lineup_multiplier':lm,'recent_change_multiplier':rm,'expected_batters_faced':bf,'workload_floor':floor,'workload_ceiling':ceil,'workload_sd':sd,'projected_strikeouts':sim['projected_strikeouts'],'ladder_probabilities':sim['ladder_probabilities'],'confidence':conf,'warnings':warnings,'reasons':{'skill':sr,'lineup':lr,'recent_change':rr,'workload':wr}}
