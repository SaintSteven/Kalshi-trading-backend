from backtest_models import HistoricalStart

def historical_start_from_projection(player,game_date,actual_strikeouts,projection):
    return HistoricalStart(player=player,game_date=game_date,actual_strikeouts=actual_strikeouts,projected_strikeouts=projection["projected_strikeouts"],ladder_probabilities=projection["ladder_probabilities"],features=projection.get("features",{}))
