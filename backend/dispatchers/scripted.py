"""«Записанный» диспетчер смены: директивы из сценария поверх базовой эвристики.

Директивы (поле dispatcher.directives сценария):
  {"train": "F302", "at": "R3", "hold_until": "10:40"}   — не отправлять с R3 раньше 10:40
  {"train": "F302", "at": "R3", "wait_for": "P101"}       — ждать, пока P101 проследует R3
  {"train": "C201", "at": "JUNC", "route": "RK"}          — с узла только на RK (Курык)
  {"port": "AKTAU", "ferry_order": ["C203", "C201"], "from": "08:00"} — порядок погрузки

Директивы только СУЖАЮТ список законных действий — дать поезду то, что запрещено
правилами, они не могут. Политика видит лишь наблюдение.
"""
from __future__ import annotations

from sim.model import parse_time

from .priority import PriorityDispatcher


class ScriptedDispatcher:
    name = "scripted"

    def __init__(self, directives: list[dict], fallback=None):
        self.directives = directives or []
        self.fallback = fallback or PriorityDispatcher()
        self._woke: set[int] = set()
        self._ordered: set[int] = set()

    def _passed(self, other: dict, node: str) -> bool:
        return node in other["visited"] and (other["node"] != node or other["arrived_at"] is not None)

    def decide(self, obs: dict) -> list[dict]:
        t = obs["t"]
        trains = {x["id"]: x for x in obs["trains"]}
        extra = []
        allowed = []
        for a in obs["legal"]:
            ok = True
            for d in self.directives:
                if d.get("train") != a["train"] or d.get("at") != a["at"]:
                    continue
                if "hold_until" in d and t < parse_time(d["hold_until"]):
                    ok = False
                if "wait_for" in d and not self._passed(trains[d["wait_for"]], a["at"]):
                    ok = False
                if "route" in d and a["next"] != d["route"]:
                    ok = False
            if ok:
                allowed.append(a)
        for i, d in enumerate(self.directives):
            if "hold_until" in d and i not in self._woke:
                when = parse_time(d["hold_until"])
                if when > t:
                    extra.append({"type": "wake_at", "time": when})
                    self._woke.add(i)
            if "ferry_order" in d and i not in self._ordered and t >= parse_time(d.get("from", "00:00")):
                extra.append({"type": "ferry_order", "port": d["port"], "order": d["ferry_order"]})
                self._ordered.add(i)
        return self.fallback.decide(dict(obs, legal=allowed)) + extra
