from projection_inputs import PitcherModelInput
ROLE={"NORMAL":1,"LIMITED":.85,"OPENER":.45,"BULK":.80,"RETURNING_FROM_INJURY":.72}

def build_workload(d:PitcherModelInput):
    reasons=[]; exp=d.expected_batters_faced*ROLE[d.starter_role]
    if d.recent_pitch_counts:
        avg=sum(d.recent_pitch_counts)/len(d.recent_pitch_counts)
        if avg<75:exp*=.90;reasons.append('Recent pitch counts imply shorter leash.')
        elif avg>=95:exp*=1.03;reasons.append('Recent pitch counts support full leash.')
    else:reasons.append('No recent pitch-count history supplied.')
    floor=min(d.workload_floor,d.workload_ceiling); ceil=max(d.workload_floor,d.workload_ceiling); exp=max(floor,min(ceil,exp)); sd=max(1.5,(ceil-floor)/4)
    if d.starter_role!='NORMAL':sd+=1;reasons.append(f'Role {d.starter_role} increases workload uncertainty.')
    return round(exp,2),floor,ceil,round(sd,2),reasons
