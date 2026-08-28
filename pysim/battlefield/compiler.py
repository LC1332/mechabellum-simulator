# Battlefield compiler (重构计划 §2.2/B1): EnvironmentState -> BattleInput.
#
# Pure compile - never mutates persistent state. This is the ONLY place the
# transition layer's view of a fight gets shaped: officer device multipliers
# (10007/10008), flank teleport delays with 快速传送 (10009), equipment ids,
# tower buffs and this round's releases all become part of the frozen,
# digestable BattleInput. The engine (via legacy_engine) consumes the
# compiled numbers - it never re-derives them from officers itself.
from ..flank import FLANK_DELAY, QT_OFFICER
from ..deploy import TOWER_POS
from .model import (BattleInput, UnitBattleInput, WorldObject, TimedEvent,
                    SideMods)
from . import registry

# survey 增援卡牌-回放全量信息.md: 护盾装置护盾值+40% / 哨戒飞弹伤害+200%
OFFICER_BARRIER_HP_MULT = 1.4
OFFICER_TURRET_DMG_MULT = 3.0

_BP_BASE = {20301: 20300, 20311: 20310}   # blueprint II implies I (stacking)


def _officers_with_bp_stack(officers):
    """Mirror battle_from_units' bp_stack: II tiers re-add their base tier."""
    ids = tuple(int(o) for o in officers or ())
    extra = tuple(_BP_BASE[o] for o in ids
                  if o in _BP_BASE and _BP_BASE[o] not in ids)
    return ids + extra if extra else ids


def _device_params(cid, officers) -> tuple:
    """Contraption world-object params with officer multipliers applied."""
    from ..skills import CONTRAPTIONS
    d = CONTRAPTIONS.get(int(cid))
    if not d:
        return ()
    have = set(int(o) for o in officers or ())
    if d["kind"] == "turret":
        dmg = float(d["def"]["damage"])
        if 10008 in have:
            dmg *= OFFICER_TURRET_DMG_MULT
        return tuple(sorted(
            (("damage", dmg),
             ("range", float(d["def"]["range"])),
             ("cooling", float(d["def"]["cooling"])),
             ("prepare", float(d["def"]["prepare"])),
             ("attack_duration", float(d["def"]["attack_duration"])),
             ("bullet_speed", float(d["def"]["bullet_speed"])),
             ("splash", float(d["def"]["splash"])),
             ("radius", float(d["def"]["radius"])),
             ("hp", float(d["def"]["hp"])))))
    hp = float(d["hp"])
    if 10007 in have:
        hp *= OFFICER_BARRIER_HP_MULT
    return (("hp", hp), ("radius", float(d["radius"])))


def _skill_event_params(sid, d) -> tuple:
    if d["kind"] == "strike":
        out = (("damage", float(d["damage"])), ("splash", float(d["splash"])),
               ("t", float(d.get("t", 0.0) or 0.0)))
        if d.get("ff"):
            out = out + (("ff", 1.0),)
        if d.get("bypass"):
            out = out + (("bypass", 1.0),)
        return out
    if d["kind"] == "barrier":
        return (("hp", float(d["hp"])), ("radius", float(d["radius"])))
    if d["kind"] == "summon":
        return (("mech", float(d["mech"])), ("count", float(d["count"])),
                ("level", float(d["level"])))
    if d["kind"] == "burn":
        return (("dps", float(d["dps"])), ("radius", float(d["radius"])))
    # step5 area/status skills: every def number lands in the digestable
    # params (任务书 §3: expressiveness must not shrink)
    from ..skills import _area_effect_params
    return tuple(sorted((k, float(v)) for k, v in
                        _area_effect_params(int(sid), d).items()))


def compile_battle_input(state, battle_seed: int = 0) -> BattleInput:
    """PRE_BATTLE EnvironmentState -> frozen BattleInput (digestable)."""
    from ..skills import COMMANDER_SKILLS, CONTRAPTIONS
    units = []
    world_objects = []
    events = []
    side_mods = []
    for side in (0, 1):
        p = state.players[side]
        officers = tuple(int(o) for o in p.officers)
        # towers (world objects)
        for k, lv in enumerate(list(p.tower_strengthen)[:2]):
            tx, ty = TOWER_POS[side][k]
            world_objects.append(WorldObject(
                "tower", side, "tower:%d:%d" % (side, k), subtype=int(lv or 0),
                position=(tx, ty),
                params=(("strengthen", float(lv or 0)),),
                persistent=True, source="tower_strengthen"))
        # round buffs from ActiveEnergyTowerSkill — the transition layer
        # charges the fee at activation (deploy._activate_tower_skill) and
        # allows ONE purchase per id per round (step4 QA#4); here the buff
        # itself is compile-time only
        rng = spd = 0.0
        for sid in p.tower_mods_raw or ():
            if int(sid) == 5:
                rng += 15.0
            elif int(sid) == 6:
                spd += 3.0
        if rng or spd:
            side_mods.append(SideMods(side, rng, spd))
        # contraption devices released this round
        for n, dev in enumerate(p.devices_raw or ()):
            try:
                cid, dx, dy = int(dev[0]), float(dev[1]), float(dev[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not CONTRAPTIONS.get(cid):
                continue        # unmapped (30001): dropped, scanner blocks it
            world_objects.append(WorldObject(
                "device", side, "device:%d:%d" % (cid, n), subtype=cid,
                position=(dx, dy), params=_device_params(cid, officers),
                persistent=False,
                source="ReleaseContraption" + ("/10007" if (cid == 20001
                                                      and 10007 in officers)
                      else ("/10008" if (cid == 10001 and 10008 in officers)
                            else ""))))
        # commander battlefield skills released this round (step5 T0: the
        # TYPED releases keep the full ordered points; legacy states fall
        # back to the flat single-point view). Multi-strike ids (轨道轰炸)
        # expand deterministically; multi-point area/beacon skills stay ONE
        # event with the ordered points so the digest sees the true shape
        from ..skills import RELEASE_POINT_COUNTS
        rels = list(getattr(p, "skill_releases", ()) or ())
        if not rels:
            rels = [(int(r[0]), ((float(r[1]), float(r[2])),))
                    for r in (p.skill_events_raw or ())]
        else:
            rels = [(int(rel.skill_id),
                     tuple((float(x), float(y))
                           for (x, y) in (rel.ordered_positions or ())))
                    for rel in rels]
        for n, (sid, pts) in enumerate(rels):
            d = COMMANDER_SKILLS.get(sid)
            if not d or not pts:
                continue
            if sid in RELEASE_POINT_COUNTS \
                    and len(pts) != RELEASE_POINT_COUNTS[sid]:
                continue    # deploy already rejected it; never reshape here
            if d["kind"] == "strike" and int(d.get("strikes", 1) or 1) > 1:
                from ..skills import expand_strike_events
                for m, sev in enumerate(expand_strike_events(sid, *pts[0])):
                    events.append(TimedEvent(
                        sev["kind"], side,
                        "skill:%d:%d:%d" % (sid, n, m), sid,
                        position=(sev["x"], sev["y"]),
                        params=_skill_event_params(sid, d),
                        source="ReleaseCommanderSkill"))
                continue
            if sid in RELEASE_POINT_COUNTS:
                events.append(TimedEvent(
                    d["kind"], side, "area:%d" % n, sid, position=pts[0],
                    points=pts, params=_skill_event_params(sid, d),
                    source="ReleaseCommanderSkill"))
                continue
            for m, (sx, sy) in enumerate(pts):
                events.append(TimedEvent(
                    d["kind"], side, "skill:%d:%d:%d" % (sid, n, m), sid,
                    position=(sx, sy), params=_skill_event_params(sid, d),
                    source="ReleaseCommanderSkill"))
        # step5 任务书 §4/T6: persistent ground areas carried from earlier
        # rounds (un-ignited 黏油, ttl>=1) re-enter this round's input with
        # their stable ref so the outcome can report ignition per area
        for a in (getattr(p, "ground_areas_raw", ()) or ()):
            try:
                aref, asid = str(a[0]), int(float(a[1]))
                ax, ay, bx, by = (float(a[2]), float(a[3]),
                                  float(a[4]), float(a[5]))
            except (TypeError, ValueError, IndexError):
                continue
            d = COMMANDER_SKILLS.get(asid)
            if not d or d.get("kind") != "oil":
                continue
            events.append(TimedEvent(
                "oil", side, aref, asid, position=(ax, ay),
                points=((ax, ay), (bx, by)),
                params=_skill_event_params(asid, d),
                source="persistent_ground_area"))
        # units (ALL of them: engine-side drops stay the legacy adapter's
        # job; the BattleInput digest reflects the persistent state truth)
        techs_of = {m: list(t) for m, t in p.tech_map}
        spawned = set(int(e) for e in (getattr(p, "spawned_this_round", ())
                                       or ()))
        qt = QT_OFFICER in officers
        for u in p.units:
            # flank teleport (flank.py rules, round >= 2): a unit SPAWNED this
            # round standing in the enemy half materializes over FLANK_DELAY
            # seconds; 快速传送 halves it. Snapshot-carried units never delay.
            spawn_at = 0.0
            if state.round >= 2 and u.entity_id in spawned:
                y = u.y
                if (y > 0.0 if side == 0 else y < 0.0):
                    spawn_at = (FLANK_DELAY / 2.0) if qt else FLANK_DELAY
            units.append(UnitBattleInput(
                entity_id=u.entity_id, side=side, mech_id=u.mech_id,
                level=u.level, exp=u.exp, position=(u.x, u.y),
                rotation=u.is_rotate,
                tech_ids=tuple(techs_of.get(u.mech_id, ())),
                equipment_id=int(u.equipment_id or 0),
                effect_ids=(), spawn_at=spawn_at))
        # buildings
        for c in p.constructions_raw:
            try:
                index, cid = int(c[0]), int(c[1])
                if cid in (1, 2, 3, 4):
                    world_objects.append(WorldObject(
                        "building", side, "bld:%d" % index, subtype=cid,
                        position=(float(c[2]), float(c[3])),
                        params=(), persistent=True,
                        source="constructionSnapshot"))
            except (TypeError, ValueError, IndexError):
                continue
    return BattleInput(
        ruleset_version=state.ruleset_version, engine_version=state.engine_version,
        seed=int(battle_seed), units=tuple(units),
        world_objects=tuple(world_objects), events=tuple(events),
        side_mods=tuple(side_mods),
        officers=(_officers_with_bp_stack(state.players[0].officers),
                  _officers_with_bp_stack(state.players[1].officers)))


def officers_by_side(state) -> tuple:
    """Post-bp-stack officer tuples per side, as consumed by the compile."""
    return tuple(_officers_with_bp_stack(state.players[s].officers)
                 for s in (0, 1))
