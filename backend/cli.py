"""Прогон сценария из консоли:  python cli.py day1 --dispatcher scripted [--mode oracle] [--json out.json]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dispatchers import make_dispatcher
from sim import Simulator, export_result, load_scenario, summary_text

HERE = Path(__file__).parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--dispatcher", default="scripted", choices=["scripted", "priority"])
    ap.add_argument("--mode", default="honest", choices=["honest", "oracle"])
    ap.add_argument("--json")
    ap.add_argument("--events", action="store_true")
    a = ap.parse_args()
    path = Path(a.scenario)
    if not path.exists():
        path = HERE / "scenarios" / f"{a.scenario}.json"
    sc = load_scenario(path)
    pol = make_dispatcher(a.dispatcher, sc)
    sim = Simulator(sc, knowledge_mode=a.mode).run(pol)
    res = export_result(sim, pol.name)
    print(summary_text(res))
    if a.events:
        from sim import fmt_time
        for e in res["events"]:
            rest = {k: v for k, v in e.items() if k not in ("t", "event")}
            print(fmt_time(e["t"]), e["event"], rest)
        for r in res["rejections"]:
            print("ОТКЛОНЕНО", fmt_time(r["t"]), r["command"], r["reason"])
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
