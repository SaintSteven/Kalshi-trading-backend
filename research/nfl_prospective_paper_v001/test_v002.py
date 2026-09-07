import unittest, tempfile
from pathlib import Path
import pandas as pd
from identity import player_identity
from receiving_feed import build

class TestPilot(unittest.TestCase):
 def test_player_identity(self):
  self.assertEqual(player_identity({'ticker':'KXNFLRECYDS-26SEP14DENKC-KCXWORTHY1-90','primary_participant_key':'football_player'})[0],'KCXWORTHY1')
  self.assertEqual(player_identity({'ticker':'BAD','primary_participant_key':'football_player'})[0],'')
 def test_model_no_market_probability(self):
  m=pd.DataFrame([dict(market_ticker='T',game_id='G',prop_family='receiving_yards',yes_ask='.01')])
  p=pd.DataFrame([dict(game_id='G',player_id='P',threshold='50',fair_yes='.6',model_version='v0.1b',generated_at='2026-09-07T12:00:00Z',qc_status='PASS')])
  mapping=pd.DataFrame([dict(market_ticker='T',game_id='G',player_id='P',threshold='50')])
  self.assertAlmostEqual(build(m,p,mapping).iloc[0].fair_no,.4)
  self.assertEqual(build(m,p,mapping.iloc[0:0]).iloc[0].qc_status,'UNAVAILABLE')
  self.assertEqual(build(m,p.assign(qc_status='FAIL'),mapping).iloc[0].qc_status,'UNAVAILABLE')
if __name__=='__main__':unittest.main()
