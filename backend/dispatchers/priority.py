"""Базовая эвристика диспетчера: приоритет + простая защита от сцепок.

Работает ТОЛЬКО с наблюдением (словарём). Никакого доступа к движку.
"""
from __future__ import annotations

from collections import defaultdict


def _graph(obs):
    adj = defaultdict(list)
    for s in obs["segments"]:
        adj[s["a"]].append(s["b"])
        adj[s["b"]].append(s["a"])
    return adj


def _behind(adj, start, came, targets):
    """Какие из targets достижимы из start, не возвращаясь в came."""
    seen, stack, found = {came, start}, [start], []
    while stack:
        u = stack.pop()
        if u in targets:
            found.append(u)
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return found


class PriorityDispatcher:
    name = "priority"

    def decide(self, obs: dict) -> list[dict]:
        trains = {t["id"]: t for t in obs["trains"]}
        nodes = {n["id"]: n for n in obs["nodes"]}
        adj = _graph(obs)
        free = {nid: sum(1 for tr in n["tracks"] if tr["occupant"] is None and tr["reserved"] is None
                         and tr["kind"] == "main") for nid, n in nodes.items()}
        free_port = {nid: sum(1 for tr in n["tracks"] if tr["occupant"] is None and tr["reserved"] is None
                              and tr["kind"] == "port") for nid, n in nodes.items()}

        by_train = defaultdict(list)
        for a in obs["legal"]:
            by_train[a["train"]].append(a)
        choices = []
        for tid, acts in by_train.items():
            if len(acts) == 1:
                choices.append(acts[0])
                continue
            # развилка: выбираем порт, где больше свободных путей
            tr = trains[tid]

            def score(a):
                ports = _behind(adj, a["next"], a["at"], set(tr["destinations"]))
                return (max((free_port.get(p, 0) for p in ports), default=0), a["next"] == min(x["next"] for x in acts))
            choices.append(max(acts, key=score))

        def prio(a):
            tr = trains[a["train"]]
            return (0 if a["kind"] == "through" else 1, -tr["weight"], tr["planned_arrival"], a["train"])

        cmds, used_segs = [], set()
        taken = defaultdict(int)
        for a in sorted(choices, key=prio):
            if a["segment"] in used_segs:
                continue
            tr = trains[a["train"]]
            nxt = a["next"]
            if nodes[nxt]["type"] != "boundary" and nxt not in tr["destinations"]:
                left = free.get(nxt, 0) - taken[nxt] - 1
                if left < 1 and not self._crossing(obs, trains, nxt, a["at"]):
                    continue   # не занимаем последний путь, если там не скрещение — иначе сцепка
            if self._locks_with_oncoming(obs, trains, nodes, adj, a, taken):
                continue   # встречный длинный не разъедется с нами на nxt — не лезем
            cmds.append({"type": "dispatch", "train": a["train"], "next": nxt})
            used_segs.add(a["segment"])
            taken[nxt] += 1
        return cmds

    @staticmethod
    def _crossing(obs, trains, node, from_node):
        """Есть ли на node (или на подходе к нему) поезд, идущий навстречу — в сторону from_node."""
        for t in trains.values():
            if t["node"] == node and t["status"] in ("at_node", "passing"):
                if t["came_from"] not in (None, from_node) and node not in t["destinations"]:
                    return True
            run = t.get("run")
            if run and run["to"] == node and run["from"] not in (None, from_node):
                if node not in t["destinations"]:
                    return True
        return False

    @staticmethod
    def _locks_with_oncoming(obs, trains, nodes, adj, a, taken):
        """Сцепка длинных: мы встаём на nxt, встречный стоит/едет на следующую за nxt станцию,
        и ни он не помещается на оставшиеся пути nxt, ни мы — на свободные пути его станции."""
        tr = trains[a["train"]]
        nxt, here = a["next"], a["at"]
        if nodes[nxt]["type"] == "boundary" or nxt in tr["destinations"]:
            return False
        ahead = [v for v in adj[nxt] if v != here and _behind(adj, v, nxt, set(tr["destinations"]))]
        free_nxt = sorted((t["length_m"] for t in nodes[nxt]["tracks"]
                           if t["occupant"] is None and t["reserved"] is None and t["kind"] == "main"), reverse=True)
        mine = next((L for L in free_nxt if L >= tr["length_m"]), None)
        if mine is not None:
            free_nxt.remove(mine)
        for c in ahead:
            if nodes[c]["type"] == "boundary":
                continue
            free_c = [t["length_m"] for t in nodes[c]["tracks"]
                      if t["occupant"] is None and t["reserved"] is None and t["kind"] == "main"]
            for o in trains.values():
                there = (o["node"] == c and o["status"] == "at_node") or (
                    o.get("run") and o["run"]["to"] == c and o["run"]["from"] != nxt)
                if not there or o["id"] == tr["id"] or c in o["destinations"]:
                    continue
                if o["node"] == c and o["came_from"] == nxt:
                    continue   # он идёт в ту же сторону, что и мы
                if not _behind(adj, nxt, c, set(o["destinations"])):
                    continue   # ему не через nxt
                o_fits = any(L >= o["length_m"] for L in free_nxt)
                me_fits = any(L >= tr["length_m"] for L in free_c)
                if not o_fits and not me_fits:
                    return True
        return False
