# Settlement: BattleOutcome -> next-round long-term state + reward.
#
# Battle influence whitelist (docs §2.1): hp, pre_round_fight_result, unit
# exp (and nothing else — dead units stay, supply/tech/officers untouched).
from dataclasses import dataclass

from . import errors
from .model import (EnvironmentState, BattleOutcome, PlayerState, UnitCard,
                    Phase)
from .state_tools import assert_state_invariants, state_digest
from .economy import IncomePolicy, InjectedIncome


@dataclass
class SettlementResult:
    state: EnvironmentState          # settled state (phase SETTLEMENT)
    reward: tuple[float, float]
    done: bool
    state_digest: str
    battle_whitelist_violations: tuple = ()


def settle_transition(state: EnvironmentState, outcome: BattleOutcome,
                      max_round: int = 40, eco=None) -> SettlementResult:
    if state.phase is not Phase.PRE_BATTLE:
        raise errors.TransitionError(errors.WRONG_PHASE,
                                     "settle needs PRE_BATTLE state")
    players = []
    violations = []
    for side in (0, 1):
        p = state.players[side]
        dmg = int(outcome.damage_to_player[side])
        hp_next = max(0, p.hp - dmg)
        if outcome.winner == -1:
            res = "Deuce"
        else:
            res = "Win" if outcome.winner == side else "Lose"
        exp_by_entity = {c.entity_id: c for c in outcome.cards}
        units = []
        for u in p.units:
            c = exp_by_entity.get(u.entity_id)
            if c is not None:
                if c.exp_after < 0:
                    violations.append("negative exp for entity %d" % u.entity_id)
                exp_v = max(0, c.exp_after)
                lv = u.level
                # fight-end auto level-up (corpus-verified: units whose exp
                # crosses a threshold during the fight level up at fight end,
                # consuming the threshold; engine exp_levelup=0 matches the
                # no-mid-fight-levelup game truth, this is the post-fight step)
                if eco is not None:
                    while lv < 9:
                        need = eco.upgrade_exp_need(u.mech_id, lv)
                        if need is None or need <= 0 or exp_v < need:
                            break
                        exp_v -= need
                        lv += 1
                units.append(UnitCard(**{**u.__dict__, "exp": exp_v,
                                         "level": lv}))
            else:
                # units absent from the outcome keep their exp (e.g. oracle mode)
                units.append(u)
        players.append(PlayerState(**{**p.__dict__, "hp": hp_next,
                                      "pre_round_fight_result": res,
                                      "units": tuple(units)}))
    done = any(p.hp <= 0 for p in players) or state.round >= max_round
    reason = None
    if done:
        if players[0].hp <= 0 and players[1].hp <= 0:
            reason = "double_ko"
        elif players[0].hp <= 0:
            reason = "player1_wins"
        elif players[1].hp <= 0:
            reason = "player0_wins"
        else:
            reason = "max_round"
    new_state = EnvironmentState(
        schema_version=state.schema_version, ruleset_version=state.ruleset_version,
        engine_version=state.engine_version, round=state.round,
        phase=Phase.TERMINAL if done else Phase.SETTLEMENT,
        players=tuple(players), finished_deploy=(True, True),
        next_entity_id=state.next_entity_id,
        terminal_reason=reason, provenance=state.provenance)
    assert_state_invariants(new_state)

    d0, d1 = outcome.damage_to_player
    reward = (float(d1 - d0), float(d0 - d1))
    if done and reason in ("player0_wins", "player1_wins"):
        bonus = (1.0, -1.0) if reason == "player0_wins" else (-1.0, 1.0)
        reward = (reward[0] + bonus[0], reward[1] + bonus[1])
    assert reward[0] + reward[1] == 0.0
    return SettlementResult(state=new_state, reward=reward, done=done,
                            state_digest=state_digest(new_state),
                            battle_whitelist_violations=tuple(violations))


def advance_round(settled: EnvironmentState,
                  income_policy: IncomePolicy | None = None,
                  incomes: tuple[int, int] | None = None,
                  max_round: int = 40,
                  gd=None) -> EnvironmentState:
    """Round tick: round+1, income, phase back to DEPLOYMENT.

    incomes, when given, overrides the policy (replay runners pass the
    injected per-round amounts so historical actions stay affordable; the
    amounts are logged in that round's ledger by the env).

    Shared round event (step3 任务书 §5.3, gd given): officers grant their
    timed commander-skill slots and equipment at the new round's start — the
    human and the historical opponent consume the SAME code path."""
    if settled.phase is Phase.TERMINAL:
        raise errors.TransitionError("TERMINAL_STATE",
                                     "cannot advance a terminal state")
    if settled.round >= max_round:
        raise errors.TransitionError("MAX_ROUND", "round budget exhausted")
    players = []
    for side in (0, 1):
        p = settled.players[side]
        if incomes is not None:
            inc = int(incomes[side])
        elif income_policy is not None:
            inc = int(income_policy.income(side, p, settled.round + 1,
                                           p.pre_round_fight_result))
        else:
            inc = 0
        skills = p.commander_skills_raw
        equipment = tuple(p.equipment_inventory or ())
        if gd is not None:
            from .equipment import (round_officer_skills,
                                    round_officer_equipment, top_up_skill_slots)
            new_round = settled.round + 1
            grants = round_officer_skills(gd, p.officers, new_round)
            if grants:
                skills = tuple(top_up_skill_slots(skills, grants))
            eq_grants = round_officer_equipment(p.officers, new_round)
            if eq_grants:
                equipment = tuple(sorted(tuple(equipment) + eq_grants))
        players.append(PlayerState(**{**p.__dict__,
                                      "supply": p.supply + inc,
                                      "bought_this_round": 0,
                                      "commander_skills_raw": skills,
                                      "equipment_inventory": equipment,
                                      "blueprints_round": (),
                                      "tower_mods_raw": (),
                                      "devices_raw": (),
                                      "skill_events_raw": ()}))
    return EnvironmentState(
        schema_version=settled.schema_version,
        ruleset_version=settled.ruleset_version,
        engine_version=settled.engine_version, round=settled.round + 1,
        phase=Phase.DEPLOYMENT, players=tuple(players),
        finished_deploy=(False, False), next_entity_id=settled.next_entity_id,
        terminal_reason=None, provenance=settled.provenance)
