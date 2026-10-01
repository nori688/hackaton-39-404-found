import copy
import json
from pathlib import Path

from sim import Simulator, load_scenario

SCEN = Path(__file__).resolve().parent.parent / "scenarios"

TYPES = {
    "passenger": {"speed_kmh": 80, "accel_min": 2, "brake_min": 1.5, "weight": 3},
    "container": {"speed_kmh": 65, "accel_min": 5, "brake_min": 3, "weight": 1.5},
    "freight": {"speed_kmh": 55, "accel_min": 7, "brake_min": 4, "weight": 1},
}


def tiny(trains, disruptions=(), s1=(1100, 850), s2=(1100, 850), horizon="12:00", noise=0.03):
    """Мини-участок  W(граница) — S1 — S2 — E(граница)."""
    return {
        "id": "tiny", "seed": 3, "horizon": horizon,
        "params": {"route_setup_min": 1, "approach_lead_min": 3, "segment_interval_min": 2,
                   "running_noise": noise},
        "train_types": TYPES,
        "nodes": [
            {"id": "W", "type": "boundary"},
            {"id": "S1", "type": "siding", "tracks": [{"id": str(i + 1), "length_m": l} for i, l in enumerate(s1)]},
            {"id": "S2", "type": "siding", "tracks": [{"id": str(i + 1), "length_m": l} for i, l in enumerate(s2)]},
            {"id": "E", "type": "boundary"},
        ],
        "segments": [{"a": "W", "b": "S1", "length_km": 20}, {"a": "S1", "b": "S2", "length_km": 25},
                     {"a": "S2", "b": "E", "length_km": 20}],
        "trains": list(trains), "disruptions": list(disruptions), "dispatcher": {},
    }


def train(id, orig, dest, appear, type="freight", length=800, **kw):
    return {"id": id, "type": type, "origin": orig, "destination": dest, "appear": appear,
            "length_m": length, "wagons": 40, "planned_arrival": kw.pop("planned", "11:00"), **kw}


class Func:
    name = "func"

    def __init__(self, fn):
        self.fn = fn

    def decide(self, obs):
        return self.fn(obs) or []


class Recorder:
    """Оборачивает политику и запоминает все наблюдения до cutoff."""
    name = "recorder"

    def __init__(self, inner, cutoff=10**9):
        self.inner, self.cutoff, self.seen = inner, cutoff, []

    def decide(self, obs):
        if obs["t"] < self.cutoff:
            self.seen.append(json.dumps(obs, sort_keys=True, ensure_ascii=False))
        return self.inner.decide(obs)


def all_legal(obs):
    """Наивная политика: отправить всё, что можно (по одному на перегон)."""
    out, used = [], set()
    for a in obs["legal"]:
        if a["segment"] not in used:
            used.add(a["segment"])
            out.append({"type": "dispatch", "train": a["train"], "next": a["next"]})
    return out


def run(raw, policy, **kw):
    return Simulator(load_scenario(copy.deepcopy(raw)), **kw).run(policy)


def day(name):
    return json.loads((SCEN / f"{name}.json").read_text(encoding="utf-8"))
