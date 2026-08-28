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
             "damage": 70000.0, "splash": 100.0, "t": 15.0,
             "ff": True,
             "conf": "step4 P1: 70000 at t=15s (user table); step5 QA-2 "
                     "user-frozen radius 100 (was cal 40); ff per QA#6"},
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
    # ---- step5 任务书 §2.1 frozen rules + §12 QA answers (user, 2026-08-28).
    # Geometry law shared by the swept skills: from the first point's r=30
    # circle to the second point's r=30 circle = capsule(A, B, 30). A live
    # barrier covering the ground at drop time clips generation permanently
    # (盾后消失不补生成). Numbers marked cal stay provisional until the
    # Windows oracle A/B lands - they NEVER claim verified.
    400002: {"kind": "oil", "name": "黏油弹",
             "radius": 30.0, "slow_mult": 0.45, "ttl_rounds": 2,
             "shield_block": True, "layers": "ground",
             "conf": "step5§2.1: capsule r30, enemy ground speed x0.45, "
                     "unlit oil lasts 2 battles, ignite->flame (gone at "
                     "fight end); 鬼鳐/火神 tech oils do NOT persist"},
    600002: {"kind": "smoke", "name": "烟雾弹",
             "radius": 30.0, "range_mult": 0.65, "shield_block": True,
             "conf": "step5 QA-2 user-frozen: capsule r30, enemy range "
                     "x0.65; duration/air/stacking cal (whole-battle v1)"},
    500002: {"kind": "acid", "name": "酸液弹",
             "radius": 30.0, "pct_dps": 0.03, "vuln_mult": 2.5,
             "shield_block": True,
             "conf": "step5 QA-2 user-frozen: 3% maxHP/s + damage taken "
                     "x2.5 while inside; tick/duration/air cal"},
    200001: {"kind": "emp", "name": "电磁冲击",
             "radius": 60.0, "shield_damage": 20000.0, "duration": 25.0,
             "slow_mult": 0.60,
             "conf": "step5§2.1 user-frozen: r60, 20000 to shields, "
                     "unprotected units tech-disable + speed x0.60 for 25s; "
                     "shield-covered ground units immune"},
    200002: {"kind": "emp", "name": "巨型电磁冲击",
             "radius": 130.0, "shield_damage": 20000.0, "duration": 25.0,
             "slow_mult": 0.60,
             "conf": "step5 QA-2 user-frozen: r130 (only radius differs "
                     "from 200001; same effect spec)"},
    200003: {"kind": "photon", "name": "光子投射",
             "radius": 30.0, "duration": 20.0, "dmg_taken_mult": 0.70,
             "conf": "step5§2.1+QA-4 user-frozen: friendly 20s photon, "
                     "damage taken x0.70, immune+clears EMP/引燃/酸液/退化"
                     "光束; shape assumed capsule r30 (cal)"},
    300005: {"kind": "storm", "name": "闪电风暴",
             "radius": 130.0, "duration": 12.0, "interval": 0.8,
             "damage": 800.0, "splash": 8.0, "slow_mult": 0.60,
             "slow_duration": 1.0,
             "conf": "step5 QA-2 user-frozen r130 ONLY; provisional v1 = "
                     "seeded random enemy unit inside the circle per strike "
                     "(duration/damage/slow cal; strict-effect stays blocked "
                     "until the storm oracle)"},
    300006: {"kind": "ion", "name": "离子轰炸",
             "radius": 20.0, "speed": 25.0, "dps": 600.0,
             "conf": "step5§2.1 user-frozen: moving circle r20 swept A->B; "
                     "speed/dps/tick/ff cal"},
    1500001: {"kind": "beacon", "name": "移动信标",
              "radius": 40.0,
              "conf": "step5§2.1+QA-2 user-frozen: 3 ordered points (A "
                      "select r40 / B / C), member-level offsets, stop-to-"
                      "attack; walls+cannon buildings unaffected, both ids "
                      "identical"},
    1500002: {"kind": "beacon", "name": "移动信标",
              "radius": 40.0,
              "conf": "step5 QA-2 user ruling: same effect as 1500001 "
                      "(增援卡 variant)"},
    # deliberately NOT mapped (real effect unimplemented — precise blockers,
    # never a wrong approximation):
    #   1000001 再部署 redeploy -> TRANSITION_SKILLS (transition-only)
    #   1200006+ 移动信标 variants, 900001 supply family (900001 unit/建设
    #   recycle both route through typed sell/release paths)
    # step5 residual blockers: contraption 30001 (identity unknown — QA-7
    # "不太清楚", stays a precise blocker) and unresolved ID=0 releases
    # (QA-2: commander skill 0 identity unknown — slot table decides, never
    # a global SkillIndex=0 guess).
}

# step5 任务书 §3 T0: ordered-position count per multi-point release. One
# release with the wrong count rejects precisely (deploy receipt) instead of
# silently re-shaping the skill geometry.
RELEASE_POINT_COUNTS = {400002: 2, 500002: 2, 600002: 2,
                        1500001: 3, 1500002: 3}

# swept-capsule battlefield skills (shape shared with the burn migration)
CAPSULE_SKILLS = frozenset(RELEASE_POINT_COUNTS) - {1500001, 1500002}


def persistent_area_release(sid: int) -> bool:
    """step5 任务书 §6/T6: releases whose ground area OUTLIVES the battle.
    Only 黏油 400002 carries across rounds (ttl=2 battles); the tech oils
    (鬼鳐/火神) are not commander releases and never enter this path."""
    return int(sid) == 400002

# transition-layer commander skills (no battle event): 1100001 强化训练
# jumps the target unit's exp to its next upgrade threshold (deploy.py);
# 1000001 再部署 unlocks one locked unit's move this round (step4 任务书
# §1.3 — per-slot once per round, cd=1, next round usable again);
# 900001 战地回收 is transition-only too — UNIT targets are the typed sell
# path, CONSTRUCTION targets refund through deploy._recycle_construction
# (step5 任务书 §5 T2: wall 50 / cannons 100, user-frozen 2026-08-28).
TRANSITION_SKILLS = {
    1100001: {"name": "强化训练", "target_kind": "unit"},
    1000001: {"name": "再部署", "target_kind": "unit"},
    900001: {"name": "战地回收", "target_kind": "construction_or_unit"},
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
            if sid in RELEASE_POINT_COUNTS:
                # step5 T0: multi-point releases stay ONE event with the
                # ordered points (beacon 3 / capsule 2) — never expanded
                ev = {"kind": d["kind"], "x": spots[0][0], "y": spots[0][1],
                      "name": d["name"], "id": sid,
                      "points": [(x, y) for (x, y) in spots]}
                ev.update(_area_effect_params(sid, d))
                out.append(ev)
                continue
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


def _area_effect_params(sid: int, d) -> dict:
    """step5 area/status skills -> engine event params (single source with
    the battlefield compile; numbers carry their provenance in skills.py)."""
    ev = {"radius": float(d["radius"])}
    k = d["kind"]
    if k == "oil":
        ev["slow_mult"] = float(d["slow_mult"])
        ev["ttl_rounds"] = int(d.get("ttl_rounds", 2))
    elif k == "smoke":
        ev["range_mult"] = float(d["range_mult"])
    elif k == "acid":
        ev["pct_dps"] = float(d["pct_dps"])
        ev["vuln_mult"] = float(d["vuln_mult"])
    elif k == "emp":
        ev["shield_damage"] = float(d["shield_damage"])
        ev["duration"] = float(d["duration"])
        ev["slow_mult"] = float(d["slow_mult"])
    elif k == "photon":
        ev["duration"] = float(d["duration"])
        ev["dmg_taken_mult"] = float(d["dmg_taken_mult"])
    elif k == "storm":
        for f in ("duration", "interval", "damage", "splash", "slow_mult",
                  "slow_duration"):
            ev[f] = float(d[f])
    elif k == "ion":
        ev["speed"] = float(d["speed"])
        ev["dps"] = float(d["dps"])
    elif k == "beacon":
        pass                    # radius IS the selection radius
    if d.get("shield_block"):
        ev["shield_block"] = True
    return ev


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
        elif d["kind"] == "burn":
            ev["params"] = {"dps": d["dps"], "radius": d["radius"]}
        elif sid in RELEASE_POINT_COUNTS:
            ev["points"] = RELEASE_POINT_COUNTS[sid]
            ev["params"] = _area_effect_params(sid, d)
        out.append(ev)
    return out
