"""Тесты беспристрастности симулятора: он не подыгрывает ни одной политике."""
import copy
import json

import pytest

from dispatchers import PriorityDispatcher, make_dispatcher
from sim import Simulator, export_result, load_scenario, parse_time
from sim.model import noise_factor, nominal_run_s
from tests.helpers import Func, Recorder, all_legal, day, run, tiny, train


def _dump(sim):
    return json.dumps(export_result(sim), sort_keys=True, ensure_ascii=False)


# ------------------------------------------------------------ детерминизм

@pytest.mark.parametrize("name", ["day1", "day2"])
@pytest.mark.parametrize("disp", ["priority", "scripted"])
def test_deterministic(name, disp):
    raw = day(name)
    a = run(raw, make_dispatcher(disp, load_scenario(raw)))
    b = run(raw, make_dispatcher(disp, load_scenario(raw)))
    assert _dump(a) == _dump(b)


def test_fork_equals_uninterrupted():
    """Форк в середине дня и продолжение дают ровно тот же день — основа реплея."""
    raw = day("day1")
    sc = load_scenario(raw)
    full = Simulator(sc).run(make_dispatcher("scripted", sc))
    pol = make_dispatcher("scripted", sc)
    part = Simulator(sc).run(pol, until=parse_time("12:00"))
    fork, pol2 = part.clone(), copy.deepcopy(pol)
    fork.run(pol2)
    part.run(pol)
    assert _dump(fork) == _dump(full) == _dump(part)


# ------------------------------------------------------------ изоляция знаний

def test_hidden_future_does_not_leak():
    """Два мира, отличающиеся только скрытым будущим, дают одинаковые наблюдения до момента раскрытия."""
    base = day("day1")
    alt = copy.deepcopy(base)
    alt["disruptions"] += [
        {"id": "X1", "kind": "loco_failure", "train": "P103", "at": "16:00",
         "duration_min": 120, "announced_duration_min": 20},
        {"id": "X2", "kind": "segment_outage", "segment": "R2-R1", "at": "15:00", "duration_min": 60},
        {"id": "X3", "kind": "maintenance_window", "segment": "R5-R4", "start": "15:30", "duration_min": 60},
    ]
    t205 = next(t for t in alt["trains"] if t["id"] == "C205")
    t205["appear_actual"] = "12:25"            # опоздал без предупреждения
    fa2 = next(f for f in alt["ferries"] if f["id"] == "FA2")
    fa2["actual_arrival"] = "17:00"            # паром опоздал без предупреждения
    # первое наблюдаемое расхождение: C205 в базовом мире подходит к границе в 11:37
    cutoff = parse_time("11:37")
    ra, rb = Recorder(PriorityDispatcher(), cutoff), Recorder(PriorityDispatcher(), cutoff)
    run(base, ra)
    run(alt, rb)
    assert len(ra.seen) > 20
    assert ra.seen == rb.seen


def test_running_noise_not_visible():
    """Политика видит номинальное ETA, а не фактическое время хода с шумом."""
    raw = tiny([train("A", "W", "E", "01:00")], noise=0.10)
    raw2 = copy.deepcopy(raw)
    raw2["seed"] = 999
    r1, r2 = Recorder(Func(all_legal), parse_time("01:00") + 600), Recorder(Func(all_legal), parse_time("01:00") + 600)
    run(raw, r1)
    run(raw2, r2)
    assert r1.seen and r1.seen == r2.seen


def test_failure_shows_announced_not_actual_duration():
    raw = tiny([train("A", "W", "E", "01:00")],
               [{"kind": "loco_failure", "train": "A", "at": "01:30", "duration_min": 90,
                 "announced_duration_min": 20}])
    rec = Recorder(Func(all_legal))
    run(raw, rec)
    at_fail = [json.loads(o) for o in rec.seen if json.loads(o)["t"] == parse_time("01:30")][0]
    a = next(t for t in at_fail["trains"] if t["id"] == "A")
    assert a["failure"]["est_until"] == parse_time("01:50")


def test_oracle_is_explicit():
    raw = day("day1")
    rec_h, rec_o = Recorder(PriorityDispatcher()), Recorder(PriorityDispatcher())
    run(raw, rec_h)
    run(raw, rec_o, knowledge_mode="oracle")
    assert all("oracle" not in json.loads(o) for o in rec_h.seen)
    assert all(json.loads(o)["mode"] == "oracle" and "oracle" in json.loads(o) for o in rec_o.seen)


def test_policy_cannot_mutate_engine():
    raw = day("day1")
    clean = run(raw, PriorityDispatcher())

    class Vandal(PriorityDispatcher):
        def decide(self, obs):
            cmds = super().decide(obs)
            for t in obs["trains"]:
                t["status"] = "done"
                t["stops"].clear()
            obs["segments"].clear()
            obs["legal"].clear()
            return cmds
    dirty = run(raw, Vandal())
    assert _dump(clean) == _dump(dirty)


# ------------------------------------------------------------ одинаковое «везение»

def test_running_noise_independent_of_decisions():
    raw = tiny([train("A", "W", "E", "01:00")])
    hold_until = parse_time("02:30")

    def holder(obs):
        cmds = [{"type": "wake_at", "time": hold_until}] if obs["t"] < hold_until else []
        legal = [a for a in obs["legal"] if not (a["at"] == "S1" and obs["t"] < hold_until)]
        return cmds + all_legal(dict(obs, legal=legal))

    sc = load_scenario(raw)
    tt = sc.types["freight"]
    pure = {}
    for pol in (Func(all_legal), Func(holder)):
        sim = Simulator(load_scenario(raw)).run(pol)
        segs = [p for p in sim.trains["A"].path if "segment" in p]
        got = {p["segment"]: p["exit"] - p["enter"] - (tt.accel_s if p["from_stop"] else 0)
               - (tt.brake_s if p["stop"] else 0) for p in segs}
        pure.setdefault("runs", []).append(got)
    a, b = pure["runs"]
    assert a == b
    for sid, v in a.items():
        seg = sc.segments[sid]
        assert v == round(nominal_run_s(tt, seg) * noise_factor(sc.seed, "A", sid, sc.params.running_noise))


# ------------------------------------------------------------ физика стоит времени

def test_stop_costs_brake_accel_and_setup():
    raw = tiny([train("A", "W", "E", "01:00")])
    T = parse_time("02:00")

    def holder(obs):
        legal = [a for a in obs["legal"] if not (a["at"] == "S1" and obs["t"] < T)]
        return all_legal(dict(obs, legal=legal)) + ([{"type": "wake_at", "time": T}] if obs["t"] < T else [])

    fast = run(raw, Func(all_legal))
    slow = run(raw, Func(holder))
    tt = load_scenario(raw).types["freight"]
    s1_fast = next(p for p in fast.trains["A"].path if p.get("node") == "S1")
    assert s1_fast["through"]
    enter_fast = next(p for p in fast.trains["A"].path if p.get("segment") == "S1-S2")["enter"]
    s1_slow = next(p for p in slow.trains["A"].path if p.get("node") == "S1")
    assert not s1_slow["through"] and s1_slow["arr"] == s1_fast["arr"] + tt.brake_s
    expected = (T + 60 + tt.accel_s) - enter_fast
    assert slow.trains["A"].arrived_at - fast.trains["A"].arrived_at == expected


def test_through_decision_only_at_approach():
    """Пропустить «на проход» можно только в момент подхода; потом поезд уже тормозит."""
    raw = tiny([train("A", "W", "E", "01:00")])
    asked = []

    def late(obs):
        a = next(t for t in obs["trains"] if t["id"] == "A")
        if a.get("run") and a["run"]["to"] == "S1" and not a["run"]["awaiting_decision"]:
            asked.append(obs["t"])
            return [{"type": "dispatch", "train": "A", "next": "S2"}]
        return all_legal(obs) if a["status"] != "running" or a["run"]["to"] != "S1" else []
    sim = run(raw, Func(late))
    assert sim.rejections and all(r["reason"].startswith("not_ready") for r in sim.rejections)
    assert not next(p for p in sim.trains["A"].path if p.get("node") == "S1")["through"]


# ------------------------------------------------------------ команды не исправляются

def _rej(sim):
    return [r["reason"] for r in sim.rejections]


def test_reject_busy_segment_and_state_untouched():
    raw = tiny([train("A", "S1", "E", "01:00"), train("B", "S1", "E", "01:00", length=600)])

    def both(obs):
        if obs["t"] == parse_time("01:00"):
            return [{"type": "dispatch", "train": "A", "next": "S2"},
                    {"type": "dispatch", "train": "B", "next": "S2"}]
        return all_legal(obs)
    sim = Simulator(load_scenario(raw))
    sim.run(Func(both), until=parse_time("01:00"))
    assert _rej(sim) == ["segment_busy"]
    assert sim.segs["S1-S2"].reserved == "A"
    assert sim.trains["B"].move is None


def test_reject_too_long_for_free_tracks():
    raw = tiny([train("Y", "S1", "E", "00:30", length=1000), train("X", "S1", "E", "00:30", length=800),
                train("Z", "W", "E", "01:00", type="container", length=1000)])
    sim = Simulator(load_scenario(raw))

    def push(obs):
        return [{"type": "dispatch", "train": "Z", "next": "S1"}] if any(
            t["id"] == "Z" and t["status"] in ("approaching", "at_node") for t in obs["trains"]) else []
    sim.run(Func(push), until=parse_time("01:00"))
    assert "no_track" in _rej(sim)


def test_reject_passenger_before_timetable():
    raw = tiny([train("P", "S1", "E", "01:00", type="passenger", length=450,
                      stops=[{"node": "S1", "not_before": "02:00"}])])
    sim = Simulator(load_scenario(raw))
    sim.run(Func(lambda o: [{"type": "dispatch", "train": "P", "next": "S2"}]), until=parse_time("01:30"))
    assert set(_rej(sim)) == {"dwell_or_timetable"}
    sim.run(Func(all_legal))
    dep = next(p for p in sim.trains["P"].path if p.get("node") == "S1")["dep"]
    assert dep >= parse_time("02:00")


def test_reject_through_at_required_stop():
    raw = tiny([train("P", "W", "E", "01:00", type="passenger", length=450,
                      stops=[{"node": "S1", "min_dwell_min": 5}])])

    def eager(obs):
        p = next(t for t in obs["trains"] if t["id"] == "P")
        if p.get("run") and p["run"]["to"] == "S1" and p["run"]["awaiting_decision"]:
            return [{"type": "dispatch", "train": "P", "next": "S2"}]
        return all_legal(obs)
    sim = run(raw, Func(eager))
    assert "must_stop" in _rej(sim)
    s1 = next(p for p in sim.trains["P"].path if p.get("node") == "S1")
    assert s1["dep"] - s1["arr"] >= 300


def test_reject_during_loco_failure():
    raw = tiny([train("A", "S1", "E", "01:00")],
               [{"kind": "loco_failure", "train": "A", "at": "01:10", "duration_min": 40}])
    sim = Simulator(load_scenario(raw))
    sim.run(Func(lambda o: [{"type": "dispatch", "train": "A", "next": "S2"}]
                 if o["t"] >= parse_time("01:10") else []), until=parse_time("01:20"))
    assert "loco_failure" in _rej(sim)


def test_reject_window_conflict_only_if_announced():
    win = {"kind": "maintenance_window", "segment": "S1-S2", "start": "02:00", "duration_min": 60}
    t = [train("A", "S1", "E", "01:50")]
    known = run(tiny(t, [dict(win, announce_at="00:00")]), Func(all_legal))
    assert known.trains["A"].path[0]["dep"] >= parse_time("03:00")
    unknown = run(tiny(t, [win]), Func(all_legal))
    # не объявлено — диспетчер не мог знать; поезд ушёл, бригада ждала, окно сдвинулось
    assert unknown.trains["A"].path[0]["dep"] < parse_time("02:00")
    assert unknown.windows["D1"]["shift_s"] > 0


def test_unknown_and_malformed_commands_rejected():
    raw = tiny([train("A", "S1", "E", "01:00")])
    sim = Simulator(load_scenario(raw))
    sim.run(Func(lambda o: [{"type": "teleport", "train": "A"}, {"type": "dispatch", "train": "NOPE"},
                            {"type": "dispatch", "train": "A", "next": "W"}, "garbage",
                            {"type": "wake_at", "time": 0}] if o["t"] == parse_time("01:00") else []),
            until=parse_time("01:00"))
    assert _rej(sim) == ["unknown_command", "unknown_train", "not_on_route", "bad_command", "bad_time"]


# ------------------------------------------------------------ тупики не разруливаются

def test_deadlock_is_reported_not_resolved():
    raw = tiny([train("E1", "S1", "E", "01:00"), train("W1", "S2", "W", "01:00")], s1=(1100,), s2=(1100,))
    sim = run(raw, Func(all_legal))
    assert sim.status == "deadlock"
    assert sim.end_time == load_scenario(raw).horizon
    assert {t.arrived_at for t in sim.trains.values()} == {None}


def test_policy_inaction_is_stall_not_deadlock():
    raw = tiny([train("A", "S1", "E", "01:00")])
    sim = run(raw, Func(lambda o: []))
    assert sim.status == "stalled"


# ------------------------------------------------------------ порт и паромы

def test_ferries_respect_capacity_and_order_of_operations():
    raw = day("day2")
    sc = load_scenario(raw)
    sim = Simulator(sc).run(make_dispatcher("scripted", sc))
    for fid, fs in sim.ferries.items():
        spec = sc.ferries[fid]
        assert fs.loaded <= spec.capacity
        if fs.departed is not None:
            assert fs.berthed >= spec.actual_arrival
            assert fs.departed >= fs.berthed + spec.unload_s
    for tr in sim.trains.values():
        if tr.port:
            assert sum(tr.port["loaded_on"].values()) + tr.port["remaining"] == tr.spec.wagons
            for fid in tr.port["loaded_on"]:
                assert sim.ferries[fid].berthed + sc.ferries[fid].unload_s >= 0
    m = export_result(sim)["metrics"]
    for port, v in m["ports"].items():
        assert 0 < v["peak_occupied"] <= v["port_tracks"], port   # шторм заполняет порт, но не сверх путей
