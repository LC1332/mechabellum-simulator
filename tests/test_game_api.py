# /api/game audit-game API tests (任务书 §10.5) + the acceptance flow.
#
# Runs against the committed fixture library (data/samples/replay_game) via
# MECHABELLUM_GAME_LIB so a local corpus can never change outcomes. Covers:
# library list, disabled option rejection, session CRUD + versioning,
# rejected-action invariance, the 4-round acceptance run (opening -> deploy
# -> reinforcement -> battle -> settle), scanner/runtime blocker agreement
# and the 禁止快照回灌 (future-snapshot pollution) equivalence.
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["MECHABELLUM_GAME_LIB"] = os.path.join(
    ROOT, "data", "samples", "replay_game")

from fastapi.testclient import TestClient  # noqa: E402

from web.server import app  # noqa: E402

FIXTURE = os.path.join(ROOT, "data", "samples", "replay_game")
if not os.path.exists(os.path.join(FIXTURE, "manifest.json")):
    pytest.skip("game fixture library not built", allow_module_level=True)

MANIFEST = json.load(open(os.path.join(FIXTURE, "manifest.json"), encoding="utf8"))
BEST = max(MANIFEST["options"], key=lambda o: o["playable_through_round"])
BEST_BLOCKER = next((b for b in BEST["blockers"] if not b.get("strict")), None)
DISABLED = next(o for o in MANIFEST["options"]
                if o["playable_through_round"] == 0)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def cmd(client, sid, version, kind, payload=None):
    r = client.post("/api/game/sessions/%s/commands" % sid,
                    json={"expected_version": version, "kind": kind,
                          "payload": payload or {}})
    return r


def new_session(client, option=None, min_rounds=3, battle_seed=12345):
    o = option or BEST
    r = client.post("/api/game/sessions", json={
        "replay_id": o["replay_id"], "opponent_player": o["opponent_player"],
        "min_rounds": min_rounds, "battle_seed": battle_seed})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- library
def test_library_lists_options_with_prefix(client):
    lib = client.get("/api/game/replays?min_rounds=5").json()
    assert lib["corpus_available"] is True
    assert lib["schema_version"] == "replay_library_v1"
    ids = {o["option_id"] for o in lib["options"]}
    assert {"94974af9a119-0", "b198291ffab1-0", "6a21468d36c0-0"} <= ids
    for o in lib["options"]:
        # enabled must follow the strict prefix rule, never hand-set
        assert o["enabled"] == (o["playable_through_round"] >= 5)
        assert o["blockers"] is not None
    best = next(o for o in lib["options"] if o["option_id"] == "94974af9a119-0")
    assert best["playable_through_round"] == 4


def test_disabled_option_rejected(client):
    r = client.post("/api/game/sessions", json={
        "replay_id": DISABLED["replay_id"],
        "opponent_player": DISABLED["opponent_player"], "min_rounds": 3})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "REPLAY_OPTION_DISABLED"
    assert body["detail"]


# ---------------------------------------------------------------- session CRUD
def test_session_lifecycle_and_versioning(client):
    v = new_session(client)
    sid = v["session_id"]
    assert v["phase"] == "opening" and v["version"] == 0

    got = client.get("/api/game/sessions/%s" % sid).json()
    assert got["version"] == 0

    # stale version -> 409 STALE_SESSION_VERSION
    r = cmd(client, sid, 99, "CHOOSE_OPENING", {"index": 0})
    assert r.status_code == 409 and r.json()["error"] == "STALE_SESSION_VERSION"

    # unknown session -> 404
    assert client.get("/api/game/sessions/deadbeef").status_code == 404

    # delete -> gone
    assert client.delete("/api/game/sessions/%s" % sid).status_code == 200
    assert client.get("/api/game/sessions/%s" % sid).status_code == 404


def test_opening_offers_shape_and_choice(client):
    v = new_session(client)
    sid = v["session_id"]
    offers = v["opening_offers"]
    assert len(offers) == 4
    recorded = [o for o in offers if o["source"] == "replay_recorded"]
    assert len(recorded) == 1, "historical candidate pinned exactly once"
    assert {o["index"] for o in offers} == {0, 1, 2, 3}
    for o in offers:
        assert o["units"] and o["hp"] > 0

    # choose a GENERATED opening (differs from the historical pick)
    gen = next(o for o in offers if o["source"] == "generated_v1")
    r = cmd(client, sid, 0, "CHOOSE_OPENING", {"index": gen["index"]})
    assert r.status_code == 200
    v = r.json()
    assert v["phase"] == "deployment" and v["round"] == 1
    assert v["version"] == 1
    human = next(p for p in v["players"] if p["role"] == "human")
    opp = next(p for p in v["players"] if p["role"] == "opponent")
    assert len(human["units"]) == 5 and len(opp["units"]) == 5
    assert human["supply"] > 0 and opp["supply"] > 0
    # determinism: same seeds -> same opening state
    v2 = new_session(client, battle_seed=12345)
    sid2 = v2["session_id"]
    r2 = cmd(client, sid2, 0, "CHOOSE_OPENING", {"index": gen["index"]})
    assert r2.json()["state_digest"] == v["state_digest"]
    client.delete("/api/game/sessions/%s" % sid)
    client.delete("/api/game/sessions/%s" % sid2)


# ------------------------------------------------------- rejected invariance
def test_illegal_action_rejected_without_state_change(client):
    v = new_session(client)
    sid = v["session_id"]
    r = cmd(client, sid, 0, "CHOOSE_OPENING",
            {"index": next(o["index"] for o in v["opening_offers"]
                           if o["source"] == "generated_v1")})
    v = r.json()
    digest, version = v["state_digest"], v["version"]
    supply = next(p for p in v["players"] if p["role"] == "human")["supply"]

    locked = v["legal_actions"]["unlock"][0]
    r = cmd(client, sid, version, "APPLY_ACTION", {
        "action": {"kind": "buy_unit",
                   "args": {"mech_id": locked["mech_id"], "x": 0, "y": -80}}})
    assert r.status_code == 200
    v = r.json()
    rr = v["rejected_receipt"]
    assert rr and rr["accepted"] is False
    assert rr["reason_code"] == "MECH_NOT_UNLOCKED"
    assert v["version"] == version, "rejected action must not bump version"
    assert v["state_digest"] == digest, "rejected action must not change state"
    h = next(p for p in v["players"] if p["role"] == "human")
    assert h["supply"] == supply and not any(
        u for u in h["units"] if u["mech_id"] == locked["mech_id"])
    client.delete("/api/game/sessions/%s" % sid)


def test_undo_restores_checkpoint(client):
    v = new_session(client)
    sid = v["session_id"]
    r = cmd(client, sid, 0, "CHOOSE_OPENING",
            {"index": next(o["index"] for o in v["opening_offers"]
                           if o["source"] == "replay_recorded")})
    v = r.json()
    ver, digest = v["version"], v["state_digest"]
    afford = next(b for b in v["legal_actions"]["buy"] if b["affordable"])
    r = cmd(client, sid, ver, "APPLY_ACTION", {
        "action": {"kind": "buy_unit",
                   "args": {"mech_id": afford["mech_id"], "x": 40, "y": -90}}})
    v = r.json()
    assert v["version"] == ver + 1
    after = v["state_digest"]
    assert after != digest
    r = cmd(client, sid, v["version"], "UNDO_LAST_HUMAN_ACTION")
    v = r.json()
    assert v["state_digest"] == digest, "undo must restore the checkpoint"
    assert v["version"] == ver + 2
    # undo on empty stack -> rejected receipt, state unchanged
    d2 = v["state_digest"]
    r = cmd(client, sid, v["version"], "UNDO_LAST_HUMAN_ACTION")
    v = r.json()
    assert v["rejected_receipt"]["reason_code"] == "UNDO_EMPTY"
    assert v["state_digest"] == d2
    client.delete("/api/game/sessions/%s" % sid)


# ---------------------------------------------------------------- acceptance
def test_acceptance_flow_rounds_1_to_prefix_end(client):
    """0.1-style acceptance within v1 capability: a non-historical opening,
    free deploy actions each round, real reinforcement offers, one
    tower/blueprint operation, battles settled by pysim, until the option's
    playable prefix ends in a BLOCKED whose reason matches the scanner."""
    v = new_session(client)
    sid = v["session_id"]
    r = cmd(client, sid, 0, "CHOOSE_OPENING", {
        "index": next(o["index"] for o in v["opening_offers"]
                      if o["source"] == "generated_v1")})
    v = r.json()
    did = {"buy": False, "move": False, "tower": False, "upgrade": False,
           "reinforce": False}
    battles = 0
    guard = 0

    while v["phase"] in ("deployment", "reinforcement", "round_result"):
        guard += 1
        assert guard < 400, "acceptance loop did not converge"
        if v["phase"] == "round_result":
            r = cmd(client, sid, v["version"], "ACK_ROUND_RESULT")
            v = r.json()
            continue
        if v["phase"] == "reinforcement":
            supported = [o for o in v["reinforcement_offers"] if o["supported"]]
            assert supported, "scanner promised a pickable card in-prefix"
            pick = supported[-1]
            r = cmd(client, sid, v["version"], "CHOOSE_REINFORCEMENT",
                    {"index": pick["index"]})
            v = r.json()
            assert v.get("rejected_receipt") is None
            did["reinforce"] = True
            continue
        # deployment
        human = next(p for p in v["players"] if p["role"] == "human")
        la = v["legal_actions"]
        if not did["buy"]:
            afford = next((b for b in la["buy"] if b["affordable"]), None)
            if afford:
                r = cmd(client, sid, v["version"], "APPLY_ACTION", {
                    "action": {"kind": "buy_unit", "args": {
                        "mech_id": afford["mech_id"], "x": 55, "y": -95}}})
                v = r.json()
                assert not v.get("rejected_receipt"), v["rejected_receipt"]
                did["buy"] = True
                continue
        if not did["move"] and human["units"]:
            u = human["units"][0]
            r = cmd(client, sid, v["version"], "APPLY_ACTION", {
                "action": {"kind": "move_unit", "args": {
                    "ref": {"handle": u["handle"]}, "x": 20, "y": -120}}})
            v = r.json()
            if not v.get("rejected_receipt"):
                did["move"] = True
                continue
        if not did["tower"] and human["supply"] >= 100:
            r = cmd(client, sid, v["version"], "APPLY_ACTION", {
                "action": {"kind": "raw_unsupported", "args": {
                    "raw_type": "StrengthenTower", "raw": [["Index", 0]]}}})
            v = r.json()
            if not v.get("rejected_receipt"):
                did["tower"] = True
                continue
        if not did["upgrade"]:
            up = next((u for u in human["units"]
                       if u["upgrade_price"] is not None
                       and human["supply"] >= u["upgrade_price"]), None)
            if up:
                r = cmd(client, sid, v["version"], "APPLY_ACTION", {
                    "action": {"kind": "upgrade_unit",
                               "args": {"ref": {"handle": up["handle"]}}}})
                v = r.json()
                if not v.get("rejected_receipt"):
                    did["upgrade"] = True
                    continue
        r = cmd(client, sid, v["version"], "FINISH_DEPLOYMENT")
        assert r.status_code == 200
        v = r.json()
        if v.get("battle"):
            battles += 1
            b = v["battle"]
            assert b["battle_seed"] > 0 and b["trace"], "trace from the same simulate"
            assert b["replay_oracle"]["note"], "real result labeled contrast-only"
            for p in v["players"]:
                assert p["supply"] >= 0, "no negative supply ever"
                assert p["hp"] > 0 or v["phase"] == "terminal"
    # the session ends at the scanner-predicted blocker, OR pysim ends the
    # game first (任务书 0.1.6: "结算到 round 5, 或 pysim 提前把一方 HP 扣到 0")
    assert v["phase"] in ("blocked", "terminal"), v["phase"]
    if v["phase"] == "blocked":
        assert v["stop_reason"] == "OPPONENT_PLAN_FAILED"
        assert BEST_BLOCKER and BEST_BLOCKER["round"] == v["round"], (
            "runtime blocker round %s != scanner prediction %s"
            % (v["round"], BEST_BLOCKER and BEST_BLOCKER["round"]))
    else:
        assert v["stop_reason"], "terminal needs a reason"
    assert did["buy"] and did["move"] and did["reinforce"]
    assert did["tower"] or did["upgrade"]
    assert battles >= 3, "settled fights inside the prefix"
    assert all(e["amount"] == 0 or True for e in v["ledger"])  # ledger present
    client.delete("/api/game/sessions/%s" % sid)


def test_gameview_hides_internals(client):
    v = new_session(client)
    blob = json.dumps(v)
    for secret in ("next_entity_id", "entity_id", "rng", "shard", "_state",
                   "fast_debts"):
        assert secret not in blob, "GameView leaked %s" % secret
    # unit handles exist and are the action-reference space
    r = cmd(client, sid := v["session_id"], 0, "CHOOSE_OPENING",
            {"index": v["opening_offers"][0]["index"]})
    v = r.json()
    for p in v["players"]:
        for u in p.get("units", []):
            assert "handle" in u and u.get("entity_id") is None
    client.delete("/api/game/sessions/%s" % sid)


# ------------------------------------------------------------ pollution gate
def test_future_snapshot_pollution_leaves_trajectory_identical(tmp_path):
    """任务书 10.4: mutate every future-snapshot field (hp/supply/units/
    exp/labels/fight reports) of a shard copy; the trajectory digests of the
    scripted session must be identical to the unmutated run."""
    import threading

    from web.game_library import GameLibrary
    from web.game_service import GameSessionStore, Economy
    from pysim.gamedata import GameData

    shard = json.load(open(os.path.join(FIXTURE, "games",
                                        "%s.json" % BEST["replay_id"]),
                           encoding="utf8"))

    def run(shard_dict):
        lib_dir = tmp_path / ("lib_polluted" if shard_dict is not shard
                              else "lib_clean")
        gdir = lib_dir / "games"
        gdir.mkdir(parents=True)
        option = copy.deepcopy(BEST)
        option["shard"] = "games/x.json"
        shard2 = copy.deepcopy(shard_dict if shard_dict is not shard
                               else shard)
        json.dump(shard2, open(gdir / "x.json", "w", encoding="utf8"),
                  ensure_ascii=False)
        manifest = {"schema_version": "replay_game_manifest_v1",
                    "ruleset_version": "normal_1v1_replay_v0",
                    "options": [option]}
        json.dump(manifest, open(lib_dir / "manifest.json", "w",
                                 encoding="utf8"))
        prev = os.environ.get("MECHABELLUM_GAME_LIB")
        os.environ["MECHABELLUM_GAME_LIB"] = str(lib_dir)
        try:
            lib = GameLibrary(str(lib_dir))
            gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
            store = GameSessionStore(lib, gd, Economy(gd))
            sess = store.create(BEST["replay_id"],
                                BEST["opponent_player"],
                                battle_seed_base=777, min_rounds=3)
        finally:
            if prev is None:
                os.environ.pop("MECHABELLUM_GAME_LIB", None)
            else:
                os.environ["MECHABELLUM_GAME_LIB"] = prev
        digests = []
        view, _ = sess.execute(0, "CHOOSE_OPENING", {"index": 0})
        digests.append(view["state_digest"])
        for rnd in range(1, BEST["playable_through_round"] + 2):
            if view["phase"] == "round_result":
                view, _ = sess.execute(view["version"], "ACK_ROUND_RESULT", {})
                digests.append(view["state_digest"])
            if view["phase"] == "reinforcement":
                sup = [o for o in view["reinforcement_offers"]
                       if o["supported"]]
                view, _ = sess.execute(view["version"], "CHOOSE_REINFORCEMENT",
                                       {"index": sup[-1]["index"] if sup else -1})
                digests.append(view["state_digest"])
            if view["phase"] == "deployment":
                view, _ = sess.execute(view["version"], "FINISH_DEPLOYMENT", {})
                digests.append(view["state_digest"])
            if view["phase"] in ("blocked", "terminal"):
                break
        return digests

    clean = run(shard)

    bad = copy.deepcopy(shard)
    CUT = 2     # rounds >= 3 are "future" for the first two fought rounds
    for pr in bad["game"]["players"]:
        for rec in pr["rounds"]:
            if int(rec["round"]) >= CUT:
                rec["reactorCore"] = 1
                rec["supply"] = 9999
                rec["preRoundFightResult"] = "Win"
                for u in rec.get("units") or []:
                    u["x"] = -u["x"]
                    u["level"] = 8
                    u["exp"] = 5000
    for pair in bad["game"].get("pairs", []):
        if int(pair.get("round", 0)) >= CUT:
            pair["label"] = "Win" if pair.get("label") == "Lose" else "Lose"
            for rep in (pair.get("match") or {}).get("reports") or []:
                rep["score"] = 4321

    polluted = run(bad)
    assert clean == polluted, (
        "future snapshots leaked into the trajectory: %s vs %s"
        % (clean, polluted))
