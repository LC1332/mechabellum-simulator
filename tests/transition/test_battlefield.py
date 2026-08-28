# battlefield 重构计划第一阶段测试 (B0/B1/E2/M1):
#   - BattleInput/BattleOutcomeV2 契约、digest 确定性 (B0/B1)
#   - 高频四件 + 第二批静态装备 A/B golden fixture (E2)
#   - compiler: 专家 10007/10008 装置强化、10009 快速传送、flank spawn (B1/M1)
#   - deploy: 10004 额外部署位、技能槽消费/CD tick、typed SURRENDER、
#     强化模块升级折扣、升级候选一致性 (M1)
#   - registry: 六段闭合 + confidence 口径 (M1)
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pysim.gamedata import GameData
from pysim.transition import (Economy, Phase, ActionKind, CanonicalAction,
                              CanonicalActionPlan, EntityRef, UnitCard,
                              PlayerState, EnvironmentState, deploy_transition,
                              run_battle, settle_transition, advance_round,
                              state_digest, capability)
from pysim.transition.model import (BuyArgs, UpgradeArgs,
                                    ReleaseCommanderSkillArgs,
                                    UseEquipmentArgs, SurrenderArgs)
from pysim.transition.battle_adapter import battle_from_state
from pysim.battlefield.compiler import compile_battle_input
from pysim.battlefield import registry
from pysim.battlefield.model import BattleInput, UnitBattleInput

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))
ECO = Economy(GD)


# ---------------------------------------------------------------- helpers
def sandbox(units0=((10, -60.0), (10, 0.0)), units1=((10, 60.0),),
            supply0=2000, supply1=2000, officers0=(), officers1=(),
            equip0=(), skills0=(), spawned0=(), round_no=1):
    def mk(entry, side, k):
        mech, x = entry[0], entry[1]
        y = entry[2] if len(entry) > 2 else (-150.0 if side == 0 else 150.0)
        return UnitCard(entity_id=side * 100 + k, mech_id=mech, level=1,
                        exp=0, x=x, y=y,
                        sell_supply=ECO.buy_price(mech) or 0,
                        replay_index=side * 100 + k)
    p0 = PlayerState(hp=4500, max_hp=4500, supply=supply0,
                     pre_round_fight_result=None,
                     units=tuple(mk(e, 0, k) for k, e in enumerate(units0)),
                     unlocked_mechs=frozenset({e[0] for e in units0} | {2, 7}),
                     tech_map=(), officers=tuple(officers0),
                     commander_skills_raw=tuple(skills0),
                     equipment_inventory=tuple(equip0),
                     spawned_this_round=tuple(spawned0))
    p1 = PlayerState(hp=4500, max_hp=4500, supply=supply1,
                     pre_round_fight_result=None,
                     units=tuple(mk(e, 1, k) for k, e in enumerate(units1)),
                     unlocked_mechs=frozenset({e[0] for e in units1}),
                     tech_map=(), officers=tuple(officers1))
    return EnvironmentState(schema_version="t", ruleset_version="sandbox",
                            engine_version="e", round=round_no,
                            phase=Phase.DEPLOYMENT, players=(p0, p1),
                            next_entity_id=500)


def apply0(state, action):
    return deploy_transition(state, (CanonicalActionPlan(
        player=0, actions=(action,)),), ECO)


def pre_battle(state):
    return EnvironmentState(**{**state.__dict__,
                               "finished_deploy": (True, True),
                               "phase": Phase.PRE_BATTLE})


def equip_unit0(state, eid, unit_k=0):
    u = state.players[0].units[unit_k]
    return apply0(state, CanonicalAction(
        ActionKind.USE_EQUIPMENT,
        UseEquipmentArgs(equipment_id=eid,
                         unit_ref=EntityRef(handle=u.replay_index))))


def _members(b, card_idx):
    import numpy as np
    return np.where((b.card_idx == card_idx) & ~b.dead)[0]


# ================================================================ B0/B1 契约
def test_battle_input_contract_and_digest():
    st = sandbox(units0=((21, -60.0),), units1=((10, 60.0),),
                 equip0=(13030001,))
    res = equip_unit0(st, 13030001)
    bi = compile_battle_input(pre_battle(res.state), battle_seed=7)
    assert bi.contract_version == "battlefield-input-v2"
    assert len(bi.units) == 2
    u0 = next(u for u in bi.units if u.side == 0)
    assert u0.equipment_id == 13030001     # E1b: equipment rides the input
    assert u0.entity_id == res.state.players[0].units[0].entity_id
    d1 = bi.digest()
    # digest determinism: same compile twice
    assert compile_battle_input(pre_battle(res.state), 7).digest() == d1
    # changing the equipment id changes the digest
    st_b = sandbox(units0=((21, -60.0),), units1=((10, 60.0),),
                   equip0=(13030002,))
    res_b = equip_unit0(st_b, 13030002)
    assert compile_battle_input(pre_battle(res_b.state), 7).digest() != d1
    # seed rides the digest
    assert compile_battle_input(pre_battle(res.state), 8).digest() != d1


def test_outcome_determinism_same_seed():
    st = pre_battle(sandbox(units0=((21, -60.0), (10, -30.0)),
                            units1=((10, 60.0), (21, 90.0))))
    o1, e1 = run_battle(st, GD, battle_seed=42, with_trace=True)
    o2, e2 = run_battle(st, GD, battle_seed=42, with_trace=True)
    assert (o1.winner, o1.score_by_team, o1.damage_to_player) == \
        (o2.winner, o2.score_by_team, o2.damage_to_player)
    assert [(c.entity_id, c.damage, c.kills) for c in o1.cards] == \
        [(c.entity_id, c.damage, c.kills) for c in o2.cards]
    assert e1["battle_input_digest"] == e2["battle_input_digest"]
    assert e1["outcome_v2_digest"] == e2["outcome_v2_digest"]
    assert e1["outcome_v2"]["outcome_version"] == "battle-outcome-v2"
    # V2 carries per-entity rows for persistent units only
    ent_ids = {e["entity_id"] for e in e1["outcome_v2"]["entities"]}
    assert ent_ids == {u.entity_id for p in st.players for u in p.units}


def test_legacy_battle_matches_old_direct_path():
    """The compiler->legacy bridge must feed the same battle the old direct
    add path built (same cards, same techs, same officers)."""
    st = pre_battle(sandbox(units0=((21, -60.0),), units1=((10, 60.0),),
                            officers0=(20301,)))
    b, emap, cmap = battle_from_state(st, GD, battle_seed=3)
    assert len(b.cards) == 2
    assert b.officer_ids[0] == (20301, 20300)    # bp_stack: II implies I
    assert [c["mech"] for c in b.cards] == [21, 10]


def test_compiler_buildings_and_towers_reach_battle():
    """constructions_raw/tower_strengthen compile into world objects and
    the legacy bridge feeds them to the engine (B2 object stream)."""
    import numpy as np
    p0 = PlayerState(hp=4500, max_hp=4500, supply=100,
                     pre_round_fight_result=None,
                     units=(UnitCard(entity_id=0, mech_id=10, level=1, exp=0,
                                     x=-60.0, y=-150.0, sell_supply=100,
                                     replay_index=0),),
                     unlocked_mechs=frozenset({10}), tech_map=(),
                     tower_strengthen=(2, 0),
                     constructions_raw=(("5", "1", "-100.0", "-55.0"),
                                        ("6", "3", "100.0", "-55.0")))
    p1 = PlayerState(hp=4500, max_hp=4500, supply=100,
                     pre_round_fight_result=None,
                     units=(UnitCard(entity_id=100, mech_id=10, level=1,
                                     exp=0, x=0.0, y=150.0,
                                     sell_supply=100, replay_index=100),),
                     unlocked_mechs=frozenset({10}), tech_map=())
    st = EnvironmentState(schema_version="t", ruleset_version="s",
                          engine_version="e", round=1,
                          phase=Phase.PRE_BATTLE, players=(p0, p1))
    bi = compile_battle_input(st, 5)
    kinds = sorted(o.ref for o in bi.world_objects)
    # both sides carry their (0,0)-strengthen tower pair, like the old path
    assert kinds == ["bld:5", "bld:6", "tower:0:0", "tower:0:1",
                     "tower:1:0", "tower:1:1"]
    b, emap, _ = battle_from_state(st, GD, battle_seed=5)
    # building placements expand into module rows (2 groups, >= 2 rows)
    groups = b.building_groups()
    assert {(k[0], k[1]) for k in groups} == {(0, 1), (0, 3)}
    assert int(np.count_nonzero(b.is_bld)) >= 2
    assert int(np.count_nonzero(b.is_tower)) == 4    # both tower pairs fed
    assert int(b.tower_str[b.is_tower][0]) == 2      # strengthen carried


# ================================================================ E2 装备 A/B
def _equipped_battle(eid, mech=21, techs=()):
    """PRE_BATTLE sandbox with one equipped unit vs one enemy -> battle
    (equipment binds during DEPLOYMENT, then the phase flips)."""
    st = sandbox(units0=((mech, -60.0),), units1=((10, 60.0),),
                 equip0=((eid,) if eid else ()))
    if eid:
        st = equip_unit0(st, eid).state
    if techs:
        p = st.players[0]
        st = EnvironmentState(**{
            **st.__dict__,
            "players": (PlayerState(**{**p.__dict__,
                                       "tech_map": ((mech, tuple(techs)),)}),
                        st.players[1])})
    b, emap, _ = battle_from_state(pre_battle(st), GD, battle_seed=11)
    return b


@pytest.mark.parametrize("eid,stat,expect", [
    # 激光瞄具: 射程 +20 (flat, after tech/officer)
    (13030001, "range", 20.0),
    # 重型装甲: 生命 +75% (multiplicative on the post-tech value)
    (13030002, "hp_mult", 0.75),
    # 改良火控: 攻击 +65%
    (13030003, "dmg_mult", 0.65),
    # 速攻模块: 移速 +5
    (13030005, "speed_add", 5.0),
    # 超重型装甲: 生命 +150%
    (13030006, "hp_mult", 1.50),
    # 增幅核心: 攻击/生命 +50%
    (13030007, "hp_mult", 0.50),
    # 强化模块 battle half: 攻击/生命 +25%
    (13030004, "dmg_mult", 0.25),
])
def test_static_equipment_golden_ab(eid, stat, expect):
    """A/B golden: the equipped card's baked stat differs from the
    unequipped control by exactly the survey value (equipment_stage_v1)."""
    import numpy as np
    b_eq = _equipped_battle(eid)
    b_no = _equipped_battle(0)
    m_eq = _members(b_eq, 0)
    m_no = _members(b_no, 0)
    if stat == "range":
        got = float(b_eq.range[m_eq][0]) - float(b_no.range[m_no][0])
    elif stat == "hp_mult":
        got = float(b_eq.max_hp[m_eq][0]) / float(b_no.max_hp[m_no][0]) - 1.0
    elif stat == "dmg_mult":
        got = float(b_eq.base_dmg[m_eq][0]) / float(b_no.base_dmg[m_no][0]) - 1.0
    elif stat == "speed_add":
        got = float(b_eq.move_speed[m_eq][0]) - float(b_no.move_speed[m_no][0])
    assert abs(got - expect) < 1e-6, (eid, stat, got, expect)


def test_equipment_stacks_after_tech_multiplicatively():
    """equipment_stage_v1 order: base -> level -> tech -> officer ->
    equipment. A life tech (+x%) then 重型装甲 (+75%) multiplies."""
    import numpy as np
    # find a +life tech of mech 21 (剑齿虎)
    card = GD.cards.get(21)
    life_tech = None
    for tid in card.technologies or ():
        td = GD.techs.get(tid)
        if td is None:
            continue
        agg = GD.sum_tech_mods([tid], 1)
        if agg["life_rate"] > 0:
            life_tech = tid
            break
    if life_tech is None:
        pytest.skip("no +life tech on mech 21")
    agg = GD.sum_tech_mods([life_tech], 1)
    b_eq = _equipped_battle(13030002, techs=(life_tech,))
    b_no = _equipped_battle(0, techs=(life_tech,))
    m_eq, m_no = _members(b_eq, 0), _members(b_no, 0)
    ratio = float(b_eq.max_hp[m_eq][0]) / float(b_no.max_hp[m_no][0])
    assert abs(ratio - 1.75) < 1e-6


def test_equipment_changes_battle_and_warns_only_unimplemented():
    """激光瞄具 changes the fight (longer range -> different outcome) and
    no longer emits an approximation warning; unimplemented ids still do."""
    st = sandbox(units0=((21, -60.0),), units1=((10, 60.0),),
                 equip0=(13030001,))
    st = equip_unit0(st, 13030001).state
    outcome = run_battle(pre_battle(st), GD, battle_seed=9)
    assert outcome.fidelity_warnings == ()      # only 13030001 bound
    st2 = sandbox(units0=((21, -60.0),), units1=((10, 60.0),),
                  equip0=(13030010,))
    st2 = equip_unit0(st2, 13030010).state
    outcome2 = run_battle(pre_battle(st2), GD, battle_seed=9)
    assert any("13030010" in w for w in outcome2.fidelity_warnings)


def test_battle_from_units_equipment_id_passthrough():
    """The benchmark path (battle_from_units) consumes equipmentId the same
    way; no equipmentId -> byte-identical legacy behavior."""
    from pysim.engine import battle_from_units
    u0 = [{"id": 21, "level": 0, "x": -60.0, "y": -60.0}]
    u1 = [{"id": 10, "level": 0, "x": 0.0, "y": 60.0}]
    b_plain = battle_from_units(GD, u0, u1, opts={"seed": 1})
    u_eq = [dict(u0[0], equipmentId=13030003)]
    b_eq = battle_from_units(GD, u_eq, u1, opts={"seed": 1})
    m0 = _members(b_plain, 0)
    m1 = _members(b_eq, 0)
    assert float(b_eq.base_dmg[m1][0]) == \
        pytest.approx(float(b_plain.base_dmg[m0][0]) * 1.65)


# ================================================================ B1/M1 compiler
def test_compiler_officer_device_enhancements():
    """10007 先进护盾装置: barrier hp x1.4; 10008 先进飞弹装置: turret
    damage x3.0 (survey desc verbatim)."""
    def state_with(officers):
        p0 = PlayerState(hp=4500, max_hp=4500, supply=100, units=(),
                         pre_round_fight_result=None,
                         unlocked_mechs=frozenset(), tech_map=(),
                         officers=tuple(officers),
                         devices_raw=((10001, 50.0, -100.0),
                                      (20001, -50.0, -100.0)))
        p1 = PlayerState(hp=4500, max_hp=4500, supply=100, units=(),
                         pre_round_fight_result=None,
                         unlocked_mechs=frozenset(), tech_map=())
        return EnvironmentState(schema_version="t", ruleset_version="s",
                                engine_version="e", round=2,
                                phase=Phase.PRE_BATTLE, players=(p0, p1))
    bi_plain = compile_battle_input(state_with(()))
    bi_off = compile_battle_input(state_with((10007, 10008)))
    dev = {o.ref: dict(o.params) for o in bi_plain.world_objects}
    dev_off = {o.ref: dict(o.params) for o in bi_off.world_objects}
    turret_p = next(v for k, v in dev.items() if k.startswith("device:10001"))
    turret_o = next(v for k, v in dev_off.items()
                    if k.startswith("device:10001"))
    barrier_p = next(v for k, v in dev.items() if k.startswith("device:20001"))
    barrier_o = next(v for k, v in dev_off.items()
                     if k.startswith("device:20001"))
    assert turret_o["damage"] == pytest.approx(turret_p["damage"] * 3.0)
    assert barrier_o["hp"] == pytest.approx(barrier_p["hp"] * 1.4)
    # 10007/10008 do NOT touch the other device type
    assert turret_o["hp"] == pytest.approx(turret_p["hp"])
    assert barrier_o["radius"] == pytest.approx(barrier_p["radius"])


def test_compiler_flank_spawn_and_quick_teleport():
    """A unit BOUGHT this round standing in the enemy half teleports in over
    FLANK_DELAY seconds; 快速传送 10009 halves it; snapshot-carried units in
    the enemy half never delay; round 1 never delays."""
    from pysim.flank import FLANK_DELAY
    enemy_y = 100.0          # side 0 unit in side 1 territory
    # side-0 entity ids are 0,1 (side*100+k convention of the sandbox)
    st = pre_battle(sandbox(units0=((10, -60.0), (21, 40.0, enemy_y)),
                            units1=((10, 60.0),), spawned0=(1,),
                            round_no=2))
    bi = compile_battle_input(st, 3)
    spawns = {u.entity_id: u.spawn_at for u in bi.units}
    assert spawns[0] == 0.0              # snapshot unit: no delay
    assert spawns[1] == FLANK_DELAY      # new unit in enemy half
    st_qt = pre_battle(sandbox(units0=((10, -60.0), (21, 40.0, enemy_y)),
                               units1=((10, 60.0),), officers0=(10009,),
                               spawned0=(1,), round_no=2))
    bi_qt = compile_battle_input(st_qt, 3)
    spawns_qt = {u.entity_id: u.spawn_at for u in bi_qt.units}
    assert spawns_qt[1] == FLANK_DELAY / 2.0
    # round 1: never delays (flank.py)
    st_r1 = pre_battle(sandbox(units0=((10, -60.0), (21, 40.0, enemy_y)),
                               units1=((10, 60.0),), spawned0=(1,),
                               round_no=1))
    assert all(u.spawn_at == 0.0
               for u in compile_battle_input(st_r1, 3).units)
    # deployed via a real buy + move (flank deploys are bought in the own
    # half and moved across the midline in the same round): deploy marks
    # spawned_this_round, the compiler applies the delay
    st_buy = sandbox(units0=((10, -60.0),), units1=((10, 60.0),), round_no=2)
    res = apply0(st_buy, CanonicalAction(
        ActionKind.BUY_UNIT, BuyArgs(mech_id=2, x=40.0, y=-100.0,
                                     new_ref=1)))
    assert res.receipts[0][0].accepted, res.receipts[0][0].detail
    bought = res.state.players[0].spawned_this_round[0]
    unit = next(u for u in res.state.players[0].units
                if u.entity_id == bought)
    from pysim.transition.model import MoveArgs
    res = apply0(res.state, CanonicalAction(
        ActionKind.MOVE_UNIT, MoveArgs(ref=EntityRef(handle=unit.replay_index),
                                       x=40.0, y=120.0, is_rotate=None)))
    assert res.receipts[0][0].accepted
    bi_buy = compile_battle_input(pre_battle(res.state), 3)
    u_buy = next(u for u in bi_buy.units if u.entity_id == bought)
    assert u_buy.spawn_at == FLANK_DELAY


# ================================================================ M1 deploy
def test_officer_10004_extra_deploy_slot():
    """额外部署位: +1 buy limit per held copy (可重复 -> stacks).
    step4: BASE_BUY_LIMIT 2 (user ruling) — 1 copy -> 3, 2 copies -> 4."""
    st = sandbox(units0=((10, -60.0),), units1=((10, 60.0),),
                 officers0=(10004,))
    bought = 0
    s = st
    for k in range(3):            # BASE_BUY_LIMIT 2 + 1 copy
        rr = apply0(s, CanonicalAction(
            ActionKind.BUY_UNIT, BuyArgs(mech_id=2, x=0.0, y=-100.0 - k,
                                         new_ref=k + 1)))
        assert rr.receipts[0][0].accepted, rr.receipts[0][0].detail
        s = rr.state
        bought += 1
    rr = apply0(s, CanonicalAction(
        ActionKind.BUY_UNIT, BuyArgs(mech_id=2, x=0.0, y=-199.0, new_ref=99)))
    assert not rr.receipts[0][0].accepted
    assert rr.receipts[0][0].reason_code == "BUY_LIMIT_REACHED"
    # two copies stack to +2
    st2 = sandbox(units0=((10, -60.0),), units1=((10, 60.0),),
                  officers0=(10004, 10004))
    s2 = st2
    for k in range(4):            # 2 + 2 copies
        rr = apply0(s2, CanonicalAction(
            ActionKind.BUY_UNIT, BuyArgs(mech_id=2, x=0.0, y=-100.0 - k,
                                         new_ref=k + 1)))
        assert rr.receipts[0][0].accepted
        s2 = rr.state
    rr = apply0(s2, CanonicalAction(
        ActionKind.BUY_UNIT, BuyArgs(mech_id=2, x=0.0, y=-199.0, new_ref=98)))
    assert not rr.receipts[0][0].accepted


def test_skill_slot_consumption_and_reject():
    st = sandbox(units0=((10, -60.0), (21, -30.0)), units1=((10, 60.0),),
                 skills0=(("0", "300001", "true", "0"),))
    res = apply0(st, CanonicalAction(
        ActionKind.RELEASE_COMMANDER_SKILL, ReleaseCommanderSkillArgs(
            skill_index=0, positions=((30.0, 40.0),))))
    r = res.receipts[0][0]
    assert r.accepted and "consumed" in r.detail
    slots = res.state.players[0].commander_skills_raw
    assert slots[0][2] == "false" and slots[0][3] == "2"   # corpus cd=2
    assert res.state.players[0].skill_events_raw == ((300001, 30.0, 40.0),)
    # double release: slot inactive -> precise rejection, digest untouched
    before = state_digest(res.state)
    res2 = apply0(res.state, CanonicalAction(
        ActionKind.RELEASE_COMMANDER_SKILL, ReleaseCommanderSkillArgs(
            skill_index=0, positions=((10.0, 10.0),))))
    r2 = res2.receipts[0][0]
    assert not r2.accepted and r2.reason_code == "SKILL_SLOT_UNAVAILABLE"
    assert "not active" in r2.detail
    assert state_digest(res2.state) == before


def test_skill_slot_cooldown_tick_lifecycle():
    """300001 (cd=2): released round N -> (false,2) at N+1, (false,1) at
    N+2, active at N+3; corpus-dominant reactivation streaks match."""
    from pysim.transition.settlement import tick_skill_cooldowns
    slots = [("0", "300001", "false", "2")]
    slots = tick_skill_cooldowns(slots)
    assert slots[0][3] == "1" and slots[0][2] == "false"
    slots = tick_skill_cooldowns(slots)
    assert slots[0][2] == "true" and slots[0][3] == "0"
    # 1100001 (cd=1): inactive exactly one round
    slots = [("0", "1100001", "false", "1")]
    slots = tick_skill_cooldowns(slots)
    assert slots[0][2] == "true"
    # 900001 (cd=0): re-arms next round
    slots = [("0", "900001", "false", "0")]
    slots = tick_skill_cooldowns(slots)
    assert slots[0][2] == "true"
    # active slots untouched
    assert tick_skill_cooldowns([("1", "300001", "true", "0")]) == \
        [("1", "300001", "true", "0")]


def test_slot_lifecycle_across_rounds_via_env():
    """Full round trip: release in round 1, settle, advance — the slot is
    consumed and re-arms after the corpus cooldown."""
    st = sandbox(units0=((10, -60.0),), units1=((10, 60.0),),
                 skills0=(("0", "300001", "true", "0"),))
    res = apply0(st, CanonicalAction(
        ActionKind.RELEASE_COMMANDER_SKILL, ReleaseCommanderSkillArgs(
            skill_id=300001, positions=((30.0, 40.0),))))
    st2 = pre_battle(res.state)
    outcome = run_battle(st2, GD, battle_seed=2)
    settled = settle_transition(st2, outcome, eco=ECO)
    n1 = advance_round(settled.state, None, None, gd=GD)
    assert n1.players[0].commander_skills_raw[0][2] == "false"
    assert n1.players[0].commander_skills_raw[0][3] == "1"
    # round 3: active again (cd 2 -> inactive exactly rounds 2 and 3? no:
    # released r1 -> (false,2) at r2 start, (false,1) at r3, active r4)
    st_r3 = pre_battle(n1)
    o3 = run_battle(st_r3, GD, battle_seed=2)
    s3 = settle_transition(st_r3, o3, eco=ECO)
    n2 = advance_round(s3.state, None, None, gd=GD)
    assert n2.players[0].commander_skills_raw[0][2] == "true"
    # spawned_this_round reset by the tick
    assert n2.players[0].spawned_this_round == ()


def test_sell_consumes_recycle_slot():
    st = sandbox(units0=((10, -60.0),), units1=((10, 60.0),),
                 skills0=(("0", "900001", "true", "0"),))
    unit = st.players[0].units[0]
    res = apply0(st, CanonicalAction(
        ActionKind.SELL_UNIT,
        __import__("pysim.transition.model", fromlist=["SellArgs"]).SellArgs(
            ref=EntityRef(handle=unit.replay_index))))
    assert res.receipts[0][0].accepted
    slots = res.state.players[0].commander_skills_raw
    assert slots[0][2] == "false" and slots[0][3] == "0"


def test_typed_surrender_terminal():
    from pysim.transition import TransitionEnv
    st = sandbox(units0=((10, -60.0),), units1=((10, 60.0),))
    env = TransitionEnv(GD, ECO)
    env.reset(st)
    res = env.step_joint(
        CanonicalActionPlan(player=0, actions=(
            CanonicalAction(ActionKind.SURRENDER, SurrenderArgs()),)),
        CanonicalActionPlan(player=1, actions=()))
    assert res.done and res.state.phase is Phase.TERMINAL
    assert res.state.terminal_reason == "surrender:player0"
    assert res.reward == (-1.0, 1.0)          # zero-sum, opponent wins
    # further deploy calls on the terminal state are precise errors
    with pytest.raises(Exception):
        deploy_transition(res.state, (CanonicalActionPlan(
            player=0, actions=(CanonicalAction(ActionKind.END_DEPLOY,
                                               None),)),), ECO)
    # the raw GiveUp passthrough (recorded after the finish click) routes
    # to the same terminal rule
    st2 = sandbox(units0=((10, -60.0),), units1=((10, 60.0),))
    from pysim.transition.model import UnsupportedArgs
    fin = apply0(st2, CanonicalAction(ActionKind.END_DEPLOY, None))
    raw = CanonicalAction(ActionKind.RAW_UNSUPPORTED,
                          UnsupportedArgs(raw_type="GiveUp", raw=()))
    res3 = apply0(fin.state, raw)
    assert res3.state.phase is Phase.TERMINAL
    assert res3.state.terminal_reason == "surrender:player0"


def test_surrender_normalizer_and_capability():
    from pysim.transition.normalize import Normalizer
    norm = Normalizer(ECO)
    base = {"round": 5, "unit_index": 3, "units": [
        {"index": 0, "id": 10, "sellSupply": 100}],
        "commanderSkills_raw": [], "officers": [], "techMap": {}}
    r = norm.normalize_round(dict(base, actions=[
        {"type": "FinishDeploy"}, {"type": "GiveUp"}]))
    assert [e["t"] for e in r.actions_norm] == ["finish", "surrender"]
    from pysim.transition import canonicalize_plan
    plan, rep = canonicalize_plan(0, r.actions_norm)
    assert plan.actions[1].kind is ActionKind.SURRENDER
    assert capability.classify_norm_entry({"t": "surrender"}, None,
                                          None, None) is None


def test_upgrade_discount_for_strengthen_module():
    """强化模块: the bound unit's upgrades cost -100 (survey text)."""
    st = sandbox(units0=((21, -60.0),), units1=((10, 60.0),),
                 equip0=(13030004,))
    res = equip_unit0(st, 13030004)
    unit = res.state.players[0].units[0]
    base_price = ECO.upgrade_price(21)
    r = apply0(res.state, CanonicalAction(
        ActionKind.UPGRADE_UNIT, UpgradeArgs(ref=EntityRef(
            handle=unit.replay_index))))
    assert r.receipts[0][0].accepted
    assert r.receipts[0][0].resource_delta == -(base_price - 100)
    assert any(e.amount == -(base_price - 100)
               for e in r.ledgers[0].entries)
    # without the module: full price
    st_plain = sandbox(units0=((21, -60.0),), units1=((10, 60.0),))
    r2 = apply0(st_plain, CanonicalAction(
        ActionKind.UPGRADE_UNIT, UpgradeArgs(ref=EntityRef(
            handle=st_plain.players[0].units[0].replay_index))))
    assert r2.receipts[0][0].resource_delta == -base_price


def test_upgrade_candidates_match_deploy_no_exp_gate():
    from pysim.transition import TransitionEnv
    # a fresh unit with ZERO exp: deploy accepts the upgrade (corpus 455/455)
    # so the candidate list must offer it too (M1 consistency)
    st = sandbox(units0=((21, -60.0),), units1=((10, 60.0),), supply0=5000)
    env = TransitionEnv(GD, ECO)
    env.reset(st)
    cands = [a for a in env.legal_action_candidates(0)
             if a.kind is ActionKind.UPGRADE_UNIT]
    assert cands, "upgrade candidate missing for zero-exp unit"
    for a in cands:
        rr = apply0(st, a)
        assert rr.receipts[0][0].accepted, rr.receipts[0][0].detail


# ================================================================ M1 registry
def test_registry_six_stage_closure():
    # implemented equipment: six stages complete but confidence provisional
    s = registry.mechanism_support("equipment", 13030001)
    assert (s.decode, s.legality, s.economy, s.persistent_state,
            s.battle, s.settlement) == ("complete",) * 6
    assert s.confidence == "provisional" and not s.effect_complete
    # 统御核心: cross-round gaps report on settlement
    s10 = registry.mechanism_support("equipment", 13030010)
    assert s10.battle == "missing" and s10.settlement == "partial"
    assert s10.transition_complete           # still runtime-playable
    # unknown ids are never guessed
    su = registry.mechanism_support("equipment", 999)
    assert su.battle_fidelity == "unsupported" and not su.transition_complete
    # commander skills: path complete, numbers provisional where cal
    for sid in (300001, 800001, 100002, 1200001, 1200003):
        sk = registry.mechanism_support("commander_skill", sid)
        assert sk.battle == "complete"
        assert sk.confidence == "provisional"
        assert not sk.effect_complete
    # 强化训练 is the one verified+complete skill
    tr = registry.mechanism_support("commander_skill", 1100001)
    assert tr.effect_complete
    # officers 10004/10007/10008/1009 implemented, provisional confidence
    for oid in (10004, 10007, 10008, 10009):
        so = registry.mechanism_support("officer", oid)
        assert so.battle == "complete" and so.confidence == "provisional"


def test_registry_dump_metrics():
    d = registry.registry_dump()
    assert d["summary"]["equipment_total"] == 25
    # step32: 7 static E2 specs + 11 runtime specs (任务书 selected IDs,
    # 次级增幅核心 rides the runtime table's static block)
    assert d["summary"]["equipment_battle_implemented"] == 18
    assert d["summary"]["equipment_runtime_implemented"] == 11
    names = {e["ident"] for e in d["equipment"]}
    assert {13030001, 13030002, 13030003, 13030005, 13030004, 13030006,
            13030007} <= names
    cds = {e["ident"]: e["cooldown_rounds"] for e in d["commander_skill"]}
    assert cds[300001] == 2 and cds[800001] == 2 and cds[100002] == 3
    assert cds[1200001] == 3 and cds[1200003] == 3 and cds[1100001] == 1


def test_scanner_strict_gate_requires_verified():
    """scan_offers strict mode: implemented-but-provisional equipment still
    blocks the strict prefix (effect_complete gate, M1)."""
    item = 13030001
    offers = [item, 0, 0, 0]
    # costs must exist for the non-strict scan
    class _Eco:
        items = ECO.items
        gd = GD

        def item_cost(self, iid):
            return ECO.item_cost(iid)
    assert capability.scan_offers(offers, _Eco()) is None
    assert capability.scan_offers(offers, _Eco(), strict_all_supported=True) \
        == "APPROXIMATE_REINFORCEMENT_EFFECT"
