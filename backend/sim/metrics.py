"""Метрики и выгрузка результата прогона. Одна функция для факта и для любых альтернатив."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .model import fmt_time

if TYPE_CHECKING:
    from .engine import Simulator


def compute_metrics(sim: "Simulator") -> dict:
    end = sim.end_time if sim.end_time is not None else sim.t
    trains = []
    total = weighted = 0.0
    wagon_idle_s = 0
    unfinished = []
    for tid in sorted(sim.trains):
        tr = sim.trains[tid]
        sp = tr.spec
        arr = tr.arrived_at
        if arr is None:
            unfinished.append(tid)
            delay = max(0, end - sp.planned_arrival)
        else:
            delay = max(0, arr - sp.planned_arrival)
        # время стоянки сверх обязательной — для поездов в пути считаем до конца прогона
        idle = tr.idle_s
        if tr.status in ("at_node",) and tr.arrived_at is None:
            idle += max(0, end - tr.since)
        port_wait = 0
        if tr.port:
            # вагоны ждут парома: от прибытия до погрузки последнего вагона (или до конца)
            last = tr.port["last_load"] if tr.port["remaining"] == 0 else end
            port_wait = max(0, (last or end) - tr.port["arrived"])
        wagon_idle_s += idle * sp.wagons + port_wait * sp.wagons
        total += delay
        weighted += delay * tr.tt.weight
        trains.append({"id": tid, "delay_min": round(delay / 60, 1),
                       "weighted_delay_min": round(delay * tr.tt.weight / 60, 1),
                       "unplanned_stops": tr.unplanned_stops, "idle_min": round(idle / 60, 1),
                       "port_wait_min": round(port_wait / 60, 1), "finished": arr is not None})

    ports = {}
    for node, spec in sim.sc.ports.items():
        n_tracks = sum(1 for t in sim.sc.nodes[node].tracks if t.kind == "port")
        peak = max((n for _, n in sim.port_occupancy.get(node, [])), default=0)
        ports[node] = {"port_tracks": n_tracks, "peak_occupied": peak,
                       "peak_pct": round(100 * peak / n_tracks, 1) if n_tracks else 0}

    shipped = sum(fs.loaded for fs in sim.ferries.values() if fs.status == "departed")
    on_target = 0
    target_total = 0
    for tr in sim.trains.values():
        tf = tr.spec.target_ferry
        if not tf or tr.spec.cargo != "ferry":
            continue
        target_total += tr.spec.wagons
        if not tr.port:
            continue
        tdep = sim.ferries[tf].departed
        for fid, n in tr.port["loaded_on"].items():
            dep = sim.ferries[fid].departed
            if dep is not None and (fid == tf or (tdep is not None and dep <= tdep)):
                on_target += n

    return {
        "status": sim.status,
        "end_time": end,
        "total_delay_min": round(total / 60, 1),
        "weighted_delay_min": round(weighted / 60, 1),
        "wagon_idle_hours": round(wagon_idle_s / 3600, 1),
        "ports": ports,
        "wagons_shipped": shipped,
        "wagons_on_target_ferry": on_target,
        "wagons_with_target_ferry": target_total,
        "unfinished_trains": unfinished,
        "unplanned_stops": sum(t["unplanned_stops"] for t in trains),
        "rejected_commands": len(sim.rejections),
        "window_shift_min": round(sum(w["shift_s"] for w in sim.windows.values()) / 60, 1),
        "per_train": trains,
    }


def layout_of(sc) -> dict:
    return {
        "nodes": [{"id": n.id, "name": n.name, "type": n.type, "x": n.x, "y": n.y,
                   "tracks": [{"id": t.id, "length_m": t.length_m, "kind": t.kind} for t in n.tracks]}
                  for n in sc.nodes.values()],
        "segments": [{"id": s.id, "a": s.a, "b": s.b, "length_km": s.length_km}
                     for s in sc.segments.values()],
    }


def export_result(sim: "Simulator", policy_name: str = "") -> dict:
    sc = sim.sc
    return {
        "scenario": {"id": sc.id, "name": sc.name, "horizon": sc.horizon},
        "policy": policy_name,
        "mode": sim.mode,
        "metrics": compute_metrics(sim),
        "layout": layout_of(sc),
        "trains": [{
            "id": tid, "type": tr.spec.type, "origin": tr.spec.origin,
            "destinations": list(tr.spec.destinations), "final_node": tr.final_node,
            "planned_arrival": tr.spec.planned_arrival, "arrived_at": tr.arrived_at,
            "status": tr.status, "path": tr.path,
        } for tid, tr in sorted(sim.trains.items())],
        "ferries": [{"id": fid, "port": sc.ferries[fid].port, "capacity": sc.ferries[fid].capacity,
                     "planned_arrival": sc.ferries[fid].planned_arrival, "arrived": fs.arrived,
                     "berthed": fs.berthed, "departed": fs.departed, "loaded": fs.loaded,
                     "loads": dict(fs.loads)} for fid, fs in sim.ferries.items()],
        "port_occupancy": {k: v for k, v in sim.port_occupancy.items()},
        "events": sim.log,
        "decisions": [{k: v for k, v in d.items() if k != "observation"} for d in sim.decisions],
        "rejections": sim.rejections,
    }


def summary_text(res: dict) -> str:
    m = res["metrics"]
    lines = [f"Сценарий {res['scenario']['id']} · политика {res['policy']} · режим {res['mode']}",
             f"Итог: {m['status']} в {fmt_time(m['end_time'])}",
             f"Суммарная задержка: {m['total_delay_min']} мин · взвешенная: {m['weighted_delay_min']}",
             f"Простой вагонов: {m['wagon_idle_hours']} ваг·ч · внеплановых остановок: {m['unplanned_stops']}",
             f"Отправлено паромами: {m['wagons_shipped']} ваг · успели на свой паром: "
             f"{m['wagons_on_target_ferry']}/{m['wagons_with_target_ferry']}",
             f"Отклонено команд: {m['rejected_commands']} · сдвиг окон: {m['window_shift_min']} мин"]
    for p, v in m["ports"].items():
        lines.append(f"Порт {p}: пик {v['peak_occupied']}/{v['port_tracks']} путей ({v['peak_pct']}%)")
    if m["unfinished_trains"]:
        lines.append(f"Не доехали: {', '.join(m['unfinished_trains'])}")
    lines.append("")
    lines.append(f"{'поезд':<7}{'тип':<11}{'план':>7}{'факт':>7}{'задерж':>8}{'стоп':>6}")
    def hm(x):
        return fmt_time(x - x % 60) if x is not None else "—"
    for tr, pt in zip(res["trains"], m["per_train"]):
        lines.append(f"{tr['id']:<7}{tr['type']:<11}{hm(tr['planned_arrival']):>7}"
                     f"{hm(tr['arrived_at']):>7}{pt['delay_min']:>8}{pt['unplanned_stops']:>6}")
    return "\n".join(lines)
