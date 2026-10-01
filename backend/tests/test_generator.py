import json

import pytest

from dispatchers import PriorityDispatcher
from scenarios.build import route, straight
from scenarios.generator import generate
from sim import Simulator, load_scenario, parse_time


def test_same_seed_same_day():
    assert json.dumps(generate(5)) == json.dumps(generate(5))
    assert json.dumps(generate(5)) != json.dumps(generate(6))


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("traffic,dis", [("low", "low"), ("normal", "normal"), ("high", "high")])
def test_generated_days_are_within_bounds_and_playable(seed, traffic, dis):
    raw = generate(seed, traffic, dis)
    sc = load_scenario(raw)
    for s in sc.segments.values():
        assert 16 <= s.length_km <= 36
    longest = {n.id: max((t.length_m for t in n.tracks), default=10**9) for n in sc.nodes.values()}
    for t in sc.trains.values():
        for node in (t.origin, *t.destinations):
            assert t.length_m <= longest[node]
        assert t.planned_arrival > t.appear_planned
    for b in [n.id for n in sc.nodes.values() if n.is_boundary]:
        times = sorted(t.appear_planned for t in sc.trains.values() if t.origin == b)
        assert all(y - x >= 25 * 60 for x, y in zip(times, times[1:])), b
    # любые два встречных могут скреститься на любом разъезде: длинные — только контейнерные,
    # а они все идут в одну сторону (к портам)
    longest = max(t.length_m for t in sc.trains.values())
    other = max(t.length_m for t in sc.trains.values() if t.cargo != "ferry")
    for n in sc.nodes.values():
        if n.type == "siding":
            ls = sorted((t.length_m for t in n.tracks), reverse=True)
            assert ls[0] >= longest and ls[1] >= other
    # маршруты сквозные: на узлах поезд не разворачивается
    for t in sc.trains.values():
        for d in t.destinations:
            assert straight(raw, route(raw, t.origin, d)), (t.id, d)
    for d in sc.disruptions:
        assert d.announced_duration_s > 0
    # прогон движком проходит без нарушений инвариантов
    Simulator(sc).run(PriorityDispatcher())


def test_storm_delays_ferries_with_imperfect_forecasts():
    raw = generate(3, "normal", "normal", storm=True)
    assert raw["generator"]["storm"]
    late = [f for f in raw["ferries"]
            if parse_time(f["actual_arrival"]) - parse_time(f["planned_arrival"]) > 4 * 3600]
    assert late
    for f in late:
        for u in f["eta_updates"]:
            assert parse_time(u["at"]) < parse_time(f["actual_arrival"])
