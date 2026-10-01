"""Генератор случайного синтетического дня в пределах допустимого.

Всё определяется одним seed: тот же seed + те же настройки -> тот же день.
Топология фиксирована (10 точек, два порта), а параметры случайны в реалистичных границах:
  * перегоны 16–36 км, главные пути разъездов 1100–1200 м, вторые не короче 850 м;
  * грузовые до 850 м, контейнерные 850–1050 м: любые два встречных могут скреститься
    на любом разъезде (длину состава в жизни ограничивают полезной длиной путей участка);
  * пути порта: Актау 5–8, Курык 3–5;
  * интервалы входа поездов с одной точки не меньше 25 мин (соседний участок так не отдаст);
  * опоздания на входе: прогноз приходит заранее, но может ошибаться на ±15 мин, иногда не приходит вовсе;
  * поломки: объявленная длительность — 40–110% фактической;
  * шторм: паромы сдвигаются на часы, ETA уточняется ступенями и никогда не совпадает с фактом заранее.
Плановые времена считаются той же номинальной физикой, что в движке (scenarios.build.plan).
"""
from __future__ import annotations

import copy
import math
import random

from sim.model import fmt_time, load_scenario, parse_time

from .build import BASE, PORTS, C, F, P, plan, route, straight, throat_sides

LEVELS = {
    "traffic": {
        "low":    {"containers": 6, "east_freight": 6, "pass_pairs": 1, "aty_pass": 0, "west_freight": 1,
                   "cement": 1, "oil": 1},
        "normal": {"containers": 9, "east_freight": 8, "pass_pairs": 2, "aty_pass": 1, "west_freight": 2,
                   "cement": 1, "oil": 1},
        "high":   {"containers": 12, "east_freight": 10, "pass_pairs": 2, "aty_pass": 1, "west_freight": 3,
                   "cement": 2, "oil": 2},
    },
    "disruptions": {
        "none":   {"failures": (0, 0), "outage_p": 0.0, "window_p": 0.0, "late_p": 0.0, "storm_p": 0.0},
        "low":    {"failures": (0, 1), "outage_p": 0.1, "window_p": 0.3, "late_p": 0.15, "storm_p": 0.1},
        "normal": {"failures": (1, 2), "outage_p": 0.3, "window_p": 0.6, "late_p": 0.25, "storm_p": 0.25},
        "high":   {"failures": (2, 4), "outage_p": 0.6, "window_p": 0.8, "late_p": 0.35, "storm_p": 0.5},
    },
}

MIN_GAP_S = 25 * 60


def _t(sec: float) -> str:
    return fmt_time(int(sec) - int(sec) % 60)


def _spread(rng, n, start, end, jitter, min_gap):
    """n моментов, равномерно по [start, end] с дрожанием и минимальным зазором."""
    if n <= 0:
        return []
    step = (end - start) / n
    out = sorted(start + step * (i + 0.5) + rng.uniform(-jitter, jitter) for i in range(n))
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + min_gap)
    return [max(0, x) for x in out]


def _layout(rng, raw):
    for s in raw["segments"]:
        s["length_km"] = round(min(36, max(16, s["length_km"] * rng.uniform(0.8, 1.2))), 1)
    for n in raw["nodes"]:
        if n["type"] == "siding":
            k = 3 if rng.random() < 0.3 else 2
            # главный путь — под самый длинный состав; второй не короче 850 м (норма для скрещения
            # с любым грузовым), примерно у половины разъездов он тоже длинный
            n["tracks"] = [{"id": "1", "length_m": rng.choice([1100, 1150, 1200])},
                           {"id": "2", "length_m": rng.choice([1100, 1150]) if rng.random() < 0.5
                            else rng.choice([850, 900, 950])}]
            if k == 3:
                n["tracks"].append({"id": "3", "length_m": rng.choice([850, 900, 950])})
        elif n["type"] == "port":
            n_port = rng.randint(5, 8) if n["id"] == "AKTAU" else rng.randint(3, 5)
            mains = [t for t in n["tracks"] if t.get("kind", "main") == "main"]
            for m in mains:
                m["length_m"] = rng.choice([900, 950, 1000])
            n["tracks"] = mains + [{"id": f"P{i}", "length_m": 1100, "kind": "port"} for i in range(1, n_port + 1)]


def _late(rng, t, appear_s, late_p):
    """Опоздание на входе/формировании: правда + (возможно) неточный прогноз."""
    if rng.random() >= late_p:
        return
    delay = rng.uniform(10, 75) * 60
    t["appear_actual"] = _t(appear_s + delay)
    if rng.random() < 0.7:
        at = max(0, appear_s - rng.uniform(60, 180) * 60)
        eta = appear_s + delay + rng.uniform(-15, 15) * 60
        t["forecasts"] = [{"at": _t(at), "eta": _t(max(eta, at + 60))}]


def generate(seed: int, traffic: str = "normal", disruptions: str = "normal",
             storm: bool | None = None) -> dict:
    if traffic not in LEVELS["traffic"] or disruptions not in LEVELS["disruptions"]:
        raise ValueError("traffic: low|normal|high, disruptions: none|low|normal|high")
    rng = random.Random(f"{seed}|{traffic}|{disruptions}|{storm}")
    tl, dl = LEVELS["traffic"][traffic], LEVELS["disruptions"][disruptions]
    raw = copy.deepcopy(BASE)
    raw["seed"] = seed
    _layout(rng, raw)

    sides = throat_sides(raw)
    boundaries = {n["id"] for n in raw["nodes"] if n["type"] == "boundary"}
    placed: dict[str, list[int]] = {}
    paths: dict[tuple, list] = {}

    def path_of(o, d):
        if (o, d) not in paths:
            paths[(o, d)] = route(raw, o, d)
        return paths[(o, d)]

    def ok(o, dests):
        """Маршрут сквозной на всех узлах (без разворота) — иначе такого поезда не бывает."""
        return all(straight(raw, path_of(o, d), sides) for d in dests)

    def slot(origin, s):
        """Вход с соседнего участка не чаще раза в 25 мин, формирование на станции — раз в час."""
        gap = MIN_GAP_S if origin in boundaries else 3600
        s = int(s // 60 * 60)
        while any(abs(s - p) < gap for p in placed.setdefault(origin, [])):
            s += 300
        placed[origin].append(s)
        return s

    trains = []
    # --- пассажирские пары: Шалкар — Актау и Атырау — Актау
    stops_w = [{"node": "OPOR", "min_dwell_min": 3}, {"node": "JUNC", "min_dwell_min": 15},
               {"node": "SAY", "min_dwell_min": 3}]
    pairs = [("EAST", i) for i in range(tl["pass_pairs"])] + [("ATY", i) for i in range(tl["aty_pass"])]
    times = _spread(rng, len(pairs), parse_time("04:00"), parse_time("20:00"), 1800, 2 * 3600)
    for j, ((src, _), w) in enumerate(zip(pairs, times)):
        w = slot(src, w // 300 * 300)
        e = slot("AKTAU", (w + rng.uniform(1.5, 3.5) * 3600) // 300 * 300)
        trains.append(P(f"P{101 + 2 * j}", src, "AKTAU", _t(w), length_m=rng.choice([350, 400, 450, 500]),
                        stops=[dict(x) for x in stops_w]))
        trains.append(P(f"P{102 + 2 * j}", "AKTAU", src, _t(e), length_m=rng.choice([350, 400, 450, 500]),
                        stops=[{"node": "AKTAU", "not_before": _t(e)}] + [dict(x) for x in reversed(stops_w)]))
    # --- контейнерные к портам (Средний коридор): с востока, из Узбекистана, с Атырау
    for i, s in enumerate(_spread(rng, tl["containers"], parse_time("00:30"), parse_time("21:30"), 1800, 0)):
        src = rng.choices(["EAST", "UZB", "ATY"], weights=[0.6, 0.25, 0.15])[0]
        length = rng.randrange(850, 1060, 10)
        s = slot(src, s)
        t = C(f"C{201 + i}", _t(s), origin=src, length_m=length, wagons=length // 35)
        _late(rng, t, s, dl["late_p"])
        trains.append(t)
    # --- попутные грузовые вглубь участка
    for i, s in enumerate(_spread(rng, tl["west_freight"], parse_time("01:00"), parse_time("21:00"), 2400, 0)):
        src, dest = rng.choice([("EAST", "JUNC"), ("EAST", "AKTAU"), ("UZB", "AKTAU"), ("ATY", "JUNC")])
        s = slot(src, s)
        length = rng.randrange(600, 860, 10)
        t = F(f"F{401 + 2 * i}", src, dest, _t(s), length_m=length, wagons=length // 18)
        _late(rng, t, s, dl["late_p"])
        trains.append(t)
    # --- грузовые из портов и с узла наружу: на Шалкар, Атырау, Узбекистан
    for i, s in enumerate(_spread(rng, tl["east_freight"], parse_time("01:00"), parse_time("22:00"), 2400, 0)):
        o = rng.choices(["KURYK", "AKTAU", "JUNC"], weights=[0.35, 0.35, 0.3])[0]
        dest = rng.choices(["EAST", "ATY", "UZB"], weights=[0.6, 0.25, 0.15])[0]
        if not ok(o, [dest]):
            dest = "EAST"
        s = slot(o, s)
        length = rng.randrange(600, 860, 10)
        t = F(f"F{302 + 2 * i}", o, dest, _t(s), length_m=length, wagons=length // 18)
        _late(rng, t, s, dl["late_p"] * 0.6)
        trains.append(t)
    # --- цементные: порожняк на завод и гружёные обратно (до 850 м — пути завода 850–900)
    for i in range(tl["cement"]):
        a_in = slot("EAST", int(rng.uniform(1, 14) * 3600) // 300 * 300)
        length = rng.randrange(600, 860, 10)
        t_in = F(f"F{501 + 2 * i}", "EAST", "CEM", _t(a_in), length_m=length, wagons=length // 18)
        _late(rng, t_in, a_in, dl["late_p"])
        a_out = slot("CEM", a_in + int(rng.uniform(6, 9) * 3600) // 300 * 300)
        t_out = F(f"F{502 + 2 * i}", "CEM", "EAST", _t(a_out), length_m=length, wagons=length // 18)
        trains += [t_in, t_out]
    # --- нефть Жанаозена: гружёные цистерны наружу, порожние обратно
    for i in range(tl["oil"]):
        dest = rng.choice(["ATY", "EAST", "UZB"])
        a_out = slot("ZHAN", int(rng.uniform(2, 12) * 3600) // 300 * 300)
        length = rng.randrange(640, 860, 10)
        trains.append(F(f"F{701 + 2 * i}", "ZHAN", dest, _t(a_out), length_m=length, wagons=length // 16))
        a_in = slot(dest, a_out + int(rng.uniform(3, 7) * 3600) // 300 * 300)
        t_in = F(f"F{702 + 2 * i}", dest, "ZHAN", _t(a_in), length_m=length, wagons=length // 16)
        _late(rng, t_in, a_in, dl["late_p"])
        trains.append(t_in)
    bad = [t["id"] for t in trains if not ok(t["origin"], t.get("destinations") or [t["destination"]])]
    if bad:
        raise ValueError(f"генератор построил маршрут с разворотом: {bad}")

    # --- паромы: суммарная вместимость ~ 1.0–1.25 спроса, 60% Актау / 40% Курык
    demand = sum(t["wagons"] for t in trains if t.get("cargo") == "ferry")
    cap = rng.choice([50, 52, 54, 56, 60])
    n_total = max(2, math.ceil(demand * rng.uniform(1.0, 1.25) / cap))
    n_a = max(1, round(n_total * 0.6))
    ferries = []
    for port, n in (("AKTAU", n_a), ("KURYK", max(1, n_total - n_a))):
        for j, s in enumerate(_spread(rng, n, parse_time("05:00"), parse_time("23:30"), 1800, 4 * 3600)):
            s = int(s // 600 * 600)
            actual = s + rng.uniform(-20, 60) * 60
            ferries.append({"id": f"F{port[0]}{j + 1}", "port": port, "capacity_wagons": cap,
                            "planned_arrival": _t(s), "actual_arrival": _t(max(0, actual)),
                            "eta_updates": [{"at": _t(max(0, s - 7200)),
                                             "eta": _t(max(0, actual + rng.uniform(-15, 15) * 60))}],
                            "unload_min": rng.choice([90, 120]), "max_stay_min": rng.choice([300, 360, 420])})

    # --- шторм на Каспии
    is_storm = (rng.random() < dl["storm_p"]) if storm is None else storm
    storm_info = None
    if is_storm:
        s0 = rng.uniform(2, 10) * 3600
        length = rng.uniform(8, 16) * 3600
        storm_info = {"from": _t(s0), "to": _t(s0 + length)}
        for f in ferries:
            pl = parse_time(f["planned_arrival"])
            if pl + 3 * 3600 < s0 or pl > s0 + length + 2 * 3600:
                continue
            actual = max(pl, s0 + length) + rng.uniform(0, 120) * 60
            f["actual_arrival"] = _t(actual)
            ups = []
            for frac, err in ((0.05, 0.6), (0.35, 0.3), (0.7, 0.1)):
                at = s0 + length * frac
                if at < actual - 1800:
                    guess = pl + (actual - pl) * (1 - err) + rng.uniform(-30, 30) * 60
                    ups.append({"at": _t(at), "eta": _t(max(guess, at + 1800))})
            f["eta_updates"] = ups

    # --- сбои
    dis = []
    mainline = [s for s in raw["segments"] if s["a"] not in ("AKTAU", "KURYK") and s["b"] not in ("AKTAU", "KURYK")]
    for k in range(rng.randint(*dl["failures"])):
        victim = rng.choice(trains)
        base = parse_time(victim.get("appear_actual", victim["appear"]))
        dur = rng.choice([20, 30, 45, 60, 90, 120])
        dis.append({"id": f"L{k + 1}", "kind": "loco_failure", "train": victim["id"],
                    "at": _t(base + rng.uniform(0.5, 3.5) * 3600), "duration_min": dur,
                    "announced_duration_min": max(10, round(dur * rng.uniform(0.4, 1.1) / 5) * 5)})
    if rng.random() < dl["outage_p"]:
        seg = rng.choice(mainline)
        dur = rng.choice([30, 45, 60, 90])
        dis.append({"id": "O1", "kind": "segment_outage", "segment": f"{seg['a']}-{seg['b']}",
                    "at": _t(rng.uniform(6, 20) * 3600), "duration_min": dur,
                    "announced_duration_min": max(10, round(dur * rng.uniform(0.5, 1.2) / 5) * 5)})
    if rng.random() < dl["window_p"]:
        seg = rng.choice(mainline)
        dis.append({"id": "W1", "kind": "maintenance_window", "segment": f"{seg['a']}-{seg['b']}",
                    "start": _t(int(rng.uniform(9, 15) * 2) * 1800), "duration_min": rng.choice([60, 90, 120]),
                    "announce_at": "00:00"})

    # --- целевой паром: ближайший по плану, куда поезд успевает
    order = sorted(ferries, key=lambda f: parse_time(f["planned_arrival"]))
    left = {f["id"]: f["capacity_wagons"] for f in order}
    for t in sorted((t for t in trains if t.get("cargo") == "ferry"), key=lambda t: parse_time(t["appear"])):
        ready = parse_time(t["appear"]) + 4 * 3600
        for f in order:
            if parse_time(f["planned_arrival"]) + 7200 >= ready and left[f["id"]] >= t["wagons"]:
                t["target_ferry"] = f["id"]
                left[f["id"]] -= t["wagons"]
                break

    raw.update(
        id=f"rnd-{seed}-{traffic}-{disruptions}" + ("-storm" if is_storm else ""),
        name=f"Случайный день #{seed} · трафик {traffic} · сбои {disruptions}" + (" · шторм" if is_storm else ""),
        horizon="36:00" if is_storm else "30:00",
        trains=trains, ferries=ferries, disruptions=dis, dispatcher={"directives": []},
        generator={"seed": seed, "traffic": traffic, "disruptions": disruptions, "storm": storm_info},
    )
    raw = plan(raw)
    load_scenario(raw)   # валидация: если генератор нарушил границы — упадём здесь, а не в игре
    return raw
