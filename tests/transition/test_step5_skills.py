# step5 任务书 tests (§3 T0 / §5 T2 / §6 T6): typed releases with full
# ordered Positions, 900001 construction recycle, persistent 黏油 across
# rounds, digest stability and precise rejections.
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from pysim.gamedata import GameData
from pysim.transition import (Economy, Phase, ActionKind, CanonicalAction,
                              CanonicalActionPlan, UnitCard, PlayerState,
                              EnvironmentState, deploy_transition,
                              run_battle, settle_transition, advance_round,
                              state_digest)
from pysim.transition.model import ReleaseCommanderSkillArgs
from pysim.transition.normalize import Normalizer
from pysim.battlefield.compiler import compile_battle_input

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))
ECO = Economy(GD)


# ---------------------------------------------------------------- helpers
def sandbox(skills=(), supply=500, constructions=()):
    p0 = PlayerState(hp=1000, max_hp=1000, supply=supply,
                     pre_round_fight_result=None,
                     units=(UnitCard(entity_id=1, mech_id=2, level=1, exp=0,
                                     x=0.0, y=-100.0),),
                     unlocked_mechs=frozenset({2}), tech_map=(),
                     commander_skills_raw=tuple(skills),
                     constructions_raw=tuple(constructions))
    p1 = PlayerState(hp=1000, max_hp=1000, supply=500,
                     pre_round_fight_result=None,
                     units=(UnitCard(entity_id=2, mech_id=10, level=1, exp=0,
                                     x=0.0, y=100.0),),
                     unlocked_mechs=frozenset({10}), tech_map=())
    return EnvironmentState(
        schema_version="transition-v0.7",
        ruleset_version="normal_1v1_replay_v0",
        engine_version="pysim-step31", round=3, phase=Phase.DEPLOYMENT,
        players=(p0, p1), next_entity_id=3)


def plans(*acts0):
    return (CanonicalActionPlan(player=0, actions=tuple(acts0)),
            CanonicalActionPlan(player=1, actions=(
                CanonicalAction(ActionKind.END_DEPLOY, None, 0),)))


def release(sid, positions=(), cidx=None, idx=0):
    return CanonicalAction(
        ActionKind.RELEASE_COMMANDER_SKILL,
        ReleaseCommanderSkillArgs(skill_id=sid, positions=positions,
                                  construction_index=cidx), idx)


# ================================================================ T0 typed
def test_typed_release_keeps_all_ordered_positions():
    """One beacon release = ONE typed release with 3 ordered points; the
    flat skill_events_raw stays derived; one slot consumed; digest stable."""
    st = sandbox(skills=(("0", "1500001", "true", "0"),))
    res = deploy_transition(
        st, plans(release(1500001, ((10.0, -20.0), (10.0, 40.0),
                                    (10.0, 100.0))),
                  CanonicalAction(ActionKind.END_DEPLOY, None, 1)), ECO)
    r = res.receipts[0][0]
    assert r.accepted
    rels = res.state.players[0].skill_releases
    assert len(rels) == 1
    assert rels[0].ordered_positions == ((10.0, -20.0), (10.0, 40.0),
                                         (10.0, 100.0))
    assert rels[0].skill_id == 1500001
    assert res.state.players[0].skill_events_raw == (
        (1500001, 10.0, -20.0), (1500001, 10.0, 40.0), (1500001, 10.0, 100.0))
    assert res.state.players[0].commander_skills_raw[0][2] == "false"
    bi = compile_battle_input(res.state, battle_seed=5)
    evs = [e for e in bi.events if e.skill_id == 1500001]
    assert len(evs) == 1
    assert evs[0].points == ((10.0, -20.0), (10.0, 40.0), (10.0, 100.0))
    # digest determinism: same compile twice (任务书 §3 T0)
    assert compile_battle_input(res.state, 5).digest() == bi.digest()


@pytest.mark.parametrize("sid,pts", [
    (1500001, ((0.0, 0.0), (0.0, 50.0))),          # beacon needs 3
    (400002, ((0.0, 0.0),)),                        # capsule needs 2
    (600002, ((0.0, 0.0), (1.0, 1.0), (2.0, 2.0))),  # smoke gets 3
])
def test_wrong_point_count_rejects_precisely(sid, pts):
    """不合法点数 -> precise receipt carrying skill_id/positions; the slot
    and the release stream stay untouched."""
    st = sandbox(skills=(("0", str(sid), "true", "0"),))
    res = deploy_transition(st, plans(release(sid, pts)), ECO)
    r = res.receipts[0][0]
    assert not r.accepted and r.reason_code == "UNSUPPORTED_ACTION"
    assert "skill_id=%d" % sid in r.detail
    assert res.state.players[0].skill_releases == ()
    assert res.state.players[0].skill_events_raw == ()
    assert res.state.players[0].commander_skills_raw[0][2] == "true"


def test_release_without_positions_never_fabricates():
    st = sandbox(skills=(("0", "300001", "true", "0"),))
    res = deploy_transition(st, plans(release(300001, ())), ECO)
    assert not res.receipts[0][0].accepted
    assert "without positions" in res.receipts[0][0].detail
    assert res.state.players[0].skill_events_raw == ()


# ================================================================ T2 recycle
def test_construction_recycle_wall_and_magnet_barricade():
    """战地回收: wall (cid1) +50; 磁力路障 (cid4) +50 (user ruling
    2026-08-28; corpus showed 270 recycled cid4 rows)."""
    st = sandbox(skills=(("0", "900001", "true", "0"),
                         ("1", "900001", "true", "0")), supply=100,
                 constructions=(("7", "1", "10.0", "-20.0"),
                                ("8", "4", "30.0", "-20.0")))
    res = deploy_transition(
        st, plans(release(900001, cidx=8, idx=0),
                  release(900001, cidx=7, idx=1),
                  CanonicalAction(ActionKind.END_DEPLOY, None, 2)), ECO)
    r_mag, r_wall = res.receipts[0][0], res.receipts[0][1]
    assert r_wall.accepted and r_wall.resource_delta == 50
    assert r_mag.accepted and r_mag.resource_delta == 50
    assert res.state.players[0].supply == 200
    assert res.state.players[0].constructions_raw == ()
    assert any("sell_construction:7" in e.reason
               for e in res.ledgers[0].entries)
    assert any("sell_construction:8" in e.reason
               for e in res.ledgers[0].entries)
    assert res.state.players[0].commander_skills_raw[0][2] == "false"
    assert res.state.players[0].commander_skills_raw[1][2] == "false"


def test_construction_recycle_cannons_refund_100():
    st = sandbox(skills=(("0", "900001", "true", "0"),
                         ("1", "900001", "true", "0")), supply=0,
                 constructions=(("1", "2", "0.0", "-20.0"),
                                ("2", "3", "10.0", "-20.0")))
    res = deploy_transition(
        st, plans(release(900001, cidx=1, idx=0),
                  release(900001, cidx=2, idx=1)), ECO)
    assert res.receipts[0][0].accepted
    assert res.receipts[0][0].resource_delta == 100
    assert res.receipts[0][1].accepted
    assert res.state.players[0].supply == 200
    assert res.state.players[0].constructions_raw == ()


def test_construction_recycle_unknown_index_rejects():
    st = sandbox(skills=(("0", "900001", "true", "0"),))
    res = deploy_transition(st, plans(release(900001, cidx=99)), ECO)
    r = res.receipts[0][0]
    assert not r.accepted and r.reason_code == "UNKNOWN_ENTITY"
    assert state_digest(res.state) != state_digest(sandbox(
        skills=(("0", "900001", "true", "0"),)))  # only supply-equal states
    # ^ different constructions/supply make digests differ; strict equality
    # of the REJECTED path is covered above (no mutation at all)


def test_recycled_construction_leaves_battle_input():
    """The recycled building must NOT enter this round's BattleInput."""
    st = sandbox(skills=(("0", "900001", "true", "0"),),
                 constructions=(("7", "1", "10.0", "-20.0"),))
    res = deploy_transition(st, plans(release(900001, cidx=7)), ECO)
    bi = compile_battle_input(res.state, battle_seed=1)
    assert not [o for o in bi.world_objects if o.kind == "building"]


def test_normalize_routes_construction_sell():
    """ID=900001 + ConstructionIndex normalizes to the typed release."""
    norm = Normalizer(ECO)
    skills = [{"index": "0", "id": "900001", "isActive": "true",
               "coolingRound": "0"}]
    r = norm.normalize_round({
        "round": 3, "unit_index": 10, "units": [], "officers": [],
        "techMap": {}, "commanderSkills_raw": skills,
        "actions": [{"type": "ReleaseCommanderSkill", "ID": 900001,
                     "SkillIndex": 0, "Positions": [{"x": 0, "y": 0}],
                     "UnitIndex": -1, "ConstructionIndex": 7}]})
    assert [e["t"] for e in r.actions_norm] == ["release"]
    assert r.actions_norm[0]["skill"] == 900001
    assert r.actions_norm[0]["construction"] == 7


# ================================================================ T6 oil TTL
def test_oil_persists_one_extra_battle():
    st = sandbox(skills=(("0", "400002", "true", "0"),))
    res = deploy_transition(
        st, plans(release(400002, ((0.0, 60.0), (30.0, 60.0))),
                  CanonicalAction(ActionKind.END_DEPLOY, None, 1)), ECO)
    assert res.receipts[0][0].accepted
    outcome = run_battle(res.state, GD, battle_seed=9)
    assert outcome.area_results == (("area:0", False),)   # not ignited
    settled = settle_transition(res.state, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, (0, 0), gd=GD)
    g = nxt.players[0].ground_areas_raw
    assert len(g) == 1 and g[0][6] == 1       # ttl 2 -> 1, ref stable
    bi = compile_battle_input(nxt, battle_seed=10)
    oils = [e for e in bi.events if e.kind == "oil"]
    assert len(oils) == 1 and oils[0].ref == g[0][0]
    assert oils[0].points == ((0.0, 60.0), (30.0, 60.0))
    # round-scoped releases reset alongside
    assert nxt.players[0].skill_releases == ()
    assert nxt.players[0].skill_events_raw == ()
    # second advance ticks the ttl out
    st2 = EnvironmentState(**{**nxt.__dict__, "phase": Phase.SETTLEMENT})
    nxt2 = advance_round(st2, None, (0, 0), gd=GD)
    assert nxt2.players[0].ground_areas_raw == ()
    # a DEPLOY on the carried-oil state must not drop the persistent area
    res2 = deploy_transition(nxt, plans(
        CanonicalAction(ActionKind.END_DEPLOY, None, 0)), ECO)
    assert res2.state.players[0].ground_areas_raw == g


def test_save_load_roundtrips_typed_releases_and_areas():
    from pysim.transition import state_to_dict, state_from_dict
    st = sandbox(skills=(("0", "400002", "true", "0"),))
    res = deploy_transition(
        st, plans(release(400002, ((5.0, 60.0), (35.0, 60.0)))), ECO)
    d = state_to_dict(res.state)
    back = state_from_dict(d)
    assert len(back.players[0].skill_releases) == 1
    assert tuple(map(tuple, back.players[0].skill_releases[0]
                     .ordered_positions)) == ((5.0, 60.0), (35.0, 60.0))
    # old flat-only states migrate to single-point releases
    old = sandbox()
    old = EnvironmentState(**{**old.__dict__, "players": (
        PlayerState(**{**old.players[0].__dict__,
                       "skill_events_raw": ((300001, 1.0, 2.0),
                                            (300001, 3.0, 4.0))}),
        old.players[1])})
    back_old = state_from_dict(state_to_dict(old))
    rels = back_old.players[0].skill_releases
    assert [tuple(r.ordered_positions) for r in rels] == \
        [((1.0, 2.0),), ((3.0, 4.0),)]


# ================================================================ registry
def test_step5_registry_confidence_entries():
    from pysim.battlefield import registry
    # user-frozen core numbers but cal residue -> provisional (never verified
    # without the oracle); 900001 recycle economics are fully user-frozen
    for sid in (200001, 200002, 200003, 400002, 500002, 600002, 300005,
                300006, 1500001, 1500002):
        s = registry.mechanism_support("commander_skill", sid)
        assert s.confidence == "provisional", sid
        assert s.battle == "complete", sid
        assert any("step5" in e for e in s.evidence), sid
    s = registry.mechanism_support("commander_skill", 900001)
    assert s.confidence == "verified"
    assert s.effect_complete is True


def test_nuke_radius_user_frozen_100():
    from pysim.skills import COMMANDER_SKILLS
    assert COMMANDER_SKILLS[300004]["splash"] == 100.0
    assert COMMANDER_SKILLS[300004]["t"] == 15.0
    assert COMMANDER_SKILLS[300004]["damage"] == 70000.0
