"""Реплей честен: альтернативы строятся из видимого, ожидаемый мир не знает будущего,
факт воспроизводится точно, сравнение — при одинаковом продолжении дня."""
import json

import pytest

from dispatchers import make_dispatcher
from game import GameSession
from replay import collect, evaluate_point, log_source, policy_source, run_replay
from replay.runner import compact_path
from scenarios.generator import generate
from sim import compute_metrics, load_scenario
from sim.observe import build_observation
from sim.worlds import expected_world, retro_world
from tests.helpers import day


@pytest.fixture(scope="module")
def day1_points():
    sc = load_scenario(day("day1"))
    fact, pts = collect(sc, policy_source(make_dispatcher("scripted", sc)), max_points=1000)
    return sc, fact, pts


def _strip(o):
    return json.dumps({k: v for k, v in o.items() if k != "last_rejections"}, sort_keys=True, ensure_ascii=False)


def test_expected_world_shows_exactly_what_was_known(day1_points):
    sc, _, pts = day1_points
    assert len(pts) > 10
    for p in pts[::3]:
        w = expected_world(p["snap"])
        assert _strip(build_observation(w)) == _strip(p["obs"]), f"момент {p['t']}"
        for k in (1, 2):
            assert _strip(build_observation(expected_world(p["snap"], k))) == _strip(p["obs"])


def test_expected_world_has_no_future_secrets(day1_points):
    sc, _, pts = day1_points
    for p in pts[::4]:
        t = p["t"]
        w = expected_world(p["snap"])
        revealed = {f["id"] for f in p["snap"].revealed_failures}
        for d in w.sc.disruptions:
            assert d.id in revealed or (d.kind == "maintenance_window" and (
                (d.announce_at is not None and d.announce_at <= t) or p["snap"].windows[d.id]["state"] != "scheduled"))
        assert all(f.at <= t for tr in w.sc.trains.values() for f in tr.forecasts)
        assert all(u.at <= t for f in w.sc.ferries.values() for u in f.eta_updates)
        assert w.sc.params.running_noise == 0
        assert not any(ev[3] == "ANNOUNCE" for ev in w.q)


def test_retro_sample_zero_is_the_real_day(day1_points):
    sc, fact, pts = day1_points
    p = pts[len(pts) // 2]
    res = evaluate_point({**{k: v for k, v in p.items() if k != "obs"}, "samples": 1})
    base = res["base"]["true"][0]
    assert base["weighted"] == compute_metrics(fact)["weighted_delay_min"]
    assert res["paths"]["true"]["base"] == {tid: compact_path(tr.path) for tid, tr in fact.trains.items()}


def test_alternatives_use_only_visible_legal_moves(day1_points):
    _, _, pts = day1_points
    for p in pts:
        legal = {(a["train"], a["next"]) for a in p["obs"]["legal"]}
        for a in p["alts"]:
            for c in a["cmds"]:
                if c.get("type") == "dispatch":
                    assert (c["train"], c["next"]) in legal
            assert a["label"] and a["kind"]


def test_played_game_is_reproduced_exactly_from_log():
    raw = generate(21, "low", "normal")
    sc = load_scenario(raw)
    g = GameSession(sc, raw, autopilot=False, pause_on=["approach", "ready"])
    step = 0
    while not g.ended and step < 3000:
        step += 1
        if g.sim.paused:
            r = g.prompt["reasons"][0]
            if step % 3 and r.get("next"):
                g.command({"type": "dispatch", "train": r["train"], "next": r["next"]})
            g.resume()
        else:
            if step % 17 == 0:
                g.command({"type": "autopilot", "on": step % 34 == 0})
            legal = build_observation(g.sim)["legal"]
            if step % 5 == 0 and legal:
                a = legal[step % len(legal)]       # приказ «сейчас» между событиями
                g.command({"type": "dispatch", "train": a["train"], "next": a["next"]})
            g.advance(900 if step % 2 else 420)
    assert g.ended
    assert g.sim.external_wakes, "тест должен проверить приказы «сейчас»"
    src = log_source(g.sim.decisions, g.sim.external_wakes,
                     {k: v for k, v in g.policy.routes.items()})
    fact, _ = collect(sc, src, max_points=0)
    for tid, tr in g.sim.trains.items():
        assert fact.trains[tid].path == tr.path, tid
    assert compute_metrics(fact)["weighted_delay_min"] == compute_metrics(g.sim)["weighted_delay_min"]


def test_small_report_is_complete():
    sc = load_scenario(day("day2"))
    rep = run_replay(sc, policy_source(make_dispatcher("scripted", sc)), "scripted", workers=1, max_points=5)
    assert rep["stats"]["points"] == 5
    for key in ("missed", "tradeoffs", "good", "better_than_algo", "bad_luck", "problem_nodes", "method"):
        assert key in rep
    for c in rep["missed"] + rep["good"] + rep["bad_luck"]:
        assert c["compare_id"] in rep["compares"]
        assert c["verdict_honest"] in ("better", "worse", "uncertain")
    json.dumps(rep, ensure_ascii=False)
