"""Сборщик черновых сценариев day1.json / day2.json.

Поезда и сбои прописаны вручную ниже. Плановые времена прибытия считаются по
номинальной физике (та же функция, что в движке) + запас 10% + 10 мин —
как реальный график с резервом. Запуск:  python -m scenarios.build
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from sim.model import fmt_time, load_scenario, nominal_run_s, parse_time

HERE = Path(__file__).parent

BASE = {
    "seed": 7,
    "params": {"route_setup_min": 1, "approach_lead_min": 3, "segment_interval_min": 2,
               "running_noise": 0.03},
    "train_types": {
        "passenger": {"speed_kmh": 80, "accel_min": 2, "brake_min": 1.5, "weight": 3},
        "container": {"speed_kmh": 65, "accel_min": 5, "brake_min": 3, "weight": 1.5},
        "freight":   {"speed_kmh": 55, "accel_min": 7, "brake_min": 4, "weight": 1},
    },
    "nodes": [
        {"id": "EAST", "name": "на Шалкар", "type": "boundary", "x": 1080, "y": 200},
        {"id": "R5", "name": "Р-5", "type": "siding", "x": 990, "y": 194,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 850}]},
        {"id": "R4", "name": "Р-4", "type": "siding", "x": 905, "y": 206,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 850}]},
        {"id": "R3", "name": "Р-3", "type": "siding", "x": 820, "y": 196,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 1100}, {"id": "3", "length_m": 850}]},
        # узел Опорная: сюда с севера примыкает линия на Атырау
        {"id": "OPOR", "name": "Опорная", "type": "junction", "x": 735, "y": 212,
         "tracks": [{"id": "1", "length_m": 1200}, {"id": "2", "length_m": 1150}, {"id": "3", "length_m": 1100},
                    {"id": "4", "length_m": 1050}, {"id": "5", "length_m": 900}]},
        {"id": "R6", "name": "Р-6", "type": "siding", "x": 770, "y": 118,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 900}]},
        {"id": "ATY", "name": "на Атырау", "type": "boundary", "x": 805, "y": 36},
        {"id": "R2", "name": "Р-2", "type": "siding", "x": 650, "y": 200,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 850}]},
        {"id": "R1", "name": "Р-1", "type": "siding", "x": 565, "y": 206,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 850}]},
        # узел Бейнеу: четыре направления — Шалкар, Узбекистан, Актау, Курык
        {"id": "JUNC", "name": "Бейнеу", "type": "junction", "x": 480, "y": 200,
         "tracks": [{"id": str(i), "length_m": 1100 + (50 if i < 3 else 0)} for i in range(1, 7)]},
        {"id": "R7", "name": "Р-7", "type": "siding", "x": 560, "y": 292,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 900}]},
        {"id": "UZB", "name": "на Узбекистан", "type": "boundary", "x": 630, "y": 354},
        # ветка на Актау; от Сай-Утёса — тупик на цементный завод
        {"id": "SAY", "name": "Сай-Утёс", "type": "junction", "x": 400, "y": 135,
         "tracks": [{"id": "1", "length_m": 1200}, {"id": "2", "length_m": 1150},
                    {"id": "3", "length_m": 1100}, {"id": "4", "length_m": 950}]},
        {"id": "CEM", "name": "Цемзавод", "type": "industrial", "x": 455, "y": 62, "terminal_clear_min": 30,
         "tracks": [{"id": "1", "length_m": 900}, {"id": "2", "length_m": 900}, {"id": "3", "length_m": 850}]},
        {"id": "RA", "name": "Р-А", "type": "siding", "x": 315, "y": 100,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 1100}, {"id": "3", "length_m": 850}]},
        {"id": "AKTAU", "name": "Актау", "type": "port", "x": 210, "y": 100, "terminal_clear_min": 20,
         "tracks": [{"id": "M1", "length_m": 900}, {"id": "M2", "length_m": 900}]
                   + [{"id": f"P{i}", "length_m": 1100, "kind": "port"} for i in range(1, 7)]},
        # ветка на Курык; от Жетыбая — тупик на нефтяной Жанаозен
        {"id": "JET", "name": "Жетыбай", "type": "junction", "x": 400, "y": 268,
         "tracks": [{"id": "1", "length_m": 1150}, {"id": "2", "length_m": 1100},
                    {"id": "3", "length_m": 1050}, {"id": "4", "length_m": 900}]},
        {"id": "ZHAN", "name": "Жанаозен", "type": "industrial", "x": 440, "y": 354, "terminal_clear_min": 30,
         "tracks": [{"id": "1", "length_m": 900}, {"id": "2", "length_m": 900},
                    {"id": "3", "length_m": 850}, {"id": "4", "length_m": 850}]},
        {"id": "RK", "name": "Р-К", "type": "siding", "x": 315, "y": 302,
         "tracks": [{"id": "1", "length_m": 1100}, {"id": "2", "length_m": 900}]},
        {"id": "KURYK", "name": "Курык", "type": "port", "x": 210, "y": 302, "terminal_clear_min": 20,
         "tracks": [{"id": "M1", "length_m": 900}]
                   + [{"id": f"P{i}", "length_m": 1100, "kind": "port"} for i in range(1, 5)]},
    ],
    "segments": [
        {"a": "EAST", "b": "R5", "length_km": 22},
        {"a": "R5", "b": "R4", "length_km": 28},
        {"a": "R4", "b": "R3", "length_km": 26},
        {"a": "R3", "b": "OPOR", "length_km": 14},
        {"a": "OPOR", "b": "R6", "length_km": 24},
        {"a": "R6", "b": "ATY", "length_km": 20},
        {"a": "OPOR", "b": "R2", "length_km": 16},
        {"a": "R2", "b": "R1", "length_km": 27},
        {"a": "R1", "b": "JUNC", "length_km": 25},
        {"a": "JUNC", "b": "R7", "length_km": 26},
        {"a": "R7", "b": "UZB", "length_km": 22},
        {"a": "JUNC", "b": "SAY", "length_km": 15},
        {"a": "SAY", "b": "RA", "length_km": 15},
        {"a": "SAY", "b": "CEM", "length_km": 10, "max_speed_kmh": 40},
        {"a": "RA", "b": "AKTAU", "length_km": 24, "max_speed_kmh": 50},
        {"a": "JUNC", "b": "JET", "length_km": 14},
        {"a": "JET", "b": "RK", "length_km": 14},
        {"a": "JET", "b": "ZHAN", "length_km": 30, "max_speed_kmh": 60},
        {"a": "RK", "b": "KURYK", "length_km": 20, "max_speed_kmh": 50},
    ],
    "ports": [
        {"node": "AKTAU", "berths": 1, "processing_min": 60, "load_min_per_wagon": 3},
        {"node": "KURYK", "berths": 1, "processing_min": 60, "load_min_per_wagon": 3},
    ],
}

PORTS = ["AKTAU", "KURYK"]


def P(id, orig, dest, appear, **kw):
    return {"id": id, "type": "passenger", "origin": orig, "destination": dest, "appear": appear,
            "length_m": 450, "wagons": 14, **kw}


def C(id, appear, **kw):
    return {"id": id, "type": "container", "origin": "EAST", "destinations": PORTS, "appear": appear,
            "length_m": 1000, "wagons": 28, "cargo": "ferry", **kw}


def F(id, orig, dest, appear, **kw):
    return {"id": id, "type": "freight", "origin": orig, "destination": dest, "appear": appear,
            "length_m": 800, "wagons": 45, **kw}


def throat_sides(raw: dict) -> dict:
    """С какой горловины станции примыкает каждый перегон: (узел, сосед) -> 'east' | 'west'.
    Ось станции направлена от соседа, через которого станция связана с восточной границей;
    перегон «вперёд» по оси примыкает к западной горловине, «назад» — к восточной.
    Та же геометрия, что на живой карте (frontend/src/world.js)."""
    import math as _m
    nodes = {n["id"]: n for n in raw["nodes"]}
    adj = {n: [] for n in nodes}
    for sg in raw["segments"]:
        adj[sg["a"]].append(sg["b"])
        adj[sg["b"]].append(sg["a"])
    root = next(n["id"] for n in raw["nodes"] if n["type"] == "boundary")
    parent, order = {root: None}, [root]
    for u in order:
        for v in sorted(adj[u]):
            if v not in parent:
                parent[v] = u
                order.append(v)

    def unit(a, b):
        dx, dy = nodes[b]["x"] - nodes[a]["x"], nodes[b]["y"] - nodes[a]["y"]
        ln = _m.hypot(dx, dy) or 1
        return dx / ln, dy / ln
    out = {}
    for u in nodes:
        ax = unit(parent[u], u) if parent[u] else unit(u, adj[u][0])
        for v in adj[u]:
            d = unit(u, v)
            out[(u, v)] = "west" if ax[0] * d[0] + ax[1] * d[1] > 0 else "east"
    return out


def straight(raw: dict, path: list, sides: dict | None = None) -> bool:
    """Поезд проходит каждую станцию насквозь: въезжает с одной горловины, выезжает с другой."""
    sides = sides or throat_sides(raw)
    return all(sides[(b, a)] != sides[(b, c)] for a, b, c in zip(path, path[1:], path[2:]))


def route(raw: dict, origin: str, dest: str) -> list:
    sc = load_scenario({**raw, "trains": [], "disruptions": [], "ferries": [], "dispatcher": {}})
    path, cur, prev = [origin], origin, None
    while cur != dest:
        nxt = sc.next_hops(cur, [dest], prev)[0]
        path.append(nxt)
        prev, cur = cur, nxt
    return path


def _ceil5(sec):
    return int(math.ceil(sec / 300) * 300)


def plan(raw: dict) -> dict:
    """Проставляет not_before пассажирским остановкам и planned_arrival — по номинальной физике."""
    sc = load_scenario({**raw, "trains": [], "disruptions": [], "ferries": [], "dispatcher": {}})
    for t in raw["trains"]:
        tt = sc.types[t["type"]]
        dests = t.get("destinations") or [t["destination"]]
        best = None
        for dest in dests:
            path, cur, prev = [t["origin"]], t["origin"], None
            while cur != dest:
                nxt = sc.next_hops(cur, [dest], prev)[0]
                path.append(nxt)
                prev, cur = cur, nxt
            clock = parse_time(t["appear"])
            if not sc.nodes[t["origin"]].is_boundary:
                clock += tt.accel_s
            stops = {s["node"]: s for s in t.get("stops", [])}
            for a, b in zip(path, path[1:]):
                clock += nominal_run_s(tt, sc.seg_between(a, b))
                if b in stops:
                    clock += tt.brake_s + int(float(stops[b].get("min_dwell_min", 0)) * 60)
                    stops[b]["not_before"] = fmt_time(_ceil5(clock))
                    clock = _ceil5(clock) + tt.accel_s
            clock += tt.brake_s
            if best is None or clock < best:
                best = clock
        travel = best - parse_time(t["appear"])
        t["planned_arrival"] = fmt_time(_ceil5(parse_time(t["appear"]) + travel * 1.10 + 600))
    return raw


def day1() -> dict:
    raw = copy.deepcopy(BASE)
    raw.update(id="day1", name="День 1 — спокойный, мелкие сбои", horizon="30:00")
    raw["trains"] = [
        P("P101", "EAST", "AKTAU", "05:00", stops=[{"node": "JUNC", "min_dwell_min": 15}]),
        P("P102", "AKTAU", "EAST", "07:00", stops=[{"node": "AKTAU", "not_before": "07:00"},
                                                   {"node": "JUNC", "min_dwell_min": 15}]),
        P("P103", "EAST", "AKTAU", "15:30", stops=[{"node": "JUNC", "min_dwell_min": 15}]),
        P("P104", "AKTAU", "EAST", "17:30", stops=[{"node": "AKTAU", "not_before": "17:30"},
                                                   {"node": "JUNC", "min_dwell_min": 15}]),
        C("C201", "01:00", target_ferry="FA1"),
        C("C202", "03:30", target_ferry="FA1"),
        C("C203", "06:30", target_ferry="FK1"),
        C("C204", "09:00", appear_actual="09:40", target_ferry="FK1",
          forecasts=[{"at": "07:00", "eta": "09:25"}]),
        C("C205", "11:40", target_ferry="FA2"),
        C("C206", "14:20", target_ferry="FA2"),
        C("C207", "17:00", target_ferry="FK2"),
        C("C208", "19:40", target_ferry="FA3"),
        F("F301", "EAST", "JUNC", "07:30"),
        F("F303", "EAST", "AKTAU", "12:30"),
        F("F302", "KURYK", "EAST", "04:00"),
        F("F304", "AKTAU", "EAST", "08:30"),
        F("F306", "JUNC", "EAST", "10:00"),
        F("F308", "KURYK", "EAST", "13:00"),
        F("F310", "AKTAU", "EAST", "16:00"),
        F("F312", "JUNC", "EAST", "18:30"),
        F("F314", "AKTAU", "EAST", "21:00"),
        F("F316", "KURYK", "EAST", "20:00"),
        F("F318", "JUNC", "EAST", "02:30"),
        C("C209", "08:10", origin="UZB", target_ferry="FA2"),
        C("C210", "13:20", origin="ATY", target_ferry="FA3"),
        F("F601", "ZHAN", "ATY", "05:40", length_m=820, wagons=45),
        F("F602", "ATY", "ZHAN", "10:30", length_m=820, wagons=45),
        F("F603", "AKTAU", "UZB", "11:30"),
    ]
    raw["ferries"] = [
        {"id": "FA1", "port": "AKTAU", "capacity_wagons": 56, "planned_arrival": "06:00",
         "actual_arrival": "06:20", "eta_updates": [{"at": "04:00", "eta": "06:20"}]},
        {"id": "FA2", "port": "AKTAU", "capacity_wagons": 56, "planned_arrival": "14:00"},
        {"id": "FA3", "port": "AKTAU", "capacity_wagons": 56, "planned_arrival": "22:00"},
        {"id": "FK1", "port": "KURYK", "capacity_wagons": 56, "planned_arrival": "10:00"},
        {"id": "FK2", "port": "KURYK", "capacity_wagons": 56, "planned_arrival": "20:00"},
    ]
    raw["disruptions"] = [
        {"id": "D1", "kind": "loco_failure", "train": "F306", "at": "10:50",
         "duration_min": 50, "announced_duration_min": 30},
        {"id": "D2", "kind": "maintenance_window", "segment": "R4-R3", "start": "13:00",
         "duration_min": 90, "announce_at": "00:00"},
    ]
    raw["dispatcher"] = {"directives": [
        {"train": "C203", "at": "JUNC", "route": "SAY"},
        {"train": "F304", "at": "R1", "wait_for": "C204"},
    ]}
    return plan(raw)


def day2() -> dict:
    raw = copy.deepcopy(BASE)
    raw.update(id="day2", name="День 2 — шторм на Каспии, паромы задержаны", horizon="36:00")
    raw["trains"] = [
        P("P101", "EAST", "AKTAU", "05:00", stops=[{"node": "JUNC", "min_dwell_min": 15}]),
        P("P102", "AKTAU", "EAST", "07:00", stops=[{"node": "AKTAU", "not_before": "07:00"},
                                                   {"node": "JUNC", "min_dwell_min": 15}]),
        P("P103", "EAST", "AKTAU", "15:30", stops=[{"node": "JUNC", "min_dwell_min": 15}]),
        P("P104", "AKTAU", "EAST", "17:30", stops=[{"node": "AKTAU", "not_before": "17:30"},
                                                   {"node": "JUNC", "min_dwell_min": 15}]),
        *[C(f"C2{i:02d}", a, target_ferry=tf) for i, (a, tf) in enumerate([
            ("00:30", "FA1"), ("02:30", "FA1"), ("04:30", "FK1"), ("06:30", "FK1"),
            ("08:30", "FA2"), ("10:30", "FA2"), ("12:30", "FK2"), ("14:30", "FK2"),
            ("16:30", "FA3"), ("18:30", "FA3")], start=1)],
        F("F302", "KURYK", "EAST", "04:00"),
        F("F304", "AKTAU", "EAST", "08:30"),
        F("F306", "JUNC", "EAST", "10:00"),
        F("F308", "KURYK", "EAST", "13:00"),
        F("F310", "AKTAU", "EAST", "16:00"),
        F("F312", "JUNC", "EAST", "18:30"),
        F("F318", "JUNC", "EAST", "02:30"),
        C("C211", "07:40", origin="UZB", target_ferry="FK1"),
        F("F601", "ZHAN", "UZB", "06:20", length_m=820, wagons=45),
        F("F602", "KURYK", "ATY", "12:40"),
    ]
    raw["ferries"] = [
        {"id": "FA1", "port": "AKTAU", "capacity_wagons": 56, "planned_arrival": "06:00",
         "actual_arrival": "17:20",
         "eta_updates": [{"at": "03:00", "eta": "09:00"}, {"at": "07:30", "eta": "13:00"},
                         {"at": "11:00", "eta": "17:00"}]},
        {"id": "FA2", "port": "AKTAU", "capacity_wagons": 56, "planned_arrival": "14:00",
         "actual_arrival": "26:00",
         "eta_updates": [{"at": "07:30", "eta": "20:00"}, {"at": "13:00", "eta": "25:30"}]},
        {"id": "FA3", "port": "AKTAU", "capacity_wagons": 56, "planned_arrival": "22:00",
         "actual_arrival": "31:00", "eta_updates": [{"at": "13:00", "eta": "30:00"}]},
        {"id": "FK1", "port": "KURYK", "capacity_wagons": 56, "planned_arrival": "10:00",
         "actual_arrival": "21:30",
         "eta_updates": [{"at": "06:00", "eta": "15:00"}, {"at": "12:00", "eta": "21:00"}]},
        {"id": "FK2", "port": "KURYK", "capacity_wagons": 56, "planned_arrival": "20:00",
         "actual_arrival": "28:00", "eta_updates": [{"at": "12:00", "eta": "27:00"}]},
    ]
    raw["disruptions"] = [
        {"id": "D1", "kind": "loco_failure", "train": "C206", "at": "12:10",
         "duration_min": 70, "announced_duration_min": 40},
    ]
    raw["dispatcher"] = {"directives": [
        {"train": "C205", "at": "JUNC", "route": "SAY"},
        {"train": "C206", "at": "JUNC", "route": "SAY"},
    ]}
    return plan(raw)


def main():
    for build in (day1, day2):
        raw = build()
        load_scenario(raw)  # валидация
        out = HERE / f"{raw['id']}.json"
        out.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print("записан", out.name, "поездов:", len(raw["trains"]))


if __name__ == "__main__":
    main()
