"""Политики реплея.

LogReplayPolicy — воспроизводит записанную смену байт в байт: в вызов №seq отдаёт ровно
  те команды, которые тогда были применены.
Continuation — кто доигрывает день после подменённого решения, если исходную политику
  нельзя спросить снова (живой игрок): автопилот + постоянные приказы игрока о портах.
Override — «подменить одно решение»: в момент развилки отдаёт заданные команды, дальше —
  продолжение, с временными «держать до …» для альтернатив вида «скрестить здесь».
  Базовая линия (факт) идёт через тот же Override с неизменёнными командами —
  поэтому факт и альтернатива сравниваются при одинаковом продолжении дня.
"""
from __future__ import annotations

import copy

from dispatchers import PriorityDispatcher


class LogReplayPolicy:
    name = "log"

    def __init__(self, decisions: list[dict]):
        self.by_seq = {d["seq"]: d["applied"] for d in decisions}
        self.n = 0

    def decide(self, obs):
        cmds = copy.deepcopy(self.by_seq.get(self.n, []))
        self.n += 1
        return cmds


class Continuation:
    """Автопилот, уважающий постоянные приказы о маршруте (выбор порта на развилке)."""
    name = "continuation"

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})          # (поезд, узел) -> следующий узел
        self.inner = PriorityDispatcher()

    def decide(self, obs):
        legal = [a for a in obs["legal"]
                 if self.routes.get((a["train"], a["at"])) in (None, a["next"])]
        return self.inner.decide(dict(obs, legal=legal))


def _passed(obs_trains: dict, tid: str, node: str) -> bool:
    t = obs_trains.get(tid)
    return bool(t) and node in t["visited"] and (t["node"] != node or t["arrived_at"] is not None)


class Override:
    name = "override"

    def __init__(self, first_cmds: list[dict], continuation, holds=(), warmup: bool = False):
        self.first = copy.deepcopy(first_cmds)
        self.cont = continuation
        self.holds = [dict(h) for h in holds]     # {train, at, until_t, until_passed}
        self.warmup = warmup
        self.called = False

    def _held(self, obs, a) -> bool:
        trains = {t["id"]: t for t in obs["trains"]}
        for h in self.holds:
            if h["train"] != a["train"] or h["at"] != a["at"]:
                continue
            if obs["t"] >= h["until_t"]:
                continue
            if h.get("until_passed") and _passed(trains, h["until_passed"], h["at"]):
                continue
            return True
        return False

    def decide(self, obs):
        if not self.called:
            self.called = True
            if self.warmup:
                # продвинуть внутреннее состояние продолжения так же, как в факте (его ответ = факт)
                self.cont.decide(copy.deepcopy(obs))
            wakes = [{"type": "wake_at", "time": h["until_t"]} for h in self.holds if h["until_t"] > obs["t"]]
            return copy.deepcopy(self.first) + wakes
        if self.holds:
            obs = dict(obs, legal=[a for a in obs["legal"] if not self._held(obs, a)])
        return self.cont.decide(obs)
