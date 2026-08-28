# Normalizer unit tests (transition v0.1 T2): undo pairs, chains,
# multi-move splitting, grant expansion, counter reclaim/burn, gifts,
# cancel semantics, determinism.
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from pysim.gamedata import GameData
from pysim.transition.economy import Economy
from pysim.transition.normalize import Normalizer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
eco = Economy(gd)
norm = Normalizer(eco)


def rec(**kw):
    base = {"round": 2, "unit_index": 10, "units": [
        {"index": 0, "id": 10, "sellSupply": 100},
        {"index": 1, "id": 25, "sellSupply": 200}],
        "commanderSkills_raw": [], "officers": [], "actions": [],
        "techMap": {}}
    base.update(kw)
    return base


def kinds(stream):
    return [e["t"] for e in stream]


def test_buy_undo_pair_reclaims_index():
    r = norm.normalize_round(rec(actions=[
        {"type": "BuyUnit", "UID": 10, "position": {"x": 0, "y": -160}},
        {"type": "Undo"},
        {"type": "BuyUnit", "UID": 10, "position": {"x": 5, "y": -160}},
    ]))
    assert kinds(r.actions_norm) == ["buy"]
    assert r.actions_norm[0]["game_index"] == 10     # reclaimed slot reused
    assert r.report["counter_end"] == 11
    assert r.report["folded"] == [{"raw_index": 0, "undone_by": 1,
                                   "kind": "buy"}]


def test_chain_undo_all():
    r = norm.normalize_round(rec(actions=[
        {"type": "BuyUnit", "UID": 10, "position": {"x": 0, "y": 0}},
        {"type": "BuyUnit", "UID": 10, "position": {"x": 5, "y": 0}},
        {"type": "Undo"}, {"type": "Undo"}, {"type": "Undo"},
    ]))
    assert r.actions_norm == []
    assert r.report["counter_end"] == 10
    assert r.report["undo_on_empty"] == 1   # third undo ignored (Q3)


def test_multi_move_split_and_atomic_revert():
    r = norm.normalize_round(rec(actions=[
        {"type": "MoveUnit", "moveUnitDatas": [
            {"unitID": 10, "unitIndex": 0, "position": {"x": 1, "y": 2}},
            {"unitID": 25, "unitIndex": 1, "position": {"x": 3, "y": 4}}]},
        {"type": "Undo"},
        {"type": "MoveUnit", "moveUnitDatas": [
            {"unitID": 10, "unitIndex": 0, "position": {"x": 9, "y": 9}}]},
    ]))
    moves = [e for e in r.actions_norm if e["t"] == "move"]
    assert len(moves) == 1                  # whole first record reverted
    assert moves[0]["x"] == 9


def test_grant_expansion_and_counter():
    item = next(iid for iid in eco.items
                if (eco.item_grant(iid) or [0, 0, 0])[1] == 2)
    r = norm.normalize_round(rec(actions=[
        {"type": "ChooseReinforceItem", "ID": item, "Index": 0},
    ]))
    re = r.actions_norm[0]
    assert [g["game_index"] for g in re["grants"]] == [10, 11]
    assert r.report["counter_end"] == 12


def test_sell_burns_no_reclaim():
    r = norm.normalize_round(rec(actions=[
        {"type": "BuyUnit", "UID": 10, "position": {"x": 0, "y": 0}},
        {"type": "ReleaseCommanderSkill", "ID": 900001, "SkillIndex": 0,
         "UnitIndex": 10},
    ]))
    assert kinds(r.actions_norm) == ["buy", "sell"]
    assert r.report["counter_end"] == 11    # sold index burned above


def test_sell_undo_revives_unit():
    r = norm.normalize_round(rec(actions=[
        {"type": "ReleaseCommanderSkill", "ID": 900001, "SkillIndex": 0,
         "UnitIndex": 1},
        {"type": "Undo"},
    ]))
    assert r.actions_norm == []
    assert r.report["counter_end"] == 10


def test_unresolved_ref_after_sell_is_reported():
    r = norm.normalize_round(rec(actions=[
        {"type": "ReleaseCommanderSkill", "ID": 900001, "SkillIndex": 0,
         "UnitIndex": 0},
        {"type": "MoveUnit", "moveUnitDatas": [
            {"unitID": 10, "unitIndex": 0, "position": {"x": 1, "y": 1}}]},
    ]))
    assert r.report["unresolved_refs"][0]["reason"] == "unknown_index"


def test_cancel_removes_release_and_undo_restores():
    skills = [{"index": "2", "id": "300001", "isActive": "true",
               "coolingRound": "0"}]
    r = norm.normalize_round(rec(
        commanderSkills_raw=skills,
        actions=[
            {"type": "ReleaseCommanderSkill", "ID": 300001, "SkillIndex": 2,
             "Positions": [{"x": 1, "y": 1}], "UnitIndex": -1},
            {"type": "CancelReleaseCommanderSkill", "ID": 300001,
             "SkillIndex": 2},
        ]))
    assert r.actions_norm == []
    assert r.report["n_cancel_folded"] == 1
    # undoing the cancel restores the release (Q9); a MAPPED skill (300001)
    # comes back as the typed `release` entry (step3 任务书 §5.2)
    r2 = norm.normalize_round(rec(
        commanderSkills_raw=skills,
        actions=[
            {"type": "ReleaseCommanderSkill", "ID": 300001, "SkillIndex": 2,
             "Positions": [{"x": 1, "y": 1}], "UnitIndex": -1},
            {"type": "CancelReleaseCommanderSkill", "ID": 300001,
             "SkillIndex": 2},
            {"type": "Undo"},
        ]))
    assert kinds(r2.actions_norm) == ["release"]
    assert r2.actions_norm[0]["skill"] == 300001
    assert r2.actions_norm[0]["positions"] == [[1.0, 1.0]]
    # an UNMAPPED skill still degrades to passthrough (precise blocker);
    # step5: 200001 EMP is implemented now, so probe with 200004 (unknown)
    skills_emp = [{"index": "2", "id": "200004", "isActive": "true",
                   "coolingRound": "0"}]
    r3 = norm.normalize_round(rec(
        commanderSkills_raw=skills_emp,
        actions=[
            {"type": "ReleaseCommanderSkill", "ID": 200004, "SkillIndex": 2,
             "Positions": [{"x": 1, "y": 1}], "UnitIndex": -1},
        ]))
    assert kinds(r3.actions_norm) == ["passthrough"]


def test_opening_team_gift_round_spawn():
    r = norm.normalize_round(rec(round=2, officers=[20029], actions=[
        {"type": "FinishDeploy"}]))
    assert r.actions_norm[0]["t"] == "gift"
    assert r.actions_norm[0]["mech"] == 2
    assert r.actions_norm[0]["game_index"] == 10


def test_determinism_byte_identical():
    actions = [
        {"type": "BuyUnit", "UID": 10, "position": {"x": 0, "y": 0}},
        {"type": "Undo"},
        {"type": "MoveUnit", "moveUnitDatas": [
            {"unitID": 10, "unitIndex": 0, "position": {"x": 1, "y": 1}}]},
    ]
    a = norm.normalize_round(rec(actions=copy.deepcopy(actions)))
    b = norm.normalize_round(rec(actions=copy.deepcopy(actions)))
    assert json.dumps(a.actions_norm, sort_keys=False) == \
        json.dumps(b.actions_norm, sort_keys=False)
    assert a.report == b.report


def test_undo_in_norm_stream_rejected_by_canonicalize():
    from pysim.transition import canonicalize_plan, TransitionError
    with pytest.raises(TransitionError):
        canonicalize_plan(0, [{"t": "passthrough", "raw_type": "Undo",
                               "raw": [0], "raw_rec": {}}])


def test_golden_fixture_roundtrip():
    """Golden fixture: one normalized (player, round) from the sample
    corpus, human-audited via raw[] back-references."""
    fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "golden_norm_round.json")
    samples = os.path.join(ROOT, "data", "samples", "rounds.json")
    if not os.path.exists(samples):
        pytest.skip("sample corpus missing")
    d = json.load(open(samples, encoding="utf8"))
    g = d[0]
    # pick a round with an undo fold, a buy and a move
    for pr in g["players"]:
        for r in pr["rounds"]:
            res = Normalizer(eco).normalize_round(r)
            has_fold = res.report["n_undo_folded"] > 0
            has_buy = any(e["t"] == "buy" for e in res.actions_norm)
            if has_fold and has_buy:
                golden = {"game": g["file"], "round": r["round"],
                          "actions_norm": res.actions_norm,
                          "report": res.report}
                if os.path.exists(fixture):
                    want = json.load(open(fixture, encoding="utf8"))
                    assert want["actions_norm"] == res.actions_norm
                    assert want["report"]["counter_end"] == \
                        res.report["counter_end"]
                else:
                    os.makedirs(os.path.dirname(fixture), exist_ok=True)
                    json.dump(golden, open(fixture, "w", encoding="utf8"),
                              ensure_ascii=False, indent=1)
                return
    pytest.fail("no round with undo+buy found in sample corpus")
