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
    # step3 任务书 §5.1 frozen mapping (2026-08-27 user ruling; fixes the
    # step15 misattribution): 200001 is EMP (NOT 燃烧弹) and 1000001 is
    # 再部署 (NOT a summon) — both stay unmapped until their real effects
    # are implemented, so a wrong approximation cannot leak into battles.
    # 燃烧弹 is 100002; the summons are 1200001/1200003.
    # 导弹打击 family. 300001 is the base strike (620 uses); the P1
    # variants (step4 任务书 §7.1, user-frozen numbers 2026-08-27) join it:
    # 300003 轨道轰炸 (15 x 2500 multi-strike), 300004 核弹 (t=15s, 70000),
    # 300007 轨道标枪 (r30, 70000, bypasses barriers). Distribution/timing
    # details stay cal — confidence provisional.
    300001: {"kind": "strike", "name": "导弹打击",
             "damage": 3000.0, "splash": 20.0,
             "conf": "wiki(1 missile 3000)+fit(cast 8-34m from enemies)"},
    300003: {"kind": "strike", "name": "轨道轰炸",
             "damage": 2500.0, "splash": 20.0, "strikes": 15,
             "ff": True,
             "conf": "step4 P1: 15x2500 (user table); spread pattern cal "
                     "(sunflower, deterministic); friendly fire per QA#6"},
    300004: {"kind": "strike", "name": "核弹",
             "damage": 70000.0, "splash": 40.0, "t": 15.0,
             "ff": True,
             "conf": "step4 P1: 70000 at t=15s (user table); splash 40 cal; "
                     "friendly fire per QA#6"},
    300007: {"kind": "strike", "name": "轨道标枪",
             "damage": 70000.0, "splash": 30.0,
             "bypass": True,
             "conf": "step4 P1: r30 70000 bypass barriers (user table+QA#6)"},
    # 燃烧弹 (burning ground patch; previously mis-filed under 200001).
    # step4 QA#5 ruling (2026-08-27): the commander-skill napalm is a
    # STRAIGHT firewall at 270/s (survey table correct); pysim still models
    # a circular patch until the P2 TimedAreaEffect/line framework lands —
    # dps corrected 352 -> 270, shape stays an approximation (provisional).
    100002: {"kind": "burn", "name": "燃烧弹",
             "dps": 270.0, "radius": 15.0,
             "conf": "step4 QA#5: line firewall 270/s (survey); circle "
                     "approx until P2 area framework; duration cal"},
    800001: {"kind": "barrier", "name": "空投护盾",
             "hp": 50000.0, "radius": BARRIER_RADIUS_DEFAULT,
             "conf": "wiki(50000)+fit(own-side casts 130-210m, r2+)"},
    # CSD summons (step3 id fix: previously mis-filed as 1000001/100002).
    # 1200001 地底威胁 = crawler eruption (24x), 1200003 呼叫机群 = wasp
    # swarm (12x); mech/count stay cal until calibration games.
    # step4 P1 single-unit airdrops (user table 2026-08-27): 1200002 犀牛
    # (mech 5), 1200004 霸主 (mech 11), 1200005 火神 (mech 3) — battle-only
    # summons, never persistent units.
    1200001: {"kind": "summon", "name": "地底威胁",
              "mech": 10, "count": 24, "level": 1,
              "conf": "cal(mech/count provisional; crawler card = 24x)"
                      "+id_fix(step3)"},
    1200002: {"kind": "summon", "name": "犀牛来袭",
              "mech": 5, "count": 1, "level": 1,
              "conf": "step4 P1: airdrop 1 犀牛 (mech 5, user table)"},
    1200003: {"kind": "summon", "name": "呼叫机群",
              "mech": 6, "count": 12, "level": 1,
              "conf": "cal(mech/count provisional; wasp card = 12x HP311)"
                      "+id_fix(step3)"},
    1200004: {"kind": "summon", "name": "呼叫战舰",
              "mech": 11, "count": 1, "level": 1,
              "conf": "step4 P1: airdrop 1 霸主 (mech 11, user table)"},
    1200005: {"kind": "summon", "name": "天降火神",
              "mech": 3, "count": 1, "level": 1,
              "conf": "step4 P1: airdrop 1 火神 (mech 3, user table)"},
    # deliberately NOT mapped (real effect unimplemented — precise blockers,
    # never a wrong approximation):
    #   200001/200002 EMP 电磁冲击 (was wrongly burning ground in step15)
    #   1000001 再部署 redeploy -> TRANSITION_SKILLS (transition-only)
    #   400002 黏油弹 (oil, r7+), 1200006+ 移动信标 variants,
    #   15000xx WayPoint, 900001 supply family, 300005/300006 (P2 areas),
    #   200003 光子投射, 500002 酸液弹, 600002 烟雾弹, 1500002 移动信标
}

# transition-layer commander skills (no battle event): 1100001 强化训练
# jumps the target unit's exp to its next upgrade threshold (deploy.py);
# 1000001 再部署 unlocks one locked unit's move this round (step4 任务书
# §1.3 — per-slot once per round, cd=1, next round usable again).
TRANSITION_SKILLS = {
    1100001: {"name": "强化训练", "target_kind": "unit"},
    1000001: {"name": "再部署", "target_kind": "unit"},
}


def commander_skill_target_kind(sid: int) -> str:
    """UI-facing target shape: position (map落点) / unit / unknown."""
    d = COMMANDER_SKILLS.get(int(sid))
    if d:
        return "position"
    return TRANSITION_SKILLS.get(int(sid), {}).get("target_kind", "unknown")


def _first_pos(entry):
    ps = entry.get("positions") or []
    if ps:
        return float(ps[0][0]), float(ps[0][1])
    return float(entry.get("x", 0.0) or 0.0), float(entry.get("y", 0.0) or 0.0)


# deterministic multi-strike spread (step4 任务书 §7.1: 落点分布进入
# seed/digest): sunflower pattern — radius grows with sqrt(i), golden-angle
# azimuth; pure function of (sid, x, y) so the BattleInput digest is stable
# across runs and rebuilds.
_SPREAD_STEP = 11.0
_GOLDEN_ANGLE = 2.399963229728653


def spread_offsets(sid: int, n: int):
    """Deterministic impact offsets [(dx, dy), ...] around one release."""
    import math
    out = [(0.0, 0.0)]
    for i in range(1, max(1, n)):
        r = _SPREAD_STEP * math.sqrt(i)
        a = i * _GOLDEN_ANGLE + (int(sid) % 7) * 0.37
        out.append((r * math.cos(a), r * math.sin(a)))
    return out[:max(1, n)]


def expand_strike_events(sid: int, x: float, y: float):
    """One strike-skill release at (x, y) -> engine strike event dicts
    (multi-strike skills expand to one event per impact; single-strike ids
    stay one event). Deterministic; consumed by battlefield/compiler so the
    full落点 distribution lands in the digestable BattleInput."""
    d = COMMANDER_SKILLS.get(int(sid))
    if not d or d["kind"] != "strike":
        return []
    n = int(d.get("strikes", 1) or 1)
    base = {"kind": "strike", "name": d["name"], "id": int(sid),
            "damage": float(d["damage"]), "splash": float(d["splash"]),
            "t": float(d.get("t", 0.0) or 0.0)}
    if d.get("ff"):
        base["ff"] = True
    if d.get("bypass"):
        base["bypass"] = True
    out = []
    for (dx, dy) in spread_offsets(sid, n):
        ev = dict(base)
        ev["x"] = float(x) + dx
        ev["y"] = float(y) + dy
        out.append(ev)
    return out


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
            sid = int(a.get("id", 0) or 0)
            d = COMMANDER_SKILLS.get(sid)
            if not d:
                continue
            ps = a.get("positions") or []
            spots = [(float(p[0]), float(p[1])) for p in ps] or [_first_pos(a)]
            for x, y in spots:
                if d["kind"] == "strike":
                    # multi-strike ids expand deterministically (step4 §7.1)
                    out.extend(expand_strike_events(sid, x, y))
                    continue
                ev = {"kind": d["kind"], "x": x, "y": y, "name": d["name"],
                      "id": sid}
                if d["kind"] == "barrier":
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
            ev["params"] = {"damage": d["damage"], "splash": d["splash"],
                            "t": d.get("t", 0.0), "strikes": d.get("strikes", 1),
                            "ff": bool(d.get("ff")),
                            "bypass": bool(d.get("bypass"))}
        elif d["kind"] == "barrier":
            ev["params"] = {"hp": d["hp"], "radius": d["radius"]}
        elif d["kind"] == "summon":
            ev["params"] = {"mech": d["mech"], "count": d["count"],
                            "level": d["level"]}
        out.append(ev)
    return out
