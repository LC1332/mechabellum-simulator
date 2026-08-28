# R0 tests: contract pinning, observation symmetry/handles, prefix walk.
import dataclasses
import json
import os

import pytest

from pysim.gamedata import GameData
from pysim.transition import errors as terr
from pysim.transition.economy import Economy
from pysim.transition.model import (EnvironmentState, PlayerState, Phase,
                                    UnitCard)
from pysim.transition.replay_adapter import ReplayAdapter

from pysim.rl import contracts
from pysim.rl.observation import (battle_observation, policy_observation,
                                  ego_mirror_state, HandleMap)
from pysim.rl.prefix_env import (PrefixEnv, teacher_force_walk, apply_incomes,
                                derive_round_incomes)
from pysim.rl.masks import (build_action_space, action_from_norm_entry,
                            target_in_mask, SKIP, to_engine_action, RLAction)

GD = GameData(os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "data", "gamedata.json"))
ECO = Economy(GD)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "golden_prefix_round.json")


# ---------------------------------------------------------------- helpers
def make_root(spec: dict, incomes=0) -> EnvironmentState:
    def build_units(recs, start_eid):
        out = []
        for r in recs:
            out.append(UnitCard(
                entity_id=start_eid, mech_id=r["mech"], level=r["level"],
                exp=r.get("exp", 0), x=r["x"], y=r["y"],
                is_rotate=r.get("rot", False),
                replay_index=r["index"]))
            start_eid += 1
        return tuple(out), start_eid

    u0, nxt = build_units(spec["units"], 1)
    u1, nxt = build_units(spec["side1_units"], nxt)
    players = (
        PlayerState(hp=spec["hp"], max_hp=spec["max_hp"],
                    supply=spec["supply"] + incomes,
                    pre_round_fight_result=None, units=u0,
                    unlocked_mechs=frozenset(spec["unlocked_mechs"]),
                    tech_map=(), officers=tuple(spec["officers"]),
                    spawned_this_round=tuple(u.entity_id for u in u0)),
        PlayerState(hp=spec["hp"], max_hp=spec["max_hp"], supply=0,
                    pre_round_fight_result=None, units=u1,
                    unlocked_mechs=frozenset(), tech_map=(),
                    spawned_this_round=tuple(u.entity_id for u in u1)),
    )
    return EnvironmentState(
        schema_version=contracts.SCHEMA_VERSION if hasattr(
            contracts, "SCHEMA_VERSION") else "transition-v0.6",
        ruleset_version="normal_1v1_replay_v0",
        engine_version="pysim-step30", round=spec["round"],
        phase=Phase.DEPLOYMENT, players=players,
        finished_deploy=(False, False), next_entity_id=nxt)


@pytest.fixture
def root():
    spec = json.load(open(FIXTURE))["state_spec"]
    return make_root(spec, incomes=spec["income_inject"])


# ---------------------------------------------------------------- contracts
def test_contract_roundtrip_and_pin():
    c = contracts.build_contract(git_commit="test", git_dirty=False)
    assert c["schema_version"] == "transition-v0.6"
    assert c["action_profile"] == "rl_phase1_core_v1"
    assert "END_DEPLOY" in c["profile_verbs"]
    assert "BUY_UNIT" in c["profile_verbs"]
    bad = contracts.check_contract(c)
    assert bad == []
    c2 = dict(c)
    c2["engine_version"] = "pysim-step29"
    assert contracts.check_contract(c2)


def test_seed_derivation_stable():
    a = contracts.derive_seed("sample|x", 0)
    b = contracts.derive_seed("sample|x", 0)
    c = contracts.derive_seed("sample|x", 1)
    assert a == b and a != c
    assert 1 <= a < (1 << 30)


def test_observation_digest_stable_and_label_free(root):
    o = policy_observation(root, 0, buy_remaining=2)
    d1, d2 = o.digest(), policy_observation(root, 0, buy_remaining=2).digest()
    assert d1 == d2
    blob = json.dumps(o.to_dict())
    for label in ("winner", "damage", " FightReport", "replay_index",
                  "entity_id"):
        assert label not in blob


# ---------------------------------------------------------------- handles
def test_handle_unique_reversible(root):
    p = root.players[0]
    hm = HandleMap(p)
    ridxs = [hm.resolve(h) for h in range(len(p.units))]
    assert len(set(ridxs)) == len(ridxs)                 # unique
    for h, ri in enumerate(ridxs):
        assert hm.handle_of_ridx(ri) == h                # reversible


def test_handle_translation_executes_same_unit(root):
    """Task §4.4: every handle->action must resolve 100% to the same unit."""
    env = PrefixEnv(root, 0, ECO, GD)
    obs, space = env.observation()
    # move handle for the unit at replay_index 4
    h = obs.handle_map.handle_of_ridx(4)
    a = RLAction("MOVE_UNIT", handle=h, x=-95.0, y=-65.0, rot=2)
    assert target_in_mask(a, space)
    out = env.apply(a)
    assert out.accepted and not out.noop
    # the unit with replay_index 4 sits at the new position
    u4 = [u for u in env.state.players[0].units if u.replay_index == 4][0]
    assert (u4.x, u4.y) == (-95.0, -65.0)


def test_buy_grants_new_handle(root):
    env = PrefixEnv(root, 0, ECO, GD)
    obs, space = env.observation()
    n_before = len(obs.units)
    mech = next(m for m, ok in zip(space.mech_cands,
                                   space.mech_mask["BUY_UNIT"]) if ok)
    a = RLAction("BUY_UNIT", mech=mech, x=5.0, y=-150.0, rot=0)
    assert target_in_mask(a, space)
    assert env.apply(a).accepted
    obs2, space2 = env.observation()
    assert len(obs2.units) == n_before + 1               # new handle appears
    # the new unit is referenceable by its handle
    new_h = len(obs2.units) - 1
    a2 = RLAction("MOVE_UNIT", handle=new_h, x=10.0, y=-100.0, rot=2)
    assert obs2.handle_map.handle_of_ridx(
        obs2.handle_map.resolve(new_h)) == new_h


# ---------------------------------------------------------------- symmetry
def _mirror_board_state(root):
    """PRE_BATTLE variant for battle observations."""
    s = dataclasses.replace(root, phase=Phase.PRE_BATTLE)
    return s


def test_mirror_twice_is_identity(root):
    s = _mirror_board_state(root)
    m1 = ego_mirror_state(s)
    m2 = ego_mirror_state(m1)
    o0 = battle_observation(s, 0)
    o2 = battle_observation(m2, 0)
    assert o0.digest() == o2.digest()


def test_side_swap_swaps_observations(root):
    s = _mirror_board_state(root)
    o_self = battle_observation(s, 0)
    o_swapped = battle_observation(ego_mirror_state(s), 0)
    # after mirror, ego=0 sees the OTHER side's board (mirrored)
    assert o_self.self_side["units"] == o_swapped.opp_side["units"]
    assert o_self.opp_side["units"] == o_swapped.self_side["units"]
    assert o_self.self_side["hp"] == o_swapped.opp_side["hp"]


def test_unit_permutation_invariance(root):
    """Pooled battle encoder input must not depend on unit order: the
    observation sorts units canonically, so a reordered state yields the
    same observation digest."""
    s = _mirror_board_state(root)
    reordered_players = list(s.players)
    units = list(reordered_players[0].units)
    units.reverse()
    reordered_players[0] = dataclasses.replace(reordered_players[0],
                                               units=tuple(units))
    s2 = dataclasses.replace(s, players=tuple(reordered_players))
    assert battle_observation(s, 0).digest() == battle_observation(s2, 0).digest()


def test_mirror_side1_observation(root):
    """Side 1's ego observation mirrors y and rotation."""
    o1 = policy_observation(root, 1, buy_remaining=0)
    assert all(u["y"] < 0 for u in o1.units)             # own half is upper
    raw1 = root.players[1].units
    by_handle = {h: o1.units[h] for h in range(len(o1.units))}
    for u in raw1:
        h = o1.handle_map.handle_of_ridx(u.replay_index)
        assert by_handle[h]["y"] == -u.y
        assert by_handle[h]["rot"] == (not u.is_rotate)


# ---------------------------------------------------------------- prefix
def test_golden_teacher_forcing_walk(root):
    fx = json.load(open(FIXTURE))
    res = teacher_force_walk(root, 0, fx["norm_entries"], ECO, GD)
    exp = fx["expected"]
    assert res.end_reason == exp["end_reason"]
    assert len(res.samples) == exp["n_samples"]
    assert res.n_skipped == exp["n_skipped"]
    assert res.failure is None
    p = res.final_state.players[0]
    assert p.supply == exp["final_supply"]
    assert len(p.units) == exp["n_units_after"]
    # teacher-forced targets were always in-mask (T1 gate on Gold)
    for obs, space, target in res.samples:
        assert target_in_mask(target, space)


def test_walk_two_sides_independent(root):
    fx = json.load(open(FIXTURE))
    r0 = teacher_force_walk(root, 0, fx["norm_entries"], ECO, GD)
    assert r0.end_reason == "human_end"
    assert len(r0.samples) == len(fx["norm_entries"]) - 1
    # side 1's shadow env over the SAME root is untouched by side 0's walk:
    # two PrefixEnvs from one root see identical own-state, and executing a
    # buy on side 0 changes nothing on side 1's side of its own env
    env0 = PrefixEnv(root, 0, ECO, GD)
    env1 = PrefixEnv(root, 1, ECO, GD)
    base1 = env1.observation()[0].digest()
    space0 = env0.observation()[1]
    mech = next(m for m, ok in zip(space0.mech_cands,
                                   space0.mech_mask["BUY_UNIT"]) if ok)
    assert env0.apply(RLAction("BUY_UNIT", mech=mech, x=5.0, y=-150.0,
                               rot=0)).accepted
    assert env1.observation()[0].digest() == base1
    assert env1.state.players[1].supply == root.players[1].supply


def test_noop_conversion_for_unmapped_skill(root):
    env = PrefixEnv(root, 0, ECO, GD)
    obs, space = env.observation()
    # synthetic inventory: an unmapped skill slot (200001 EMP)
    from pysim.transition.model import PlayerState
    import dataclasses as dc
    st = env.state
    p0 = dc.replace(st.players[0], commander_skills_raw=(("0", "200001",
                                                          "true", "0"),))
    st2 = dc.replace(st, players=(p0, st.players[1]))
    env._env.reset(st2)
    obs, space = env.observation()
    a = RLAction("RELEASE_COMMANDER_SKILL", skill_slot=0, skill_id=200001)
    assert target_in_mask(a, space)
    out = env.apply(a)
    assert out.accepted and out.noop                     # 执行了但没有效果
    assert env.state.players[0].supply == root.players[0].supply


def test_budget_guard(root):
    env = PrefixEnv(root, 0, ECO, GD, budget=2)
    assert env.budget == 2


def test_derive_round_incomes_anchor():
    """On the synthetic spec the derived income must exactly re-anchor
    supply: supply(r) + income == spend capacity observed in the golden walk."""
    fx = json.load(open(FIXTURE))
    game = {
        "players": [
            {"rounds": [
                {"round": 1, "supply": 0,
                 "actions": [{"type": "BuyUnit", "UID": 10},
                             {"type": "BuyUnit", "UID": 10},
                             {"type": "ActiveBlueprint", "ID": 2}]},
                {"round": 2, "supply": 0, "actions": []},
            ]},
            {"rounds": [
                {"round": 1, "supply": 0, "actions": []},
                {"round": 2, "supply": 0, "actions": []},
            ]},
        ]
    }
    tab, approx = derive_round_incomes(game, ECO)
    # 0 + income - 100 - 100 - 100 == 0 -> income == 300
    assert tab[(0, 1)] == 300
    assert (0, 1) not in approx
