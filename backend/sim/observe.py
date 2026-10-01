"""Наблюдение — единственная «дверь» от правды симулятора к политике.

Честный режим (honest): только то, что видел бы диспетчер в этот момент:
  * положения поездов, занятость путей и перегонов (это видно на табло);
  * расписание и прогнозы, которые уже ОБЪЯВЛЕНЫ к моменту t;
  * сбои — только после того как случились, и с объявленной (не фактической) длительностью;
  * время хода — номинальное, без шума.
Никогда не попадают: фактическое время появления поезда, фактический приход парома,
будущие сбои, необъявленные окна, фактическая длительность ремонта, шум времени хода.

Ретроспективный режим (oracle) явно добавляет раздел `oracle` со всей правдой.
Результат — свежий словарь из примитивов: политика не может дотянуться до движка.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .model import fmt_time

if TYPE_CHECKING:
    from .engine import Simulator


def _latest(forecasts, t, default):
    val = default
    for f in forecasts:
        if f.at <= t:
            val = f.eta
    return val


def _train_obs(sim: "Simulator", tr) -> dict:
    sp, t = tr.spec, sim.t
    o = {
        "id": sp.id, "type": sp.type, "weight": tr.tt.weight, "length_m": sp.length_m,
        "wagons": sp.wagons, "origin": sp.origin, "destinations": list(sp.destinations),
        "planned_appear": sp.appear_planned, "planned_arrival": sp.planned_arrival,
        "stops": [{"node": s.node, "min_dwell_s": s.min_dwell_s, "not_before": s.not_before}
                  for s in sp.stops],
        "cargo": sp.cargo, "target_ferry": sp.target_ferry,
        "status": tr.status, "node": tr.node, "track": tr.track, "stopped": tr.stopped,
        "visited": list(tr.visited), "came_from": tr.prev_node,
        "arrived_at": tr.arrived_at, "final_node": tr.final_node,
        "failure": ({"since": tr.failure["since"], "est_until": tr.failure["est_until"]}
                    if tr.failure else None),
    }
    if tr.status == "pending":
        o["forecast_appear"] = _latest(sp.forecasts, t, sp.appear_planned)
        o["waiting_for_track"] = tr.waiting_track
    if tr.status == "at_node":
        ready = tr.dwell_ready
        if tr.failure:
            ready = max(ready, tr.failure["est_until"])
        o["since"] = tr.since
        o["ready_at_est"] = ready
        o["departure_set"] = tr.move["to"] if tr.move else None
    if tr.status in ("running", "approaching") and tr.run:
        r = tr.run
        eta = r.est_head_end + (tr.tt.brake_s if r.stopping else 0)
        o["run"] = {"segment": r.seg, "from": r.frm, "to": r.to, "entered": r.entered,
                    "eta": max(t, eta), "through_next": r.through_next,
                    "stopping": r.stopping, "awaiting_decision": tr.awaiting}
    if tr.status == "passing" and tr.move:
        o["departure_set"] = tr.move["to"]
    if tr.port:
        o["port"] = {"node": tr.port["node"], "remaining": tr.port["remaining"],
                     "available_at": tr.port["available_at"]}
    return o


def build_observation(sim: "Simulator") -> dict:
    sc, t = sim.sc, sim.t
    nodes = []
    for n in sc.nodes.values():
        nodes.append({
            "id": n.id, "type": n.type,
            "tracks": [{"id": tr.id, "length_m": tr.length_m, "kind": tr.kind,
                        "occupant": sim.tracks[(n.id, tr.id)].occupant,
                        "reserved": sim.tracks[(n.id, tr.id)].reserved} for tr in n.tracks],
        })
    segments = []
    for s in sc.segments.values():
        ss = sim.segs[s.id]
        known_windows = []
        for d in sc.disruptions:
            if d.kind != "maintenance_window" or d.segment != s.id:
                continue
            w = sim.windows[d.id]
            if w["state"] in ("active", "done"):
                known_windows.append({"start": w["start"], "end": w["end"], "state": w["state"]})
            elif w["state"] == "pending":
                known_windows.append({"start": None, "end": None, "state": "pending"})
            elif d.announce_at is not None and d.announce_at <= t:
                known_windows.append({"start": d.at, "end": d.at + d.duration_s, "state": "scheduled"})
        segments.append({
            "id": s.id, "a": s.a, "b": s.b, "length_km": s.length_km,
            "occupant": ss.occupant, "reserved": ss.reserved,
            "outage": ({"since": ss.outage["since"], "est_until": ss.outage["est_until"]}
                       if ss.outage else None),
            "windows": known_windows,
        })
    ferries = []
    for fid, spec in sc.ferries.items():
        fs = sim.ferries[fid]
        f = {"id": fid, "port": spec.port, "capacity": spec.capacity, "status": fs.status,
             "planned_arrival": spec.planned_arrival, "loaded": fs.loaded,
             "arrived": fs.arrived, "deadline": fs.deadline, "departed": fs.departed}
        if fs.status == "expected":
            f["eta"] = _latest(spec.eta_updates, t, spec.planned_arrival)
        ferries.append(f)

    obs = {
        "t": t, "t_str": fmt_time(t), "horizon": sc.horizon, "mode": sim.mode,
        "params": {"route_setup_s": sc.params.route_setup_s,
                   "approach_lead_s": sc.params.approach_lead_s,
                   "segment_interval_s": sc.params.segment_interval_s},
        "trains": [_train_obs(sim, sim.trains[tid]) for tid in sorted(sim.trains)],
        "nodes": nodes, "segments": segments, "ferries": ferries,
        "ferry_order": {k: list(v) for k, v in sim.ferry_order.items()},
        "known_disruptions": [dict(f) for f in sim.revealed_failures],
        "legal": sim.legal_actions(),
        "last_rejections": [dict(r) for r in sim._last_rejections],
    }
    if sim.mode == "oracle":
        obs["oracle"] = {
            "appear_actual": {tid: tr.appear_at for tid, tr in sim.trains.items() if tr.status == "pending"},
            "ferry_actual_arrival": {fid: s.actual_arrival for fid, s in sc.ferries.items()
                                     if sim.ferries[fid].status == "expected"},
            "future_disruptions": [
                {"id": d.id, "kind": d.kind, "at": d.at, "duration_s": d.duration_s,
                 "train": d.train, "segment": d.segment}
                for d in sc.disruptions if d.at >= t],
            "running_noise_note": "фактическое время хода не раскрывается и оракулу",
        }
    return obs
