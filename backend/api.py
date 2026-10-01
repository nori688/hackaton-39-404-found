"""HTTP API симулятора.  Запуск:  uvicorn api:app --reload --port 8000"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dispatchers import make_dispatcher
from game import GameSession
from scenarios.generator import generate
from sim import ScenarioError, Simulator, export_result, load_scenario

SCEN_DIR = Path(__file__).parent / "scenarios"

app = FastAPI(title="Ночной реплей диспетчера — симулятор")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _raw(sid: str) -> dict:
    p = SCEN_DIR / f"{sid}.json"
    if not p.is_file() or p.parent != SCEN_DIR:
        raise HTTPException(404, f"сценарий {sid} не найден")
    return json.loads(p.read_text(encoding="utf-8"))


class SimRequest(BaseModel):
    scenario: str | None = None
    scenario_data: dict | None = None
    dispatcher: Literal["scripted", "priority"] = "scripted"
    mode: Literal["honest", "oracle"] = "honest"


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/scenarios")
def scenarios():
    out = []
    for p in sorted(SCEN_DIR.glob("*.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": raw.get("id", p.stem), "name": raw.get("name", ""),
                    "trains": len(raw.get("trains", [])), "ferries": len(raw.get("ferries", []))})
    return out


@app.get("/api/scenarios/{sid}")
def scenario(sid: str):
    return _raw(sid)


@app.post("/api/simulate")
def simulate(req: SimRequest):
    if (req.scenario is None) == (req.scenario_data is None):
        raise HTTPException(422, "нужно ровно одно из полей: scenario или scenario_data")
    raw = req.scenario_data if req.scenario_data is not None else _raw(req.scenario)
    try:
        sc = load_scenario(raw)
    except (ScenarioError, KeyError, ValueError) as e:
        raise HTTPException(422, f"ошибка сценария: {e}")
    pol = make_dispatcher(req.dispatcher, sc)
    sim = Simulator(sc, knowledge_mode=req.mode).run(pol)
    return export_result(sim, pol.name)


# ================================================================ генератор и импорт

class GenRequest(BaseModel):
    seed: int | None = None
    traffic: Literal["low", "normal", "high"] = "normal"
    disruptions: Literal["none", "low", "normal", "high"] = "normal"
    storm: bool | None = None


def _gen(g: GenRequest) -> dict:
    import random
    seed = g.seed if g.seed is not None else random.randint(1, 999_999)
    return generate(seed, g.traffic, g.disruptions, g.storm)


def _summary(raw: dict, sc) -> dict:
    return {"id": sc.id, "name": sc.name, "trains": len(sc.trains), "ferries": len(sc.ferries),
            "disruptions": len(sc.disruptions), "horizon": sc.horizon}


@app.post("/api/scenarios/validate")
def validate(raw: dict):
    """Проверка внешних данных (импорт со станции) без запуска игры."""
    try:
        sc = load_scenario(raw)
    except (ScenarioError, KeyError, ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "summary": _summary(raw, sc)}


# ================================================================ игра

GAMES: dict[str, GameSession] = {}
MAX_GAMES = 30


class NewGame(BaseModel):
    scenario: str | None = None          # готовый сценарий из папки scenarios
    scenario_data: dict | None = None    # импорт: свой JSON
    generate: GenRequest | None = None   # случайный синтетический день
    autopilot: bool = False
    pause_on: list[str] | None = None


@app.post("/api/game")
def new_game(req: NewGame):
    given = [x is not None for x in (req.scenario, req.scenario_data, req.generate)]
    if sum(given) != 1:
        raise HTTPException(422, "нужно ровно одно: scenario, scenario_data или generate")
    if req.generate is not None:
        raw = _gen(req.generate)
    elif req.scenario_data is not None:
        raw = req.scenario_data
    else:
        raw = _raw(req.scenario)
    try:
        sc = load_scenario(raw)
    except (ScenarioError, KeyError, ValueError, TypeError) as e:
        raise HTTPException(422, f"ошибка сценария: {e}")
    if len(GAMES) >= MAX_GAMES:
        GAMES.pop(next(iter(GAMES)))
    g = GameSession(sc, raw, autopilot=req.autopilot, pause_on=req.pause_on)
    GAMES[g.id] = g
    g.advance(0)
    return g.state()


def _game(gid: str) -> GameSession:
    g = GAMES.get(gid)
    if g is None:
        raise HTTPException(404, "игра не найдена (сервер перезапускался?)")
    return g


class Advance(BaseModel):
    dt: int


@app.post("/api/game/{gid}/advance")
def game_advance(gid: str, req: Advance):
    g = _game(gid)
    g.advance(req.dt)
    return g.state()


@app.post("/api/game/{gid}/resume")
def game_resume(gid: str):
    g = _game(gid)
    g.resume()
    return g.state()


@app.post("/api/game/{gid}/command")
def game_command(gid: str, cmd: dict):
    g = _game(gid)
    r = g.command(cmd)
    return {"result": r, "state": g.state()}


@app.get("/api/game/{gid}")
def game_state(gid: str):
    return _game(gid).state()


@app.get("/api/game/{gid}/report")
def game_report(gid: str):
    """Итог смены: игрок против автопилота на том же дне. Правда сценария — только после конца."""
    return _game(gid).report()


# ================================================================ ночной реплей

import os  # noqa: E402
import threading  # noqa: E402
import uuid  # noqa: E402

from replay import log_source, policy_source, run_replay  # noqa: E402

REPLAYS: dict[str, dict] = {}
REPLAY_BY_KEY: dict[str, str] = {}
WORKERS = max(1, min(3, (os.cpu_count() or 2) - 1))
SOURCE_LABEL = {"scripted": "записанный диспетчер", "priority": "автопилот"}


class ReplayReq(BaseModel):
    game: str | None = None              # разобрать сыгранную смену
    scenario: str | None = None          # или готовый сценарий...
    generate: GenRequest | None = None   # ...или случайный день
    dispatcher: Literal["scripted", "priority"] = "scripted"   # кто вёл смену в факте


def _replay_job(job, sc, src, label):
    def progress(done, total):
        job["done"], job["total"] = done, total
    try:
        rep = run_replay(sc, src, label, progress=progress, workers=WORKERS)
        job["compares"] = rep.pop("compares")
        job["result"] = rep
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001 — отдаём ошибку клиенту, а не роняем сервер
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"


@app.post("/api/replay")
def start_replay(req: ReplayReq):
    given = [x is not None for x in (req.game, req.scenario, req.generate)]
    if sum(given) != 1:
        raise HTTPException(422, "нужно ровно одно: game, scenario или generate")
    if req.game is not None:
        g = _game(req.game)
        if not g.ended:
            raise HTTPException(409, "смена ещё идёт — ночной реплей делается после неё")
        key = f"game:{g.id}"
        sc = g.sc
        src = log_source(g.sim.decisions, g.sim.external_wakes, dict(g.policy.routes))
        label = "ваша смена"
    else:
        raw = _gen(req.generate) if req.generate is not None else _raw(req.scenario)
        try:
            sc = load_scenario(raw)
        except (ScenarioError, KeyError, ValueError, TypeError) as e:
            raise HTTPException(422, f"ошибка сценария: {e}")
        key = f"scn:{sc.id}:{req.dispatcher}"
        src = policy_source(make_dispatcher(req.dispatcher, sc))
        label = SOURCE_LABEL[req.dispatcher]
    if key in REPLAY_BY_KEY and REPLAYS[REPLAY_BY_KEY[key]]["status"] != "error":
        return {"id": REPLAY_BY_KEY[key]}
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "status": "running", "done": 0, "total": None, "result": None, "error": None, "compares": {}}
    REPLAYS[jid] = job
    REPLAY_BY_KEY[key] = jid
    threading.Thread(target=_replay_job, args=(job, sc, src, label), daemon=True).start()
    return {"id": jid}


def _job(jid: str) -> dict:
    job = REPLAYS.get(jid)
    if job is None:
        raise HTTPException(404, "реплей не найден (сервер перезапускался?)")
    return job


@app.get("/api/replay/{jid}")
def replay_status(jid: str):
    job = _job(jid)
    return {k: job[k] for k in ("id", "status", "done", "total", "error", "result")}


@app.get("/api/replay/{jid}/compare/{cid}")
def replay_compare(jid: str, cid: str):
    job = _job(jid)
    if cid not in job["compares"]:
        raise HTTPException(404, "нет такого сравнения")
    return job["compares"][cid]
