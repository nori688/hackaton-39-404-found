"""Модель сценария: инфраструктура, поезда, паромы, сбои.

Здесь только данные и чистые функции (время хода, шум). Логики движения нет.
В JSON длительности задаются в минутах (`*_min`), моменты — строкой "HH:MM"
(часы могут быть >= 24 — это следующие сутки). Внутри всё в целых секундах.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path


class ScenarioError(ValueError):
    pass


def parse_time(v) -> int:
    if not isinstance(v, str):
        raise ScenarioError(f"время должно быть строкой 'HH:MM', получено {v!r}")
    parts = v.split(":")
    if len(parts) not in (2, 3):
        raise ScenarioError(f"неверный формат времени {v!r}")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) == 3 else 0
    return h * 3600 + m * 60 + s


def fmt_time(sec: int | None) -> str | None:
    if sec is None:
        return None
    sign = "-" if sec < 0 else ""
    sec = abs(int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h:02d}:{m:02d}" + (f":{s:02d}" if s else "")


def minutes(v) -> int:
    return int(round(float(v) * 60))


# ---------------------------------------------------------------- сущности

@dataclass(frozen=True)
class TrainType:
    name: str
    speed_kmh: float
    accel_s: int      # потеря времени на разгон после остановки
    brake_s: int      # потеря времени на торможение до остановки
    weight: float     # вес задержки в метрике (пассажирские весят больше)


@dataclass(frozen=True)
class Track:
    id: str
    length_m: int
    kind: str = "main"   # "main" — обычный путь, "port" — путь порта под вагоны на паром


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    type: str            # boundary | siding | junction | port
    tracks: tuple[Track, ...]
    x: float = 0.0
    y: float = 0.0
    terminal_clear_s: int = 900   # через сколько освобождается путь после прибытия в конечный пункт

    @property
    def is_boundary(self) -> bool:
        return self.type == "boundary"


@dataclass(frozen=True)
class Segment:
    id: str
    a: str
    b: str
    length_km: float
    max_speed_kmh: float = 999.0

    def other(self, n: str) -> str:
        return self.b if n == self.a else self.a


@dataclass(frozen=True)
class Stop:
    node: str
    min_dwell_s: int = 0
    not_before: int | None = None   # пассажирский не уходит раньше расписания


@dataclass(frozen=True)
class Forecast:
    at: int     # когда диспетчер узнал
    eta: int    # что ему сказали


@dataclass(frozen=True)
class TrainSpec:
    id: str
    type: str
    origin: str
    destinations: tuple[str, ...]
    appear_planned: int
    appear_actual: int            # правда: скрыта от политики до факта
    forecasts: tuple[Forecast, ...]
    length_m: int
    wagons: int
    stops: tuple[Stop, ...]
    planned_arrival: int
    cargo: str | None = None      # "ferry" — вагоны едут на паром
    target_ferry: str | None = None


@dataclass(frozen=True)
class FerrySpec:
    id: str
    port: str
    capacity: int
    planned_arrival: int
    actual_arrival: int           # правда: скрыта до факта
    eta_updates: tuple[Forecast, ...]
    unload_s: int
    max_stay_s: int


@dataclass(frozen=True)
class PortSpec:
    node: str
    berths: int
    processing_s: int             # отцепка, осмотр — после этого вагоны можно грузить
    load_s_per_wagon: int


@dataclass(frozen=True)
class Disruption:
    id: str
    kind: str                     # loco_failure | segment_outage | maintenance_window
    at: int                       # начало (правда); для окна — плановое начало
    duration_s: int               # фактическая длительность (правда)
    announced_duration_s: int     # что сообщили диспетчеру
    announce_at: int | None = None  # для плановых окон: когда о них известно
    train: str | None = None
    segment: str | None = None


@dataclass(frozen=True)
class Params:
    route_setup_s: int = 60        # приготовление маршрута отправления
    approach_lead_s: int = 180     # за сколько до станции решается «проход / остановка»
    segment_interval_s: int = 120  # интервал после освобождения перегона
    running_noise: float = 0.03    # разброс времени хода ±3%, одинаков для всех прогонов


@dataclass
class Scenario:
    id: str
    name: str
    seed: int
    horizon: int
    params: Params
    types: dict[str, TrainType]
    nodes: dict[str, Node]
    segments: dict[str, Segment]
    trains: dict[str, TrainSpec]
    ferries: dict[str, FerrySpec]
    ports: dict[str, PortSpec]
    disruptions: tuple[Disruption, ...]
    dispatcher: dict
    raw: dict = field(repr=False, default_factory=dict)

    def __post_init__(self):
        self._pair: dict[frozenset, Segment] = {}
        self.adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for s in self.segments.values():
            self._pair[frozenset((s.a, s.b))] = s
            self.adj[s.a].append(s.b)
            self.adj[s.b].append(s.a)
        for n in self.adj:
            self.adj[n].sort()
        self._dist = {n: self._dijkstra(n) for n in self.nodes}

    def seg_between(self, a: str, b: str) -> Segment | None:
        return self._pair.get(frozenset((a, b)))

    def _dijkstra(self, src: str) -> dict[str, float]:
        dist = {src: 0.0}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, math.inf):
                continue
            for v in self.adj[u]:
                nd = d + self.seg_between(u, v).length_km
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def next_hops(self, cur: str, dests, came_from: str | None) -> list[str]:
        """Соседи cur, лежащие на кратчайшем пути к одному из dests. Разворот назад запрещён."""
        out = set()
        for d in dests:
            if d == cur or d not in self._dist[cur]:
                continue
            total = self._dist[cur][d]
            for v in self.adj[cur]:
                if v == came_from:
                    continue
                if math.isclose(self.seg_between(cur, v).length_km + self._dist[v].get(d, math.inf), total):
                    out.add(v)
        return sorted(out)


# ---------------------------------------------------------------- чистые функции

def nominal_run_s(tt: TrainType, seg: Segment) -> int:
    v = min(tt.speed_kmh, seg.max_speed_kmh)
    return int(math.ceil(seg.length_km / v * 3600))


def pass_s(tt: TrainType, length_m: int) -> int:
    """Время проследования станции на ходу (голова прошла станцию)."""
    return max(30, int(math.ceil(length_m / (tt.speed_kmh / 3.6))))


def clear_s(tt: TrainType, length_m: int) -> int:
    """Время, за которое хвост освобождает путь после трогания с места."""
    return max(60, int(math.ceil(length_m / (tt.speed_kmh / 3.6 / 3))))


def noise_factor(seed: int, train_id: str, seg_id: str, amp: float) -> float:
    """Детерминированный множитель времени хода.

    Зависит только от (seed, поезд, перегон) — НЕ от решений и порядка событий.
    Поэтому факт и любая альтернатива получают одинаковое «везение» на перегоне.
    """
    if amp <= 0:
        return 1.0
    h = hashlib.sha256(f"{seed}|{train_id}|{seg_id}|run".encode()).digest()
    u = int.from_bytes(h[:8], "big") / 2**64          # [0, 1)
    return 1.0 + amp * (2 * u - 1)


# ---------------------------------------------------------------- загрузка

def _req(d: dict, key: str, ctx: str):
    if key not in d:
        raise ScenarioError(f"{ctx}: нет поля '{key}'")
    return d[key]


def load_scenario(src) -> Scenario:
    if isinstance(src, (str, Path)):
        raw = json.loads(Path(src).read_text(encoding="utf-8"))
    else:
        raw = src
    p = raw.get("params", {})
    params = Params(
        route_setup_s=minutes(p.get("route_setup_min", 1)),
        approach_lead_s=minutes(p.get("approach_lead_min", 3)),
        segment_interval_s=minutes(p.get("segment_interval_min", 2)),
        running_noise=float(p.get("running_noise", 0.03)),
    )
    if params.approach_lead_s < params.route_setup_s:
        raise ScenarioError("approach_lead_min не может быть меньше route_setup_min")

    types = {}
    for name, t in _req(raw, "train_types", "scenario").items():
        types[name] = TrainType(name, float(t["speed_kmh"]), minutes(t["accel_min"]),
                                minutes(t["brake_min"]), float(t["weight"]))

    nodes = {}
    for n in _req(raw, "nodes", "scenario"):
        tracks = tuple(Track(str(tr["id"]), int(tr["length_m"]), tr.get("kind", "main"))
                       for tr in n.get("tracks", []))
        if n["type"] != "boundary" and not tracks:
            raise ScenarioError(f"узел {n['id']}: нет путей")
        nodes[n["id"]] = Node(n["id"], n.get("name", n["id"]), n["type"], tracks,
                              float(n.get("x", 0)), float(n.get("y", 0)),
                              minutes(n.get("terminal_clear_min", 15)))

    segments = {}
    for s in _req(raw, "segments", "scenario"):
        sid = s.get("id", f"{s['a']}-{s['b']}")
        for e in (s["a"], s["b"]):
            if e not in nodes:
                raise ScenarioError(f"перегон {sid}: нет узла {e}")
        segments[sid] = Segment(sid, s["a"], s["b"], float(s["length_km"]),
                                float(s.get("max_speed_kmh", 999)))

    def fc(lst):
        return tuple(sorted((Forecast(parse_time(f["at"]), parse_time(f["eta"])) for f in lst or []),
                            key=lambda f: f.at))

    trains = {}
    for t in _req(raw, "trains", "scenario"):
        ctx = f"поезд {t.get('id')}"
        if t["type"] not in types:
            raise ScenarioError(f"{ctx}: неизвестный тип {t['type']}")
        dests = tuple(t["destinations"]) if "destinations" in t else (t["destination"],)
        for d in (t["origin"], *dests):
            if d not in nodes:
                raise ScenarioError(f"{ctx}: нет узла {d}")
        appear = parse_time(_req(t, "appear", ctx))
        stops = tuple(Stop(s["node"], minutes(s.get("min_dwell_min", 0)),
                           parse_time(s["not_before"]) if s.get("not_before") else None)
                      for s in t.get("stops", []))
        trains[t["id"]] = TrainSpec(
            id=t["id"], type=t["type"], origin=t["origin"], destinations=dests,
            appear_planned=appear,
            appear_actual=parse_time(t["appear_actual"]) if t.get("appear_actual") else appear,
            forecasts=fc(t.get("forecasts")),
            length_m=int(_req(t, "length_m", ctx)), wagons=int(_req(t, "wagons", ctx)),
            stops=stops, planned_arrival=parse_time(_req(t, "planned_arrival", ctx)),
            cargo=t.get("cargo"), target_ferry=t.get("target_ferry"),
        )
        longest = max((tr.length_m for tr in nodes[t["origin"]].tracks), default=10**9)
        if trains[t["id"]].length_m > longest and not nodes[t["origin"]].is_boundary:
            raise ScenarioError(f"{ctx}: не помещается ни на один путь станции отправления")

    ports = {}
    for p_ in raw.get("ports", []):
        ports[p_["node"]] = PortSpec(p_["node"], int(p_.get("berths", 1)),
                                     minutes(p_.get("processing_min", 60)),
                                     minutes(p_.get("load_min_per_wagon", 3)))

    ferries = {}
    for f in raw.get("ferries", []):
        if f["port"] not in ports:
            raise ScenarioError(f"паром {f['id']}: порт {f['port']} не описан в ports")
        planned = parse_time(f["planned_arrival"])
        ferries[f["id"]] = FerrySpec(
            f["id"], f["port"], int(f["capacity_wagons"]), planned,
            parse_time(f["actual_arrival"]) if f.get("actual_arrival") else planned,
            fc(f.get("eta_updates")), minutes(f.get("unload_min", 120)),
            minutes(f.get("max_stay_min", 360)))

    dis = []
    for i, d in enumerate(raw.get("disruptions", [])):
        kind = d["kind"]
        did = d.get("id", f"D{i + 1}")
        if kind == "loco_failure":
            if d["train"] not in trains:
                raise ScenarioError(f"сбой {did}: нет поезда {d['train']}")
        elif kind in ("segment_outage", "maintenance_window"):
            if d["segment"] not in segments:
                raise ScenarioError(f"сбой {did}: нет перегона {d['segment']}")
        else:
            raise ScenarioError(f"сбой {did}: неизвестный вид {kind}")
        dur = minutes(d["duration_min"])
        dis.append(Disruption(
            id=did, kind=kind, at=parse_time(d.get("at") or d.get("start")), duration_s=dur,
            announced_duration_s=minutes(d.get("announced_duration_min", d["duration_min"])),
            announce_at=parse_time(d["announce_at"]) if d.get("announce_at") else None,
            train=d.get("train"), segment=d.get("segment")))

    return Scenario(
        id=raw.get("id", "scenario"), name=raw.get("name", ""), seed=int(raw.get("seed", 1)),
        horizon=parse_time(raw.get("horizon", "30:00")), params=params, types=types,
        nodes=nodes, segments=segments, trains=trains, ferries=ferries, ports=ports,
        disruptions=tuple(dis), dispatcher=raw.get("dispatcher", {}), raw=raw)
