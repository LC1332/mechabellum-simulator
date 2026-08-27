# step4 任务书 tests: buy-limit quote (T1), cross-round movement permission
# (T2), 再部署 1000001 (T3), typed 能量塔技能 + single purchase (T4), P1
# commander skills / engine strikes & summons (T5), schema migration (§8).
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from pysim.gamedata import GameData
from pysim.transition import (Economy, Income200r, Phase, ActionKind,
                              CanonicalAction, CanonicalActionPlan, EntityRef,
                              UnitCard, PlayerState, EnvironmentState,
                              deploy_transition, run_battle, settle_transition,
                              advance_round, state_digest, state_to_dict,
                              state_from_dict, copy_state, canonicalize_plan,
                              capability, buy_limit_quote, movement_permission,
                              BASE_BUY_LIMIT, MOBILITY_TECHS,
                              DEPLOYMENT_MODULE_EQUIPMENT, REDEPLOY_SKILL_ID)
from pysim.transition.model import (BuyArgs, TechArgs, MoveArgs,
                                    ChooseReinforceArgs,
                                    ReleaseCommanderSkillArgs,
                                    UseEquipmentArgs, UnsupportedArgs,
                                    ActivateEnergyTowerSkillArgs)
from pysim.transition.normalize import Normalizer
from pysim.skills import (COMMANDER_SKILLS, TRANSITION_SKILLS,
                          expand_strike_events)
from pysim.battlefield import registry as bf_registry

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))
ECO = Economy(GD)


# ---------------------------------------------------------------- helpers
def sandbox(units0=((10, -60.0),), units1=((10, 60.0),), supply0=2000,
            supply1=2000, officers0=(), equip0=(), skills0=(),
            tech0=(), spawned0=(), round_no=2):
    """Round-`round_no` DEPLOYMENT state (round 2 by default so snapshot
    units are NOT auto-movable — the step4 default rule)."""
    def mk(mech, x, side, k):
        return UnitCard(entity_id=side * 100 + k, mech_id=mech, level=1,
                        exp=0, x=x, y=-150.0 if side == 0 else 150.0,
                        sell_supply=ECO.buy_price(mech) or 0,
                        replay_index=side * 100 + k)
    p0 = PlayerState(hp=4500, max_hp=4500, supply=supply0,
                     pre_round_fight_result=None,
                     units=tuple(mk(m, x, 0, k) for k, (m, x)
                                 in enumerate(units0)),
                     unlocked_mechs=frozenset({m for m, _ in units0} | {2, 7}),
                     tech_map=tuple(tech0), officers=tuple(officers0),
                     commander_skills_raw=tuple(skills0),
                     equipment_inventory=tuple(equip0),
                     spawned_this_round=tuple(spawned0))
    p1 = PlayerState(hp=4500, max_hp=4500, supply=supply1,
                     pre_round_fight_result=None,
                     units=tuple(mk(m, x, 1, k) for k, (m, x)
                                 in enumerate(units1)),
                     unlocked_mechs=frozenset({m for m, _ in units1}),
                     tech_map=(), officers=())
    return EnvironmentState(schema_version="t", ruleset_version="sandbox",
                            engine_version="e", round=round_no,
                            phase=Phase.DEPLOYMENT, players=(p0, p1),
                            next_entity_id=500)


def apply0(state, action):
    return deploy_transition(state, (CanonicalActionPlan(
        player=0, actions=(action,)),), ECO)


def raw_action(raw_type, raw):
    return CanonicalAction(ActionKind.RAW_UNSUPPORTED,
                           UnsupportedArgs(raw_type=raw_type,
                                           raw=tuple(sorted(raw))))


def buy(k=0, mech=2, y=-100.0):
    return CanonicalAction(ActionKind.BUY_UNIT, BuyArgs(
        mech_id=mech, x=0.0, y=y - k, new_ref=k + 1))


def move(handle, x=10.0, y=-120.0, rot=None):
    return CanonicalAction(ActionKind.MOVE_UNIT, MoveArgs(
        ref=EntityRef(handle=handle), x=x, y=y, is_rotate=rot))


# ================================================================ T1 buy limit
def test_buy_limit_quote_table():
    """任务书 §3 gate: base + 批量征召(能量塔技能3) + 额外部署位 strict
    addition. User ruling 2026-08-27: base = 2; each 额外部署位 copy +1."""
    st = sandbox(officers0=())
    assert buy_limit_quote(st.players[0]).limit == BASE_BUY_LIMIT
    assert BASE_BUY_LIMIT == 2
    # 能量塔技能3 批量征召: +1 this round (blueprint 2 research adds NONE)
    res = apply0(st, tskill(3))
    assert res.receipts[0][0].accepted
    q = buy_limit_quote(res.state.players[0])
    assert q.limit == BASE_BUY_LIMIT + 1 and q.blueprint_bonus == 1
    res_bp = apply0(st, raw_action("ActiveBlueprint", [("ID", 2)]))
    qb = buy_limit_quote(res_bp.state.players[0])
    assert qb.limit == BASE_BUY_LIMIT and qb.blueprint_bonus == 0
    # one 10004 officer: +1
    st_o = sandbox(officers0=(10004,))
    assert buy_limit_quote(st_o.players[0]).officer_bonus == 1
    # two 10004: +2 (每份额外部署位 +1, 可重复)
    st_o2 = sandbox(officers0=(10004, 10004))
    assert buy_limit_quote(st_o2.players[0]).limit == BASE_BUY_LIMIT + 2
    # tower3 + one 10004 = +2 together
    res2 = apply0(st_o, tskill(3))
    assert buy_limit_quote(res2.state.players[0]).limit == BASE_BUY_LIMIT + 2


def test_reinforce_grants_do_not_consume_buy_quota():
    """user ruling: 增援单位不计入购买额度 — a grant-then-buy round keeps
    the full base quota (and grants are still movable via NEW_THIS_ROUND)."""
    st = sandbox(supply0=2000)
    # 102219 grants 2x mech 9 without touching bought_this_round
    res = apply0(st, CanonicalAction(
        ActionKind.CHOOSE_REINFORCE,
        ChooseReinforceArgs(item_id=102219)))
    r = res.receipts[0][0]
    assert r.accepted, r.detail
    granted = res.state.players[0].units[-2:]
    q0 = buy_limit_quote(res.state.players[0])
    assert q0.used == 0 and q0.remaining == BASE_BUY_LIMIT
    for u in granted:
        assert movement_permission(res.state.players[0], u).allowed
    # full base quota of paid buys still available afterwards
    s = res.state
    for k in range(BASE_BUY_LIMIT):
        rr = apply0(s, buy(k))
        assert rr.receipts[0][0].accepted, rr.receipts[0][0].detail
        s = rr.state


def test_buy_limit_enforced_with_reject_and_undo():
    st = sandbox()
    s = st
    for k in range(BASE_BUY_LIMIT):
        rr = apply0(s, buy(k))
        assert rr.receipts[0][0].accepted, rr.receipts[0][0].detail
        s = rr.state
    over = apply0(s, buy(BASE_BUY_LIMIT))
    r = over.receipts[0][0]
    assert not r.accepted and r.reason_code == "BUY_LIMIT_REACHED"
    assert "base %d" % BASE_BUY_LIMIT in r.detail
    # rejected buy: digest unchanged, bought_this_round unchanged
    assert state_digest(over.state) == state_digest(s)
    assert over.state.players[0].bought_this_round == BASE_BUY_LIMIT
    assert len(over.state.players[0].units) == len(s.players[0].units)
    # tower3 批量征召 mid-round lifts the limit for the very next buy
    res = apply0(s, tskill(3))
    rr = apply0(res.state, buy(BASE_BUY_LIMIT))
    assert rr.receipts[0][0].accepted


def test_buy_limit_resets_on_advance_round():
    st = sandbox()
    s = st
    for k in range(BASE_BUY_LIMIT):
        s = apply0(s, buy(k)).state
    assert s.players[0].bought_this_round == BASE_BUY_LIMIT
    st2 = EnvironmentState(**{**s.__dict__, "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome = run_battle(st2, GD, battle_seed=7)
    settled = settle_transition(st2, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    assert nxt.players[0].bought_this_round == 0
    q = buy_limit_quote(nxt.players[0])
    assert q.blueprint_bonus == 0 and q.limit == BASE_BUY_LIMIT


# ===================================================== T2 movement permission
def test_old_unit_locked_round2_new_sources_movable():
    st = sandbox(units0=((10, -60.0), (21, -30.0)), round_no=2)
    old = st.players[0].units[0]
    # snapshot-carried unit with no rights: rejected, nothing changes
    res = apply0(st, move(old.replay_index, 40.0, -40.0, rot=True))
    r = res.receipts[0][0]
    assert not r.accepted and r.reason_code == "UNIT_NOT_MOVABLE_THIS_ROUND"
    assert state_digest(res.state) == state_digest(st)
    u2 = next(u for u in res.state.players[0].units
              if u.entity_id == old.entity_id)
    assert (u2.x, u2.y, u2.is_rotate) == (old.x, old.y, old.is_rotate)
    # a unit spawned this round moves freely (and repeatedly)
    res2 = apply0(st, buy(0))
    bought = res2.state.players[0].units[-1]
    m1 = apply0(res2.state, move(bought.replay_index, 30.0, -80.0))
    assert m1.receipts[0][0].accepted
    m2 = apply0(m1.state, move(bought.replay_index, 50.0, -60.0))
    assert m2.receipts[0][0].accepted
    assert "NEW_THIS_ROUND" in m2.receipts[0][0].detail


def test_movement_module_and_tech_unlock():
    # 部署模块 bound on the unit card
    st = sandbox(units0=((10, -60.0),), round_no=2)
    u = st.players[0].units[0]
    st_mod = EnvironmentState(**{**st.__dict__, "players": (
        PlayerState(**{**st.players[0].__dict__, "units": (
            UnitCard(**{**u.__dict__,
                        "equipment_id": DEPLOYMENT_MODULE_EQUIPMENT}),)}),
        st.players[1])})
    res = apply0(st_mod, move(u.replay_index))
    assert res.receipts[0][0].accepted
    assert "DEPLOYMENT_MODULE" in res.receipts[0][0].detail
    # 高速引擎 tech: 1606 -> mech 6 (兵蜂)
    st_tech = sandbox(units0=((6, -60.0),), round_no=2,
                      tech0=((6, (1606,)),))
    res2 = apply0(st_tech, move(st_tech.players[0].units[0].replay_index))
    assert res2.receipts[0][0].accepted
    assert "MOBILITY_TECH" in res2.receipts[0][0].detail
    # other mech's tech does not unlock this unit
    st_other = sandbox(units0=((10, -60.0),), round_no=2,
                       tech0=((6, (1606,)),))
    res3 = apply0(st_other, move(st_other.players[0].units[0].replay_index))
    assert not res3.receipts[0][0].accepted
    # buying the tech mid-round unlocks the very next move (corpus pattern)
    st_buy_tech = sandbox(units0=((6, -60.0),), round_no=2)
    r_t = apply0(st_buy_tech, CanonicalAction(
        ActionKind.BUY_TECH, TechArgs(mech_id=6, tech_id=1606)))
    assert r_t.receipts[0][0].accepted, r_t.receipts[0][0].detail
    r_m = apply0(r_t.state, move(st_buy_tech.players[0].units[0].replay_index))
    assert r_m.receipts[0][0].accepted


def test_round1_opening_units_all_movable():
    """QA#1: 开局单位全部可以移动 — the opening builder seeds spawned ids."""
    from pysim.transition import opening as opening_mod
    catalog = opening_mod.load_catalog(os.path.join(
        ROOT, "data", "game", "opening_catalog.json"))
    pkg = next(iter(catalog["packages"].values()))
    p, _next = opening_mod.player_state_from_package(pkg, eco=ECO, gd=GD)
    assert p.spawned_this_round == tuple(
        sorted(u.entity_id for u in p.units))
    for u in p.units:
        assert movement_permission(p, u).allowed
        assert movement_permission(p, u).reasons == ("NEW_THIS_ROUND",)


def test_spawned_and_redeployed_reset_on_advance():
    st = sandbox(units0=((10, -60.0),), round_no=2,
                 spawned0=(100,))
    st2 = EnvironmentState(**{**st.__dict__, "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome = run_battle(st2, GD, battle_seed=5)
    settled = settle_transition(st2, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    assert nxt.players[0].spawned_this_round == ()
    assert nxt.players[0].redeployed_this_round == ()


def test_move_permission_save_load_migration():
    """§8: old states without redeployed_this_round adapt to () (never
    'everything movable')."""
    st = sandbox(units0=((10, -60.0),), round_no=2)
    d = state_to_dict(st)
    d["players"][0].pop("redeployed_this_round")
    d["players"][0].pop("spawned_this_round")
    st_old = state_from_dict(d)
    assert st_old.players[0].redeployed_this_round == ()
    assert st_old.players[0].spawned_this_round == ()
    assert not movement_permission(st_old.players[0],
                                   st_old.players[0].units[0]).allowed


def test_legal_mask_agrees_with_apply():
    """env.legal_action_candidates never proposes a move that deploy rejects
    (scanner/runtime agreement on the movement rule)."""
    from pysim.transition import TransitionEnv
    st = sandbox(units0=((10, -60.0), (21, -30.0)), round_no=2)
    env = TransitionEnv(GD, ECO)
    env.reset(st)
    cands = env.legal_action_candidates(0)
    move_cands = [a for a in cands if a.kind is ActionKind.MOVE_UNIT]
    # all units are locked snapshot units -> zero move candidates
    assert not move_cands
    # buy one unit -> its move candidates appear
    rr = env.apply_player_action(0, buy(0))
    assert rr.accepted
    cands2 = env.legal_action_candidates(0)
    new_unit = env.state.players[0].units[-1]
    handles = {a.args.ref.handle for a in cands2
               if a.kind is ActionKind.MOVE_UNIT}
    assert handles == {new_unit.replay_index}


# ================================================================ T3 redeploy
def _redeploy(handle, skill_index=1):
    return CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                           ReleaseCommanderSkillArgs(
                               skill_id=REDEPLOY_SKILL_ID,
                               skill_index=skill_index,
                               unit_ref=EntityRef(handle=handle)))


def test_redeploy_unlocks_locked_unit_consumes_slot():
    st = sandbox(units0=((10, -60.0),), round_no=2,
                 skills0=(("0", "900001", "true", "0"),
                          ("1", str(REDEPLOY_SKILL_ID), "true", "0")))
    u = st.players[0].units[0]
    before = state_digest(st)
    res = apply0(st, _redeploy(u.replay_index))
    r = res.receipts[0][0]
    assert r.accepted, r.detail
    assert res.state.players[0].redeployed_this_round == (u.entity_id,)
    assert res.state.players[0].skill_events_raw == ()   # no battle event
    # slot consumed (inactive, cd=1) and now the unit can move
    slot = [e for e in res.state.players[0].commander_skills_raw
            if e[0] == "1"][0]
    assert slot[2] == "false" and int(slot[3]) == 1
    m = apply0(res.state, move(u.replay_index))
    assert m.receipts[0][0].accepted
    assert "REDEPLOY_SKILL" in m.receipts[0][0].detail
    assert state_digest(res.state) != before


def test_redeploy_rejections_are_atomic():
    st = sandbox(units0=((10, -60.0),), round_no=2,
                 skills0=(("1", str(REDEPLOY_SKILL_ID), "true", "0"),))
    u = st.players[0].units[0]
    before = state_digest(st)
    # missing unit target
    r1 = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                    ReleaseCommanderSkillArgs(
                                        skill_id=REDEPLOY_SKILL_ID)))
    assert not r1.receipts[0][0].accepted
    assert r1.receipts[0][0].reason_code == "SKILL_TARGET_INVALID"
    # unknown unit
    r2 = apply0(st, _redeploy(9999))
    assert not r2.receipts[0][0].accepted
    assert r2.receipts[0][0].reason_code == "UNKNOWN_ENTITY"
    # slot not active -> unavailable, nothing consumed
    st_cd = sandbox(units0=((10, -60.0),), round_no=2,
                    skills0=(("1", str(REDEPLOY_SKILL_ID), "false", "1"),))
    before_cd = state_digest(st_cd)
    r3 = apply0(st_cd, _redeploy(u.replay_index))
    assert not r3.receipts[0][0].accepted
    assert r3.receipts[0][0].reason_code == "SKILL_SLOT_UNAVAILABLE"
    assert state_digest(r1.state) == before
    assert state_digest(r2.state) == before
    assert state_digest(r3.state) == before_cd


def test_redeploy_on_movable_unit_rejected_no_consumption():
    u0_id = 0                     # sandbox entity ids are side*100+k
    st = sandbox(units0=((10, -60.0),), round_no=2,
                 skills0=(("1", str(REDEPLOY_SKILL_ID), "true", "0"),),
                 spawned0=(u0_id,))
    u = st.players[0].units[0]
    before = state_digest(st)
    res = apply0(st, _redeploy(u.replay_index))
    r = res.receipts[0][0]
    assert not r.accepted and r.reason_code == "UNIT_ALREADY_MOVABLE"
    assert state_digest(res.state) == before
    slot = [e for e in res.state.players[0].commander_skills_raw
            if e[0] == "1"][0]
    assert slot[2] == "true"          # slot NOT consumed


def test_redeploy_per_slot_once_next_round_rearms():
    """QA#3: 每个再部署槽一回合一次；使用后进入冷却，下一回合恢复."""
    st = sandbox(units0=((10, -60.0), (21, -40.0)), round_no=2,
                 skills0=(("1", str(REDEPLOY_SKILL_ID), "true", "0"),
                          ("2", str(REDEPLOY_SKILL_ID), "true", "0")))
    u1, u2 = st.players[0].units
    res = apply0(st, _redeploy(u1.replay_index, skill_index=1))
    assert res.receipts[0][0].accepted
    # second release through the SAME slot: unavailable
    res2 = apply0(res.state, _redeploy(u2.replay_index, skill_index=1))
    assert not res2.receipts[0][0].accepted
    assert res2.receipts[0][0].reason_code == "SKILL_SLOT_UNAVAILABLE"
    # the OTHER slot still works (two slots = two unlocks per round)
    res3 = apply0(res.state, _redeploy(u2.replay_index, skill_index=2))
    assert res3.receipts[0][0].accepted
    assert set(res3.state.players[0].redeployed_this_round) == \
        {u1.entity_id, u2.entity_id}
    # next round: the consumed slot re-arms (cd=1 -> tick)
    st2 = EnvironmentState(**{**res3.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome = run_battle(st2, GD, battle_seed=9)
    settled = settle_transition(st2, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    slot1 = [e for e in nxt.players[0].commander_skills_raw
             if e[0] == "1"][0]
    assert slot1[2] == "true" and slot1[3] == "0"


def test_redeploy_norm_entries_and_scanner_agree():
    """normalizer resolves in-round 再部署 card picks + releases; scanner
    accepts the typed entry."""
    rec = {
        "round": 5, "unit_index": 20,
        "units": [{"index": 5, "id": 10, "x": 0.0, "y": -10.0,
                   "sellSupply": 100}],
        "officers": [],
        "commanderSkills_raw": [{"index": "0", "id": "900001",
                                 "isActive": "true", "coolingRound": "0"}],
        "actions": [
            {"type": "ChooseReinforceItem", "ID": 1000001, "Index": 3},
            {"type": "ReleaseCommanderSkill", "ID": 0, "SkillIndex": 1,
             "UnitIndex": 5, "ConstructionIndex": -1,
             "Positions": [{"x": -1.0, "y": -2.0}]},
            {"type": "MoveUnit", "moveUnitDatas": [
                {"unitIndex": 5, "unitID": 10,
                 "position": {"x": 30.0, "y": -30.0}, "isRotate": False}]},
            {"type": "FinishDeploy"},
        ],
    }
    res = Normalizer(ECO).normalize_round(rec)
    kinds = [e["t"] for e in res.actions_norm]
    assert kinds == ["reinforce", "release", "move", "finish"]
    rel = res.actions_norm[1]
    assert rel["skill"] == REDEPLOY_SKILL_ID and rel["unit"] == 5 \
        and rel["construction"] is None
    assert capability.classify_norm_entry(rel, rec, ECO, GD) is None
    # and the full round runs green through deploy (snapshot carries slot 0
    # = 900001 so the picked card stocks slot 1; the release resolves there)
    plan, crep = canonicalize_plan(0, res.actions_norm,
                                   norm_report=res.report)
    st = sandbox(units0=((10, -60.0),), round_no=5,
                 skills0=(("0", "900001", "true", "0"),))
    # give the snapshot unit the replay index the stream addresses (5)
    su = st.players[0].units[0]
    st = EnvironmentState(**{**st.__dict__, "players": (
        PlayerState(**{**st.players[0].__dict__, "units": (
            UnitCard(**{**su.__dict__, "replay_index": 5}),)}),
        st.players[1])})
    out = deploy_transition(st, (plan,), ECO)
    receipts = out.receipts[0]
    assert all(r.accepted for r in receipts), \
        [(r.kind, r.reason_code, r.detail) for r in receipts]


# ================================================================ T4 tower skills
def tskill(sid):
    return CanonicalAction(ActionKind.ACTIVATE_ENERGY_TOWER_SKILL,
                           ActivateEnergyTowerSkillArgs(skill_id=sid))


def test_tower_skill_typed_charge_and_single_purchase():
    st = sandbox(supply0=200)
    res = apply0(st, tskill(5))
    r = res.receipts[0][0]
    assert r.accepted and r.resource_delta == -100
    assert res.state.players[0].tower_mods_raw == (5,)
    assert any(e.reason == "tower_skill:5" and e.amount == -100
               for e in res.ledgers[0].entries)
    # QA#4: second purchase the SAME round -> stable rejection, no charge
    res2 = apply0(res.state, tskill(5))
    r2 = res2.receipts[0][0]
    assert not r2.accepted and r2.reason_code == "TOWER_SKILL_ALREADY_ACTIVE"
    assert state_digest(res2.state) == state_digest(res.state)
    # the other skills are still purchasable
    res3 = apply0(res.state, tskill(6))
    assert res3.receipts[0][0].accepted
    assert res3.state.players[0].tower_mods_raw == (5, 6)
    # unknown ids stay precise blockers (id 2 never observed in the corpus)
    res4 = apply0(st, tskill(2))
    assert not res4.receipts[0][0].accepted
    assert res4.receipts[0][0].reason_code == "UNSUPPORTED_ACTION"
    # funds: rejected for lack of supply, state unchanged
    res5 = apply0(sandbox(supply0=49), tskill(6))
    assert not res5.receipts[0][0].accepted
    assert res5.receipts[0][0].reason_code == "INSUFFICIENT_SUPPLY"


def test_tower3_mass_recruit_lifts_quota_once_per_round():
    st = sandbox(supply0=500)
    s = st
    for k in range(BASE_BUY_LIMIT):
        s = apply0(s, buy(k)).state
    over = apply0(s, buy(BASE_BUY_LIMIT))
    assert over.receipts[0][0].reason_code == "BUY_LIMIT_REACHED"
    res = apply0(s, tskill(3))
    assert res.receipts[0][0].accepted
    assert res.receipts[0][0].resource_delta == -50
    q = buy_limit_quote(res.state.players[0])
    assert q.limit == BASE_BUY_LIMIT + 1 and q.blueprint_bonus == 1
    rr = apply0(res.state, buy(BASE_BUY_LIMIT))
    assert rr.receipts[0][0].accepted
    # single purchase per round: a second 批量征召 is rejected
    again = apply0(rr.state, tskill(3))
    assert not again.receipts[0][0].accepted
    assert again.receipts[0][0].reason_code == "TOWER_SKILL_ALREADY_ACTIVE"
    # the quota resets next round (sentinel cleared by advance_round)
    st2 = EnvironmentState(**{**rr.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome = run_battle(st2, GD, battle_seed=8)
    settled = settle_transition(st2, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    assert buy_limit_quote(nxt.players[0]).limit == BASE_BUY_LIMIT
    assert all(int(b) not in (101, 102, 103)
               for b in nxt.players[0].blueprints_round)


def test_tower4_elite_recruit_levels_subsequent_buys_only():
    st = sandbox(supply0=300, units0=((10, -60.0),), round_no=2)
    # buy BEFORE the activation spawns at level 1
    pre = apply0(st, buy(0, mech=2))
    assert pre.receipts[0][0].accepted
    bought_before = pre.state.players[0].units[-1]
    assert bought_before.level == 1
    res = apply0(pre.state, tskill(4))
    assert res.receipts[0][0].accepted
    assert res.receipts[0][0].resource_delta == -100
    # buy AFTER the activation spawns at level 2 (doc order rule)
    post = apply0(res.state, buy(1, mech=2))
    assert post.receipts[0][0].accepted
    bought_after = post.state.players[0].units[-1]
    assert bought_after.level == 2
    # single purchase per round
    again = apply0(post.state, tskill(4))
    assert not again.receipts[0][0].accepted
    assert again.receipts[0][0].reason_code == "TOWER_SKILL_ALREADY_ACTIVE"


def test_tower1_fast_supply_loan_and_income_debt():
    from pysim.transition import Income200r
    st = sandbox(supply0=100, round_no=2)
    res = apply0(st, tskill(1))
    r = res.receipts[0][0]
    assert r.accepted and r.resource_delta == 200
    assert res.state.players[0].supply == 300
    assert any(e.reason == "blueprint_loan:+200" and e.amount == 200
               for e in res.ledgers[0].entries)
    # single purchase per round
    again = apply0(res.state, tskill(1))
    assert not again.receipts[0][0].accepted
    assert again.receipts[0][0].reason_code == "TOWER_SKILL_ALREADY_ACTIVE"
    # the -300 debt lands on next round's income via Income200r
    pol = Income200r()
    debt_round = pol.record_fast_supply(0, 3, 1) \
        if hasattr(pol, "record_fast_supply") else None
    base = Income200r().income(0, res.state.players[0], 3, "Win")
    pol2 = Income200r()
    pol2.record_fast_supply(0, 3, 1)
    after = pol2.income(0, res.state.players[0], 3, "Win")
    assert after == base - 300


def test_tower_skill_raw_adapter_forwards_to_typed_handler():
    st = sandbox(supply0=200)
    via_raw = apply0(st, raw_action("ActiveEnergyTowerSkill",
                                    [("SkillID", 6)]))
    via_typed = apply0(st, tskill(6))
    assert via_raw.receipts[0][0].accepted
    assert state_digest(via_raw.state) == state_digest(via_typed.state)


def test_tower_skill_norm_entry_and_round_reset():
    rec = {"round": 3, "unit_index": 0, "units": [], "actions": [
        {"type": "ActiveEnergyTowerSkill", "SkillID": 5},
        {"type": "ActiveEnergyTowerSkill", "SkillID": 2},
        {"type": "FinishDeploy"}], "commanderSkills_raw": []}
    res = Normalizer(ECO).normalize_round(rec)
    kinds = [(e["t"], e.get("skill")) for e in res.actions_norm]
    assert kinds == [("tower_skill", 5), ("passthrough", None), ("finish", None)]
    assert capability.classify_norm_entry(res.actions_norm[0], rec, ECO,
                                          GD) is None
    assert capability.classify_norm_entry(res.actions_norm[1], rec, ECO,
                                          GD) == "UNSUPPORTED_ACTION_FIELD"
    # ids 1/3/4 are typed too (user ruling: all five are tower skills)
    rec3 = {"round": 3, "unit_index": 0, "units": [], "actions": [
        {"type": "ActiveEnergyTowerSkill", "SkillID": 3},
        {"type": "ActiveEnergyTowerSkill", "SkillID": 1},
        {"type": "ActiveEnergyTowerSkill", "SkillID": 4},
        {"type": "FinishDeploy"}], "commanderSkills_raw": []}
    res3 = Normalizer(ECO).normalize_round(rec3)
    assert [(e["t"], e.get("skill")) for e in res3.actions_norm] == \
        [("tower_skill", 3), ("tower_skill", 1), ("tower_skill", 4),
         ("finish", None)]
    # undo folds a typed tower_skill op like the raw form (Q1)
    rec2 = {"round": 3, "unit_index": 0, "units": [], "actions": [
        {"type": "ActiveEnergyTowerSkill", "SkillID": 5},
        {"type": "Undo"},
        {"type": "FinishDeploy"}], "commanderSkills_raw": []}
    res2 = Normalizer(ECO).normalize_round(rec2)
    assert [e["t"] for e in res2.actions_norm] == ["finish"]
    # advance_round clears the buffs
    st = sandbox(supply0=200)
    res3b = apply0(st, tskill(5))
    st2 = EnvironmentState(**{**res3b.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome = run_battle(st2, GD, battle_seed=4)
    settled = settle_transition(st2, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    assert nxt.players[0].tower_mods_raw == ()


def test_tower_skill_battle_side_mods_compile():
    from pysim.battlefield.compiler import compile_battle_input
    st = sandbox(supply0=200, units0=((10, -60.0),), round_no=2)
    res = apply0(st, tskill(5))
    res = apply0(res.state, tskill(6))
    st2 = EnvironmentState(**{**res.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    bi = compile_battle_input(st2, 123)
    sm = [m for m in bi.side_mods if m.side == 0][0]
    assert sm.range_add == 15.0 and sm.speed_add == 3.0


# ================================================================ T5 P1 skills
@pytest.mark.parametrize("sid", [300003, 300004, 300007])
def test_p1_strikes_release_and_digest(sid):
    st = sandbox(units0=((10, -60.0),), units1=((10, 60.0),), round_no=2,
                 skills0=(("0", str(sid), "true", "0"),))
    res = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                     ReleaseCommanderSkillArgs(
                                         skill_id=sid,
                                         positions=((30.0, 40.0),))))
    r = res.receipts[0][0]
    assert r.accepted, r.detail
    assert res.state.players[0].skill_events_raw == ((sid, 30.0, 40.0),)
    # battle input expansion: 轨道轰炸 -> 15 impacts; others 1 impact
    from pysim.battlefield.compiler import compile_battle_input
    st2 = EnvironmentState(**{**res.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    bi = compile_battle_input(st2, 42)
    evs = [e for e in bi.events if e.skill_id == sid]
    n = COMMANDER_SKILLS[sid].get("strikes", 1)
    assert len(evs) == n
    # deterministic: same release -> same digest
    bi2 = compile_battle_input(st2, 42)
    assert bi.digest() == bi2.digest()
    # spread around the target
    if sid == 300003:
        xs = [e.position[0] for e in evs]
        assert min(xs) < 30.0 < max(xs)


def test_nuke_lands_at_15s_and_hits_friendlies():
    """300004: t=15s delayed strike with friendly fire (QA#6)."""
    from pysim.battlefield.legacy_engine import legacy_battle
    from pysim.battlefield.compiler import compile_battle_input
    st = sandbox(units0=((3, -60.0),), units1=((3, 60.0),), round_no=2,
                 skills0=(("0", "300004", "true", "0"),))
    res = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                     ReleaseCommanderSkillArgs(
                                         skill_id=300004,
                                         positions=((0.0, 150.0),))))
    # strike centered in the OPPONENT half hits the opponent's units
    st2 = EnvironmentState(**{**res.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome, extra = run_battle(st2, GD, battle_seed=1, with_trace=True)
    hits = [ln for ln in extra["trace"] if "strike_hit" in ln]
    assert hits, "nuke never landed"
    t_hit = float(hits[0].split("|")[1])
    assert 14.5 <= t_hit <= 16.5
    # friendly fire: place a friend at the blast center -> it takes damage
    friend = UnitCard(entity_id=900, mech_id=3, level=1, exp=0,
                      x=0.0, y=150.0, replay_index=77)
    st3 = EnvironmentState(**{**st2.__dict__, "players": (
        PlayerState(**{**st2.players[0].__dict__,
                       "units": st2.players[0].units + (friend,)}),
        st2.players[1])})
    out3 = run_battle(st3, GD, battle_seed=1)
    card = next(c for c in out3.cards if c.entity_id == 900)
    assert not card.survived, "70000 ff nuke must kill a friend at ground 0"


def test_javelin_bypasses_barrier():
    """300007 轨道标枪: r30, 70000, explicitly bypasses shield absorption."""
    from pysim.skills import CONTRAPTIONS
    st = sandbox(units0=((3, -60.0),), units1=((3, 60.0),), round_no=2,
                 skills0=(("0", "300007", "true", "0"),))
    res = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                     ReleaseCommanderSkillArgs(
                                         skill_id=300007,
                                         positions=((0.0, 150.0),))))
    # opponent drops a barrier covering their unit
    res2 = deploy_transition(res.state, (CanonicalActionPlan(player=1, actions=(
        raw_action("ReleaseContraption", [
            ("ContraptionID", "20001"),
            ("Position", {"x": 0.0, "y": 150.0})]),)),), ECO)
    assert res2.receipts[0][0].accepted
    st2 = EnvironmentState(**{**res2.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    out = run_battle(st2, GD, battle_seed=2)
    opp_card = next(c for c in out.cards if c.entity_id == 100)
    assert not opp_card.survived, "javelin must bypass the barrier (70000)"


@pytest.mark.parametrize("sid,mech", [(1200002, 5), (1200004, 11),
                                      (1200005, 3)])
def test_p1_summon_airdrop(sid, mech):
    st = sandbox(units0=((10, -60.0),), units1=((10, 60.0),), round_no=2,
                 skills0=(("0", str(sid), "true", "0"),))
    res = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                     ReleaseCommanderSkillArgs(
                                         skill_id=sid,
                                         positions=((20.0, 30.0),))))
    assert res.receipts[0][0].accepted
    # battle-only summon: NOT in persistent units, no spawn rights
    assert len(res.state.players[0].units) == 1
    assert res.state.players[0].spawned_this_round == ()
    st2 = EnvironmentState(**{**res.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    from pysim.battlefield.compiler import compile_battle_input
    bi = compile_battle_input(st2, 3)
    evs = [e for e in bi.events if e.skill_id == sid]
    assert len(evs) == 1
    assert dict(evs[0].params)["mech"] == float(mech)


def test_p1_registry_confidence_stays_provisional():
    for sid in (300003, 300004, 300007, 1200002, 1200004, 1200005):
        fid = bf_registry.mechanism_support("commander_skill", sid).two_axis()
        assert fid["transition_complete"] and fid["battle_fidelity"] == "exact"
        assert fid["confidence"] == "provisional"   # never verified w/o oracle
    # redeploy is a verified transition-only skill (no battle stage involved)
    fid = bf_registry.mechanism_support("commander_skill",
                                        REDEPLOY_SKILL_ID).two_axis()
    assert fid["transition_complete"] and fid["confidence"] == "verified"


def test_napalm_def_corrected_to_survey():
    d = COMMANDER_SKILLS[100002]
    assert d["dps"] == 270.0            # step4 QA#5: 270/s (was 352)
    assert "line" in d["conf"]          # shape note: circle until P2 area


# ============================================================ regression
def test_replay_reproduction_rate_holds():
    """The sample-corpus unit-set reproduction keeps its floor under the
    frozen movement rules. step4 final: with tower skill 3 (批量征召)
    modeled as the per-round +1 quota purchase, the corpus is CONSISTENT
    with base 2 — quota-diverged rounds should be ZERO (any that appear
    are excluded from the denominator as rule divergence, not regression)."""
    from pysim.transition import ReplayAdapter
    samples = os.path.join(ROOT, "data", "samples", "rounds.json")
    if not os.path.exists(samples):
        pytest.skip("sample corpus missing")
    adapter = ReplayAdapter(samples)
    from collections import Counter
    ok = n = quota_diverged = 0
    for gi in range(len(adapter.games())):
        g = adapter.games()[gi]
        for side in (0, 1):
            rs = g["players"][side]["rounds"]
            for i in range(len(rs) - 1):
                rnd = int(rs[i]["round"])
                if rnd < 1:
                    continue
                base = adapter.environment_state(gi, rnd, economy=ECO)
                from pysim.transition.model import PlayerState as PS
                inc = Income200r().income(
                    side, base.players[side], rnd,
                    base.players[side].pre_round_fight_result)
                state = EnvironmentState(**{**base.__dict__, "players": (
                    PS(**{**base.players[0].__dict__,
                          "supply": base.players[0].supply
                          + (inc if side == 0 else 0)}),
                    PS(**{**base.players[1].__dict__,
                          "supply": base.players[1].supply
                          + (inc if side == 1 else 0)}))})
                acts, rep = adapter.norm_actions(g, side, rnd)
                try:
                    plan, crep = canonicalize_plan(side, acts,
                                                   norm_report=rep)
                except Exception:
                    n += 1
                    continue
                res = deploy_transition(state, (plan,), ECO)
                if any(not r.accepted and r.reason_code == "BUY_LIMIT_REACHED"
                       for r in res.receipts[0]):
                    quota_diverged += 1
                    continue

                def key(u):
                    return (u.mech_id, u.level, round(u.x, 1), round(u.y, 1),
                            u.is_rotate)

                want = Counter((int(u["id"]), int(u["level"]) + 1,
                                round(float(u["x"]), 1),
                                round(float(u["y"]), 1),
                                bool(u.get("isRotate")))
                               for u in rs[i + 1]["units"])
                got = Counter(key(u) for u in res.state.players[side].units)
                n += 1
                if got == want:
                    ok += 1
                    continue
                extra = list((got - want).elements())
                missing = list((want - got).elements())
                if len(extra) == len(missing) and all(
                        e[0] == m[0] and e[1] == m[1] - 1 and e[2:] == m[2:]
                        for e, m in zip(extra, missing)):
                    ok += 1
    assert n >= 15
    assert ok / n >= 0.75, "unit-set exact rate degraded: %d/%d" % (ok, n)
    assert quota_diverged == 0    # corpus is consistent with base2+tower3
