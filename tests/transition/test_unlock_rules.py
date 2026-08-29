# 爬虫动力学与伤害标定修正任务书 (2026-08-29) T11 tests: the per-round
# manual unlock quota and the quota-free auto unlocks. Case ids mirror the
# 任务书 §6.4 oracle matrix (U1..U10); every assertion runs against the SAME
# rule source (rules.unlock_limit_quote / rules.expert_auto_unlock_mechs).
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from pysim.gamedata import GameData
from pysim.transition import (Economy, Phase, ActionKind, CanonicalAction,
                              CanonicalActionPlan, UnitCard, PlayerState,
                              EnvironmentState, deploy_transition,
                              advance_round, unlock_limit_quote,
                              pending_expert_auto_unlocks,
                              UNIT_EXPERT_OFFICERS, AUTO_UNLOCK_EXPERT_TAG,
                              AUTO_UNLOCK_REINFORCEMENT_TAG,
                              BASE_MANUAL_UNLOCK_LIMIT)
from pysim.transition.model import UnlockArgs, ChooseReinforceArgs
from pysim.transition import errors

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))
ECO = Economy(GD)


# ---------------------------------------------------------------- helpers
def sandbox(round_no=1, supply0=2000, supply1=2000, officers0=(),
            unlocked0=(2, 7), units0=((10, -60.0),)):
    def mk(mech, x, side, k):
        return UnitCard(entity_id=side * 100 + k, mech_id=mech, level=1,
                        exp=0, x=x, y=-150.0 if side == 0 else 150.0,
                        sell_supply=ECO.buy_price(mech) or 0,
                        replay_index=side * 100 + k)
    p0 = PlayerState(hp=4500, max_hp=4500, supply=supply0,
                     pre_round_fight_result=None,
                     units=tuple(mk(m, x, 0, k) for k, (m, x)
                                 in enumerate(units0)),
                     unlocked_mechs=frozenset(unlocked0),
                     tech_map=(), officers=tuple(officers0))
    p1 = PlayerState(hp=4500, max_hp=4500, supply=supply1,
                     pre_round_fight_result=None,
                     units=tuple([mk(10, 60.0, 1, 0)]),
                     unlocked_mechs=frozenset({10}), tech_map=(), officers=())
    return EnvironmentState(schema_version="t", ruleset_version="sandbox",
                            engine_version="e", round=round_no,
                            phase=Phase.DEPLOYMENT, players=(p0, p1),
                            next_entity_id=500)


def apply0(state, *actions, player=0):
    res = deploy_transition(state, (CanonicalActionPlan(
        player=player, actions=actions),), ECO)
    return res


def unlock(mech):
    return CanonicalAction(ActionKind.UNLOCK_UNIT, UnlockArgs(mech_id=mech))


def first_grant_item():
    """Any 单位获得卡 (unit-grant reinforcement) from the survey table."""
    for iid, e in sorted(ECO.items.items()):
        if e.get("kind") == "单位获得卡" and e.get("grant"):
            return int(iid)
    raise AssertionError("no unit-grant reinforcement in the item table")


def first_strengthen_item():
    """Any 单位强化卡 bound to exactly one mech (must NOT auto-unlock)."""
    for iid, e in sorted(ECO.items.items()):
        if e.get("kind") == "单位强化卡":
            mods = ECO.price_mods.get(int(iid))
            if mods:
                return int(iid)
    return None


# ============================================================== quota (U1)
def test_u1_second_manual_unlock_rejected_state_untouched():
    st = sandbox(supply0=2000)
    res = apply0(st, unlock(1), unlock(3))
    r1, r2 = res.receipts[0]
    assert r1.accepted and r1.reason_code == errors.OK
    assert not r2.accepted
    assert r2.reason_code == errors.UNLOCK_LIMIT_REACHED
    p0 = res.state.players[0]
    # atomic rejection: supply, set and counter all untouched by #2
    assert p0.supply == 2000 - ECO.unlock_price(1)
    assert 1 in p0.unlocked_mechs and 3 not in p0.unlocked_mechs
    assert p0.manual_unlocks_this_round == 1
    # single rule source view agrees
    assert unlock_limit_quote(p0).remaining == 0


def test_u1b_already_unlocked_and_failures_never_consume():
    st = sandbox(supply0=210)         # affords unlock(1) only
    res = apply0(st, unlock(2),       # already unlocked: OK no charge
                 unlock(17),          # INSUFFICIENT_SUPPLY: rejected
                 unlock(2))           # query again: OK no charge
    r1, r2, r3 = res.receipts[0]
    assert r1.accepted and r2.reason_code == errors.INSUFFICIENT_SUPPLY
    assert r3.accepted
    p0 = res.state.players[0]
    assert p0.manual_unlocks_this_round == 0
    # the quota is still intact afterwards
    res2 = apply0(res.state, unlock(1))
    assert res2.receipts[0][0].accepted


def test_u9_zero_cost_manual_unlock_still_consumes_quota():
    # 巨型专家 makes 堡垒(1) unlock at 0 — the limit is on COUNT not spend
    st = sandbox(officers0=(20005,))
    res = apply0(st, unlock(1), unlock(3))
    r1, r2 = res.receipts[0]
    assert r1.accepted and r1.resource_delta == 0
    assert not r2.accepted
    assert r2.reason_code == errors.UNLOCK_LIMIT_REACHED


# ============================================================== reset (U2)
def test_u2_quota_resets_next_round_and_both_sides_independent():
    st = sandbox(supply0=2000, supply1=2000)
    res = apply0(st, unlock(1), unlock(3))   # p0 uses its quota
    assert res.receipts[0][1].reason_code == errors.UNLOCK_LIMIT_REACHED
    # U10: player 1 is independent and still has its own quota
    res_p1 = apply0(res.state, unlock(5), player=1)
    assert res_p1.receipts[0][0].accepted
    # U2: advancing the round restores p0's quota
    nxt = advance_round(res_p1.state, gd=GD)
    assert nxt.players[0].manual_unlocks_this_round == 0
    assert nxt.players[1].manual_unlocks_this_round == 0
    res2 = apply0(nxt, unlock(3))
    assert res2.receipts[0][0].accepted


# ==================================================== expert auto unlock
def test_u3_unit_expert_auto_unlocks_at_active_round():
    # 剑齿虎专家 20036 activeRound 3: held from round 2, unlocks mech 21 at
    # the round-3 start; NO quota consumed, provenance tag recorded
    st = sandbox(round_no=2, officers0=(20036,))
    nxt = advance_round(st, gd=GD)
    assert nxt.round == 3
    p0 = nxt.players[0]
    assert 21 in p0.unlocked_mechs
    assert p0.manual_unlocks_this_round == 0
    tags = [k for k, _ in nxt.provenance
            if k.startswith("AUTO_UNLOCK_EXPERT")]
    assert tags == [AUTO_UNLOCK_EXPERT_TAG % 20036]
    # idempotent: a second advance does not duplicate or change anything
    nxt2 = advance_round(nxt, gd=GD)
    assert 21 in nxt2.players[0].unlocked_mechs
    assert len([k for k, _ in nxt2.provenance
                if k.startswith("AUTO_UNLOCK_EXPERT")]) == 1


def test_u3b_expert_does_not_unlock_before_active_round():
    st = sandbox(round_no=2, officers0=(20036,))
    mid = advance_round(st, gd=GD)      # round 3: unlocked
    assert 21 in mid.players[0].unlocked_mechs
    # 长弓专家 20029 activeRound 2 already passed at round 2 -> unlocked at
    # the first advance; 犀牛 20033 (activeRound 4) waits until round 4
    st2 = sandbox(round_no=1, officers0=(20033,))
    r2 = advance_round(st2, gd=GD)      # round 2: not yet
    assert 5 not in r2.players[0].unlocked_mechs
    r4 = advance_round(advance_round(r2, gd=GD), gd=GD)   # round 4
    assert 5 in r4.players[0].unlocked_mechs


def test_u6_category_experts_never_batch_unlock():
    # 巨型专家 20005 (10 unitIds) / 空军专家 20021 (7 unitIds) stay price/
    # stat experts only — no batch auto unlock of their unitIds sets
    st = sandbox(round_no=5, officers0=(20005, 20021))
    assert pending_expert_auto_unlocks(st.players[0].officers, GD, 6) == ()
    nxt = advance_round(st, gd=GD)
    for mech in GD.officers[20005].unit_ids | GD.officers[20021].unit_ids:
        assert mech not in nxt.players[0].unlocked_mechs
    # the registry itself only carries the six single-mech experts
    assert UNIT_EXPERT_OFFICERS == frozenset(
        {20029, 20033, 20036, 20037, 20038, 20039})


def test_u3c_prophet_expert_unlocks_without_modeled_gift():
    # 先知专家 20037 (mech 26, activeRound 4) has NO gift spawn in the
    # corpus model — the user-frozen auto unlock still applies at round 4
    st = sandbox(round_no=3, officers0=(20037,))
    nxt = advance_round(st, gd=GD)
    assert 26 in nxt.players[0].unlocked_mechs


# ============================================ reinforcement auto unlock
def test_u4_unit_grant_reinforcement_auto_unlocks_mech():
    iid = first_grant_item()
    grant_mech, count, _level = ECO.item_grant(iid)
    st = sandbox(supply0=5000, unlocked0=(2, 7))
    assert grant_mech not in st.players[0].unlocked_mechs
    res = apply0(st, CanonicalAction(ActionKind.CHOOSE_REINFORCE,
                                     ChooseReinforceArgs(item_id=iid)))
    r = res.receipts[0][0]
    assert r.accepted
    p0 = res.state.players[0]
    assert grant_mech in p0.unlocked_mechs
    assert p0.manual_unlocks_this_round == 0
    assert AUTO_UNLOCK_REINFORCEMENT_TAG % iid in r.detail
    assert "players[0].unlocked_mechs" in r.changed_paths
    # idempotent: the same grant again (funded) does not double-count
    res2 = apply0(res.state, CanonicalAction(
        ActionKind.CHOOSE_REINFORCE, ChooseReinforceArgs(item_id=iid)))
    assert res2.state.players[0].manual_unlocks_this_round == 0


def test_u5_single_mech_strengthen_card_does_not_auto_unlock():
    iid = first_strengthen_item()
    if iid is None:
        pytest.skip("no single-mech strengthen card in the item table")
    mods = ECO.price_mods[iid]
    mech = mods["mech"]
    if mech in (2, 7, 10):
        pytest.skip("strengthen card targets an already-unlocked mech")
    st = sandbox(supply0=5000, unlocked0=(2, 7, 10))
    res = apply0(st, CanonicalAction(ActionKind.CHOOSE_REINFORCE,
                                     ChooseReinforceArgs(item_id=iid)))
    assert res.receipts[0][0].accepted
    # oracle-pending (Q12): buffs persist into officers, unlock is NOT
    # generalized from a single-mech binding without frozen evidence
    assert mech not in res.state.players[0].unlocked_mechs


def test_u7_auto_unlock_does_not_block_or_consume_manual_quota():
    iid = first_grant_item()
    grant_mech, _c, _l = ECO.item_grant(iid)
    manual_mech = 1 if grant_mech != 1 else 3
    # order A: reinforcement first, manual unlock second
    st = sandbox(supply0=5000)
    res = apply0(st,
                 CanonicalAction(ActionKind.CHOOSE_REINFORCE,
                                 ChooseReinforceArgs(item_id=iid)),
                 unlock(manual_mech))
    assert all(r.accepted for r in res.receipts[0])
    # order B: manual unlock first, reinforcement second — same final set
    st_b = sandbox(supply0=5000)
    res_b = apply0(st_b,
                   unlock(manual_mech),
                   CanonicalAction(ActionKind.CHOOSE_REINFORCE,
                                   ChooseReinforceArgs(item_id=iid)))
    assert all(r.accepted for r in res_b.receipts[0])
    assert res.state.players[0].unlocked_mechs == \
        res_b.state.players[0].unlocked_mechs
    assert grant_mech in res.state.players[0].unlocked_mechs
    assert manual_mech in res.state.players[0].unlocked_mechs
    assert res.state.players[0].manual_unlocks_this_round == 1


# ============================================================== env legal
def test_env_candidates_stop_after_quota_spent():
    from pysim.transition import TransitionEnv
    from pysim.transition.replay_adapter import ReplayAdapter  # noqa: F401
    env = TransitionEnv(GD, ECO)
    st = sandbox(supply0=2000)
    env.reset(st)
    cands = env.legal_action_candidates(0)
    assert any(c.kind is ActionKind.UNLOCK_UNIT for c in cands)
    res = apply0(st, unlock(1))
    env.reset(res.state)
    cands2 = env.legal_action_candidates(0)
    assert not any(c.kind is ActionKind.UNLOCK_UNIT for c in cands2)


# ==================================================== save/load + folding
def test_manual_unlock_counter_survives_save_load_and_advances():
    from pysim.transition import state_to_dict, state_from_dict
    st = sandbox(supply0=2000)
    res = apply0(st, unlock(1))
    rt = state_from_dict(state_to_dict(res.state))
    assert rt.players[0].manual_unlocks_this_round == 1
    # old saves predating the field adapt to 0 (adapter default)
    d = state_to_dict(res.state)
    for p in d["players"]:
        p.pop("manual_unlocks_this_round", None)
    old = state_from_dict(d)
    assert old.players[0].manual_unlocks_this_round == 0


def test_gift_auto_unlocks_expert_mech():
    # the delayed gift of a unit expert unlocks the gifted mech (no quota)
    st = sandbox(round_no=3)
    res = apply0(st, CanonicalAction(ActionKind.GIFT_UNIT,
                                     __import__(
                                         "pysim.transition.model",
                                         fromlist=["GiftArgs"]).GiftArgs(
                                             mech_id=21)))
    r = res.receipts[0][0]
    assert r.accepted
    assert 21 in res.state.players[0].unlocked_mechs
    assert AUTO_UNLOCK_EXPERT_TAG % 20036 in r.detail
    assert res.state.players[0].manual_unlocks_this_round == 0
