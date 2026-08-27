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
                      opts=None, engine=None, with_trace: bool = False) -> tuple:
    """Build (Battle, entity_map, card_map) from a PRE_BATTLE state.

    entity_map[card_idx] = entity_id; card_map[entity_id] = card_idx.
    with_trace=True enables the engine frame/event trace for the frontend
    player (settlement never reads it; same simulate call feeds both)."""
    if state.phase is not Phase.PRE_BATTLE:
        from . import errors
        raise errors.TransitionError(errors.WRONG_PHASE,
                                     "battle needs PRE_BATTLE state")
    b = Battle(gd)
    b.opts.update({"exp_seed": 1})
    if opts:
        b.opts.update(opts)
    b.trace_enabled = bool(with_trace)
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
        # round buffs from ActiveEnergyTowerSkill (stacking, free)
        mods = {"range": 0, "speed": 0}
        for sid in p.tower_mods_raw or ():
            if int(sid) == 5:
                mods["range"] += 15
            elif int(sid) == 6:
                mods["speed"] += 3
        if mods["range"] or mods["speed"]:
            b.tower_mods[side] = dict(mods)
        # contraption devices released this round (10001 turret / 20001
        # barrier; unmapped ids are dropped here and blocked by the scanner)
        for dev in p.devices_raw or ():
            try:
                cid, dx, dy = int(dev[0]), float(dev[1]), float(dev[2])
            except (TypeError, ValueError, IndexError):
                continue
            from ..skills import CONTRAPTIONS
            d = CONTRAPTIONS.get(cid)
            if not d:
                continue
            ev = {"kind": d["kind"], "x": dx, "y": dy, "name": d["name"],
                  "id": cid}
            if d["kind"] == "turret":
                ev.update(d["def"])
            else:
                ev["hp"] = d["hp"]
                ev["radius"] = d["radius"]
            b.add_skill_event(side, ev)
        # commander battlefield skills released this round (strike/burn/
        # barrier/summon; one event per recorded position)
        for rel in p.skill_events_raw or ():
            try:
                sid, sx, sy = int(rel[0]), float(rel[1]), float(rel[2])
            except (TypeError, ValueError, IndexError):
                continue
            from ..skills import COMMANDER_SKILLS
            d = COMMANDER_SKILLS.get(sid)
            if not d:
                continue
            ev = {"kind": d["kind"], "x": sx, "y": sy, "name": d["name"],
                  "id": sid}
            if d["kind"] == "strike":
                ev["damage"] = d["damage"]
                ev["splash"] = d["splash"]
            elif d["kind"] == "barrier":
                ev["hp"] = d["hp"]
                ev["radius"] = d["radius"]
            elif d["kind"] == "summon":
                ev["mech"] = d["mech"]
                ev["count"] = d["count"]
                ev["level"] = d["level"]
            elif d["kind"] == "burn":
                ev["dps"] = d["dps"]
                ev["radius"] = d["radius"]
            b.add_skill_event(side, ev)
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


def _equipment_warnings(state: EnvironmentState) -> tuple:
    """step3 任务书 §7.2: pysim ignores equipment combat modifiers. Every
    equipped equipment id (either side) produces one visible warning — the
    effect is never silently dropped."""
    seen = set()
    for p in state.players:
        for u in p.units:
            eid = int(u.equipment_id or 0)
            if eid:
                seen.add(eid)
    return tuple(
        "equipment:%d battle effect not simulated (battle_approximate)" % eid
        for eid in sorted(seen))


def run_battle(state: EnvironmentState, gd, battle_seed: int = 0,
               opts=None, with_trace: bool = False):
    """Simulate one fight and produce the public outcome.

    Damage rule pysim_survivor_value_v1: winner's survivor value becomes the
    loser's hp deduction; draws deduct nothing (real-report probing may
    revise this; the rule name rides in the ruleset).
    with_trace=True returns (outcome, battle_extra) where battle_extra
    carries the engine trace + public result fields for the frontend player;
    the outcome driving settlement is the same object either way."""
    b, entity_map, _ = battle_from_state(state, gd, battle_seed, opts,
                                         with_trace=with_trace)
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
    res = b.result(winner)
    extra = {
        "trace": res["trace"],
        "survivors": {str(t): res["survivors"][t] for t in (0, 1)},
        "towers_down": {str(t): res["towers_down"].get(t, 0) for t in (0, 1)},
        "buildings": res["buildings"],
        "stats": res["stats"],
        "card_index": {str(ci): entity_map.get(ci)
                       for ci in range(len(b.cards))},
    }
    return outcome, extra


def _unit_exp(state: EnvironmentState, entity_id: int) -> int:
    for p in state.players:
        for u in p.units:
            if u.entity_id == entity_id:
                return u.exp
    return 0
