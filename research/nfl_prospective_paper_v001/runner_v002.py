"""Compatibility wrapper: preserve v0.01 frozen rules and existing ledger."""
import importlib.util
from pathlib import Path
import runner as legacy
from identity import player_identity

_original_participant=legacy.participant

def participant(m):
    key,reason=player_identity(m)
    _,name=_original_participant(m)
    return key,name

legacy.participant=participant

if __name__=='__main__':
    legacy.main()
