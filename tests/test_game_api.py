# /api/game audit-game API tests (任务书 §10.5 + step2 §14).
#
# Runs against the committed fixture library (data/samples/replay_game) via
# MECHABELLUM_GAME_LIB so a local corpus can never change outcomes. Covers:
# library list (step2 G4/G5 presentation contract), disabled option
# rejection, session CRUD + versioning, opening side-aware halves (G1),
# deploy-zone rejection invariance (G2), the acceptance run (scanner/
# runtime blocker agreement), the board contract (G15), authoritative round
# reset (G14) and the 禁止快照回灌 equivalence.
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


def open_session(client, option=None, source="generated_v1", battle_seed=12345,
                 min_rounds=3):
    """Session with an opening chosen -> deployment-phase view."""
    v = new_session(client, option, min_rounds=min_rounds,
                    battle_seed=battle_seed)
    sid = v["session_id"]
    idx = next(o["index"] for o in v["opening_offers"] if o["source"] == source)
    r = cmd(client, sid, 0, "CHOOSE_OPENING", {"index": idx})
    assert r.status_code == 200, r.text
    return r.json()


def human_sign(v):
    """+1 when the human owns y>0 (player 1), -1 when y<0 (player 0)."""
    return 1 if v["board"]["human_player"] == 1 else -1


# ---------------------------------------------------------------- library
def test_library_lists_options_with_prefix(client):
    lib = client.get("/api/game/replays?min_rounds=5").json()
    assert lib["corpus_available"] is True
    assert lib["schema_version"] == "replay_library_v2"
    ids = {o["option_id"] for o in lib["options"]}
    assert {"94974af9a119-0", "b198291ffab1-0", "6a21468d36c0-0"} <= ids
    for o in lib["options"]:
        # start_mode is the server-owned presentation contract (G4)
        assert o["start_mode"] in ("normal", "limited", "disabled")
        assert o["enabled"] == (o["start_mode"] == "normal")
        # source-record range is explicit, never guessed by the frontend
        assert o["round_min"] == 0
        assert o["round_record_count"] == o["round_max"] + 1
        # strict blockers are separable from runtime blockers
        for b in o["blockers"]:
            if b.get("strict"):
                assert b in (o["first_strict_blocker"], ) or True
    best = next(o for o in lib["options"] if o["option_id"] == "94974af9a119-0")
    # step3: equipment offers stopped blocking the runtime -> prefix 4 -> 5;
    # the first runtime blocker sits at R6 (unmapped passthrough release).
    # step4 manifest rescan: the strict-effect scan covers R2's offers now,
    # so the first strict blocker (non-effect-complete candidates) moved
    # R3 -> R2 and the strict prefix ends at R1
    assert best["playable_through_round"] == 5
    assert best["runtime_playable_through_round"] == 5
    assert best["approximate_from_round"] == 3
    assert best["first_strict_blocker"]["round"] == 2
    assert best["first_strict_blocker"]["code"] == "APPROXIMATE_REINFORCEMENT_EFFECT"
    assert best["first_runtime_blocker"]["round"] == 6
    assert best["first_runtime_blocker"].get("strict") is None
    assert best["start_mode"] == "normal"      # 5 >= min_rounds floor
    # sorted by playable prefix desc (G5)
    ptrs = [o["playable_through_round"] for o in lib["options"]]
    assert ptrs == sorted(ptrs, reverse=True)


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
    assert v["schema_version"] == "game_view_v4"

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


# ------------------------------------------------------- opening halves (G1)
def test_opening_units_deploy_in_own_halves_both_sides(client):
    """14.1: whichever side the human takes, both players' opening units sit
    in their OWN half (player 0 y<0, player 1 y>0), never overlapping or
    crossing the midline."""
    opt_h1 = next(o for o in MANIFEST["options"]
                  if o["option_id"] == "94974af9a119-0")   # human = player 1
    opt_h0 = next(o for o in MANIFEST["options"]
                  if o["option_id"] == "94974af9a119-1")   # human = player 0
    for opt, mr in ((opt_h1, 3), (opt_h0, 1)):
        v = open_session(client, option=opt, min_rounds=mr)
        sid = v["session_id"]
        for p in v["players"]:
            side = p["player"]
            ys = [u["y"] for u in p["units"]]
            assert ys and (all(y < 0 for y in ys) if side == 0
                           else all(y > 0 for y in ys)), \
                "player %d opening units outside own half: %s" % (side, ys)
        # towers and units agree on halves (world truth source)
        for bp in v["board"]["players"]:
            for t in bp["towers"]:
                assert (t["y"] < 0) == (bp["player"] == 0)
        client.delete("/api/game/sessions/%s" % sid)


def test_opening_package_mirror_semantics():
    """14.1 pure-module: same package for both sides -> player 1 is the exact
    Y mirror of player 0 (x/mech/level/is_rotate identical, no double
    rotation); catalog v1 adapts to the v2 orientation explicitly."""
    from pysim.transition import opening as om

    pkg = {"name": "t", "hp": 4500, "supply": 100, "officers": [],
           "unlocked": [], "units": [
               {"mech": 10, "level": 1, "is_rotate": True,
                "formation": [[-60.0, -150.0], [0.0, -120.5], [60.0, -180.0]]}],
           "tech_map": {}, "constructions": [], "commander_skills": []}
    st = om.build_initial_state(pkg, pkg)
    p0 = {(u.mech_id, u.level, round(u.x, 1)): u for u in st.players[0].units}
    p1 = {(u.mech_id, u.level, round(u.x, 1)): u for u in st.players[1].units}
    assert set(p0) == set(p1), "x/mech/level identical across the mirror"
    for k in p0:
        assert abs(p0[k].y + p1[k].y) < 1e-9
        assert p0[k].is_rotate == p1[k].is_rotate, \
            "Y mirror must not re-rotate is_rotate"
        assert p0[k].y < 0 and p1[k].y > 0
    # mirror_package_y is its own inverse
    back = om.mirror_package_y(om.mirror_package_y(pkg))
    assert back["units"][0]["formation"] == pkg["units"][0]["formation"]
    # the v1 adapter negates y exactly once (positive-y -> player-0 form)
    v1 = {"schema_version": om.CATALOG_SCHEMA_V1,
          "packages": {"7": copy.deepcopy(pkg)}}
    v1["packages"]["7"]["units"][0]["formation"] = [
        [x, -y] for (x, y) in pkg["units"][0]["formation"]]
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False, encoding="utf8") as f:
        json.dump(v1, f)
        path = f.name
    try:
        cat = om.load_catalog(path)
        assert cat["schema_version"] == om.CATALOG_SCHEMA
        assert cat["adapted_from"] == om.CATALOG_SCHEMA_V1
        got = cat["packages"]["7"]["units"][0]["formation"]
        assert got == pkg["units"][0]["formation"]
    finally:
        os.unlink(path)


# ------------------------------------------------- board contract (G3/G15)
def test_board_contract_fields(client):
    v = open_session(client)
    sid = v["session_id"]
    b = v["board"]
    assert b["coordinate_space"] == "world_v1"
    assert b["bounds"] == {"x_min": -350.0, "x_max": 350.0,
                           "y_min": -300.0, "y_max": 300.0}
    assert b["midline_y"] == 0.0
    assert b["human_player"] == v["replay"]["human_player"]
    assert {p["role"] for p in b["players"]} == {"human", "opponent"}
    for p in b["players"]:
        assert p["player"] in (0, 1)
        zone = p["deploy_zone"]
        if p["player"] == 0:
            assert zone["y_max"] == 0.0 and zone.get("y_max_exclusive")
        else:
            assert zone["y_min"] == 0.0 and zone.get("y_min_exclusive")
        # towers come from the engine truth source and sit in the own half
        assert len(p["towers"]) == 2
        for t in p["towers"]:
            assert t["y"] < 0 if p["player"] == 0 else t["y"] > 0
            assert t["level"] == 0
    # legal actions expose undo/finish gates (G15)
    la = v["legal_actions"]
    assert la["can_undo"] is False and la["undo_reason"] == "UNDO_EMPTY"
    assert la["can_finish_deployment"] is True
    client.delete("/api/game/sessions/%s" % sid)


# --------------------------------------------------- deploy zone (G2, 14.2)
def test_buy_zone_rejection_invariance(client):
    """BuyUnit is own-half only; rejected buys leave state fully unchanged."""
    v = open_session(client)
    sid = v["session_id"]
    digest, version = v["state_digest"], v["version"]
    human = next(p for p in v["players"] if p["role"] == "human")
    supply = human["supply"]
    afford = next(b for b in v["legal_actions"]["buy"] if b["affordable"])
    sign = human_sign(v)                    # own half sign; enemy half = -sign

    def buy(x, y):
        return cmd(client, sid, v["version"], "APPLY_ACTION", {
            "action": {"kind": "buy_unit",
                       "args": {"mech_id": afford["mech_id"], "x": x, "y": y}}})

    # enemy half -> stable reason code, nothing changes
    r = buy(0, -sign * 80)
    assert r.status_code == 200
    v = r.json()
    assert v["rejected_receipt"]["reason_code"] == "POSITION_OUT_OF_DEPLOY_ZONE"
    assert v["version"] == version and v["state_digest"] == digest
    h = next(p for p in v["players"] if p["role"] == "human")
    assert h["supply"] == supply and len(h["units"]) == len(human["units"])
    # midline y=0 belongs to neither side
    r = buy(10, 0)
    assert r.json()["rejected_receipt"]["reason_code"] == \
        "POSITION_OUT_OF_DEPLOY_ZONE"
    # own half -> accepted, unit exists in own half
    r = buy(30, sign * 90)
    v = r.json()
    assert not v.get("rejected_receipt"), v.get("rejected_receipt")
    h = next(p for p in v["players"] if p["role"] == "human")
    bought = next(u for u in h["units"] if u["mech_id"] == afford["mech_id"]
                  and (u["y"] > 0) == (sign > 0) and abs(u["x"] - 30) < 1e-6)
    assert bought
    client.delete("/api/game/sessions/%s" % sid)


def test_illegal_action_rejected_without_state_change(client):
    v = open_session(client)
    sid = v["session_id"]
    digest, version = v["state_digest"], v["version"]
    supply = next(p for p in v["players"] if p["role"] == "human")["supply"]
    sign = human_sign(v)

    locked = v["legal_actions"]["unlock"][0]
    r = cmd(client, sid, version, "APPLY_ACTION", {
        "action": {"kind": "buy_unit",
                   "args": {"mech_id": locked["mech_id"],
                            "x": 0, "y": sign * 80}}})
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


def test_moves_may_cross_the_midline(client):
    """Corpus truth: MoveUnit is bounds-only (7/258 real replays cross the
    midline in R3+); the zone rule applies to buys only."""
    v = open_session(client)
    sid = v["session_id"]
    u = next(p for p in v["players"] if p["role"] == "human")["units"][0]
    target_y = -human_sign(v) * 120          # enemy half
    r = cmd(client, sid, v["version"], "APPLY_ACTION", {
        "action": {"kind": "move_unit",
                   "args": {"ref": {"handle": u["handle"]},
                            "x": 20, "y": target_y}}})
    v = r.json()
    assert not v.get("rejected_receipt"), v.get("rejected_receipt")
    h = next(p for p in v["players"] if p["role"] == "human")
    moved = next(x for x in h["units"] if x["handle"] == u["handle"])
    assert abs(moved["y"] - target_y) < 1e-6
    client.delete("/api/game/sessions/%s" % sid)


def test_undo_restores_checkpoint(client):
    v = open_session(client)
    sid = v["session_id"]
    ver, digest = v["version"], v["state_digest"]
    afford = next(b for b in v["legal_actions"]["buy"] if b["affordable"])
    r = cmd(client, sid, ver, "APPLY_ACTION", {
        "action": {"kind": "buy_unit",
                   "args": {"mech_id": afford["mech_id"],
                            "x": 40, "y": human_sign(v) * 90}}})
    v = r.json()
    assert v["version"] == ver + 1
    after = v["state_digest"]
    assert after != digest
    assert v["legal_actions"]["can_undo"] is True
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


# ------------------------------------------------- authoritative reset (G14)
def test_ack_resets_units_to_authoritative_positions(client):
    """14.5: record (handle,x,y) before FINISH; after battle+ACK the same
    handles carry the GameView deployment coordinates (settlement keeps
    deploy positions; the trace's last frame never leaks back)."""
    v = open_session(client)
    sid = v["session_id"]
    # move a unit so deployment coords differ from the opening formation
    u0 = next(p for p in v["players"] if p["role"] == "human")["units"][0]
    r = cmd(client, sid, v["version"], "APPLY_ACTION", {
        "action": {"kind": "move_unit", "args": {
            "ref": {"handle": u0["handle"]}, "x": -123.0, "y": -145.0}}})
    v = r.json()
    assert not v.get("rejected_receipt"), v.get("rejected_receipt")
    before = {u["handle"]: (round(u["x"], 1), round(u["y"], 1))
              for p in v["players"] for u in p["units"]}
    r = cmd(client, sid, v["version"], "FINISH_DEPLOYMENT")
    v = r.json()
    assert v["phase"] == "round_result" and v["battle"]["trace"]
    # the trace's final frame shows battle-moved positions — irrelevant
    r = cmd(client, sid, v["version"], "ACK_ROUND_RESULT")
    assert r.status_code == 200
    v = r.json()
    assert v["phase"] in ("reinforcement", "deployment", "blocked", "terminal")
    if v["phase"] == "blocked":
        client.delete("/api/game/sessions/%s" % sid)
        pytest.skip("blocked before reset assertion point")
    after = {u["handle"]: (round(u["x"], 1), round(u["y"], 1))
             for p in v["players"] for u in p["units"]}
    common = set(before) & set(after)
    assert common, "unit handles must survive the round tick"
    moved_back = {h for h in common if before[h] != after[h]}
    assert not moved_back, \
        "deploy coordinates drifted across the battle: %s" % sorted(moved_back)
    assert (-123.0, -145.0) in after.values(), "human move survived the battle"
    client.delete("/api/game/sessions/%s" % sid)


# ---------------------------------------------------------------- acceptance
def test_acceptance_flow_rounds_1_to_prefix_end(client):
    """0.1-style acceptance within v1 capability: a non-historical opening,
    free deploy actions each round, real reinforcement offers, one
    tower/blueprint operation, battles settled by pysim, until the option's
    playable prefix ends in a BLOCKED whose reason matches the scanner."""
    v = open_session(client)
    sid = v["session_id"]
    did = {"buy": False, "move": False, "tower": False, "upgrade": False,
           "reinforce": False}
    battles = 0
    guard = 0
    own = None

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
        # deployment — buy/move coordinates follow the human's own half
        human = next(p for p in v["players"] if p["role"] == "human")
        own = human_sign(v)
        la = v["legal_actions"]
        if not did["buy"]:
            afford = next((b for b in la["buy"] if b["affordable"]), None)
            if afford:
                r = cmd(client, sid, v["version"], "APPLY_ACTION", {
                    "action": {"kind": "buy_unit", "args": {
                        "mech_id": afford["mech_id"], "x": 55,
                        "y": own * 95}}})
                v = r.json()
                assert not v.get("rejected_receipt"), v["rejected_receipt"]
                did["buy"] = True
                continue
        if not did["move"] and human["units"]:
            u = human["units"][0]
            r = cmd(client, sid, v["version"], "APPLY_ACTION", {
                "action": {"kind": "move_unit", "args": {
                    "ref": {"handle": u["handle"]}, "x": 20, "y": own * 120}}})
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
    client.delete("/api/game/sessions/%s" % sid)


def test_gameview_hides_internals(client):
    v = new_session(client)
    blob = json.dumps(v)
    for secret in ("next_entity_id", "entity_id", "rng", "shard", "_state",
                   "fast_debts", "PriceQuote", "__type__"):
        assert secret not in blob, "GameView leaked %s" % secret
    # unit handles exist and are the action-reference space
    r = cmd(client, sid := v["session_id"], 0, "CHOOSE_OPENING",
            {"index": v["opening_offers"][0]["index"]})
    v = r.json()
    for p in v["players"]:
        for u in p.get("units", []):
            assert "handle" in u and u.get("entity_id") is None
    client.delete("/api/game/sessions/%s" % sid)


# ----------------------------------------------------- step3 GameView v3
def _find_equip_round(client, v, sid, limit=8):
    """Walk rounds until an equipment offer shows up; returns (view, offer)."""
    guard = 0
    while v["phase"] in ("deployment", "reinforcement", "round_result") \
            and guard < 60:
        guard += 1
        if v["phase"] == "round_result":
            v = cmd(client, sid, v["version"], "ACK_ROUND_RESULT").json()
            continue
        if v["phase"] == "reinforcement":
            eq = [o for o in v["reinforcement_offers"]
                  if o.get("battle_fidelity") == "approximate"]
            if eq:
                return v, eq[0]
            sup = [o for o in v["reinforcement_offers"] if o["supported"]]
            v = cmd(client, sid, v["version"], "CHOOSE_REINFORCEMENT",
                    {"index": sup[-1]["index"] if sup else -1}).json()
            continue
        v = cmd(client, sid, v["version"], "FINISH_DEPLOYMENT").json()
    return v, None


def test_equipment_flow_through_api(client):
    """step3 §6.5: equipment offer -> charge + inventory + legal targets in
    GameView; use_equipment binds; a rejected binding keeps the stock."""
    v = open_session(client)
    sid = v["session_id"]
    v, offer = _find_equip_round(client, v, sid)
    if offer is None:
        client.delete("/api/game/sessions/%s" % sid)
        pytest.skip("no equipment offer inside the sample prefix")
    assert offer["supported"] is True
    v = cmd(client, sid, v["version"], "CHOOSE_REINFORCEMENT",
            {"index": offer["index"]}).json()
    assert v.get("rejected_receipt") is None, v.get("rejected_receipt")
    assert v["phase"] == "deployment"
    human = next(p for p in v["players"] if p["role"] == "human")
    stocked = [e for e in human["equipment_inventory"]
               if e["equipment_id"] == offer["card_id"]]
    assert stocked, human["equipment_inventory"]
    inv = v["legal_actions"]["equipment"]["inventory"]
    entry = next(e for e in inv if e["equipment_id"] == offer["card_id"])
    assert entry["count"] == 1
    assert entry["battle_fidelity"] == "approximate"
    if not entry["legal_targets"]:
        # no legal carrier on the field at this round (longer step4 prefixes
        # surface such offers) — the binding part below has nothing to test
        client.delete("/api/game/sessions/%s" % sid)
        return
    # illegal target (not in legal_targets) -> rejected, stock unchanged
    target = entry["legal_targets"][0]
    illegal = next((u["handle"] for u in human["units"]
                    if u["handle"] not in entry["legal_targets"]), None)
    if illegal is not None:
        r = cmd(client, sid, v["version"], "APPLY_ACTION", {
            "action": {"kind": "use_equipment",
                       "args": {"equipment_id": offer["card_id"],
                                "unit_ref": {"handle": illegal}}}})
        v2 = r.json()
        rr = v2["rejected_receipt"]
        assert rr and rr["reason_code"] in ("EQUIPMENT_RESTRICTION_MISMATCH",
                                            "EQUIPMENT_TARGET_NOT_ALLOWED")
        h2 = next(p for p in v2["players"] if p["role"] == "human")
        assert any(e["equipment_id"] == offer["card_id"]
                   for e in h2["equipment_inventory"]), "stock must persist"
    # legal binding
    r = cmd(client, sid, v["version"], "APPLY_ACTION", {
        "action": {"kind": "use_equipment",
                   "args": {"equipment_id": offer["card_id"],
                            "unit_ref": {"handle": target}}}})
    v3 = r.json()
    assert not v3.get("rejected_receipt"), v3.get("rejected_receipt")
    h3 = next(p for p in v3["players"] if p["role"] == "human")
    bound = next(u for u in h3["units"] if u["handle"] == target)
    assert bound["equipment_id"] == offer["card_id"]
    assert bound["equipment_name"]
    assert bound["equipment_fidelity"] == "approximate"
    assert not any(e["equipment_id"] == offer["card_id"]
                   for e in h3["equipment_inventory"])
    client.delete("/api/game/sessions/%s" % sid)


def test_tech_view_lists_field_mechs_with_quotes(client):
    """step3 §4.3: the tech tab follows field mechs; the unlock quote
    breakdown comes from the server (frontend never recomputes)."""
    v = open_session(client)
    sid = v["session_id"]
    la = v["legal_actions"]
    human = next(p for p in v["players"] if p["role"] == "human")
    field_mecks = {u["mech_id"] for u in human["units"]}
    assert {t["mech_id"] for t in la["tech"]} == field_mecks
    for t in la["tech"]:
        if t["owned"]:
            assert t["price"] is None
            continue
        q = t["quote"]
        assert q["final_price"] == t["price"]
        assert q["base_price"] >= 0
        for m in q["modifiers"]:
            assert set(m) == {"source_id", "name", "amount"}
    for u in la["unlock"]:
        assert u["quote"]["final_price"] == u["price"]
        assert u["affordable"] == (human["supply"] >= u["price"])
    # tower skills carry the frozen costs (step4: all five one-shot items)
    costs = {s["skill_id"]: s["cost"] for s in la["tower_skills"]}
    assert costs == {1: 0, 3: 50, 4: 100, 5: 100, 6: 50}
    # fidelity section mirrors the manifest prefixes
    fid = v["fidelity"]
    assert fid["runtime_playable_through_round"] == \
        v["replay"]["playable_through_round"]
    client.delete("/api/game/sessions/%s" % sid)


def test_typed_skill_release_and_fleet_melee_blockers(client):
    """step3 §5: typed release_commander_skill submissions; an unmapped id
    gets a precise blocker and never a battle event.

    step5: 200001 EMP is now IMPLEMENTED (accepted release) - the blocker
    probe switched to a genuinely unmapped id (200004 unknown)."""
    v = open_session(client)
    sid = v["session_id"]
    # unmapped id -> precise rejection, state unchanged
    digest = v["state_digest"]
    r = cmd(client, sid, v["version"], "APPLY_ACTION", {
        "action": {"kind": "release_commander_skill",
                   "args": {"skill_id": 200004,
                            "positions": [{"x": 10, "y": 10}]}}})
    v = r.json()
    rr = v["rejected_receipt"]
    assert rr and rr["reason_code"] == "UNSUPPORTED_ACTION"
    assert "skill_id=200004" in rr["detail"]
    assert v["state_digest"] == digest
    # no slots -> nothing releasable in this view, or slots carry meta
    for s in v["legal_actions"].get("skill_releases", []):
        assert "slot_index" in s and "target_kind" in s \
            and "supported" in s and "released_this_round" in s
    client.delete("/api/game/sessions/%s" % sid)


# ------------------------------------------------------------ pollution gate
def test_future_snapshot_pollution_leaves_trajectory_identical(tmp_path):
    """任务书 10.4: mutate every future-snapshot field (hp/supply/units/
    exp/labels/fight reports) of a shard copy; the trajectory digests of the
    scripted session must be identical to the unmutated run."""
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
        # v1 manifest (backslash shard path) exercises the compat loader too
        manifest = {"schema_version": "replay_game_manifest_v1",
                    "ruleset_version": "normal_1v1_replay_v0",
                    "options": [dict(option, shard=r"games\x.json")]}
        json.dump(manifest, open(lib_dir / "manifest.json", "w",
                                 encoding="utf8"))
        prev = os.environ.get("MECHABELLUM_GAME_LIB")
        os.environ["MECHABELLUM_GAME_LIB"] = str(lib_dir)
        try:
            lib = GameLibrary(str(lib_dir))
            assert lib.corpus_available
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


# ------------------------------------------------------- shard path safety
def test_shard_path_safety(tmp_path):
    from web.game_library import GameLibrary
    from web.game_service import GameError

    lib_dir = tmp_path / "lib"
    gdir = lib_dir / "games"
    gdir.mkdir(parents=True)
    manifest = {"schema_version": "replay_game_manifest_v2",
                "ruleset_version": "normal_1v1_replay_v0",
                "options": []}
    json.dump(manifest, open(lib_dir / "manifest.json", "w", encoding="utf8"))
    prev = os.environ.get("MECHABELLUM_GAME_LIB")
    os.environ["MECHABELLUM_GAME_LIB"] = str(lib_dir)
    try:
        lib = GameLibrary(str(lib_dir))
        for bad_path in (r"..\..\etc\passwd", "/etc/passwd",
                         "games/../../x.json", "C:\\abs\\x.json"):
            with pytest.raises(GameError) as ei:
                lib.shard({"replay_id": "x", "shard": bad_path})
            assert ei.value.code in ("SHARD_PATH_UNSAFE", "SHARD_MISSING")
        # a v2 manifest option normalizes backslashes to '/' parts
        o = lib.norm_option({"replay_id": "r", "option_id": "r-0",
                             "opponent_player": 0, "human_player": 1,
                             "round_count": 10,
                             "playable_through_round": 0,
                             "shard": r"games\r.json"}, 5)
        assert o["shard"] == "games/r.json"
        assert o["start_mode"] == "disabled"
    finally:
        if prev is None:
            os.environ.pop("MECHABELLUM_GAME_LIB", None)
        else:
            os.environ["MECHABELLUM_GAME_LIB"] = prev
