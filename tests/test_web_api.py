# web API integration test (adapted from RouteC web/test_api.py):
# /api/simulate (with techs/towers/mods) + /api/gamedata + /api/replay import.
# Requires a running server (start_server.bat / .sh) - skips otherwise.
# run: pytest tests/test_web_api.py   (server on http://127.0.0.1:8300)
import json, urllib.request

import pytest

B = "http://127.0.0.1:8300"


def get(path):
    return json.load(urllib.request.urlopen(B + path, timeout=10))


def post(path, body):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


@pytest.fixture(scope="module", autouse=True)
def _server_up():
    try:
        get("/api/gamedata")
    except OSError:
        pytest.skip("sandbox server not running on %s (see start_server.bat)" % B)


def test_gamedata_techs():
    gd = get("/api/gamedata")
    assert gd.get("techs")
    c10 = next(c for c in gd["cards"] if c["mechID"] == 10)
    assert isinstance(c10["technologies"], list)


def test_simulate_with_techs():
    gd = get("/api/gamedata")
    owner = next(c["mechID"] for c in gd["cards"]
                 if 10102 in (c.get("technologies") or []))
    body = {"p0": [{"id": owner, "level": 1, "x": 0, "y": -150, "techs": [10102]}],
            "p1": [{"id": 28, "level": 1, "x": 0, "y": 150, "techs": []}]}
    d = post("/api/simulate", body)
    assert d["winner"] in (0, 1, -1)
    assert d["survivors"]["0"]["mechs"] + d["survivors"]["1"]["mechs"] >= 0


def test_simulate_towers_and_mods():
    body = {"p0": [{"id": 2, "level": 9, "x": 0, "y": -150, "techs": []},
                   {"id": 2, "level": 9, "x": 60, "y": -150, "techs": []}],
            "p1": [{"id": 10, "level": 1, "x": 0, "y": 150},
                   {"id": 10, "level": 1, "x": 60, "y": 150}],
            "towers": True, "towers0": [1, 0], "towers1": [0, 2],
            "mods0": {"range": 15, "speed": 0}, "mods1": {"range": 0, "speed": 3}}
    d = post("/api/simulate", body)
    assert "towers_down" in d
    assert d["winner"] in (0, 1, -1)


def test_replay_import():
    rp = get("/api/replays")
    if not rp:
        pytest.skip("no replay corpus loaded (local_data/ empty and sample missing)")
    # first pair that actually has units (round-0 pairs can be 0v0 empty
    # boards when one side deployed nothing)
    rnd = next((p["round"] for p in rp[0]["pairs"] if p["n0"] + p["n1"] > 0), None)
    if rnd is None:
        pytest.skip("first replay has no non-empty rounds")
    rr = get("/api/replay/%d/%d" % (rp[0]["idx"], rnd))
    assert rr["round"] == rnd
    assert rr["p0"]["units"] or rr["p1"]["units"]
    assert "techs" in rr["p0"] and "towerLevels" in rr["p0"]
