"""Игровой режим: человек — диспетчер участка.

Игрок для движка — такая же политика, как скрипт или ИИ: видит только наблюдение,
его приказы проходят ту же проверку. Пауза в моменты решений даёт время подумать,
но не даёт знаний: экран строится из того же наблюдения, а правда сценария
(фактические опоздания, будущие сбои, шум) открывается только после конца смены.

Время: клиент двигает игровые часы (advance dt). Приказ «сейчас» исполняется
в момент `clock`, а не задним числом в момент последнего события.
"""
from __future__ import annotations

import copy
import uuid

from dispatchers import PriorityDispatcher
from sim import Simulator, compute_metrics
from sim.metrics import layout_of
from sim.observe import build_observation

PAUSE_KINDS = {"approach", "ready", "disruption", "ferry"}


class PlayerPolicy:
    """Приказы игрока + (по желанию) автопилот для поездов, которыми он не управляет вручную."""
    name = "player"

    def __init__(self, autopilot: bool = False):
        self.autopilot = autopilot
        self.auto_override: dict[str, bool] = {}   # поезд -> вкл/выкл автопилота
        self.queue: list[dict] = []                # приказы «сейчас»
        self.holds: dict[str, int | None] = {}     # держать до (None — до отмены)
        self.pass_next: dict[str, str] = {}        # пропустить на проход у станции
        self.routes: dict[tuple[str, str], str] = {}  # (поезд, узел) -> следующий узел
        self.fallback = PriorityDispatcher()

    def is_auto(self, tid: str) -> bool:
        return self.auto_override.get(tid, self.autopilot)

    def held(self, tid: str, t: int) -> bool:
        if tid not in self.holds:
            return False
        until = self.holds[tid]
        if until is not None and t >= until:
            del self.holds[tid]
            return False
        return True

    def handles(self, a: dict, t: int) -> bool:
        """Решение по этому действию уже принято приказом — не надо будить игрока."""
        tid = a["train"]
        if self.held(tid, t):
            return True
        if a["kind"] == "through" and self.pass_next.get(tid) == a["at"]:
            return True
        return self.is_auto(tid)

    def _route_ok(self, a):
        want = self.routes.get((a["train"], a["at"]))
        return want is None or want == a["next"]

    def decide(self, obs: dict) -> list[dict]:
        t = obs["t"]
        cmds, self.queue = self.queue, []
        manual = {c.get("train") for c in cmds if c.get("type") == "dispatch"}
        legal = [a for a in obs["legal"] if a["train"] not in manual and self._route_ok(a)]
        used = set()
        for a in legal:
            if (a["kind"] == "through" and self.pass_next.get(a["train"]) == a["at"]
                    and not self.held(a["train"], t) and a["segment"] not in used):
                cmds.append({"type": "dispatch", "train": a["train"], "next": a["next"]})
                manual.add(a["train"])
                used.add(a["segment"])
        auto = [a for a in legal if a["train"] not in manual and a["segment"] not in used
                and self.is_auto(a["train"]) and not self.held(a["train"], t)]
        if auto:
            cmds += self.fallback.decide(dict(obs, legal=auto))
        for tid, node in list(self.pass_next.items()):
            tr = next((x for x in obs["trains"] if x["id"] == tid), None)
            if tr and node in tr["visited"]:
                del self.pass_next[tid]
        return cmds


class GameSession:
    def __init__(self, scenario, raw: dict, autopilot=False, pause_on=None):
        self.id = uuid.uuid4().hex[:10]
        self.sc, self.raw = scenario, raw
        self.sim = Simulator(scenario)
        self.policy = PlayerPolicy(autopilot)
        self.clock = 0
        self.pause_on = set(PAUSE_KINDS if pause_on is None else pause_on) & PAUSE_KINDS
        self.acked: set = set()
        self.seen_dis = 0
        self.seen_ferries = 0
        self.prompt: dict | None = None
        self.notes: list[dict] = []
        self._baseline = None

    # ---------------------------------------------------------- пауза

    def _pause_if(self, obs: dict) -> bool:
        reasons = []
        trains = {x["id"]: x for x in obs["trains"]}
        for a in obs["legal"]:
            if self.policy.handles(a, obs["t"]):
                continue
            tr = trains[a["train"]]
            stamp = tr.get("since") if a["kind"] == "depart" else (tr.get("run") or {}).get("entered")
            key = (a["train"], a["kind"], a["at"], stamp)
            if key in self.acked:
                continue
            want = "approach" if a["kind"] == "through" else "ready"
            if want in self.pause_on:
                self.acked.add(key)
                reasons.append({"kind": want, "train": a["train"], "at": a["at"], "next": a["next"]})
        dis = obs["known_disruptions"]
        if len(dis) > self.seen_dis:
            if "disruption" in self.pause_on:
                reasons += [{"kind": "disruption", "disruption": d} for d in dis[self.seen_dis:]]
            self.seen_dis = len(dis)
        arrived = [f for f in obs["ferries"] if f["arrived"] is not None]
        if len(arrived) > self.seen_ferries:
            if "ferry" in self.pause_on:
                reasons += [{"kind": "ferry", "ferry": f["id"], "port": f["port"]}
                            for f in arrived[self.seen_ferries:]]
            self.seen_ferries = len(arrived)
        if reasons:
            # одинаковые подходы одного поезда (несколько вариантов пути) — один пункт
            uniq, seen = [], set()
            for r in reasons:
                k = (r["kind"], r.get("train"), r.get("at"), (r.get("disruption") or {}).get("id"), r.get("ferry"))
                if k not in seen:
                    seen.add(k)
                    uniq.append(r)
            self.prompt = {"t": obs["t"], "reasons": uniq}
            return True
        return False

    # ---------------------------------------------------------- время

    @property
    def ended(self) -> bool:
        return self.sim.status != "running"

    def advance(self, dt: int):
        if self.ended or self.sim.paused:
            return
        target = min(self.clock + max(0, int(dt)), self.sc.horizon)
        r = self.sim.advance(self.policy, target, self._pause_if, open_ended=True)
        self.clock = self.sim.t if r == "paused" else (self.sim.end_time or target) if r == "ended" else target

    def resume(self):
        if self.sim.paused:
            self.sim.resume(self.policy)
            self.prompt = None
            self._settle()

    def _settle(self):
        """Исполнить приказы «сейчас» в момент clock (без продвижения часов)."""
        if self.ended:
            return
        if self.policy.queue:
            self.sim.wake_at(self.clock)
        r = self.sim.advance(self.policy, self.clock, self._pause_if, open_ended=True)
        if r == "paused":
            self.clock = self.sim.t

    # ---------------------------------------------------------- приказы

    def command(self, c: dict) -> dict:
        p = self.policy
        typ = c.get("type")
        tid = c.get("train")
        if typ == "dispatch":
            p.holds.pop(tid, None)
            p.queue.append({"type": "dispatch", "train": tid, "next": c.get("next"),
                            **({"track": c["track"]} if c.get("track") else {})})
        elif typ == "ferry_order":
            p.queue.append({"type": "ferry_order", "port": c.get("port"), "order": c.get("order")})
        elif typ == "hold":
            p.holds[tid] = c.get("until")
            p.pass_next.pop(tid, None)
        elif typ == "release":
            p.holds.pop(tid, None)
        elif typ == "pass_next":
            p.pass_next[tid] = c.get("node")
            p.holds.pop(tid, None)
        elif typ == "cancel_pass":
            p.pass_next.pop(tid, None)
        elif typ == "route":
            if c.get("next") is None:
                p.routes.pop((tid, c.get("at")), None)
            else:
                p.routes[(tid, c.get("at"))] = c.get("next")
        elif typ == "route_all":
            # «перевести стрелку»: всем перечисленным поездам на развилке `at` — в сторону `next`
            for t in c.get("trains", []):
                if c.get("next") is None:
                    p.routes.pop((t, c.get("at")), None)
                else:
                    p.routes[(t, c.get("at"))] = c.get("next")
        elif typ == "auto":
            p.auto_override[tid] = bool(c.get("on"))
        elif typ == "autopilot":
            p.autopilot = bool(c.get("on"))
            p.auto_override.clear()
        elif typ == "pause_on":
            self.pause_on = set(c.get("kinds", [])) & PAUSE_KINDS
        else:
            return {"ok": False, "reason": "unknown_command"}
        n_rej = len(self.sim.rejections)
        if not self.sim.paused:
            self._settle()
        new = self.sim.rejections[n_rej:]
        return {"ok": not new, "rejections": new}

    # ---------------------------------------------------------- выдача

    def state(self) -> dict:
        sim = self.sim
        obs = build_observation(sim)
        res_layout = layout_of(self.sc)
        names = {n.id: n.name for n in self.sc.nodes.values()}
        p = self.policy
        return {
            "id": self.id, "scenario": {"id": self.sc.id, "name": self.sc.name},
            "clock": self.clock, "status": sim.status, "paused": sim.paused, "prompt": self.prompt,
            "obs": obs, "layout": res_layout, "names": names,
            "types": {k: {"speed_kmh": v.speed_kmh} for k, v in self.sc.types.items()},
            "paths": {tid: tr.path for tid, tr in sim.trains.items()},
            "log": sim.log[-60:],
            "rejections": sim.rejections[-10:],
            "alerts": gridlocks(obs),
            "metrics": compute_metrics(sim) if self.ended else _live_metrics(sim, self.clock),
            "orders": {
                "autopilot": p.autopilot, "auto_override": p.auto_override,
                "holds": p.holds, "pass_next": p.pass_next,
                "routes": [{"train": k[0], "at": k[1], "next": v} for k, v in p.routes.items()],
                "pause_on": sorted(self.pause_on),
            },
        }

    def report(self) -> dict:
        if not self.ended:
            return {"ready": False}
        if self._baseline is None:
            base = Simulator(self.sc).run(PriorityDispatcher())
            self._baseline = compute_metrics(base)
        return {"ready": True, "player": compute_metrics(self.sim), "autopilot": self._baseline,
                "scenario_truth": self.raw}


def _live_metrics(sim: Simulator, clock: int) -> dict:
    """Текущий счёт: задержки «на сейчас» по уже доехавшим и уже опаздывающим."""
    s = copy.copy(sim)
    s.end_time = clock
    m = compute_metrics(s)
    m["status"] = "running"
    return m


def gridlocks(obs: dict) -> list[dict]:
    """Видимые диспетчеру сцепки: два встречных на соседних станциях, и ни один не может двинуться.
    Только подсказка на экране — движок ничего не разруливает."""
    adj: dict[str, set] = {}
    for sg in obs["segments"]:
        adj.setdefault(sg["a"], set()).add(sg["b"])
        adj.setdefault(sg["b"], set()).add(sg["a"])
    movable = {a["train"] for a in obs["legal"]}
    standing = [t for t in obs["trains"] if t["status"] == "at_node" and t["node"] and t["id"] not in movable
                and not t["failure"] and t["node"] not in t["destinations"] and t.get("ready_at_est", 0) <= obs["t"]]
    out = []
    for i, x in enumerate(standing):
        for y in standing[i + 1:]:
            if y["node"] in adj.get(x["node"], ()) and x["came_from"] != y["node"] and y["came_from"] != x["node"]                     and x["came_from"] is not None and y["came_from"] is not None:
                out.append({"kind": "gridlock", "trains": [x["id"], y["id"]], "nodes": [x["node"], y["node"]]})
    return out
