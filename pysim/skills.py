# step8-B battlefield skills: definitions + replay skill_actions ->
# engine skill events.
#
# Two release channels, both observed 100% pre-fight in the corpus
# (LocalTime <= prepareTime+deployTime = 130s, tools/step8_probe6 - so every
# effect lands at battle t=0; no mid-fight scheduler needed for v1):
#   ReleaseContraption    {ContraptionID, Position}      -> devices (装置)
#   ReleaseCommanderSkill {ID|SkillIndex, Positions,...} -> card skills
#
# Numeric values are SERVER-SIDE (not in container.json); the table below
# mixes wiki.mbxmas.com values with corpus-calibrated evidence. Every entry
# carries its provenance; "cal" = awaiting user calibration games (Q1).
#
# ID family law (corpus-fitted, matches dump.cs CSD_* order):
#   ID = family*100000 + n; 2=Fire 3=Damage 4=Oil 5=Fog 6=Acid 8=Shield
#   10=Unit(summon) 11=AddExp 12=MoveUnit 15=WayPoint
# Confirmed anchors: 400002 oil = late-game only (user + r7+ distribution),
# 30000x strikes land 8-34m from nearest enemy, 800001 casts 130-210m
# (own side). Contraptions: 10001 = 飞弹 (r7+ A/B + max-8/round cadence),
# 20001 = 护盾装置 (early r1-2 usage, ~1/round, deep backfield placement).
from .deploy import (DEVICE_BARRIER, DEVICE_MISSILE, BARRIER_RADIUS,
                     TURRET_RADIUS, TURRET_HP)

# contraption 10001 = 飞弹/哨兵导弹 (sentry missile turret). Mapping decided
# by the r7+ A/B (2026-08-20): with 10001=turret / 20001=barrier the device
# harm vanishes (r7+ 58.0% = no-devices baseline, vs 55.2% the other way
# round). Supporting evidence: 10001 releases up to 8/round = the wiki
# "max 8 sentry missile devices" cap; the 100-cost shield stays ~1/round.
# The earlier reading (20001=missile via early/deep placement) is REJECTED.
# Damage 5000/missile (wiki 0.6.177: 6000 -> 5000; hit slows 14s - NOT
# modeled in v1). Range / cadence / HP unknown -> cal.
SENTRY_MISSILE = {
    "damage": 5000.0,
    "range": 100.0,        # cal
    "cooling": 5.0,        # cal
    "prepare": 0.5,        # cal
    "attack_duration": 1.0,
    "bullet_speed": 80.0,  # cal
    "splash": 0.0,
    "radius": TURRET_RADIUS,
    "hp": TURRET_HP,       # cal (targetable device; unknown real HP)
}

# barriers: 空投护盾 (commander skill 800001, 50000 HP) and 护盾装置
# (contraption 20001, 60000 HP). Absorbs damage dealt to covered allies
# until depleted (redirect model; wiki: blocks battlefield skills except
# orbital javelin).
BARRIER_RADIUS_DEFAULT = BARRIER_RADIUS

CONTRAPTIONS = {
    10001: {"kind": "turret", "name": "飞弹", "def": SENTRY_MISSILE,
            "conf": "wiki(dmg)+r7+AB-mapping+cal(range/cd/hp)"},
    20001: {"kind": "barrier", "name": "护盾装置", "hp": 60000.0,
            "radius": BARRIER_RADIUS_DEFAULT,
            "conf": "wiki(60000)+r7+AB-mapping"},
    30001: None,   # unknown device, 19 uses, late rounds - unmapped (cal)
}

COMMANDER_SKILLS = {
    # 导弹打击 family. 300001 is the base strike (620 uses); variants
    # 300003..300007 (leveled/multi-missile?) stay unmapped until calibration
    # so a wrong guess cannot pollute the A/B.
    300001: {"kind": "strike", "name": "导弹打击",
             "damage": 3000.0, "splash": 20.0,
             "conf": "wiki(1 missile 3000)+fit(cast 8-34m from enemies)"},
    # step15: 燃烧弹 - burning ground patch. DPS from a Reddit test thread
    # ("~350/s, 88 per 0.25s tick"); radius/duration are cal (patch assumed
    # to burn the whole fight, re-ignited per cast).
    200001: {"kind": "burn", "name": "燃烧弹",
             "dps": 352.0, "radius": 15.0,
             "conf": "reddit(88dmg/0.25s)+cal(radius/duration)"},
    800001: {"kind": "barrier", "name": "空投护盾",
             "hp": 50000.0, "radius": BARRIER_RADIUS_DEFAULT,
             "conf": "wiki(50000)+fit(own-side casts 130-210m, r2+)"},
    # CSD_Unit summons. 1000001 always carries unitIndex + backfield
    # positions, 100002 casts land near enemies (33-73m) - wasp swarm vs
    # crawler eruption reading; both flagged cal (flip on calibration games).
    1000001: {"kind": "summon", "name": "呼叫机群",
              "mech": 6, "count": 12, "level": 1,
              "conf": "cal(mech/count provisional; wasp card = 12x HP311)"},
    100002: {"kind": "summon", "name": "地底威胁",
              "mech": 10, "count": 24, "level": 1,
              "conf": "cal(mech/count provisional; crawler card = 24x)"},
    # deliberately NOT mapped (user: late tech / low value for v1):
    #   400002 黏油弹 (oil, r7+), 20000x 燃烧弹, 12000xx 移动信标,
    #   15000xx WayPoint, 900001 (40% of casts, supply family, unknown -
    #   no construction-durability correlation, needs calibration)
}


def _first_pos(entry):
    ps = entry.get("positions") or []
    if ps:
        return float(ps[0][0]), float(ps[0][1])
    return float(entry.get("x", 0.0) or 0.0), float(entry.get("y", 0.0) or 0.0)


def events_from_skill_actions(actions):
    """Normalized pN.skill_actions -> engine skill events
    [{kind, x, y, ...params}] (team implied by the input list). Unmapped
    ids are dropped. Multiple positions on one release -> one event per
    position (strike/summon repeat per落点)."""
    out = []
    for a in actions or []:
        if a.get("type") == "contraption":
            d = CONTRAPTIONS.get(int(a.get("id", 0) or 0))
            if not d:
                continue
            x, y = float(a.get("x", 0.0) or 0.0), float(a.get("y", 0.0) or 0.0)
            ev = {"kind": d["kind"], "x": x, "y": y, "name": d["name"],
                  "id": int(a.get("id"))}
            if d["kind"] == "turret":
                ev.update(d["def"])
            else:
                ev["hp"] = d["hp"]
                ev["radius"] = d["radius"]
            out.append(ev)
        elif a.get("type") == "commander":
            d = COMMANDER_SKILLS.get(int(a.get("id", 0) or 0))
            if not d:
                continue
            ps = a.get("positions") or []
            spots = [(float(p[0]), float(p[1])) for p in ps] or [_first_pos(a)]
            for x, y in spots:
                ev = {"kind": d["kind"], "x": x, "y": y, "name": d["name"],
                      "id": int(a.get("id"))}
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
                out.append(ev)
    return out


def battle_skill_catalog():
    """UI catalog for the sandbox: one entry per placeable skill, carrying
    the engine event params so the frontend can build complete events."""
    out = []
    for cid, d in CONTRAPTIONS.items():
        if not d:
            continue
        ev = {"channel": "contraption", "id": cid, "name": d["name"],
              "kind": d["kind"], "conf": d["conf"]}
        ev["params"] = dict(d["def"]) if d["kind"] == "turret" \
            else {"hp": d["hp"], "radius": d["radius"]}
        out.append(ev)
    for sid, d in COMMANDER_SKILLS.items():
        ev = {"channel": "commander", "id": sid, "name": d["name"],
              "kind": d["kind"], "conf": d["conf"]}
        if d["kind"] == "strike":
            ev["params"] = {"damage": d["damage"], "splash": d["splash"]}
        elif d["kind"] == "barrier":
            ev["params"] = {"hp": d["hp"], "radius": d["radius"]}
        elif d["kind"] == "summon":
            ev["params"] = {"mech": d["mech"], "count": d["count"],
                            "level": d["level"]}
        out.append(ev)
    return out
