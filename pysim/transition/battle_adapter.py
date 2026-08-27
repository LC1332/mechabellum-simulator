# BattleAdapter: EnvironmentState -> battlefield compile -> pysim Battle ->
# BattleOutcome.
#
# 重构计划 §2.2/B1: the adapter no longer touches Battle setup directly. It
# compiles the state into a versioned, digestable BattleInput
# (battlefield.compiler - officer device multipliers, flank delays with
# 快速传送, equipment ids and this round's releases all bake there) and feeds
# it through battlefield.legacy_engine. The engine remains the only module
# running the fight; settlement consumes the public BattleOutcome.
#
# Level boundary: UnitCard.level is already 1-based and goes straight into
# add_card (battle_from_units would +1 again — never used here). exp_seed=1
# carries snapshot exp into the fight so settlement can continue the
# cumulative exp (pysim_exp_v1).
from .model import (EnvironmentState, BattleOutcome, CardBattleResult,
                    Phase, ENGINE_VERSION)
from ..battlefield.compiler import compile_battle_input
from ..battlefield.legacy_engine import legacy_battle, outcome_v2
from ..battlefield import registry

EXP_QUANT = "pysim_exp_quant_v1"      # round-half-even int quantization


def _quant(x: float) -> int:
    # single quantization point for fractional participation exp
    return int(round(x))


def battle_from_state(state: EnvironmentState, gd, battle_seed: int = 0,
                      opts=None, engine=None, with_trace: bool = False,
                      return_input: bool = False):
    """Build (Battle, entity_map, card_map) from a PRE_BATTLE state.

    entity_map[card_idx] = entity_id; card_map[entity_id] = card_idx.
    with_trace=True enables the engine frame/event trace for the frontend
    player (settlement never reads it; same simulate call feeds both).
    return_input=True additionally returns the frozen BattleInput (digest
    audits/tests; the input always exists, this only surfaces it)."""
    if state.phase is not Phase.PRE_BATTLE:
        from . import errors
        raise errors.TransitionError(errors.WRONG_PHASE,
                                     "battle needs PRE_BATTLE state")
    bi = compile_battle_input(state, battle_seed)
    b, entity_map, card_map = legacy_battle(bi, gd, opts=opts,
                                            with_trace=with_trace)
    if return_input:
        return b, entity_map, card_map, bi
    return b, entity_map, card_map


def _equipment_warnings(state: EnvironmentState) -> tuple:
    """Per equipped-id approximation warnings from the battlefield registry
    (single source with capability and the compiler). Implemented ids (E2
    battle specs) no longer warn; their values stay confidence=provisional
    in the registry until the equipment oracle A/B (E6)."""
    seen = set()
    for p in state.players:
        for u in p.units:
            eid = int(u.equipment_id or 0)
            if eid:
                seen.add(eid)
    out = []
    for eid in sorted(seen):
        w = registry.equipment_battle_warning(eid)
        if w:
            out.append(w)
    return tuple(out)


def run_battle(state: EnvironmentState, gd, battle_seed: int = 0,
               opts=None, with_trace: bool = False):
    """Simulate one fight and produce the public outcome.

    Damage rule pysim_survivor_value_v1: winner's survivor value becomes the
    loser's hp deduction; draws deduct nothing (real-report probing may
    revise this; the rule name rides in the ruleset).
    with_trace=True returns (outcome, battle_extra) where battle_extra
    carries the engine trace + public result fields for the frontend player;
    the outcome driving settlement is the same object either way."""
    b, entity_map, _, bi = battle_from_state(state, gd, battle_seed, opts,
                                             with_trace=with_trace,
                                             return_input=True)
    winner = b.simulate()
    s0, s1 = b.team_score(0), b.team_score(1)
    if winner == 0:
        dmg = (0, int(s0))
    elif winner == 1:
        dmg = (int(s1), 0)
    else:
        dmg = (0, 0)
    cards = []
    for rec in b.outcome_cards():
        eid = entity_map.get(rec["card_idx"])
        if eid is None:
            # battle-only cards (skill-event summons) have no persistent
            # entity; they fight inside this battle and dissolve after it
            continue
        before = _unit_exp(state, eid)
        delta = _quant(rec["exp"]) - before
        cards.append(CardBattleResult(
            entity_id=eid, exp_before=before, exp_delta=delta,
            exp_after=before + delta, damage=rec["damage"],
            kills=rec["kills"], survived=rec["survived"],
            level_after=rec["level"]))
    outcome = BattleOutcome(
        battle_seed=battle_seed, winner=int(winner),
        score_by_team=(int(s0), int(s1)), damage_to_player=dmg,
        cards=tuple(cards), end_time=float(b.end_tick) * 0.01,
        engine_version=ENGINE_VERSION,
        fidelity_warnings=_equipment_warnings(state))
    if not with_trace:
        return outcome
    # audit contract (B0 characterization): the versioned V2 outcome and the
    # BattleInput digest ride along in the extra view; settlement keeps
    # consuming the V1 fields above
    v2 = outcome_v2(b, bi, entity_map, outcome)
    res = b.result(winner)
    extra = {
        "trace": res["trace"],
        "survivors": {str(t): res["survivors"][t] for t in (0, 1)},
        "towers_down": {str(t): res["towers_down"].get(t, 0) for t in (0, 1)},
        "buildings": res["buildings"],
        "stats": res["stats"],
        "card_index": {str(ci): entity_map.get(ci)
                       for ci in range(len(b.cards))},
        "battle_input_digest": bi.digest(),
        "outcome_v2": v2.as_dict(),
        "outcome_v2_digest": v2.digest(),
    }
    return outcome, extra


def _unit_exp(state: EnvironmentState, entity_id: int) -> int:
    for p in state.players:
        for u in p.units:
            if u.entity_id == entity_id:
                return u.exp
    return 0
