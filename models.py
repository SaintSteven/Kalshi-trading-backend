from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from projection_inputs import PitcherModelInput

class Market(BaseModel):
    sport:str='MLB';market_type:str='Strikeouts';ticker:str;event_ticker:str|None=None;title:str;player:str;threshold:str
    yes_bid_cents:int|None=None;yes_ask_cents:int|None=None;no_bid_cents:int|None=None;no_ask_cents:int|None=None
    volume:float|None=None;liquidity_dollars:float|None=None;close_time:datetime|None=None;tradable:bool=False;tradability_reasons:list[str]=[]
class PaperCardRequest(BaseModel):
    bankroll:float=Field(default=100,ge=0);already_committed_today:float=Field(default=0,ge=0);max_bet:float=Field(default=1,ge=0);date:str|None=None;minimum_edge_points:float=Field(default=5,ge=0,le=50);pitchers:list[PitcherModelInput]=[]
class PaperRecommendation(BaseModel):
    ticker:str;player:str;threshold:str;side:Literal['YES','NO','NONE'];market_price_cents:int|None;fair_probability:float|None;raw_edge_points:float|None;adjusted_edge_points:float|None;projected_strikeouts:float|None;baseline_k_pct:float|None;adjusted_k_pct:float|None;expected_batters_faced:float|None;workload_floor:int|None;workload_ceiling:int|None;confidence:dict;decision:Literal['MODEL EDGE','WATCH','PASS','INSUFFICIENT DATA'];suggested_stake:float;reasons:list[str];warnings:list[str]
class PaperCardResponse(BaseModel):
    status:str;requested_slate:str;selected_slate:str;slate_shifted:bool;markets_reviewed:int;projections_matched:int;recommendations:list[PaperRecommendation];message:str
class MarketSummary(BaseModel):
    requested_slate:str;selected_slate:str;slate_shifted:bool;total_markets:int;tradable_markets:int;hidden_markets:int;pitchers:int
