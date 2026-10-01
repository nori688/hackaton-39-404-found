"""Точки решений и 2–3 разумные альтернативы в каждой.

Строится только из наблюдения в момент решения (того, что видел диспетчер) и команд факта.
Виды точек:
  contest   — на один перегон претендуют несколько поездов: кого первым;
  cross     — отправили поезд, а навстречу идёт другой: можно было скрестить здесь;
  overtake  — отправили медленный, а сзади догоняет более важный/быстрый: можно было дать обгон;
  hold      — поезд мог ехать, но его придержали: можно было отправить сразу;
  route     — на развилке выбрали один порт: можно было другой;
  ferry     — паром начал погрузку: можно было грузить первым другой состав;
  autopilot — автопилот на этом месте поступил бы иначе (для «решений лучше алгоритма»).
"""
from __future__ import annotations

from dispatchers import PriorityDispatcher

HOLD_CAP_S = 60 * 60          # «держать до прохода …» — не дольше часа
OVERTAKE_WINDOW_S = 45 * 60   # догоняющий поезд должен быть не дальше 45 минут
SPEED = {"passenger": 80, "container": 65, "freight": 55}


def _dispatches(cmds):
    return {c["train"]: c["next"] for c in cmds if c.get("type") == "dispatch"}


def _without(cmds, train):
    return [c for c in cmds if not (c.get("type") == "dispatch" and c.get("train") == train)]


def _free_fitting(node: dict, length: int, exclude_trains=()) -> int:
    return sum(1 for t in node["tracks"] if t["kind"] == "main" and t["length_m"] >= length
               and t["occupant"] is None and (t["reserved"] is None or t["reserved"] in exclude_trains))


def _graph(obs):
    adj = {}
    for s in obs["segments"]:
        adj.setdefault(s["a"], set()).add(s["b"])
        adj.setdefault(s["b"], set()).add(s["a"])
    return adj


def _reaches(adj, start, avoid, targets):
    seen, stack = {start, avoid}, [start]
    while stack:
        u = stack.pop()
        if u in targets:
            return True
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


def candidates(obs: dict, fact_cmds: list[dict], seen: set) -> list[dict]:
    t = obs["t"]
    trains = {x["id"]: x for x in obs["trains"]}
    nodes = {n["id"]: n for n in obs["nodes"]}
    adj = _graph(obs)
    legal = obs["legal"]
    D = _dispatches(fact_cmds)
    out: list[dict] = []

    def add(kind, key, label, cmds, holds=(), involved=(), node=None):
        if key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "label": label, "cmds": cmds, "holds": list(holds),
                    "involved": list(involved), "node": node})

    by_seg: dict[str, list[dict]] = {}
    for a in legal:
        by_seg.setdefault(a["segment"], []).append(a)

    # --- кто первым на перегон
    for seg, acts in by_seg.items():
        contenders = sorted({a["train"] for a in acts})
        winner = next((a for a in acts if D.get(a["train"]) == a["next"]), None)
        if len(contenders) >= 2 and winner:
            others = [a for a in acts if a["train"] != winner["train"]]
            others.sort(key=lambda a: -trains[a["train"]]["weight"])
            for o in others[:2]:
                add("contest", ("contest", seg, winner["train"], o["train"], t // 600),
                    f"Пропустить первым {o['train']} вместо {winner['train']}",
                    _without(fact_cmds, winner["train"]) + [{"type": "dispatch", "train": o["train"], "next": o["next"]}],
                    involved=[winner["train"], o["train"]], node=o["at"])

    # --- одиночные действия
    for a in legal:
        x = trains[a["train"]]
        here, nxt = a["at"], a["next"]
        sent = D.get(a["train"])
        if sent == nxt and len({b["train"] for b in by_seg[a["segment"]]}) == 1:
            # скрестить здесь со встречным
            for y in trains.values():
                if y["id"] == x["id"] or y["status"] in ("done", "delivered", "pending"):
                    continue
                at_nxt = y["node"] == nxt and y["status"] in ("at_node", "passing") and y["came_from"] != here
                into_nxt = y.get("run") and y["run"]["to"] == nxt and y["run"]["from"] not in (None, here)
                if not (at_nxt or into_nxt):
                    continue
                if not _reaches(adj, here, nxt, set(y["destinations"])):
                    continue
                if _free_fitting(nodes[here], y["length_m"], (x["id"],)) < (1 if x["status"] == "at_node" else 2):
                    continue
                add("cross", ("cross", x["id"], here, y["id"]),
                    f"Придержать {x['id']} на {here} и скрестить здесь со встречным {y['id']}",
                    _without(fact_cmds, x["id"]),
                    holds=[{"train": x["id"], "at": here, "until_t": t + HOLD_CAP_S, "until_passed": y["id"]}],
                    involved=[x["id"], y["id"]], node=here)
            # дать обгон догоняющему
            for z in trains.values():
                if z["id"] == x["id"] or not z.get("run"):
                    continue
                r = z["run"]
                behind = r["to"] == here and r["from"] is not None and r["from"] == x["came_from"]
                if not behind or r["eta"] - t > OVERTAKE_WINDOW_S:
                    continue
                more_important = z["weight"] > x["weight"] or SPEED.get(z["type"], 60) > SPEED.get(x["type"], 60) + 5
                if not more_important or not _reaches(adj, here, x["came_from"], set(z["destinations"])):
                    continue
                if _free_fitting(nodes[here], z["length_m"], (x["id"],)) < (1 if x["status"] == "at_node" else 2):
                    continue
                add("overtake", ("overtake", x["id"], here, z["id"]),
                    f"Придержать {x['id']} на {here} и пропустить вперёд {z['id']}",
                    _without(fact_cmds, x["id"]),
                    holds=[{"train": x["id"], "at": here, "until_t": t + HOLD_CAP_S, "until_passed": z["id"]}],
                    involved=[x["id"], z["id"]], node=here)
        elif sent is None and not any(D.get(b["train"]) for b in by_seg[a["segment"]]):
            stamp = x.get("since") if a["kind"] == "depart" else (x.get("run") or {}).get("entered")
            verb = "пропустить на проход" if a["kind"] == "through" else "отправить сразу"
            add("hold", ("hold", x["id"], here, stamp),
                f"{x['id']} на {here}: {verb} на {nxt}, не держать",
                fact_cmds + [{"type": "dispatch", "train": x["id"], "next": nxt}],
                involved=[x["id"]], node=here)

    # --- развилка: другой порт
    by_train: dict[str, set] = {}
    for a in legal:
        by_train.setdefault(a["train"], set()).add(a["next"])
    for tid, nexts in by_train.items():
        if len(nexts) >= 2 and D.get(tid) in nexts:
            at = next(a["at"] for a in legal if a["train"] == tid)
            for other in sorted(nexts - {D[tid]}):
                add("route", ("route", tid, at),
                    f"{tid}: на развилке {at} в сторону {other}, а не {D[tid]}",
                    _without(fact_cmds, tid) + [{"type": "dispatch", "train": tid, "next": other}],
                    involved=[tid], node=at)

    # --- паром начал погрузку
    for f in obs["ferries"]:
        if f["status"] != "loading" or f["loaded"] != 0:
            continue
        waiting = [x for x in trains.values() if x.get("port") and x["port"]["node"] == f["port"]
                   and x["port"]["remaining"] > 0 and x["port"]["available_at"] <= t]
        if len(waiting) < 2:
            continue
        order = obs["ferry_order"].get(f["port"], [])
        waiting.sort(key=lambda x: (order.index(x["id"]) if x["id"] in order else len(order),
                                    x["port"]["available_at"], x["id"]))
        for o in waiting[1:3]:
            add("ferry", ("ferry", f["id"], o["id"]),
                f"Паром {f['id']}: грузить первым {o['id']}, а не {waiting[0]['id']}",
                fact_cmds + [{"type": "ferry_order", "port": f["port"],
                              "order": [o["id"]] + [w["id"] for w in waiting if w["id"] != o["id"]]}],
                involved=[o["id"], waiting[0]["id"]], node=f["port"])

    # --- как поступил бы автопилот (если иначе)
    auto = PriorityDispatcher().decide(dict(obs))
    A = _dispatches(auto)
    if A != D and legal:
        diff = sorted(set(A.items()) ^ set(D.items()))
        trains_diff = sorted({tid for tid, _ in diff})
        add("autopilot", ("autopilot", tuple(diff)),
            "Как поступил бы автопилот: " + ", ".join(
                f"{tid} → {A[tid]}" if tid in A else f"{tid} — ждать" for tid in trains_diff),
            [c for c in fact_cmds if c.get("type") != "dispatch"] + [c for c in auto if c.get("type") == "dispatch"],
            involved=trains_diff, node=next((a["at"] for a in legal if a["train"] in trains_diff), None))
    return out


def ferry_moment(obs: dict) -> bool:
    return any(f["status"] == "loading" and f["loaded"] == 0 for f in obs["ferries"])
