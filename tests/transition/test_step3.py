# step3 任务书 tests: expert economy quotes (T1), frozen fees (T2), field
# tech loop (T3), commander-skill id fixes + typed releases + timed grants
# (T4), equipment transition chain (T5), two-axis support (T6).
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
                              capability, equipment as equipment_mod)
from pysim.transition.model import (BuyArgs, TechArgs, UnlockArgs,
                                    ChooseReinforceArgs,
                                    ReleaseCommanderSkillArgs,
                                    UseEquipmentArgs, UnsupportedArgs)
from pysim.transition.normalize import Normalizer
from pysim.skills import COMMANDER_SKILLS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))
ECO = Economy(GD)


# ---------------------------------------------------------------- helpers
def sandbox(units0=((10, -60.0), (10, 0.0), (21, 60.0)), units1=((10, 60.0),),
            supply0=2000, supply1=2000, officers0=(), equip0=(), skills0=()):
    """Round-1 DEPLOYMENT state with arbitrary field units / officers."""
    def mk(mech, x, side, k):
        return UnitCard(entity_id=side * 100 + k, mech_id=mech, level=1, exp=0,
                        x=x, y=-150.0 if side == 0 else 150.0,
                        sell_supply=ECO.buy_price(mech) or 0,
                        replay_index=side * 100 + k)
    p0 = PlayerState(hp=4500, max_hp=4500, supply=supply0,
                     pre_round_fight_result=None,
                     units=tuple(mk(m, x, 0, k) for k, (m, x)
                                 in enumerate(units0)),
                     unlocked_mechs=frozenset({m for m, _ in units0} | {2, 7}),
                     tech_map=(), officers=tuple(officers0),
                     commander_skills_raw=tuple(skills0),
                     equipment_inventory=tuple(equip0))
    p1 = PlayerState(hp=4500, max_hp=4500, supply=supply1,
                     pre_round_fight_result=None,
                     units=tuple(mk(m, x, 1, k) for k, (m, x)
                                 in enumerate(units1)),
                     unlocked_mechs=frozenset({m for m, _ in units1}),
                     tech_map=(), officers=())
    return EnvironmentState(schema_version="t", ruleset_version="sandbox",
                            engine_version="e", round=1,
                            phase=Phase.DEPLOYMENT, players=(p0, p1),
                            next_entity_id=500)


def apply0(state, action):
    res = deploy_transition(state, (CanonicalActionPlan(
        player=0, actions=(action,)),), ECO)
    return res


def raw_action(raw_type, raw):
    return CanonicalAction(ActionKind.RAW_UNSUPPORTED,
                           UnsupportedArgs(raw_type=raw_type,
                                           raw=tuple(sorted(raw))))


# ================================================================ T1 economy
def test_giant_expert_fortress_unlock_free():
    """任务书 §2.3 gate: 巨型专家 + 堡垒 = base 200, -200 modifier, final 0."""
    q = ECO.unlock_quote(1, officers=(20005,))
    assert q.base_price == 200
    assert [m.amount for m in q.modifiers] == [-200]
    assert q.final_price == 0
    # without the expert the fee stays 200
    q2 = ECO.unlock_quote(1, officers=())
    assert q2.final_price == 200 and not q2.modifiers
    # runtime: unlock executes at the quoted price
    st0 = sandbox(officers0=(20005,))
    res = apply0(st0, CanonicalAction(ActionKind.UNLOCK_UNIT,
                                      UnlockArgs(mech_id=1)))
    r = res.receipts[0][0]
    assert r.accepted and r.resource_delta == 0
    assert any(e.reason == "unlock:1" and e.amount == 0
               for e in res.ledgers[0].entries)


def test_unlock_discount_scope_is_gamedata_unit_ids_only():
    """§2.2: scope reads officer unitIds — a non-listed 400-cost mech gets no
    discount even though its base price >= 400 (no heuristics)."""
    for mech in (20005 and sorted(GD.officers[20005].unit_ids)):
        q = ECO.unlock_quote(mech, officers=(20021,))
        listed_air = mech in GD.officers[20021].unit_ids
        assert (any(m.amount == -200 for m in q.modifiers)) == listed_air, mech
    # 空军专家 discount on a listed air mech
    q = ECO.unlock_quote(6, officers=(20021,))
    assert q.final_price == 0 and q.modifiers[0].amount == -200


def test_unlock_discount_stack_and_zero_floor():
    """Multiple modifiers sum; final floors at 0 (霸主 11 is giant AND air)."""
    q = ECO.unlock_quote(11, officers=(20005, 20021))
    assert sorted(m.amount for m in q.modifiers) == [-200, -200]
    assert q.final_price == 0        # 200 - 400 floored


def test_tech_discounts_single_stack_and_floor():
    # 剑齿虎专家: sabertooth tech -50; 高效科技研发: all techs -50
    q0 = ECO.tech_quote(21, 10221, 0, officers=())
    assert q0.final_price == 300
    q1 = ECO.tech_quote(21, 10221, 0, officers=(20036,))
    assert q1.final_price == 250
    q2 = ECO.tech_quote(21, 10221, 0, officers=(20036, 20003))
    assert q2.final_price == 200 and len(q2.modifiers) == 2
    # 火獾专家 scopes to mech 20 only
    q3 = ECO.tech_quote(21, 10221, 0, officers=(20038,))
    assert q3.final_price == 300
    q4 = ECO.tech_quote(20, 10220, 0, officers=(20038,))
    assert q4.final_price == 250
    # zero floor: mech-20 tech 180220 supply 100, owned 0, both sabertooth
    # officers absent -> not applicable; build floor via 高效研发 on a 50-supply
    # tech? cheapest tech supply is 100 -> use owned staircase 0 and three
    # stacked discounts is impossible (20038 scopes to 20, 20036 to 21) ->
    # floor via unlock quote covers the rule; tech floor checked symbolically
    # with a monkeypatched-free path: mech 20 + 20038 + 20003 on supply 100
    q5 = ECO.tech_quote(20, 180220, 0, officers=(20038, 20003))
    assert q5.final_price == 0        # 100 - 50 - 50


def test_round1_supply_officers():
    """精英专家 +100 / 训练专家 +50 at round 1 only (一次性)."""
    pol = Income200r()
    st = sandbox(officers0=(20032, 10014))
    inc1 = pol.income(0, st.players[0], 1, None)
    base = Income200r().income(0, sandbox().players[0], 1, None)
    assert inc1 == base + 150
    # round 2: the one-time bonuses are gone
    inc2 = pol.income(0, st.players[0], 2, None)
    assert inc2 == 400
    only_r1 = Income200r().income(0, sandbox(officers0=(20032,)).players[0],
                                  2, None)
    assert only_r1 == 400              # 20032 has no per-round income


def test_quotes_are_the_single_source():
    """Compat int wrappers delegate to the quotes (no second truth)."""
    assert ECO.unlock_price(1, (20005,)) == \
        ECO.unlock_quote(1, (20005,)).final_price
    assert ECO.tech_price(21, 10221, 0, (20036,)) == \
        ECO.tech_quote(21, 10221, 0, (20036,)).final_price


# ================================================================ T2 fees
def test_tower_skill_costs_and_ledger():
    st0 = sandbox()
    res = apply0(st0, raw_action("ActiveEnergyTowerSkill",
                                 [("SkillID", 5)]))
    r = res.receipts[0][0]
    assert r.accepted and r.resource_delta == -100
    assert any(e.reason == "tower_skill:5" and e.amount == -100
               for e in res.ledgers[0].entries)
    assert res.state.players[0].tower_mods_raw == (5,)
    res2 = apply0(res.state, raw_action("ActiveEnergyTowerSkill",
                                        [("SkillID", 6)]))
    r2 = res2.receipts[0][0]
    assert r2.accepted and r2.resource_delta == -50
    assert res2.state.players[0].tower_mods_raw == (5, 6)


def test_blueprint2_charges_fifty_and_raises_limit():
    st0 = sandbox()
    res = apply0(st0, raw_action("ActiveBlueprint", [("ID", 2)]))
    r = res.receipts[0][0]
    assert r.accepted and r.resource_delta == -50
    assert any(e.reason == "blueprint:2" and e.amount == -50
               for e in res.ledgers[0].entries)
    # buy limit +1: 6 buys accepted with BASE_BUY_LIMIT 5
    st = res.state
    for k in range(6):
        rr = apply0(st, CanonicalAction(ActionKind.BUY_UNIT, BuyArgs(
            mech_id=2, x=0.0, y=-100.0 - k, new_ref=k + 1)))
        assert rr.receipts[0][0].accepted, rr.receipts[0][0].detail
        st = rr.state
    rr = apply0(st, CanonicalAction(ActionKind.BUY_UNIT, BuyArgs(
        mech_id=2, x=0.0, y=-199.0, new_ref=99)))
    assert not rr.receipts[0][0].accepted
    assert rr.receipts[0][0].reason_code == "BUY_LIMIT_REACHED"


@pytest.mark.parametrize("raw_type,raw,cost", [
    ("ActiveEnergyTowerSkill", [("SkillID", 5)], 100),
    ("ActiveEnergyTowerSkill", [("SkillID", 6)], 50),
    ("ActiveBlueprint", [("ID", 2)], 50),
])
def test_insufficient_funds_reject_and_keep_digest(raw_type, raw, cost):
    st = sandbox(supply0=cost - 1)
    before = state_digest(st)
    res = apply0(st, raw_action(raw_type, raw))
    r = res.receipts[0][0]
    assert not r.accepted and r.reason_code == "INSUFFICIENT_SUPPLY"
    assert state_digest(res.state) == before
    assert res.state.players[0].supply == cost - 1
    assert res.state.players[0].tower_mods_raw == ()
    assert res.state.players[0].blueprints == ()


# ================================================================ T3 tech
def test_tech_requires_field_unit_and_hidden_when_absent():
    """§4.2: BUY_TECH needs >=1 field unit of the mech; the receipt carries
    TECH_MECH_NOT_ON_FIELD and the state is untouched."""
    st = sandbox(units0=((10, -60.0),))          # only crawlers on field
    res = apply0(st, CanonicalAction(ActionKind.BUY_TECH,
                                     TechArgs(mech_id=21, tech_id=10221)))
    r = res.receipts[0][0]
    assert not r.accepted and r.reason_code == "TECH_MECH_NOT_ON_FIELD"
    assert state_digest(res.state) == state_digest(st)
    # with a sabertooth on the field the same purchase executes with quotes
    st2 = sandbox(units0=((21, -60.0),), officers0=(20036,))
    res2 = apply0(st2, CanonicalAction(ActionKind.BUY_TECH,
                                       TechArgs(mech_id=21, tech_id=10221)))
    assert res2.receipts[0][0].accepted
    assert res2.receipts[0][0].resource_delta == -250   # 300 - 50 剑齿虎专家
    assert res2.state.players[0].tech_map == ((21, (10221,)),)


def test_tech_of_other_mech_rejected():
    st = sandbox(units0=((21, -60.0),))
    # tech 10510 belongs to mech 10, not 21
    res = apply0(st, CanonicalAction(ActionKind.BUY_TECH,
                                     TechArgs(mech_id=21, tech_id=10510)))
    assert not res.receipts[0][0].accepted
    assert res.receipts[0][0].reason_code == "UNKNOWN_TECH"


def test_tech_applies_to_all_units_of_mech_in_battle_input():
    """§4.2: after the purchase every same-mech unit's battle input carries
    the tech (mech-level compilation)."""
    from pysim.transition.battle_adapter import battle_from_state
    st = sandbox(units0=((10, -60.0), (10, 0.0), (21, 60.0)))
    res = apply0(st, CanonicalAction(ActionKind.BUY_TECH,
                                     TechArgs(mech_id=10, tech_id=10510)))
    assert res.receipts[0][0].accepted
    st2 = EnvironmentState(**{**res.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    b, emap, cmap = battle_from_state(st2, GD, battle_seed=7)
    crawler_cards = [c for c in b.cards if c["mech"] == 10 and c["team"] == 0]
    assert crawler_cards
    for c in crawler_cards:
        assert 10510 in (c["techs"] or ()), "tech missing on battle card"


def test_tech_survives_selling_last_unit():
    """卖掉最后一队后科技从栏目隐藏但 state 保留（§9.2）."""
    st = sandbox(units0=((10, -60.0), (21, 60.0)))
    res = apply0(st, CanonicalAction(ActionKind.BUY_TECH,
                                     TechArgs(mech_id=10, tech_id=10510)))
    st2 = res.state
    # sell the crawler (handle = its replay_index)
    crawler = next(u for u in st2.players[0].units if u.mech_id == 10)
    res2 = apply0(st2, CanonicalAction(ActionKind.SELL_UNIT,
                                       SellRef(crawler.replay_index)))
    st3 = res2.state
    assert not any(u.mech_id == 10 for u in st3.players[0].units)
    assert st3.players[0].tech_map == ((10, (10510,)),)   # state kept


def SellRef(handle):
    from pysim.transition.model import SellArgs
    return SellArgs(ref=EntityRef(handle=handle))


def test_env_tech_candidates_follow_field_mechs():
    from pysim.transition import TransitionEnv
    st = sandbox(units0=((10, -60.0),), supply0=5000)
    env = TransitionEnv(GD, ECO, income_policy=None)
    env.reset(st)
    cands = [a for a in env.legal_action_candidates(0)
             if a.kind is ActionKind.BUY_TECH]
    assert cands and all(a.args.mech_id == 10 for a in cands)
    assert not any(a.args.mech_id == 21 for a in cands)


# ================================================================ T4 skills
def test_skill_id_mapping_frozen():
    """§5.1: 200001 is EMP (unmapped), 1000001 is redeploy (unmapped);
    燃烧弹 is 100002; summons are 1200001/1200003."""
    assert set(COMMANDER_SKILLS) == {300001, 800001, 100002, 1200001, 1200003}
    assert 200001 not in COMMANDER_SKILLS
    assert 1000001 not in COMMANDER_SKILLS
    assert COMMANDER_SKILLS[100002]["kind"] == "burn"
    assert COMMANDER_SKILLS[1200001]["name"] == "地底威胁"
    assert COMMANDER_SKILLS[1200003]["name"] == "呼叫机群"


def test_missile_expert_round2_two_slots():
    """§5.3: 导弹专家 10011 grants two independent 300001 slots at round 2."""
    st = sandbox(officers0=(10011,), units1=((10, 60.0),))
    st2 = EnvironmentState(**{**st.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome = run_battle(st2, GD, battle_seed=3)
    settled = settle_transition(st2, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    slots = nxt.players[0].commander_skills_raw
    ids = [int(e[1]) for e in slots]
    assert ids.count(300001) == 2
    assert len({e[0] for e in slots}) == len(slots)   # stable distinct indexes
    # player 1 (no officer) got nothing
    assert nxt.players[1].commander_skills_raw == ()


def test_explicit_id_and_index_resolve_identically():
    """§5.2: 显式 ID 优先，否则 SkillIndex 查当前库存 — 两者同一技能."""
    st = sandbox(skills0=(("0", "300001", "true", "0"),
                          ("1", "1100001", "true", "0")))
    via_id = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                        ReleaseCommanderSkillArgs(
                                            skill_id=300001,
                                            positions=((10.0, 20.0),))))
    via_idx = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                         ReleaseCommanderSkillArgs(
                                             skill_index=0,
                                             positions=((10.0, 20.0),))))
    assert via_id.receipts[0][0].accepted
    assert via_idx.receipts[0][0].accepted
    assert via_idx.state.players[0].skill_events_raw == \
        via_id.state.players[0].skill_events_raw == ((300001, 10.0, 20.0),)


@pytest.mark.parametrize("sid,kind", [
    (300001, "strike"), (800001, "barrier"), (100002, "burn"),
    (1200001, "summon"), (1200003, "summon")])
def test_mapped_skills_reach_battle_trace(sid, kind):
    """§5.4: battle trace must show the skill event from the same pysim."""
    st = sandbox(skills0=(("0", str(sid), "true", "0"),))
    res = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                     ReleaseCommanderSkillArgs(
                                         skill_id=sid,
                                         positions=((30.0, 40.0),))))
    assert res.receipts[0][0].accepted
    st2 = EnvironmentState(**{**res.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome, extra = run_battle(st2, GD, battle_seed=11, with_trace=True)
    assert any("|skill|" in ln for ln in extra["trace"]), "no skill event"


def test_training_skill_only_bumps_exp():
    st = sandbox(units0=((10, -60.0), (10, 0.0)))
    unit = st.players[0].units[0]
    res = apply0(st, CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                                     ReleaseCommanderSkillArgs(
                                         skill_id=1100001,
                                         unit_ref=EntityRef(
                                             handle=unit.replay_index))))
    r = res.receipts[0][0]
    assert r.accepted and "exp" in "".join(r.changed_paths)
    bumped = next(u for u in res.state.players[0].units
                  if u.entity_id == unit.entity_id)
    need = ECO.upgrade_exp_need(10, 1)
    assert bumped.exp == need
    other = next(u for u in res.state.players[0].units
                 if u.entity_id != unit.entity_id)
    assert other.exp == 0
    # nothing else moved: no battle events, no supply change
    assert res.state.players[0].skill_events_raw == ()
    assert res.state.players[0].supply == st.players[0].supply


def test_unmapped_and_wrong_target_precise_blockers():
    st = sandbox()
    # EMP 200001 must NOT burn
    res = apply0(st, raw_action("ReleaseCommanderSkill",
                                [("ID", 200001),
                                 ("Positions", [{"x": 1, "y": 2}])]))
    r = res.receipts[0][0]
    assert not r.accepted and r.reason_code == "UNSUPPORTED_ACTION"
    assert "skill_id=200001" in r.detail and "target_kind=position" in r.detail
    assert res.state.players[0].skill_events_raw == ()
    # redeploy 1000001 must NOT summon
    res2 = apply0(st, raw_action("ReleaseCommanderSkill",
                                 [("ID", 1000001),
                                  ("Positions", [{"x": 1, "y": 2}])]))
    assert not res2.receipts[0][0].accepted
    assert res2.state.players[0].skill_events_raw == ()
    # building recycle carries construction target kind (precise blocker)
    res3 = apply0(st, raw_action("ReleaseCommanderSkill",
                                 [("ID", 300001), ("ConstructionIndex", 2)]))
    r3 = res3.receipts[0][0]
    assert not r3.accepted and r3.reason_code == "UNSUPPORTED_ACTION"
    assert "target_kind=construction" in r3.detail and "skill_id=300001" \
        in r3.detail
    # scanner agreement: same rule source
    assert capability.classify_raw("ReleaseCommanderSkill",
                                   {"ID": 200001}) == "UNSUPPORTED_ACTION_FIELD"
    assert capability.classify_raw("ReleaseCommanderSkill",
                                   {"ID": 1000001}) == "UNSUPPORTED_ACTION_FIELD"
    assert capability.classify_raw("ReleaseCommanderSkill",
                                   {"ID": 300001}) is None


def test_scanner_accepts_typed_release_norm_entries():
    rec = {"commanderSkills_raw": [{"index": "0", "id": "300001"}]}
    assert capability.classify_norm_entry(
        {"t": "release", "skill": 300001, "skill_index": 0,
         "positions": [(1, 2)]}, rec, ECO, GD) is None
    assert capability.classify_norm_entry(
        {"t": "release", "skill": 200001, "skill_index": 0}, rec,
        ECO, GD) == "UNSUPPORTED_ACTION_FIELD"


def test_normalizer_emits_typed_release_and_keeps_cancel():
    norm = Normalizer(ECO)
    skills = [{"index": "3", "id": "300001", "isActive": "true",
               "coolingRound": "0"}]
    base = {"round": 2, "unit_index": 10, "units": [
        {"index": 0, "id": 10, "sellSupply": 100}],
        "commanderSkills_raw": skills, "officers": [], "actions": [],
        "techMap": {}}
    r = norm.normalize_round(dict(base, actions=[
        {"type": "ReleaseCommanderSkill", "ID": 0, "SkillIndex": 3,
         "Positions": [{"x": 5, "y": -5}]}]))
    assert [e["t"] for e in r.actions_norm] == ["release"]
    assert r.actions_norm[0]["skill"] == 300001
    # typed entries canonicalize to the typed action
    plan, rep = canonicalize_plan(0, r.actions_norm)
    assert plan.actions[0].kind is ActionKind.RELEASE_COMMANDER_SKILL
    assert plan.actions[0].args.skill_id == 300001
    assert plan.actions[0].args.positions == ((5.0, -5.0),)


# ================================================================ T5 equipment
def test_equipment_registry_covers_survey():
    raw = json.load(open(os.path.join(ROOT, "information",
                                      "增援卡牌-回放全量信息.json"),
                         encoding="utf8"))
    survey = {int(c["id"]) for c in raw["cards"] if c.get("类别") == "装备"}
    assert survey <= set(equipment_mod.EQUIPMENT_DEFS)
    assert 13030009 in equipment_mod.EQUIPMENT_DEFS   # 增幅专家's core
    d = equipment_mod.EQUIPMENT_DEFS[13030001]
    assert d.target_restriction == "any" and d.battle_fidelity == "approximate"
    assert equipment_mod.EQUIPMENT_DEFS[1306001].target_restriction == "giant"
    assert equipment_mod.EQUIPMENT_DEFS[1307001].target_restriction == \
        "ground_giant"


def test_reinforce_equipment_charges_and_stocks():
    item = 13030001                      # 激光瞄具, cost 50, 装备
    assert ECO.items[item]["kind"] == "装备"
    st = sandbox(supply0=100)
    res = apply0(st, CanonicalAction(ActionKind.CHOOSE_REINFORCE,
                                     ChooseReinforceArgs(item_id=item)))
    r = res.receipts[0][0]
    assert r.accepted and r.resource_delta == -50
    assert res.state.players[0].equipment_inventory == (13030001,)
    assert res.state.players[0].supply == 50
    assert any(e.reason == "reinforce:%s" % item and e.amount == -50
               for e in res.ledgers[0].entries)
    # capability no longer blocks known equipment offers
    assert capability.classify_norm_entry({"t": "reinforce", "id": item},
                                          None, ECO, GD) is None


def test_use_equipment_binds_and_consumes():
    st = sandbox(units0=((10, -60.0),), equip0=(13030001, 13030001))
    unit = st.players[0].units[0]
    res = apply0(st, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                     UseEquipmentArgs(
                                         equipment_id=13030001,
                                         unit_ref=EntityRef(
                                             handle=unit.replay_index))))
    r = res.receipts[0][0]
    assert r.accepted
    bound = next(u for u in res.state.players[0].units
                 if u.entity_id == unit.entity_id)
    assert bound.equipment_id == 13030001
    assert res.state.players[0].equipment_inventory == (13030001,)  # one left


def test_use_equipment_rejections_keep_state():
    st = sandbox(units0=((10, -60.0), (1, 60.0)), equip0=(13030001,))
    small = st.players[0].units[0]
    giant = st.players[0].units[1]
    # no stock
    st_nostock = sandbox(units0=((10, -60.0),), equip0=())
    res = apply0(st_nostock, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                             UseEquipmentArgs(
                                                 equipment_id=13030001,
                                                 unit_ref=EntityRef(
                                                     handle=0))))
    assert res.receipts[0][0].reason_code == "EQUIPMENT_NOT_IN_INVENTORY"
    # target missing
    res2 = apply0(st, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                      UseEquipmentArgs(
                                          equipment_id=13030001,
                                          unit_ref=EntityRef(handle=999))))
    assert res2.receipts[0][0].reason_code == "UNKNOWN_ENTITY"
    # giant-only equipment on a small unit
    st_g = sandbox(units0=((10, -60.0), (1, 60.0)), equip0=(1306001,))
    res3 = apply0(st_g, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                        UseEquipmentArgs(
                                            equipment_id=1306001,
                                            unit_ref=EntityRef(
                                                handle=small.replay_index))))
    assert res3.receipts[0][0].reason_code == "EQUIPMENT_RESTRICTION_MISMATCH"
    # ground-giant equipment on a FLYING giant (霸主 11 is giant + air)
    st_fly = sandbox(units0=((11, -60.0),), equip0=(1307001,))
    fly = st_fly.players[0].units[0]
    res4 = apply0(st_fly, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                          UseEquipmentArgs(
                                              equipment_id=1307001,
                                              unit_ref=EntityRef(
                                                  handle=fly.replay_index))))
    assert res4.receipts[0][0].reason_code == "EQUIPMENT_RESTRICTION_MISMATCH"
    # same restriction passes on a ground giant
    st_gnd = sandbox(units0=((1, -60.0),), equip0=(1307001,))
    gnd = st_gnd.players[0].units[0]
    res5 = apply0(st_gnd, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                          UseEquipmentArgs(
                                              equipment_id=1307001,
                                              unit_ref=EntityRef(
                                                  handle=gnd.replay_index))))
    assert res5.receipts[0][0].accepted
    for rr, s0 in ((res, st_nostock), (res2, st), (res3, st_g),
                   (res4, st_fly)):
        assert state_digest(rr.state) == state_digest(s0)


def test_use_equipment_unknown_id_is_hard_blocker():
    st = sandbox(units0=((10, -60.0),), equip0=(99999999,))
    res = apply0(st, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                     UseEquipmentArgs(
                                         equipment_id=99999999,
                                         unit_ref=EntityRef(handle=0))))
    assert res.receipts[0][0].reason_code == "MISSING_RULE_DATA"
    assert capability.classify_norm_entry({"t": "equip", "id": 99999999},
                                          None, ECO, GD) == "MISSING_RULE_DATA"
    assert capability.classify_norm_entry({"t": "equip", "id": 13030001},
                                          None, ECO, GD) is None


def test_equipment_replacement_consumes_old():
    st = sandbox(units0=((10, -60.0),),
                 equip0=(13030001, 13030002))
    unit = st.players[0].units[0]
    r1 = apply0(st, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                    UseEquipmentArgs(
                                        equipment_id=13030001,
                                        unit_ref=EntityRef(
                                            handle=unit.replay_index))))
    r2 = apply0(r1.state, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                          UseEquipmentArgs(
                                              equipment_id=13030002,
                                              unit_ref=EntityRef(
                                                  handle=unit.replay_index))))
    assert r2.receipts[0][0].accepted
    assert "replaced 13030001" in r2.receipts[0][0].detail
    bound = next(u for u in r2.state.players[0].units
                 if u.entity_id == unit.entity_id)
    assert bound.equipment_id == 13030002
    assert r2.state.players[0].equipment_inventory == ()   # old not restocked


def test_equipment_undo_folding_and_save_load():
    norm = Normalizer(ECO)
    base = {"round": 2, "unit_index": 10, "units": [
        {"index": 0, "id": 10, "sellSupply": 100}],
        "commanderSkills_raw": [], "officers": [], "techMap": {}}
    r = norm.normalize_round(dict(base, actions=[
        {"type": "UseEquipment", "EquipmentID": 13030001, "UnitIndex": 0},
        {"type": "Undo"},
    ]))
    assert not any(e["t"] == "equip" for e in r.actions_norm)
    r2 = norm.normalize_round(dict(base, actions=[
        {"type": "UseEquipment", "EquipmentID": 13030001, "UnitIndex": 0}]))
    assert [e["t"] for e in r2.actions_norm] == ["equip"]
    plan, _ = canonicalize_plan(0, r2.actions_norm)
    assert plan.actions[0].kind is ActionKind.USE_EQUIPMENT
    # save/load keeps the multiset
    st = sandbox(equip0=(13030009, 13030009, 13030001))
    again = state_from_dict(state_to_dict(st))
    assert again.players[0].equipment_inventory == \
        st.players[0].equipment_inventory
    # old-schema dicts (no key) adapt to the empty multiset
    d = state_to_dict(st)
    for p in d["players"]:
        p.pop("equipment_inventory")
    legacy = state_from_dict(d)
    assert legacy.players[0].equipment_inventory == ()


def test_equipment_survives_round_and_battle_warns():
    st = sandbox(units0=((10, -60.0),), equip0=(13030007,))
    unit = st.players[0].units[0]
    res = apply0(st, CanonicalAction(ActionKind.USE_EQUIPMENT,
                                     UseEquipmentArgs(
                                         equipment_id=13030007,
                                         unit_ref=EntityRef(
                                             handle=unit.replay_index))))
    st2 = EnvironmentState(**{**res.state.__dict__,
                              "finished_deploy": (True, True),
                              "phase": Phase.PRE_BATTLE})
    outcome = run_battle(st2, GD, battle_seed=5)
    assert any("13030007" in w and "battle_approximate" in w
               for w in outcome.fidelity_warnings), outcome.fidelity_warnings
    settled = settle_transition(st2, outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    kept = next(u for u in nxt.players[0].units
                if u.entity_id == unit.entity_id)
    assert kept.equipment_id == 13030007   # next-round snapshot ownership


def test_amplification_expert_grants_three_cores():
    """§6.4: 增幅专家 10013 -> 3x 13030009 at round 1 (multiplicity kept)."""
    from pysim.transition import opening as om
    pkg = {"name": "t", "hp": 4500, "supply": 100, "officers": [10013],
           "unlocked": [], "units": [
               {"mech": 10, "level": 1,
                "formation": [[0.0, -150.0]]}],
           "tech_map": {}, "constructions": [], "commander_skills": []}
    st = om.build_initial_state(pkg, copy.deepcopy(pkg), gd=GD)
    for p in st.players:
        assert p.equipment_inventory == (13030009,) * 3
    # catalog evidence adds copies without collapsing the grant (top-up only)
    pkg2 = dict(pkg, equipment_inventory=[13030009, 13030009])
    st2 = om.build_initial_state(pkg2, copy.deepcopy(pkg2), gd=GD)
    assert st2.players[0].equipment_inventory == (13030009,) * 3
    # and a round-2 tick does not re-grant
    outcome = run_battle(EnvironmentState(
        **{**st.__dict__, "finished_deploy": (True, True),
           "phase": Phase.PRE_BATTLE}), GD, battle_seed=1)
    settled = settle_transition(
        EnvironmentState(**{**st.__dict__, "finished_deploy": (True, True),
                            "phase": Phase.PRE_BATTLE}), outcome, eco=ECO)
    nxt = advance_round(settled.state, None, None, gd=GD)
    assert nxt.players[0].equipment_inventory == (13030009,) * 3


# ================================================================ T6 axes
def test_two_axis_support_matrix():
    assert capability.mechanism_support("equipment", 13030001) == \
        {"transition_complete": True, "battle_fidelity": "approximate"}
    assert capability.mechanism_support("equipment", 1) == \
        {"transition_complete": False, "battle_fidelity": "unsupported"}
    assert capability.mechanism_support("commander_skill", 300001)[
        "battle_fidelity"] == "exact"
    assert capability.mechanism_support("commander_skill", 200001)[
        "battle_fidelity"] == "unsupported"
    assert capability.mechanism_support("tower_skill", 5)[
        "battle_fidelity"] == "exact"
    assert capability.mechanism_support("blueprint", 2)[
        "transition_complete"] is True


def test_scan_option_reports_two_axis_prefixes():
    """Equipment rounds stay runtime-playable but shorten strict-effect."""
    games = json.load(open(os.path.join(ROOT, "data", "samples",
                                        "rounds.json"), encoding="utf8"))
    norm = Normalizer(ECO)
    g = None
    for cand in games:
        for pr in cand["players"]:
            for rec in pr["rounds"]:
                res = norm.normalize_round(rec)
                rec["actions_norm"] = res.actions_norm
                rec["norm_report"] = res.report
        g = cand
        break
    scan = capability.scan_option(g, 0, ECO, GD)
    assert scan["runtime_playable_through_round"] == \
        scan["playable_through_round"]
    assert scan["strict_effect_through_round"] <= \
        scan["runtime_playable_through_round"]
    for a in scan["approximate_mechanisms"]:
        assert a["mechanism"] == "equipment"
    if scan["approximate_mechanisms"]:
        assert scan["approximate_from_round"] == \
            scan["approximate_mechanisms"][0]["round"]
