#!/usr/bin/env python3
import pandas as pd
p=pd.read_csv('/tmp/nfl-smoke/projections.csv').fillna('')
f=pd.read_csv('/tmp/nfl-smoke/receiving_fair_values.csv').fillna('')
assert len(p)>0, 'no projections generated'
assert set(p['model_version'])=={'NFL-REC-RIDGE-NORMALSD-ISO-v1'}
assert p['context_flags'].astype(str).str.contains('WEEK1_NO_CURRENT_SEASON_HISTORY_UNVALIDATED').any()
assert (f['qc_status']=='MODEL_AVAILABLE').any()
print({
 'projection_rows':len(p),
 'model_available':int((f.qc_status=='MODEL_AVAILABLE').sum()),
 'week1_flagged':int(p.context_flags.astype(str).str.contains('WEEK1_NO_CURRENT_SEASON_HISTORY_UNVALIDATED').sum()),
 'warn_upstream':int((p.qc_status=='WARN').sum()),
 'pass_upstream':int((p.qc_status=='PASS').sum()),
 'model_versions':sorted(p.model_version.unique().tolist())
})
