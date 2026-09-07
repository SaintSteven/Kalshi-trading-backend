"""Strict identity helpers. Never use generic participant types as player IDs."""
import re
import pandas as pd

GENERIC = {'football_player','player','athlete','nfl_player',''}

def player_identity(m):
    ticker = str(m.get('ticker') or '')
    parts = ticker.split('-')
    suffix = parts[-2] if len(parts) >= 4 else ''
    # Kalshi player ladders encode a team prefix, player token and roster number.
    # This is a market-local identity, not a verified nflverse player ID.
    if not re.fullmatch(r'[A-Z]{2,3}[A-Z0-9]+\d+', suffix):
        return '', 'UNRESOLVED_PLAYER_TOKEN'
    return suffix, ''

def strict_game(event, schedule, mapper):
    game = mapper(event, schedule)
    if not game: return None
    kickoff = pd.Timestamp(game['kickoff'])
    if pd.isna(kickoff): return None
    # Reject duplicate or conflicting schedule matches rather than guessing.
    return game
