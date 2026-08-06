from __future__ import annotations
import csv,json
from pathlib import Path
from typing import Any

def load_feed(path:Path,fmt:str)->list[dict[str,Any]]:
    if fmt=="csv":
        with path.open("r",encoding="utf-8-sig",newline="") as handle:
            reader=csv.DictReader(handle)
            if reader.fieldnames is None: raise ValueError("CSV has no header")
            return [dict(r) for r in reader]
    if fmt=="json":
        with path.open("r",encoding="utf-8") as handle: payload=json.load(handle)
        if isinstance(payload,dict):
            for key in ("products","items","records","data"):
                if key in payload:
                    payload=payload[key];break
        if not isinstance(payload,list) or not all(isinstance(x,dict) for x in payload):
            raise ValueError("JSON must contain a list of objects")
        return [dict(x) for x in payload]
    raise ValueError(f"Unsupported format: {fmt}")

def nested_get(record:dict[str,Any],path:str):
    value:Any=record
    for part in path.split("."):
        if not isinstance(value,dict) or part not in value:return None
        value=value[part]
    return value
