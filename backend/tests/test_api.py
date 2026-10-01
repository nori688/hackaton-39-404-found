from fastapi.testclient import TestClient

from api import app

client = TestClient(app)


def test_list_and_simulate():
    ids = {s["id"] for s in client.get("/api/scenarios").json()}
    assert {"day1", "day2"} <= ids
    r = client.post("/api/simulate", json={"scenario": "day1", "dispatcher": "scripted"})
    assert r.status_code == 200
    body = r.json()
    assert body["metrics"]["status"] in ("completed", "horizon")
    assert len(body["trains"]) == len(client.get("/api/scenarios/day1").json()["trains"])


def test_bad_requests():
    assert client.post("/api/simulate", json={}).status_code == 422
    assert client.post("/api/simulate", json={"scenario": "../secret"}).status_code == 404
    assert client.post("/api/simulate", json={"scenario_data": {"trains": []}}).status_code == 422


def test_game_flow_and_no_truth_before_end():
    r = client.post("/api/game", json={"generate": {"seed": 11, "traffic": "low", "disruptions": "normal"}})
    assert r.status_code == 200
    st = r.json()
    gid = st["id"]
    assert "scenario_truth" not in st
    assert client.get(f"/api/game/{gid}/report").json() == {"ready": False}
    for _ in range(400):
        if st["status"] != "running":
            break
        if st["paused"]:
            st = client.post(f"/api/game/{gid}/resume").json()
        else:
            st = client.post(f"/api/game/{gid}/advance", json={"dt": 1800}).json()
        assert "appear_actual" not in str(st["obs"])
    assert st["status"] != "running"
    rep = client.get(f"/api/game/{gid}/report").json()
    assert rep["ready"] and "player" in rep and "autopilot" in rep


def test_validate_import():
    assert client.post("/api/scenarios/validate", json={"nodes": []}).json()["ok"] is False
    raw = client.get("/api/scenarios/day1").json()
    assert client.post("/api/scenarios/validate", json=raw).json()["ok"] is True


def test_replay_job_for_scenario():
    import time
    r = client.post("/api/replay", json={"scenario": "day2", "dispatcher": "scripted"})
    assert r.status_code == 200
    jid = r.json()["id"]
    for _ in range(600):
        st = client.get(f"/api/replay/{jid}").json()
        if st["status"] != "running":
            break
        time.sleep(0.5)
    assert st["status"] == "done", st.get("error")
    rep = st["result"]
    cards = rep["missed"] + rep["good"]
    assert cards
    cmp = client.get(f"/api/replay/{jid}/compare/{cards[0]['compare_id']}").json()
    assert "true" in cmp and "honest" in cmp
    assert client.post("/api/replay", json={"scenario": "day2", "dispatcher": "scripted"}).json()["id"] == jid


def test_replay_requires_finished_game():
    g = client.post("/api/game", json={"generate": {"seed": 3, "traffic": "low"}}).json()
    assert client.post("/api/replay", json={"game": g["id"]}).status_code == 409
