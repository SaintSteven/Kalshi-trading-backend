from dataclasses import dataclass
from automatic_input_builder import automatic_input
from feature_engineering import build_feature_record
from mlb_data_collector import collect_automatic_pitcher_data
from projection_engine import build_full_projection

@dataclass
class PipelineResult:
    raw_inputs:list
    features:list
    projections:dict
    excluded:list

async def run_research_pipeline(target_date=None):
    raw=await collect_automatic_pitcher_data(target_date)
    features=[]; projections={}; excluded=[]
    for item in raw:
        if item.get("data_warnings"):
            excluded.append({"player":item.get("player"),"reasons":item["data_warnings"]})
            continue
        feat=build_feature_record(item)
        features.append(feat)
        proj=build_full_projection(automatic_input(item))
        proj["features"]=feat
        projections[item["player"].strip().lower()]=proj
    return PipelineResult(raw,features,projections,excluded)
