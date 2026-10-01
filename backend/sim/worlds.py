"""Миры для честной оценки решений.

expected_world(sim) — копия симулятора в момент t, где будущее заменено тем, что
диспетчер ЗНАЛ в этот момент:
  * поезда, которых ещё нет, появятся по последнему объявленному прогнозу (или по плану);
  * паромы придут по последнему объявленному ETA;
  * идущие ремонты и закрытия закончатся к объявленному сроку;
  * сбоев, о которых ещё не сообщили, не будет; необъявленных окон — тоже;
  * будущих обновлений прогнозов нет — знание замораживается на момент t;
  * время хода — номинальное (шума нет), уже идущие поезда — по публичной оценке ETA.
Настоящее (положения, занятость, заказанные маршруты) не меняется.

Проверка честности: наблюдение в момент t в ожидаемом мире совпадает с наблюдением
в настоящем мире (tests/test_replay.py).
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import heapq

from .engine import Simulator

OVERDUE_S = 600   # прогноз уже прошёл, а события нет — диспетчер ждёт «вот-вот», через 10 минут


def _latest(forecasts, t, default):
    val = default
    for f in forecasts:
        if f.at <= t:
            val = f.eta
    return val


def _jitter(sample: int, key: str, amp_s: int) -> int:
    h = hashlib.sha256(f"{sample}|{key}|jitter".encode()).digest()
    return int((int.from_bytes(h[:8], "big") / 2**64 * 2 - 1) * amp_s)


def retro_world(sim: Simulator, sample: int = 0) -> Simulator:
    """Настоящее будущее дня. sample 0 — ровно как было; иначе — те же события, но заново
    разыгранный мелкий разброс времени хода (чтобы отличать эффект событий от случайности)."""
    if sample == 0:
        return sim.clone()
    w = sim.clone()
    w.sc = copy.copy(sim.sc)
    w.sc.seed = sim.sc.seed * 7919 + sample
    return w


def expected_world(sim: Simulator, sample: int | None = None) -> Simulator:
    """sample None — номинальное ожидание; число — один из правдоподобных вариантов будущего
    при том же знании: разброс времени хода и ошибка прогнозов (поезда ±10 мин, паромы ±30 мин)."""
    t = sim.t
    w = sim.clone()
    sc0 = sim.sc
    revealed = {f["id"] for f in sim.revealed_failures}

    # --- сценарий, как его знали в момент t
    sc = copy.copy(sc0)
    sc.params = dataclasses.replace(sc0.params, running_noise=0.0 if sample is None else sc0.params.running_noise)
    if sample is not None:
        sc.seed = sc0.seed * 104729 + sample
    sc.trains = {}
    for tid, sp in sc0.trains.items():
        tr = w.trains[tid]
        known = tuple(f for f in sp.forecasts if f.at <= t)
        exp = tr.appear_at
        if tr.status == "pending" and not tr.waiting_track:
            e = _latest(known, t, sp.appear_planned)
            if sample is not None:
                e += _jitter(sample, tid, 600)
            exp = e if e >= t else t + OVERDUE_S
        sc.trains[tid] = dataclasses.replace(sp, forecasts=known, appear_actual=exp)
    sc.ferries = {}
    for fid, fs in sc0.ferries.items():
        known = tuple(u for u in fs.eta_updates if u.at <= t)
        exp = fs.actual_arrival
        if w.ferries[fid].status == "expected":
            e = _latest(known, t, fs.planned_arrival)
            if sample is not None:
                e += _jitter(sample, fid, 1800)
            exp = e if e >= t else t + OVERDUE_S
        sc.ferries[fid] = dataclasses.replace(fs, eta_updates=known, actual_arrival=exp)
    keep = []
    for d in sc0.disruptions:
        if d.kind == "maintenance_window":
            st = w.windows[d.id]["state"]
            if st != "scheduled" or (d.announce_at is not None and d.announce_at <= t):
                keep.append(d)
        elif d.id in revealed:
            keep.append(d)
    sc.disruptions = tuple(keep)
    kept = {d.id for d in keep}
    w.sc = sc
    for tid, tr in w.trains.items():
        tr.spec = sc.trains[tid]
    w.windows = {k: v for k, v in w.windows.items() if k in kept}

    # --- очередь событий: убрать то, чего диспетчер не знал, и переставить ожидаемое
    queue = []
    for ev in w.q:
        et, _, _, kind, data = ev
        if kind in ("FAILURE_START", "OUTAGE_START", "WINDOW_START") and data["dis"] not in kept:
            continue
        if kind == "ANNOUNCE":
            continue           # будущих прогнозов в «мире на момент t» не существует
        if kind == "FERRY_ARRIVE":
            continue           # переставим ниже по ETA
        if kind in ("FAILURE_END", "OUTAGE_END"):
            continue           # переставим ниже на объявленный срок
        queue.append(ev)
    w.q = queue
    heapq.heapify(w.q)

    for fid, fs in w.ferries.items():
        if fs.status == "expected":
            w._push(sc.ferries[fid].actual_arrival, "FERRY_ARRIVE", ferry=fid)

    for tid, tr in w.trains.items():
        # ремонт — к объявленному сроку
        if tr.failure:
            old = tr.failure["until"]
            new = tr.failure["est_until"] if tr.failure["est_until"] > t else t + OVERDUE_S
            tr.failure["until"] = new
            w._push(new, "FAILURE_END", train=tid, dis=tr.failure["id"])
            if tr.status in ("at_node", "passing") and tr.next_ev and tr.next_ev[1] == "ENTER" and tr.next_ev[0] >= old:
                _move(w, tr, tr.next_ev[0] + (new - old), t)
        # поезда в пути — по публичной оценке, без шума
        if tr.status in ("running", "approaching") and tr.run and tr.next_ev:
            delta = tr.run.est_head_end - tr.run.head_end
            tr.run.head_end = tr.run.est_head_end
            if delta:
                _move(w, tr, tr.next_ev[0] + delta, t)
        # ещё не появившиеся — по прогнозу
        if tr.status == "pending" and not tr.waiting_track and tr.next_ev and tr.next_ev[1] in ("APPEAR", "APPROACH"):
            exp = sc.trains[tid].appear_actual
            tr.appear_at = exp
            when = exp - sc.params.approach_lead_s if tr.next_ev[1] == "APPROACH" else exp
            _move(w, tr, when, t)
    for sid, ss in w.segs.items():
        if ss.outage:
            new = ss.outage["est_until"] if ss.outage["est_until"] > t else t + OVERDUE_S
            ss.outage["until"] = new
            w._push(new, "OUTAGE_END", dis=ss.outage["id"])
    return w


def _move(w: Simulator, tr, when: int, now: int):
    """Перенести текущее движенческое событие поезда (старое станет устаревшим по версии)."""
    _, kind, data = tr.next_ev
    data = {k: v for k, v in data.items() if k not in ("train", "v")}
    w._push_train(tr, max(now, int(when)), kind, **data)

