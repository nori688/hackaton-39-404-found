"""Игрок подчиняется тем же правилам, что и любая политика."""
from game import GameSession
from sim import load_scenario, parse_time
from tests.helpers import tiny, train


def _session(raw, **kw):
    return GameSession(load_scenario(raw), raw, **kw)


def test_player_command_executes_now_not_in_past():
    raw = tiny([train("A", "S1", "E", "01:00")])
    g = _session(raw, pause_on=[])
    g.advance(parse_time("01:40"))          # игрок «прозевал» 40 минут
    assert g.clock == parse_time("01:40")
    r = g.command({"type": "dispatch", "train": "A", "next": "S2"})
    assert r["ok"]
    dep = g.sim.trains["A"].path[0]
    g.advance(600)
    assert g.sim.trains["A"].path[0]["dep"] == parse_time("01:41")  # 01:40 + приготовление маршрута


def test_player_illegal_command_rejected():
    raw = tiny([train("A", "S1", "E", "01:00"), train("B", "S1", "E", "01:00", length=600)])
    g = _session(raw, pause_on=[])
    g.advance(parse_time("01:00"))
    assert g.command({"type": "dispatch", "train": "A", "next": "S2"})["ok"]
    r = g.command({"type": "dispatch", "train": "B", "next": "S2"})
    assert not r["ok"] and r["rejections"][0]["reason"] == "segment_busy"


def test_pause_at_approach_allows_through_decision():
    raw = tiny([train("A", "W", "E", "01:00")])
    g = _session(raw, pause_on=["approach"])
    g.advance(parse_time("01:00"))
    assert g.sim.paused and g.prompt["reasons"][0]["kind"] == "approach"
    g.command({"type": "dispatch", "train": "A", "next": "S1"})
    g.resume()
    for _ in range(50):
        if g.sim.paused:
            r = g.prompt["reasons"][0]
            g.command({"type": "dispatch", "train": r["train"], "next": r["next"]})
            g.resume()
        else:
            g.advance(600)
        if g.ended:
            break
    path = g.sim.trains["A"].path
    assert all(p["through"] for p in path if "node" in p)
    assert g.sim.trains["A"].unplanned_stops == 0


def test_autopilot_same_as_priority_dispatcher():
    from dispatchers import PriorityDispatcher
    from sim import Simulator
    from tests.helpers import day
    raw = day("day1")
    g = _session(raw, autopilot=True, pause_on=[])
    while not g.ended:
        g.advance(3600)
    ref = Simulator(load_scenario(raw)).run(PriorityDispatcher())
    assert {t: s.arrived_at for t, s in g.sim.trains.items()} == {t: s.arrived_at for t, s in ref.trains.items()}
