"""Дискретно-событийный симулятор однопутного участка с портами и паромами.

Один и тот же движок считает и факт смены, и любые альтернативы. Политика
(записанный диспетчер, эвристика, ИИ) — чёрный ящик: получает только
наблюдение (observe.py, единственная «дверь» от правды к политике) и отдаёт
команды. В движке нет ни одной ветки, зависящей от того, кто принимает решения.

Что движок НЕ делает никогда:
  * не исправляет и не «додумывает» команды — незаконная команда отклоняется
    и записывается в журнал;
  * не разруливает тупики — если поезда сцепились, день так и заканчивается;
  * не перебрасывает случайность: сбои и шум времени хода заданы сценарием
    и не зависят от решений.
"""
from __future__ import annotations

import copy
import heapq
from collections import defaultdict
from dataclasses import dataclass, field

from .model import (Scenario, TrainSpec, TrainType, clear_s, noise_factor,
                    nominal_run_s, pass_s)

# Порядок обработки событий, наступивших в одну и ту же секунду. Фиксирован.
# Сначала освобождаются ресурсы и начинаются сбои, затем движение, затем триггеры.
EV_ORDER = {
    "RELEASE_SEG": 0, "RELEASE_TRACK": 1,
    "FAILURE_END": 2, "OUTAGE_END": 2, "WINDOW_END": 2,
    "FAILURE_START": 3, "OUTAGE_START": 3, "WINDOW_START": 3,
    "EXIT": 4, "ARRIVE": 4, "ENTER": 5, "APPROACH": 6, "APPEAR": 7,
    "FERRY_ARRIVE": 8, "FERRY_UNLOADED": 8, "LOAD_DONE": 8, "FERRY_DEADLINE": 9,
    "PORT_READY": 9, "ANNOUNCE": 10, "READY": 10, "WAKE": 11,
}

TRAIN_MOVES = {"APPEAR", "APPROACH", "ARRIVE", "ENTER", "EXIT"}


def _plain_copy(x):
    """Глубокая копия JSON-подобных данных (dict/list/примитивы) — в десятки раз быстрее deepcopy."""
    t = type(x)
    if t is dict:
        return {k: _plain_copy(v) for k, v in x.items()}
    if t is list or t is tuple:
        return [_plain_copy(v) for v in x]
    return x


@dataclass
class Run:
    seg: str | None          # None — подход к границе участка извне
    frm: str | None
    to: str
    entered: int
    from_stop: bool
    head_end: int            # ПРАВДА: голова у входного сигнала `to` (с шумом и сбоями)
    est_head_end: int        # публичная оценка: номинал + известные диспетчеру сдвиги
    through_next: str | None = None
    through_track: str | None = None
    stopping: bool | None = None   # None — решение «проход/остановка» ещё не принято


@dataclass
class TrainState:
    spec: TrainSpec
    tt: TrainType
    status: str = "pending"  # pending|approaching|running|passing|at_node|delivered|done
    node: str | None = None
    track: str | None = None
    prev_node: str | None = None
    run: Run | None = None
    stopped: bool = False
    since: int = 0
    dwell_ready: int = 0
    failure: dict | None = None
    move: dict | None = None
    awaiting: bool = False
    ver: int = 0
    next_ev: tuple | None = None
    arrived_at: int | None = None
    final_node: str | None = None
    unplanned_stops: int = 0
    idle_s: int = 0
    port: dict | None = None
    visited: list = field(default_factory=list)
    path: list = field(default_factory=list)
    waiting_track: bool = False
    appear_at: int = 0


@dataclass
class SegState:
    occupant: str | None = None
    reserved: str | None = None
    outage: dict | None = None
    window: dict | None = None
    window_pending: list = field(default_factory=list)


@dataclass
class TrackState:
    occupant: str | None = None
    reserved: str | None = None


@dataclass
class FerryState:
    status: str = "expected"   # expected|waiting_berth|unloading|loading|departed
    arrived: int | None = None
    berthed: int | None = None
    deadline: int | None = None
    departed: int | None = None
    loaded: int = 0
    busy: str | None = None
    loads: dict = field(default_factory=dict)


class Simulator:
    def __init__(self, scenario: Scenario, knowledge_mode: str = "honest",
                 keep_snapshots: bool = False, strict: bool = True):
        if knowledge_mode not in ("honest", "oracle"):
            raise ValueError("knowledge_mode: honest | oracle")
        self.sc = scenario
        self.mode = knowledge_mode
        self.keep_snapshots = keep_snapshots
        self.strict = strict
        self.t = 0
        self.q: list = []
        self._seq = 0
        self.status = "running"
        self.end_time: int | None = None
        self.paused = False
        self._paused_obs = None
        self._decide_seq = 0          # номер вызова политики — по нему реплей узнаёт момент решения
        self.external_wakes: list[dict] = []  # приказы «сейчас» от игрока (для точного воспроизведения)
        self.injections: list[dict] = []      # их повтор при воспроизведении записанной смены

        self.trains = {tid: TrainState(spec, scenario.types[spec.type])
                       for tid, spec in scenario.trains.items()}
        self.segs = {sid: SegState() for sid in scenario.segments}
        self.tracks = {(n.id, tr.id): TrackState() for n in scenario.nodes.values() for tr in n.tracks}
        self.ferries = {fid: FerryState() for fid in scenario.ferries}
        self.ferry_queue = defaultdict(list)
        self.port_waiting = defaultdict(list)
        self.ferry_order: dict[str, list[str]] = {}
        self.waiting_appear = defaultdict(list)
        self.windows = {d.id: {"state": "scheduled", "start": None, "end": None, "shift_s": 0}
                        for d in scenario.disruptions if d.kind == "maintenance_window"}
        self.revealed_failures: list[dict] = []

        self._awaiting: list[str] = []
        self._last_rejections: list[dict] = []
        self.log: list[dict] = []
        self.decisions: list[dict] = []
        self.rejections: list[dict] = []
        self.port_occupancy = defaultdict(list)
        self._init_events()

    # deepcopy без копирования сценария — для форков реплея
    def __deepcopy__(self, memo):
        memo[id(self.sc)] = self.sc
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        for k, v in self.__dict__.items():
            setattr(new, k, copy.deepcopy(v, memo))
        return new

    def clone(self) -> "Simulator":
        return copy.deepcopy(self)

    # ------------------------------------------------------------ события

    def _push(self, t: int, kind: str, **data):
        self._seq += 1
        heapq.heappush(self.q, (int(t), EV_ORDER[kind], self._seq, kind, data))

    def _push_train(self, tr: TrainState, t: int, kind: str, **data):
        tr.ver += 1
        data["train"] = tr.spec.id
        data["v"] = tr.ver
        tr.next_ev = (int(t), kind, dict(data))
        self._push(t, kind, **data)

    def _shift_train(self, tr: TrainState, delta: int, from_stop: bool | None = None):
        if tr.next_ev is None:
            return
        t0, kind, data = tr.next_ev
        data = {k: v for k, v in data.items() if k not in ("train", "v")}
        if from_stop is not None:
            data["from_stop"] = from_stop
        self._push_train(tr, t0 + delta, kind, **data)

    def _init_events(self):
        lead = self.sc.params.approach_lead_s
        for tr in self.trains.values():
            sp = tr.spec
            tr.appear_at = sp.appear_actual
            if self.sc.nodes[sp.origin].is_boundary:
                self._push_train(tr, max(0, sp.appear_actual - lead), "APPROACH", boundary=True)
            else:
                self._push_train(tr, sp.appear_actual, "APPEAR")
            for f in sp.forecasts:
                self._push(f.at, "ANNOUNCE", what="train_forecast", id=sp.id)
        for fid, fs in self.sc.ferries.items():
            self._push(fs.actual_arrival, "FERRY_ARRIVE", ferry=fid)
            for u in fs.eta_updates:
                self._push(u.at, "ANNOUNCE", what="ferry_eta", id=fid)
        for d in self.sc.disruptions:
            if d.kind == "loco_failure":
                self._push(d.at, "FAILURE_START", dis=d.id)
            elif d.kind == "segment_outage":
                self._push(d.at, "OUTAGE_START", dis=d.id)
            else:
                if d.announce_at is not None and d.announce_at < d.at:
                    self._push(d.announce_at, "ANNOUNCE", what="window", id=d.id)
                self._push(d.at, "WINDOW_START", dis=d.id)

    def _dis(self, did: str):
        return next(d for d in self.sc.disruptions if d.id == did)

    def _ev(self, kind: str, **data):
        self.log.append({"t": self.t, "event": kind, **data})

    # ------------------------------------------------------------ главный цикл

    def run(self, policy, until: int | None = None) -> "Simulator":
        self.advance(policy, until)
        return self

    def advance(self, policy, until: int | None = None, pause_if=None, open_ended: bool = False) -> str:
        """Крутит события до `until`. Возвращает 'until' | 'ended' | 'paused'.

        pause_if(obs) -> bool: интерактивный режим. Если True — движок замирает в момент
        решения ДО применения команд (поезд на подходе ещё можно пропустить на проход).
        Продолжение — resume(). Без pause_if поведение совпадает с пакетным прогоном.
        open_ended: внешний игрок может отдать приказ в любой момент, поэтому «событий
        больше нет» — не конец смены, пока есть законные ходы. Настоящий тупик всё равно конец.
        """
        if self.paused:
            return "paused"
        stop_at = self.sc.horizon if until is None else min(until, self.sc.horizon)
        while self.status == "running":
            # повтор внешнего «сейчас»: после того же числа решений и когда все события до t уже прошли
            while self.injections and self.injections[0]["seq"] <= self._decide_seq and (
                    not self.q or self.q[0][0] > self.injections[0]["t"]):
                inj = self.injections.pop(0)
                self._push(max(inj["t"], self.t), "WAKE")
            if not self.q:
                waiting = open_ended and stop_at < self.sc.horizon and self.legal_actions()
                if waiting:
                    self.t = max(self.t, stop_at)
                    return "until"
                self._finish_no_events()
                break
            t = self.q[0][0]
            if t > stop_at:
                if stop_at >= self.sc.horizon:
                    self._finish("horizon", self.sc.horizon)
                    break
                # до stop_at событий нет — мир в этот момент тот же; часы движка встают на stop_at
                self.t = max(self.t, stop_at)
                return "until"
            self.t = t
            changed = False
            while self.q and self.q[0][0] == t:
                _, _, _, kind, data = heapq.heappop(self.q)
                if self._handle(kind, data):
                    changed = True
            if changed:
                from .observe import build_observation
                obs = build_observation(self)
                if pause_if is not None and pause_if(obs):
                    self.paused, self._paused_obs = True, obs
                    return "paused"
                self._decide(policy, obs)
            self._close_moment()
        return "ended"

    def resume(self, policy):
        """Применить решения политики в момент паузы и закрыть этот момент."""
        if not self.paused:
            return
        obs, self.paused, self._paused_obs = self._paused_obs, False, None
        self._decide(policy, obs)
        self._close_moment()

    def wake_at(self, t: int):
        """Внешний триггер момента решения (игрок отдал приказ «сейчас»). t не раньше текущего."""
        t = max(int(t), self.t)
        self.external_wakes.append({"seq": self._decide_seq, "t": t})
        self._push(t, "WAKE")

    def _close_moment(self):
        self._finalize_approaches()
        if self.strict:
            self._check_invariants()

    def _finish(self, status: str, t: int):
        self.status = status
        self.end_time = t

    def _finish_no_events(self):
        unfinished = [tr for tr in self.trains.values() if tr.arrived_at is None]
        if not unfinished:
            self._finish("completed", self.t)
        elif self.legal_actions():
            # движение физически возможно, но политика его не выбрала и не попросила разбудить;
            # ничего больше не изменится — задержки копятся до конца горизонта
            self._finish("stalled", self.sc.horizon)
        else:
            self._finish("deadlock", self.sc.horizon)

    def _handle(self, kind: str, data: dict) -> bool:
        if kind in TRAIN_MOVES:
            tr = self.trains[data["train"]]
            if data["v"] != tr.ver:
                return False   # устаревшее событие (поезд сдвинут сбоем) — тихо игнорируем
            tr.next_ev = None
        return getattr(self, "_on_" + kind.lower())(data)

    # ------------------------------------------------------------ решения

    def _decide(self, policy, obs):
        # журнал хранит свою копию: что бы политика ни сделала с наблюдением, до движка и журнала
        # это не дойдёт (наблюдение — свежий словарь, движок после политики его не читает)
        legal = [dict(a) for a in obs["legal"]]
        snapshot = _plain_copy(obs) if self.keep_snapshots else None
        seq = self._decide_seq
        self._decide_seq += 1
        cmds = policy.decide(obs) or []
        if not isinstance(cmds, list):
            raise TypeError("политика должна вернуть список команд")
        cmds = _plain_copy(cmds)
        applied, rejected = [], []
        for c in cmds:
            ok, reason = self._apply(c)
            if ok:
                applied.append(c)
            else:
                rec = {"t": self.t, "command": c, "reason": reason}
                rejected.append(rec)
                self.rejections.append(rec)
        self._last_rejections = rejected
        if legal or cmds:
            rec = {"t": self.t, "seq": seq, "legal": legal, "applied": applied, "rejected": rejected}
            if snapshot is not None:
                rec["observation"] = snapshot
            self.decisions.append(rec)

    def _apply(self, c) -> tuple[bool, str | None]:
        if not isinstance(c, dict):
            return False, "bad_command"
        typ = c.get("type")
        if typ == "dispatch":
            return self._apply_dispatch(c)
        if typ == "wake_at":
            when = c.get("time")
            if not isinstance(when, int) or when <= self.t:
                return False, "bad_time"
            self._push(when, "WAKE")
            return True, None
        if typ == "ferry_order":
            port = c.get("port")
            order = c.get("order")
            if port not in self.sc.ports or not isinstance(order, list):
                return False, "bad_port_or_order"
            if any(o not in self.trains for o in order):
                return False, "unknown_train"
            self.ferry_order[port] = list(order)
            for fid, fs in self.ferries.items():
                if self.sc.ferries[fid].port == port and fs.status == "loading" and fs.busy is None:
                    self._try_load(fid)
            return True, None
        return False, "unknown_command"

    def _must_stop(self, tr: TrainState, node: str) -> bool:
        return node in tr.spec.destinations or any(s.node == node for s in tr.spec.stops)

    def _required_dwell(self, tr: TrainState, node: str) -> int:
        return sum(s.min_dwell_s for s in tr.spec.stops if s.node == node)

    def _apply_dispatch(self, c) -> tuple[bool, str | None]:
        tr = self.trains.get(c.get("train"))
        if tr is None:
            return False, "unknown_train"
        nxt = c.get("next")
        want = c.get("track")
        if tr.awaiting:
            node = tr.run.to
            if self._must_stop(tr, node):
                return False, "must_stop"
            if tr.failure:
                return False, "loco_failure"
            start = tr.run.head_end + (0 if self.sc.nodes[node].is_boundary
                                       else pass_s(tr.tt, tr.spec.length_m))
            ok, reason, trk = self._check_move(tr, node, nxt, start, False, tr.run.frm, want)
            if not ok:
                return False, reason
            seg = self.sc.seg_between(node, nxt)
            self.segs[seg.id].reserved = tr.spec.id
            if trk:
                self.tracks[(nxt, trk)].reserved = tr.spec.id
            tr.run.through_next, tr.run.through_track = nxt, trk
            return True, None
        if tr.status != "at_node" or tr.move is not None:
            return False, f"not_ready:{tr.status}"
        if tr.failure:
            return False, "loco_failure"
        if self.t < tr.dwell_ready:
            return False, "dwell_or_timetable"
        if tr.node in tr.spec.destinations:
            return False, "already_at_destination"
        start = self.t + self.sc.params.route_setup_s
        ok, reason, trk = self._check_move(tr, tr.node, nxt, start, True, tr.prev_node, want)
        if not ok:
            return False, reason
        seg = self.sc.seg_between(tr.node, nxt)
        self.segs[seg.id].reserved = tr.spec.id
        if trk:
            self.tracks[(nxt, trk)].reserved = tr.spec.id
        tr.move = {"seg": seg.id, "to": nxt, "track": trk, "from_stop": True}
        self._push_train(tr, start, "ENTER", from_stop=True)
        return True, None

    def _check_move(self, tr, frm, to, start, from_stop, came_from, want_track):
        sc = self.sc
        if to not in sc.next_hops(frm, tr.spec.destinations, came_from):
            return False, "not_on_route", None
        seg = sc.seg_between(frm, to)
        ss = self.segs[seg.id]
        if ss.occupant is not None or ss.reserved is not None:
            return False, "segment_busy", None
        if ss.outage:
            return False, "segment_outage", None
        if ss.window or ss.window_pending:
            return False, "maintenance_window", None
        clear = (start + (tr.tt.accel_s if from_stop else 0) + nominal_run_s(tr.tt, seg)
                 + (tr.tt.brake_s if self._must_stop(tr, to) else 0) + sc.params.segment_interval_s)
        for d in sc.disruptions:
            if d.kind != "maintenance_window" or d.segment != seg.id:
                continue
            w = self.windows[d.id]
            known = d.announce_at is not None and d.announce_at <= self.t
            if known and w["state"] == "scheduled" and start < d.at + d.duration_s and clear > d.at:
                return False, "window_conflict", None
        node = sc.nodes[to]
        if node.is_boundary:
            return (True, None, None) if to in tr.spec.destinations else (False, "not_on_route", None)
        trk = self._free_track(tr, to, want_track)
        if trk is None:
            return False, ("track_unavailable" if want_track else "no_track"), None
        return True, None, trk

    def _track_kind(self, tr: TrainState, node_id: str) -> str:
        node = self.sc.nodes[node_id]
        if node.type == "port" and tr.spec.cargo == "ferry" and node_id in tr.spec.destinations:
            return "port"
        return "main"

    def _free_track(self, tr: TrainState, node_id: str, want: str | None = None) -> str | None:
        kind = self._track_kind(tr, node_id)
        cands = [t for t in self.sc.nodes[node_id].tracks
                 if t.kind == kind and t.length_m >= tr.spec.length_m
                 and self.tracks[(node_id, t.id)].occupant is None
                 and self.tracks[(node_id, t.id)].reserved is None]
        if want is not None:
            return want if any(t.id == want for t in cands) else None
        cands.sort(key=lambda t: (t.length_m, t.id))
        return cands[0].id if cands else None

    def legal_actions(self) -> list[dict]:
        """Все команды dispatch, которые прямо сейчас будут приняты. Одинаково для всех политик."""
        out = []
        for tid in sorted(self.trains):
            tr = self.trains[tid]
            if tr.awaiting:
                node = tr.run.to
                if self._must_stop(tr, node) or tr.failure:
                    continue
                start = tr.run.head_end + (0 if self.sc.nodes[node].is_boundary
                                           else pass_s(tr.tt, tr.spec.length_m))
                came, kind = tr.run.frm, "through"
            elif (tr.status == "at_node" and tr.move is None and tr.failure is None
                  and self.t >= tr.dwell_ready and tr.node not in tr.spec.destinations):
                node, start = tr.node, self.t + self.sc.params.route_setup_s
                came, kind = tr.prev_node, "depart"
            else:
                continue
            for nxt in self.sc.next_hops(node, tr.spec.destinations, came):
                ok, _, trk = self._check_move(tr, node, nxt, start, kind == "depart", came, None)
                if ok:
                    out.append({"train": tid, "kind": kind, "at": node, "next": nxt,
                                "segment": self.sc.seg_between(node, nxt).id, "track": trk})
        return out

    def _finalize_approaches(self):
        for tid in self._awaiting:
            tr = self.trains[tid]
            if not tr.awaiting:
                continue
            tr.awaiting = False
            run = tr.run
            if run.through_next:
                run.stopping = False
                self._push_train(tr, run.head_end, "ARRIVE", through=True)
            else:
                run.stopping = True
                self._push_train(tr, run.head_end + tr.tt.brake_s, "ARRIVE", through=False)
        self._awaiting = []

    # ------------------------------------------------------------ движение

    def _on_approach(self, d):
        tr = self.trains[d["train"]]
        if d.get("boundary"):
            sp = tr.spec
            tr.run = Run(None, None, sp.origin, entered=self.t, from_stop=False,
                         head_end=max(self.t, tr.appear_at),
                         est_head_end=max(self.t, tr.appear_at))
            tr.status = "approaching"
            self._ev("appear", train=sp.id, node=sp.origin)
        tr.awaiting = True
        self._awaiting.append(tr.spec.id)
        return True

    def _on_appear(self, d):
        tr = self.trains[d["train"]]
        node = tr.spec.origin
        trk = self._free_track(tr, node)
        if trk is None:
            if not tr.waiting_track:
                tr.waiting_track = True
                self.waiting_appear[node].append(tr.spec.id)
                self._ev("appear_blocked", train=tr.spec.id, node=node)
            return True
        tr.waiting_track = False
        self._occupy(node, trk, tr.spec.id)
        tr.status, tr.node, tr.track, tr.stopped, tr.since = "at_node", node, trk, True, self.t
        tr.visited.append(node)
        self._set_dwell(tr, node)
        tr.path.append({"node": node, "track": trk, "arr": None, "dep": None, "through": False})
        self._ev("appear", train=tr.spec.id, node=node, track=trk)
        return True

    def _set_dwell(self, tr: TrainState, node: str):
        ready = self.t + self._required_dwell(tr, node)
        for s in tr.spec.stops:
            if s.node == node and s.not_before is not None:
                ready = max(ready, s.not_before)
        tr.dwell_ready = ready
        if ready > self.t:
            self._push(ready, "READY", train=tr.spec.id)

    def _on_arrive(self, d):
        tr = self.trains[d["train"]]
        run = tr.run
        node_id = run.to
        node = self.sc.nodes[node_id]
        tps = pass_s(tr.tt, tr.spec.length_m)
        if tr.path and "segment" in tr.path[-1]:
            tr.path[-1]["exit"] = self.t
            tr.path[-1]["stop"] = not d["through"]
        if d["through"]:
            if node.is_boundary:
                tr.status, tr.node, tr.track = "passing", node_id, None
                tr.visited.append(node_id)
                tr.path.append({"node": node_id, "track": None, "arr": self.t, "dep": None, "through": True})
                tr.move = {"seg": self.sc.seg_between(node_id, run.through_next).id,
                           "to": run.through_next, "track": run.through_track, "from_stop": False}
                self._push_train(tr, self.t, "ENTER", from_stop=False)
                return True
            trk = self._claim_reserved(node_id, tr)
            self._push(self.t + tps + self.sc.params.segment_interval_s, "RELEASE_SEG",
                       seg=run.seg, train=tr.spec.id)
            tr.status, tr.node, tr.track = "passing", node_id, trk
            tr.visited.append(node_id)
            tr.path.append({"node": node_id, "track": trk, "arr": self.t, "dep": None, "through": True})
            self._ev("pass", train=tr.spec.id, node=node_id, track=trk)
            tr.move = {"seg": self.sc.seg_between(node_id, run.through_next).id,
                       "to": run.through_next, "track": run.through_track, "from_stop": False}
            self._push_train(tr, self.t + tps, "ENTER", from_stop=False)
            return True
        # остановка
        tr.stopped, tr.since = True, self.t
        if node.is_boundary:
            tr.status, tr.node, tr.track = "at_node", node_id, None
            tr.visited.append(node_id)
            tr.dwell_ready = self.t
            tr.unplanned_stops += 1
            tr.path.append({"node": node_id, "track": None, "arr": self.t, "dep": None, "through": False})
            self._ev("held_at_boundary", train=tr.spec.id, node=node_id)
            return True
        trk = self._claim_reserved(node_id, tr)
        self._push(self.t + self.sc.params.segment_interval_s, "RELEASE_SEG", seg=run.seg, train=tr.spec.id)
        tr.status, tr.node, tr.track = "at_node", node_id, trk
        tr.visited.append(node_id)
        tr.path.append({"node": node_id, "track": trk, "arr": self.t, "dep": None, "through": False})
        self._ev("arrive", train=tr.spec.id, node=node_id, track=trk)
        if node_id in tr.spec.destinations:
            self._terminate(tr, node_id, trk)
        else:
            if not any(s.node == node_id for s in tr.spec.stops):
                tr.unplanned_stops += 1
            self._set_dwell(tr, node_id)
        return True

    def _claim_reserved(self, node_id: str, tr: TrainState) -> str:
        for t in self.sc.nodes[node_id].tracks:
            ts = self.tracks[(node_id, t.id)]
            if ts.reserved == tr.spec.id:
                ts.reserved = None
                self._occupy(node_id, t.id, tr.spec.id)
                return t.id
        raise AssertionError(f"{tr.spec.id} прибыл на {node_id} без зарезервированного пути")

    def _occupy(self, node_id: str, trk: str, tid: str):
        ts = self.tracks[(node_id, trk)]
        assert ts.occupant is None, f"путь {node_id}/{trk} занят {ts.occupant}, пытается {tid}"
        ts.occupant = tid
        self._log_port(node_id)

    def _log_port(self, node_id: str):
        node = self.sc.nodes[node_id]
        if node.type != "port":
            return
        n = sum(1 for t in node.tracks if t.kind == "port" and self.tracks[(node_id, t.id)].occupant)
        self.port_occupancy[node_id].append((self.t, n))

    def _terminate(self, tr: TrainState, node_id: str, trk: str | None):
        tr.arrived_at, tr.final_node = self.t, node_id
        if (trk and self.sc.nodes[node_id].type == "port" and tr.spec.cargo == "ferry"
                and node_id in self.sc.ports):
            ps = self.sc.ports[node_id]
            tr.status = "delivered"
            tr.port = {"node": node_id, "track": trk, "remaining": tr.spec.wagons,
                       "available_at": self.t + ps.processing_s, "arrived": self.t,
                       "loaded_on": {}, "last_load": None}
            self.port_waiting[node_id].append(tr.spec.id)
            self._push(self.t + ps.processing_s, "PORT_READY", port=node_id)
        else:
            tr.status = "done"
            if trk:
                self._push(self.t + self.sc.nodes[node_id].terminal_clear_s, "RELEASE_TRACK",
                           node=node_id, track=trk, train=tr.spec.id)
        self._ev("terminate", train=tr.spec.id, node=node_id)

    def _on_enter(self, d):
        tr = self.trains[d["train"]]
        mv = tr.move
        ss = self.segs[mv["seg"]]
        flying = not d.get("from_stop", True)
        blocked = 0
        if ss.outage:
            blocked = ss.outage["until"]
        if tr.failure:
            blocked = max(blocked, tr.failure["until"])
        if blocked:
            # сигнал закрыт / локомотив неисправен: стоим, маршрут остаётся за поездом
            if flying:
                tr.status, tr.stopped, tr.since = "at_node", True, self.t
                tr.unplanned_stops += 1
            self._push_train(tr, blocked + (tr.tt.brake_s if flying else 0), "ENTER", from_stop=True)
            self._ev("start_blocked", train=tr.spec.id, segment=mv["seg"], until=blocked)
            return True
        here = tr.node
        if tr.track:
            rel = pass_s(tr.tt, tr.spec.length_m) if flying else clear_s(tr.tt, tr.spec.length_m)
            self._push(self.t + rel, "RELEASE_TRACK", node=here, track=tr.track, train=tr.spec.id)
        if tr.status == "at_node":
            tr.idle_s += max(0, self.t - tr.since - self._required_dwell(tr, here))
        if tr.path and tr.path[-1]["node"] == here:
            tr.path[-1]["dep"] = self.t
        ss.reserved = None
        ss.occupant = tr.spec.id
        tr.prev_node, tr.node, tr.track, tr.move = here, None, None, None
        seg = self.sc.segments[mv["seg"]]
        tr.path.append({"segment": seg.id, "from": here, "to": mv["to"], "enter": self.t, "exit": None,
                        "from_stop": not flying, "stop": None})
        self._ev("depart", train=tr.spec.id, node=here, segment=seg.id, flying=flying)
        self._start_run(tr, seg, here, mv["to"], from_stop=not flying)
        return True

    def _start_run(self, tr: TrainState, seg, frm: str, to: str, from_stop: bool):
        nominal = nominal_run_s(tr.tt, seg)
        actual = int(round(nominal * noise_factor(self.sc.seed, tr.spec.id, seg.id,
                                                  self.sc.params.running_noise)))
        accel = tr.tt.accel_s if from_stop else 0
        tr.run = Run(seg.id, frm, to, self.t, from_stop,
                     head_end=self.t + accel + actual, est_head_end=self.t + accel + nominal)
        tr.status, tr.stopped = "running", False
        if self.sc.nodes[to].is_boundary and to in tr.spec.destinations:
            self._push_train(tr, tr.run.head_end, "EXIT")
        else:
            self._push_train(tr, max(self.t, tr.run.head_end - self.sc.params.approach_lead_s), "APPROACH")

    def _on_exit(self, d):
        tr = self.trains[d["train"]]
        run = tr.run
        if tr.path and "segment" in tr.path[-1]:
            tr.path[-1]["exit"] = self.t
            tr.path[-1]["stop"] = False
        tr.path.append({"node": run.to, "track": None, "arr": self.t, "dep": None, "through": True})
        tr.status, tr.arrived_at, tr.final_node = "done", self.t, run.to
        self._push(self.t + pass_s(tr.tt, tr.spec.length_m) + self.sc.params.segment_interval_s,
                   "RELEASE_SEG", seg=run.seg, train=tr.spec.id)
        self._ev("exit", train=tr.spec.id, node=run.to)
        return True

    def _on_release_seg(self, d):
        ss = self.segs[d["seg"]]
        if ss.occupant == d["train"]:
            ss.occupant = None
        if ss.window_pending and ss.occupant is None and ss.reserved is None:
            self._activate_window(ss.window_pending.pop(0))
        return True

    def _on_release_track(self, d):
        ts = self.tracks[(d["node"], d["track"])]
        if ts.occupant == d["train"]:
            ts.occupant = None
            self._log_port(d["node"])
        q = self.waiting_appear[d["node"]]
        for tid in list(q):
            tr = self.trains[tid]
            if self._free_track(tr, d["node"]) is not None:
                q.remove(tid)
                self._on_appear({"train": tid})
        return True

    def _on_ready(self, d):
        return True

    def _on_wake(self, d):
        return True

    def _on_announce(self, d):
        return True

    # ------------------------------------------------------------ сбои

    def _on_failure_start(self, d):
        dis = self._dis(d["dis"])
        tr = self.trains[dis.train]
        D, A = dis.duration_s, dis.announced_duration_s
        tr.failure = {"id": dis.id, "since": self.t, "until": self.t + D, "est_until": self.t + A}
        self.revealed_failures.append({"id": dis.id, "kind": dis.kind, "train": tr.spec.id,
                                       "since": self.t, "est_until": self.t + A, "ended": None})
        self._ev("loco_failure", train=tr.spec.id, est_min=A // 60)
        if tr.status == "pending":
            tr.appear_at += D
            self._shift_train(tr, D)
        elif tr.status in ("running", "approaching"):
            run = tr.run
            if self.t < run.head_end:
                shift = D + tr.tt.brake_s + tr.tt.accel_s
                run.head_end += shift
                run.est_head_end += A + tr.tt.brake_s + tr.tt.accel_s
            else:
                shift = D
                run.est_head_end += A
            self._shift_train(tr, shift)
        # passing / at_node: ENTER проверит неисправность сам; delivered/done — без эффекта
        self._push(self.t + D, "FAILURE_END", train=tr.spec.id, dis=dis.id)
        return True

    def _on_failure_end(self, d):
        tr = self.trains[d["train"]]
        if tr.failure and tr.failure["id"] == d["dis"]:
            err = (tr.failure["until"] - tr.failure["est_until"])
            if tr.run and tr.status in ("running", "approaching"):
                tr.run.est_head_end += err
            tr.failure = None
        for f in self.revealed_failures:
            if f["id"] == d["dis"]:
                f["ended"] = self.t
        self._ev("failure_end", train=d["train"])
        return True

    def _on_outage_start(self, d):
        dis = self._dis(d["dis"])
        ss = self.segs[dis.segment]
        D, A = dis.duration_s, dis.announced_duration_s
        ss.outage = {"id": dis.id, "since": self.t, "until": self.t + D, "est_until": self.t + A}
        self.revealed_failures.append({"id": dis.id, "kind": dis.kind, "segment": dis.segment,
                                       "since": self.t, "est_until": self.t + A, "ended": None})
        self._ev("segment_outage", segment=dis.segment, est_min=A // 60)
        if ss.occupant:
            tr = self.trains[ss.occupant]
            if tr.status == "running" and tr.run.seg == dis.segment and self.t < tr.run.head_end:
                pen = tr.tt.brake_s + tr.tt.accel_s
                tr.run.head_end += D + pen
                tr.run.est_head_end += A + pen
                self._shift_train(tr, D + pen)
        self._push(self.t + D, "OUTAGE_END", dis=dis.id)
        return True

    def _on_outage_end(self, d):
        dis = self._dis(d["dis"])
        ss = self.segs[dis.segment]
        if ss.outage and ss.outage["id"] == dis.id:
            err = ss.outage["until"] - ss.outage["est_until"]
            if ss.occupant:
                tr = self.trains[ss.occupant]
                if tr.status == "running" and tr.run.seg == dis.segment:
                    tr.run.est_head_end += err
            ss.outage = None
        for f in self.revealed_failures:
            if f["id"] == dis.id:
                f["ended"] = self.t
        self._ev("outage_end", segment=dis.segment)
        return True

    def _on_window_start(self, d):
        dis = self._dis(d["dis"])
        ss = self.segs[dis.segment]
        if ss.occupant or ss.reserved or ss.window:
            # бригада не может зайти на занятый перегон — окно сдвигается, это штраф решению
            self.windows[dis.id]["state"] = "pending"
            ss.window_pending.append(dis.id)
            self._ev("window_delayed", segment=dis.segment)
        else:
            self._activate_window(dis.id)
        return True

    def _activate_window(self, did: str):
        dis = self._dis(did)
        w = self.windows[did]
        w.update(state="active", start=self.t, end=self.t + dis.duration_s, shift_s=self.t - dis.at)
        self.segs[dis.segment].window = {"id": did, "start": self.t, "end": self.t + dis.duration_s}
        self._push(self.t + dis.duration_s, "WINDOW_END", dis=did)
        self._ev("window_start", segment=dis.segment, shift_min=w["shift_s"] // 60)

    def _on_window_end(self, d):
        dis = self._dis(d["dis"])
        ss = self.segs[dis.segment]
        ss.window = None
        self.windows[dis.id]["state"] = "done"
        self._ev("window_end", segment=dis.segment)
        if ss.window_pending and ss.occupant is None and ss.reserved is None:
            self._activate_window(ss.window_pending.pop(0))
        return True

    # ------------------------------------------------------------ порт и паромы

    def _on_ferry_arrive(self, d):
        fid = d["ferry"]
        fs = self.ferries[fid]
        fs.status, fs.arrived = "waiting_berth", self.t
        port = self.sc.ferries[fid].port
        self.ferry_queue[port].append(fid)
        self._ev("ferry_arrive", ferry=fid, port=port)
        self._try_berth(port)
        return True

    def _try_berth(self, port: str):
        busy = sum(1 for fid, fs in self.ferries.items()
                   if self.sc.ferries[fid].port == port and fs.status in ("unloading", "loading"))
        while busy < self.sc.ports[port].berths and self.ferry_queue[port]:
            fid = self.ferry_queue[port].pop(0)
            fs = self.ferries[fid]
            fs.status, fs.berthed = "unloading", self.t
            self._push(self.t + self.sc.ferries[fid].unload_s, "FERRY_UNLOADED", ferry=fid)
            self._ev("ferry_berth", ferry=fid, port=port)
            busy += 1

    def _on_ferry_unloaded(self, d):
        fid = d["ferry"]
        fs = self.ferries[fid]
        fs.status = "loading"
        fs.deadline = self.t + self.sc.ferries[fid].max_stay_s
        self._push(fs.deadline, "FERRY_DEADLINE", ferry=fid)
        self._try_load(fid)
        return True

    def _pick_wagon(self, port: str) -> str | None:
        order = self.ferry_order.get(port, [])
        cands = [tid for tid in self.port_waiting[port]
                 if self.trains[tid].port["remaining"] > 0
                 and self.trains[tid].port["available_at"] <= self.t]
        if not cands:
            return None

        def key(tid):
            return (order.index(tid) if tid in order else len(order),
                    self.trains[tid].port["available_at"], tid)
        return min(cands, key=key)

    def _try_load(self, fid: str):
        fs = self.ferries[fid]
        spec = self.sc.ferries[fid]
        if fs.status != "loading" or fs.busy is not None:
            return
        if fs.loaded >= spec.capacity or self.t >= fs.deadline:
            self._depart_ferry(fid)
            return
        tid = self._pick_wagon(spec.port)
        if tid is None:
            return
        self.trains[tid].port["remaining"] -= 1
        fs.busy = tid
        self._push(self.t + self.sc.ports[spec.port].load_s_per_wagon, "LOAD_DONE", ferry=fid, train=tid)

    def _on_load_done(self, d):
        fid, tid = d["ferry"], d["train"]
        fs = self.ferries[fid]
        tr = self.trains[tid]
        fs.busy = None
        fs.loaded += 1
        fs.loads[tid] = fs.loads.get(tid, 0) + 1
        tr.port["loaded_on"][fid] = tr.port["loaded_on"].get(fid, 0) + 1
        tr.port["last_load"] = self.t
        if tr.port["remaining"] == 0 and not any(f.busy == tid for f in self.ferries.values()):
            node, trk = tr.port["node"], tr.port["track"]
            self.port_waiting[node].remove(tid)
            ts = self.tracks[(node, trk)]
            if ts.occupant == tid:
                ts.occupant = None
                self._log_port(node)
                self._on_release_track({"node": node, "track": trk, "train": "__none__"})
            self._ev("wagons_cleared", train=tid, port=node)
        self._try_load(fid)
        return True

    def _on_ferry_deadline(self, d):
        fs = self.ferries[d["ferry"]]
        if fs.status == "loading" and fs.busy is None:
            self._depart_ferry(d["ferry"])
        return True

    def _depart_ferry(self, fid: str):
        fs = self.ferries[fid]
        fs.status, fs.departed = "departed", self.t
        port = self.sc.ferries[fid].port
        self._ev("ferry_depart", ferry=fid, port=port, wagons=fs.loaded)
        self._try_berth(port)

    def _on_port_ready(self, d):
        for fid, fs in self.ferries.items():
            if self.sc.ferries[fid].port == d["port"] and fs.status == "loading" and fs.busy is None:
                self._try_load(fid)
        return True

    # ------------------------------------------------------------ инварианты

    def _check_invariants(self):
        on_track = defaultdict(list)
        for (n, t), ts in self.tracks.items():
            if ts.occupant:
                on_track[ts.occupant].append((n, t))
        for sid, ss in self.segs.items():
            if ss.occupant and ss.reserved:
                raise AssertionError(f"перегон {sid}: занят {ss.occupant} и одновременно зарезервирован {ss.reserved}")
            # занятость может держаться за ушедшим поездом (хвост + интервал) — это нормально
        running = defaultdict(list)
        for tr in self.trains.values():
            if tr.status == "running":
                running[tr.run.seg].append(tr.spec.id)
                if self.segs[tr.run.seg].occupant != tr.spec.id:
                    raise AssertionError(f"{tr.spec.id} едет по {tr.run.seg}, но перегон за ним не числится")
            if tr.status in ("at_node", "passing") and tr.track:
                if self.tracks[(tr.node, tr.track)].occupant != tr.spec.id:
                    raise AssertionError(f"{tr.spec.id} стоит на {tr.node}/{tr.track}, путь за ним не числится")
        for sid, lst in running.items():
            if len(lst) > 1:
                raise AssertionError(f"на однопутном перегоне {sid} одновременно {lst}")
