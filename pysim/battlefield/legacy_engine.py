# Legacy engine bridge (migration period, 重构计划 §2.2): BattleInput ->
# the existing pysim.engine.Battle. Once B2-B4 extract the remaining systems
# the engine consumes BattleInput natively and this bridge is deleted. Until
# then it is the ONLY code translating the frozen contract into Battle
# setup calls - battle_adapter no longer touches Battle directly.
import numpy as np

from ..engine import Battle
from ..deploy import (TOWER_POS, DEVICE_BARRIER, DEVICE_MISSILE,
                      BLD_MECH_OF_CID)
from ..skills import CONTRAPTIONS, COMMANDER_SKILLS
from .model import BattleInput
from .outcome import BattleOutcomeV2, EntityOutcome, ObjectOutcome


def _dropped_by_engine(gd, mech_id: int) -> bool:
    m = gd.mechs.get(int(mech_id))
    return m is None or m.main_skill_id == 0


def legacy_battle(bi: BattleInput, gd, opts=None, with_trace: bool = False):
    """Feed the frozen BattleInput into the legacy Battle.

    Returns (battle, entity_map, card_map): entity_map[card_idx] = unit
    entity_id for persistent units (battle-only rows like skill summons have
    no entry); card_map[entity_id] = card_idx."""
    b = Battle(gd)
    b.opts.update({"exp_seed": 1})
    if opts:
        b.opts.update(opts)
    b.trace_enabled = bool(with_trace)
    b.officer_ids[0] = tuple(bi.officers[0])
    b.officer_ids[1] = tuple(bi.officers[1])
    entity_map, card_map = {}, {}
    n_cards = 0
    for obj in bi.world_objects:
        if obj.kind == "tower":
            b.add_tower(obj.side, obj.position[0], obj.position[1],
                        int(obj.subtype))
        elif obj.kind == "building":
            # ref "bld:<index>" carries the snapshot Construction Index
            try:
                gidx = int(str(obj.ref).rsplit(":", 1)[1])
            except (ValueError, IndexError):
                gidx = None
            b.add_building(obj.side, int(obj.subtype), obj.position[0],
                           obj.position[1], index=gidx)
        elif obj.kind == "device":
            d = CONTRAPTIONS.get(int(obj.subtype))
            if not d:
                continue
            params = dict(obj.params)
            ev = {"kind": d["kind"], "x": obj.position[0],
                  "y": obj.position[1], "name": d["name"], "id": obj.subtype}
            if d["kind"] == "turret":
                for k in ("damage", "range", "cooling", "prepare",
                          "attack_duration", "bullet_speed", "splash",
                          "radius", "hp"):
                    if k in params:
                        ev[k] = params[k]
            else:
                ev["hp"] = params.get("hp", d["hp"])
                ev["radius"] = params.get("radius", d["radius"])
            b.add_skill_event(obj.side, ev)
    for ev in bi.events:
        d = COMMANDER_SKILLS.get(int(ev.skill_id))
        if not d:
            continue
        params = dict(ev.params)
        out = {"kind": ev.kind, "x": ev.position[0], "y": ev.position[1],
               "name": d["name"], "id": int(ev.skill_id)}
        if ev.kind == "strike":
            out["damage"] = params.get("damage", d["damage"])
            out["splash"] = params.get("splash", d["splash"])
        elif ev.kind == "barrier":
            out["hp"] = params.get("hp", d["hp"])
            out["radius"] = params.get("radius", d["radius"])
        elif ev.kind == "summon":
            out["mech"] = int(params.get("mech", d["mech"]))
            out["count"] = int(params.get("count", d["count"]))
            out["level"] = int(params.get("level", d["level"]))
        elif ev.kind == "burn":
            out["dps"] = params.get("dps", d["dps"])
            out["radius"] = params.get("radius", d["radius"])
        b.add_skill_event(ev.side, out)
    for sm in bi.side_mods:
        b.tower_mods[sm.side] = {"range": sm.range_add, "speed": sm.speed_add}
    for u in bi.units:
        if _dropped_by_engine(gd, u.mech_id):
            continue     # engine drops these at finalize; skip symmetric
        idx = n_cards
        n_cards += 1
        b.add_card(u.side, u.mech_id, u.level, u.position[0], u.position[1],
                   u.rotation, techs=list(u.tech_ids), spawn_at=u.spawn_at,
                   exp=u.exp, equipment_id=u.equipment_id)
        entity_map[idx] = u.entity_id
        card_map[u.entity_id] = idx
    b.finalize()
    b._battle_seed = bi.seed
    return b, entity_map, card_map


def _object_rows(b, obj):
    """Legacy SoA rows matching one compiled world object (by kind, side and
    compiled position/subtype). The bridge may read the arrays - nobody
    outside it may."""
    if obj.kind == "tower":
        m = b.is_tower & (b.team == obj.side)
    elif obj.kind == "building":
        # building refs carry the snapshot index ("bld:<index>"); bld_group
        # holds that same index (add_building passes index -> group)
        try:
            gidx = int(str(obj.ref).rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return []
        return np.where(b.is_bld & (b.team == obj.side)
                        & (b.bld_group == gidx))[0]
    else:
        dev_mech = DEVICE_MISSILE if int(obj.subtype) == 10001 \
            else DEVICE_BARRIER
        m = (b.mech_id == dev_mech) & (b.team == obj.side)
    rows = []
    for i in np.where(m)[0]:
        if abs(float(b.x[i]) - obj.position[0]) < 1e-6 and \
                abs(float(b.y[i]) - obj.position[1]) < 1e-6:
            rows.append(i)
    return rows


def outcome_v2(b, bi: BattleInput, entity_map: dict, outcome) -> BattleOutcomeV2:
    """Versioned V2 outcome from a finished legacy battle (audit contract;
    settlement keeps consuming the V1 BattleOutcome until the migration gate
    flips it)."""
    entities = []
    for rec in b.outcome_cards():
        eid = entity_map.get(rec["card_idx"])
        if eid is None:
            continue           # battle-only summons dissolve after the fight
        entities.append(EntityOutcome(
            entity_id=eid, side=rec["team"], damage=rec["damage"],
            kills=rec["kills"], survived=rec["survived"],
            exp_after=int(round(rec["exp"])), level_after=rec["level"]))
    killer_of = {}
    for k in b.kills:
        # unit kills index card_idx; device kills index uid (row+1) — both
        # recorded per _fire/_on_barrier_down conventions
        killer_of[int(k.get("victim", -1))] = int(k.get("killer", -1))
    uid_to_card = {int(u): int(c) for u, c in zip(b.uid, b.card_idx)}

    def _killer_entity_of(rows):
        uids = {int(b.uid[r]) for r in rows}
        for v, k in killer_of.items():
            if v in uids:
                ci = uid_to_card.get(k)
                return entity_map.get(ci) if ci is not None and ci >= 0 \
                    else None
        return None

    objects = []
    for obj in bi.world_objects:
        rows = _object_rows(b, obj)
        alive = bool(rows) and bool(np.any(~b.dead[rows]))
        hp_left = float(np.sum(b.hp[rows]) if rows else 0.0)
        objects.append(ObjectOutcome(
            obj.ref, obj.kind, obj.side, alive, round(hp_left, 3),
            _killer_entity_of(rows), 0.0))   # object score: undecided rule
    return BattleOutcomeV2(
        battle_seed=bi.seed, winner=outcome.winner,
        score_by_team=outcome.score_by_team,
        damage_to_player=outcome.damage_to_player,
        entities=tuple(entities), objects=tuple(objects),
        end_time=outcome.end_time,
        engine_version=outcome.engine_version,
        fidelity_warnings=outcome.fidelity_warnings)
