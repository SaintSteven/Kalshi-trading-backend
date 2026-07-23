from typing import Literal
from pydantic import BaseModel, Field

class DataQuality(BaseModel):
    pitcher_skill: Literal["HIGH","MEDIUM","LOW"]="MEDIUM"
    lineup: Literal["HIGH","MEDIUM","LOW"]="MEDIUM"
    workload: Literal["HIGH","MEDIUM","LOW"]="MEDIUM"
    recent_change: Literal["HIGH","MEDIUM","LOW"]="LOW"

class PitcherModelInput(BaseModel):
    player:str
    pitcher_hand:Literal["R","L"]="R"
    starter_confirmed:bool=True
    season_k_pct:float=Field(ge=0,le=1)
    career_k_pct:float=Field(ge=0,le=1)
    recent_k_pct:float=Field(ge=0,le=1)
    season_batters_faced:int=Field(default=0,ge=0)
    career_batters_faced:int=Field(default=0,ge=0)
    recent_batters_faced:int=Field(default=0,ge=0)
    expected_batters_faced:float=Field(gt=0,le=45)
    workload_floor:int=Field(default=18,ge=1,le=45)
    workload_ceiling:int=Field(default=28,ge=1,le=45)
    recent_pitch_counts:list[int]=[]
    opponent_lineup_k_pct:float=Field(ge=0,le=1)
    league_k_pct:float=Field(default=.225,gt=0,le=1)
    lineup_confirmed:bool=False
    velocity_change_mph:float=Field(default=0,ge=-5,le=5)
    whiff_rate_change:float=Field(default=0,ge=-.2,le=.2)
    pitch_mix_change_supported:bool=False
    starter_role:Literal["NORMAL","LIMITED","OPENER","BULK","RETURNING_FROM_INJURY"]="NORMAL"
    data_quality:DataQuality=DataQuality()
    notes:str=""
