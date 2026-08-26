# BattleAdapter: EnvironmentState -> pysim Battle -> BattleOutcome.
#
# The adapter is the ONLY module touching the engine; settlement consumes the
# public BattleOutcome. Level boundary: UnitCard.level is already 1-based and
# goes straight into add_card (battle_from_units would +1 again — never used
# here). exp_seed=1 carries snapshot exp into the fight so settlement can
# continue the cumulative exp (pysim_exp_v1).
from ..engine import Battle
from ..deploy import TOWER_POS
from .model import (EnvironmentState, BattleOutcome, CardBattleResult,
                    Phase, ENGINE_VERSION)

EXP_QUANT = "pysim_exp_quant_v1"      # round-half-even int quantization


def _quant(x: float) -> int:
    # single quantization point for fractional participation exp
    return int(round(x))


def battle_from_state(state: EnvironmentState, gd, battle_seed: int = 0,
                      opts=None, engine=None) -> tuple:
    """Build (Battle, entity_map, card_map) from a PRE_BATTLE state.

    entity_map[card_idx] = entity_id; card_map[entity_id] = card_idx."""
    if state.phase is not Phase.PRE_BATTLE:
        from . import errors
        raise errors.TransitionError(errors.WRONG_PHASE,
                                     "battle needs PRE_BATTLE state")
    b = Battle(gd)
    b.opts.update({"exp_seed": 1})
    if opts:
        b.opts.update(opts)
    entity_map, card_map = {}, {}
    n_cards = 0                     # cards appended in add order (pre-finalize
    for side in (0, 1):             # count ourselves; b.cards fills at finalize)
        p = state.players[side]
        b.officer_ids[side] = tuple(int(o) for o in p.officers)
        # blueprint officer stacking (engine bp_stack default) handled inside
        # battle_from_units; mirror it here for the direct add path
        if b.opts.get("bp_stack", 1):
            BP_BASE = {20301: 20300, 20311: 20310}
            ids = b.officer_ids[side]
            extra = tuple(BP_BASE[o] for o in ids
                          if o in BP_BASE and BP_BASE[o] not in ids)
            if extra:
                b.officer_ids[side] = ids + extra
        levels = list(p.tower_strengthen)[:2]
        for k in range(min(2, len(levels))):
            tx, ty = TOWER_POS[side][k]
            b.add_tower(side, tx, ty, int(levels[k] or 0))
        techs_of = {m: list(t) for m, t in p.tech_map}
        for u in p.units:
            m = gd.mechs.get(u.mech_id)
            if m is None or m.main_skill_id == 0:
                continue     # engine drops these at finalize; skip symmetric
            idx = n_cards
            n_cards += 1
            b.add_card(side, u.mech_id, u.level, u.x, u.y, u.is_rotate,
                       techs=list(techs_of.get(u.mech_id, [])),
                       exp=int(u.exp))
            entity_map[idx] = u.entity_id
            card_map[u.entity_id] = idx
        for c in p.constructions_raw:
            try:
                cid = int(c[1])
                if cid in (1, 2, 3, 4):
                    b.add_building(side, cid, float(c[2]), float(c[3]),
                                   index=int(c[0]))
            except (TypeError, ValueError):
                continue
    b.finalize()
    b._battle_seed = battle_seed
    return b, entity_map, card_map


def run_battle(state: EnvironmentState, gd, battle_seed: int = 0,
               opts=None) -> BattleOutcome:
    """Simulate one fight and produce the public outcome.

    Damage rule pysim_survivor_value_v1: winner's survivor value becomes the
    loser's hp deduction; draws deduct nothing (real-report probing may
    revise this; the rule name rides in the ruleset)."""
    b, entity_map, _ = battle_from_state(state, gd, battle_seed, opts)
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
            from . import errors
            raise errors.TransitionError("UNKNOWN_ENTITY_MAPPING",
                                         "card %d has no entity" % rec["card_idx"])
        before = _unit_exp(state, eid)
        delta = _quant(rec["exp"]) - before
        cards.append(CardBattleResult(
            entity_id=eid, exp_before=before, exp_delta=delta,
            exp_after=before + delta, damage=rec["damage"],
            kills=rec["kills"], survived=rec["survived"],
            level_after=rec["level"]))
    return BattleOutcome(
        battle_seed=battle_seed, winner=int(winner),
        score_by_team=(int(s0), int(s1)), damage_to_player=dmg,
        cards=tuple(cards), end_time=float(b.end_tick) * 0.01,
        engine_version=ENGINE_VERSION)


def _unit_exp(state: EnvironmentState, entity_id: int) -> int:
    for p in state.players:
        for u in p.units:
            if u.entity_id == entity_id:
                return u.exp
    return 0
