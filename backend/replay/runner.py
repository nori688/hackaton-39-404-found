"""Ночной реплей: точки решений → альтернативы → перепрогон остатка дня → отчёт.

Методика (одна для любых источников факта):
  1. Факт воспроизводится тем же движком; в каждом моменте, где был выбор, сохраняется
     снимок состояния ДО применения решения.
  2. Для точки строятся 2–3 альтернативы — только из того, что было видно в этот момент.
  3. Каждая альтернатива и базовая линия (решение факта) доигрываются до конца дня ОДНИМ
     И ТЕМ ЖЕ продолжением — разница показывает эффект именно этого решения.
  4. Два режима, в каждом — несколько вариантов будущего (исход одного прогона бывает делом
     случая: мелкая разница во времени хода раскачивает весь день):
       честный       — будущее таким, каким его знал диспетчер: прогнозы и объявленные сроки
                       (с их обычной погрешностью), без неизвестных сбоев → «упущенная возможность»;
       ретроспектива — настоящие события дня → «невезение», если альтернатива лучше только тут
                       и это объясняется тем, чего диспетчер знать не мог.
     Вывод «лучше/хуже» делается, только если согласны не меньше 3 из 4 вариантов.
"""
from __future__ import annotations

import copy
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from sim import Simulator, compute_metrics, export_result, fmt_time
from sim.worlds import expected_world, retro_world

from .points import candidates, ferry_moment
from .policies import Continuation, LogReplayPolicy, Override

SIG = 5.0               # значимая разница, взвешенные минуты
SAMPLES = 4             # вариантов будущего на режим
AGREE = 0.75            # доля вариантов, которые должны согласиться
KIND_RANK = {"contest": 0, "cross": 1, "overtake": 2, "route": 3, "ferry": 4, "autopilot": 5, "hold": 6}


# ------------------------------------------------------------------ источники факта

def policy_source(policy):
    return {"kind": "policy", "policy": policy}


def log_source(decisions, wakes=(), routes=None):
    return {"kind": "log", "decisions": decisions, "wakes": list(wakes), "routes": dict(routes or {})}


def _fact_policy(src):
    if src["kind"] == "policy":
        return copy.deepcopy(src["policy"])
    return LogReplayPolicy(src["decisions"])


def _continuation(src, pol_at_fork):
    """Кто доигрывает день после развилки. Исходная политика — если её можно спросить снова."""
    if src["kind"] == "policy":
        return copy.deepcopy(pol_at_fork), True
    return Continuation(src["routes"]), False


# ------------------------------------------------------------------ проход по факту

def collect(sc, src, max_points=50):
    sim = Simulator(sc, strict=False)
    if src["kind"] == "log":
        sim.injections = sorted(src["wakes"], key=lambda w: (w["seq"], w["t"]))
    pol = _fact_policy(src)
    seen: set = set()
    points = []
    while True:
        r = sim.advance(pol, None, lambda o: bool(o["legal"]) or ferry_moment(o))
        if r != "paused":
            break
        obs = sim._paused_obs
        peek = copy.deepcopy(pol).decide(copy.deepcopy(obs)) or []
        alts = candidates(obs, peek, seen)
        if alts:
            cont, warm = _continuation(src, pol)
            points.append({"t": sim.t, "seq": sim._decide_seq, "snap": sim.clone(), "cont": cont,
                           "warmup": warm, "peek": peek, "alts": alts, "obs": obs})
        sim.resume(pol)
    if len(points) > max_points:
        # сначала конфликты и развилки, внутри — по важности поездов
        def stakes(p):
            trains = {x["id"]: x for x in p["obs"]["trains"]}
            best = min(KIND_RANK[a["kind"]] for a in p["alts"])
            w = max((trains[t]["weight"] for a in p["alts"] for t in a["involved"] if t in trains), default=0)
            return (best, -w, p["t"])
        points = sorted(sorted(points, key=stakes)[:max_points], key=lambda p: p["t"])
    return sim, points


# ------------------------------------------------------------------ оценка точки (в отдельном процессе)

def _summ(sim):
    m = compute_metrics(sim)
    return {
        "weighted": m["weighted_delay_min"], "total": m["total_delay_min"],
        "wagon_hours": m["wagon_idle_hours"], "on_target": m["wagons_on_target_ferry"],
        "shipped": m["wagons_shipped"], "unfinished": len(m["unfinished_trains"]),
        "status": m["status"], "per_train": {p["id"]: p["delay_min"] for p in m["per_train"]},
        "ports": {k: v["peak_pct"] for k, v in m["ports"].items()},
    }


def compact_path(path):
    """Нитка поезда для графика: ["n", узел, приб, отпр] | ["s", откуда, куда, вход, выход]."""
    out = []
    for r in path:
        if "segment" in r:
            out.append(["s", r["from"], r["to"], r["enter"], r["exit"]])
        else:
            out.append(["n", r["node"], r["arr"], r["dep"]])
    return out


def _paths(sim):
    return {tid: compact_path(tr.path) for tid, tr in sim.trains.items()}


def _play(world, first, cont, warm, holds=()):
    s = world.clone()
    pol = Override(first, copy.deepcopy(cont), holds, warmup=warm)
    n_rej, t0 = len(s.rejections), s.t
    s.resume(pol)
    # альтернатива — ровно то, что заявлено: если движок отклонил её команду, она не считается
    invalid = any(r["t"] == t0 and r["command"].get("type") in ("dispatch", "ferry_order")
                  for r in s.rejections[n_rej:])
    s.advance(pol)
    m = _summ(s)
    m["invalid"] = invalid
    return m, _paths(s)


def evaluate_point(p):
    """Базовая линия и каждая альтернатива в нескольких вариантах будущего каждого режима.
    Вариант 0: честный — номинальное ожидание, ретро — ровно настоящий день (они же идут на графики)."""
    k = p.get("samples", SAMPLES)
    worlds = {
        "honest": [expected_world(p["snap"])] + [expected_world(p["snap"], i) for i in range(1, k)],
        "true": [retro_world(p["snap"], i) for i in range(k)],
    }
    out = {"base": {}, "alts": [{} for _ in p["alts"]], "paths": {}}
    for wname, ws in worlds.items():
        out["base"][wname] = []
        for a in out["alts"]:
            a[wname] = []
        for j, w in enumerate(ws):
            m, paths = _play(w, p["peek"], p["cont"], p["warmup"])
            out["base"][wname].append(m)
            alt_paths = []
            for i, a in enumerate(p["alts"]):
                am, ap = _play(w, a["cmds"], p["cont"], p["warmup"], a["holds"])
                out["alts"][i][wname].append(am)
                alt_paths.append(ap)
            if j == 0:
                out["paths"][wname] = {"base": paths, "alts": alt_paths}
    return out


# ------------------------------------------------------------------ скрытое от диспетчера

def hidden_after(sc, snap, horizon_s=8 * 3600):
    """Что произошло после момента t такого, чего диспетчер в момент t знать не мог."""
    t = snap.t
    out = []
    revealed = {f["id"] for f in snap.revealed_failures}
    for d in sc.disruptions:
        if d.kind == "maintenance_window" and d.announce_at is not None and d.announce_at <= t:
            continue
        if d.id not in revealed and t < d.at <= t + horizon_s:
            what = {"loco_failure": f"сломался локомотив {d.train}", "segment_outage": f"закрыли перегон {d.segment}",
                    "maintenance_window": f"окно на {d.segment} без предупреждения"}[d.kind]
            out.append({"t": d.at, "text": f"в {fmt_time(d.at)} {what} (на {d.duration_s // 60} мин)"})
    for f in snap.revealed_failures:
        if f["ended"] is None and f.get("train"):
            tr = snap.trains.get(f["train"])
            real = tr.failure["until"] if tr and tr.failure else None
            if real and real - f["est_until"] > 900:
                out.append({"t": f["since"], "text": f"ремонт {f['train']} затянулся: обещали до "
                                                      f"{fmt_time(f['est_until'])}, починили в {fmt_time(real)}"})
    for tid, tr in snap.trains.items():
        if tr.status != "pending":
            continue
        known = tr.spec.appear_planned
        for fc in tr.spec.forecasts:
            if fc.at <= t:
                known = fc.eta
        if abs(tr.appear_at - known) > 900 and tr.appear_at <= t + horizon_s:
            out.append({"t": tr.appear_at, "text": f"{tid} пришёл на участок в {fmt_time(tr.appear_at)}, "
                                                   f"а ждали к {fmt_time(known)}"})
    for fid, fs in sc.ferries.items():
        if snap.ferries[fid].status != "expected":
            continue
        known = fs.planned_arrival
        for u in fs.eta_updates:
            if u.at <= t:
                known = u.eta
        if abs(fs.actual_arrival - known) > 1800:
            out.append({"t": fs.actual_arrival, "text": f"паром {fid} пришёл в {fmt_time(fs.actual_arrival)}, "
                                                        f"а ждали к {fmt_time(known)}"})
    return sorted(out, key=lambda x: x["t"])[:4]


# ------------------------------------------------------------------ отчёт

def _verdict(base_list, alt_list):
    """Медиана по вариантам будущего (один вариант со сцепкой не должен решать за все) и согласие."""
    d = [a["weighted"] - b["weighted"] for a, b in zip(alt_list, base_list)]
    n = len(d)
    med = statistics.median(d)
    better = sum(1 for x in d if x < 0) / n
    worse = sum(1 for x in d if x > 0) / n
    mean = sum(d) / n
    # и медиана, и согласие вариантов, и среднее — в одну сторону; иначе исход — дело случая
    if med <= -SIG and better >= AGREE and mean < 0:
        v = "better"
    elif med >= SIG and worse >= AGREE and mean > 0:
        v = "worse"
    else:
        v = "uncertain"
    per = {}
    for tid in base_list[0]["per_train"]:
        x = statistics.median(a["per_train"].get(tid, 0) - b["per_train"].get(tid, 0) for a, b in zip(alt_list, base_list))
        if abs(x) >= 1:
            per[tid] = round(x, 1)
    total = statistics.median(a["total"] - b["total"] for a, b in zip(alt_list, base_list))
    risk_alt = sum(1 for a in alt_list if a["unfinished"])
    risk_base = sum(1 for b in base_list if b["unfinished"])
    return {"mean": round(med, 1), "total": round(total, 1), "verdict": v, "samples": [round(x, 1) for x in d],
            "agree": round(max(better, worse), 2), "per_train": per,
            "gridlock_alt": risk_alt, "gridlock_base": risk_base}


def _fact_label(p, names):
    d = [c for c in p["peek"] if c.get("type") == "dispatch"]
    if not d:
        return "все стоят"
    return ", ".join(f"{c['train']} → {names.get(c['next'], c['next'])}" for c in d)


def _card(p, i, res, ev, kind, names, extra=None):
    a = p["alts"][i]
    t = p["t"]
    h, r = ev[i]["honest"], ev[i]["true"]
    card = {
        "kind": kind, "t": t, "t_str": fmt_time(t), "node": a["node"],
        "node_name": names.get(a["node"], a["node"]), "label": a["label"], "alt_kind": a["kind"],
        "involved": a["involved"], "fact": _fact_label(p, names),
        "delta_honest": h["mean"], "delta_true": r["mean"],
        "delta_total_honest": h["total"], "delta_total_true": r["total"],
        "verdict_honest": h["verdict"], "verdict_true": r["verdict"],
        "agree_honest": h["agree"], "agree_true": r["agree"],
        "samples_honest": h["samples"], "samples_true": r["samples"],
        "gridlock_risk": {"honest": [h["gridlock_base"], h["gridlock_alt"]], "true": [r["gridlock_base"], r["gridlock_alt"]]},
        "trains_honest": h["per_train"], "trains_true": r["per_train"],
        "compare_id": f"{p['seq']}-{i}",
        "alternatives": [{"label": x["label"], "kind": x["kind"], "delta_honest": e["honest"]["mean"],
                          "delta_true": e["true"]["mean"], "verdict_honest": e["honest"]["verdict"],
                          "verdict_true": e["true"]["verdict"]} for x, e in zip(p["alts"], ev)
                         if e["honest"]["verdict"] != "invalid"],
    }
    if extra:
        card.update(extra)
    return card


def build_report(sc, fact_sim, points, results, src_label, elapsed):
    names = {n.id: n.name for n in sc.nodes.values()}
    missed, bad_luck, good, algo, trade = [], [], [], [], []
    uncertain = 0
    for p, res in zip(points, results):
        ev = [{"honest": _verdict(res["base"]["honest"], r["honest"]),
               "true": _verdict(res["base"]["true"], r["true"])} for r in res["alts"]]
        for e, r in zip(ev, res["alts"]):
            if any(x["invalid"] for x in r["honest"] + r["true"]):
                e["honest"]["verdict"] = e["true"]["verdict"] = "invalid"
        if all(e["honest"]["verdict"] == "invalid" for e in ev):
            continue
        better_h = [i for i, e in enumerate(ev) if e["honest"]["verdict"] == "better"]
        better_t = [i for i, e in enumerate(ev) if e["true"]["verdict"] == "better"]
        if better_h:
            i = min(better_h, key=lambda i: ev[i]["honest"]["mean"])
            missed.append(_card(p, i, res, ev, "missed", names))
        elif better_t:
            hidden = hidden_after(sc, p["snap"])
            i = min(better_t, key=lambda i: ev[i]["true"]["mean"])
            if hidden:
                bad_luck.append(_card(p, i, res, ev, "bad_luck", names, {"hidden": hidden}))
            else:
                uncertain += 1     # лучше «по факту», но объяснить нечем — это случайность, не невезение
        elif all(e["honest"]["verdict"] in ("worse", "invalid") and e["true"]["verdict"] != "better" for e in ev)                 and any(e["honest"]["verdict"] == "worse" for e in ev):
            i = min((i for i in range(len(ev)) if ev[i]["honest"]["verdict"] == "worse"),
                    key=lambda i: ev[i]["honest"]["mean"])
            good.append(_card(p, i, res, ev, "good", names))
        elif any(e["honest"]["verdict"] == "uncertain" for e in ev):
            uncertain += 1
        for i, a in enumerate(p["alts"]):
            e = ev[i]
            if a["kind"] == "autopilot" and e["honest"]["verdict"] == "worse" and e["true"]["verdict"] != "better":
                algo.append(_card(p, i, res, ev, "better_than_algo", names))
            if e["honest"]["verdict"] == "better" and a["kind"] in ("cross", "overtake", "contest"):
                # жертва — тот, кого придержали (в «кто первым» — бывший победитель)
                victim = a["involved"][0]
                per = e["honest"]["per_train"]
                winners = {k: v for k, v in sorted(per.items(), key=lambda kv: kv[1]) if v < -3 and k != victim}
                if per.get(victim, 0) > 3 and len(winners) >= 2:
                    trade.append(_card(p, i, res, ev, "tradeoff", names,
                                       {"sacrificed": {victim: per[victim]}, "winners": dict(list(winners.items())[:5])}))
    missed.sort(key=lambda c: c["delta_honest"])
    bad_luck.sort(key=lambda c: c["delta_true"])
    good.sort(key=lambda c: -min(x["delta_honest"] for x in c["alternatives"]))
    algo.sort(key=lambda c: -c["delta_honest"])
    trade.sort(key=lambda c: c["delta_honest"])

    nodes = {}
    for c in missed:
        n = nodes.setdefault(c["node"], {"node": c["node"], "name": c["node_name"], "minutes": 0.0, "count": 0})
        n["minutes"] = round(n["minutes"] - c["delta_honest"], 1)
        n["count"] += 1
    fact = export_result(fact_sim, src_label)
    n_alts = sum(len(p["alts"]) for p in points)
    # графики сравнения — отдельно (их берут только для открытой карточки)
    compares = {}
    by_seq = {p["seq"]: (p, res) for p, res in zip(points, results)}
    for c in missed + trade + good + algo + bad_luck:
        cid = c["compare_id"]
        if cid in compares:
            continue
        seq, i = (int(x) for x in cid.split("-"))
        p, res = by_seq[seq]
        compares[cid] = {"t": p["t"], "node": c["node"], "involved": c["involved"], "label": c["label"],
                         **{w: {"base": res["paths"][w]["base"], "alt": res["paths"][w]["alts"][i],
                                "base_m": {k: v for k, v in res["base"][w][0].items() if k != "per_train"},
                                "alt_m": {k: v for k, v in res["alts"][i][w][0].items() if k != "per_train"}}
                            for w in ("honest", "true")}}
    return {
        "scenario": {"id": sc.id, "name": sc.name},
        "source": src_label,
        "fact_metrics": fact["metrics"],
        "layout": fact["layout"], "names": names, "horizon": sc.horizon,
        "types": {tid: t.type for tid, t in sc.trains.items()},
        "fact_paths": {t["id"]: compact_path(t["path"]) for t in fact["trains"]},
        "stats": {"points": len(points), "alternatives": n_alts, "uncertain": uncertain,
                  "runs": 2 * SAMPLES * (len(points) + n_alts), "seconds": round(elapsed, 1)},
        "best": missed[0] if missed else None,
        "missed": missed[:10], "tradeoffs": trade[:5], "good": good[:6],
        "better_than_algo": algo[:5], "bad_luck": bad_luck[:5],
        "problem_nodes": sorted(nodes.values(), key=lambda n: -n["minutes"]),
        "compares": compares,
        "method": {
            "threshold_min": SIG, "samples": SAMPLES, "agree": AGREE,
            "metric": "задержка с учётом приоритета (пассажирские ×3), взвешенные минуты; медиана по вариантам будущего",
            "continuation": ("после развилки день доигрывает та же политика, что и в факте"
                             if any(p["warmup"] for p in points) else
                             "после развилки день доигрывает автопилот с вашими приказами о портах — "
                             "одинаково для факта и альтернативы"),
        },
    }


def run_replay(sc, src, src_label="", progress=None, workers=3, max_points=50):
    t0 = time.perf_counter()
    fact_sim, points = collect(sc, src, max_points)
    if progress:
        progress(0, len(points))
    results = [None] * len(points)
    payloads = [{k: v for k, v in p.items() if k != "obs"} for p in points]
    if workers > 1 and len(points) > 2:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(evaluate_point, pl): i for i, pl in enumerate(payloads)}
            for done, f in enumerate(as_completed(futs), 1):
                results[futs[f]] = f.result()
                if progress:
                    progress(done, len(points))
    else:
        for i, pl in enumerate(payloads):
            results[i] = evaluate_point(pl)
            if progress:
                progress(i + 1, len(points))
    return build_report(sc, fact_sim, points, results, src_label, time.perf_counter() - t0)
