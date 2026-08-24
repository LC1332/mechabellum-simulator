# Build compact gamedata.json for the C# engine:
# mechs (stats + main skill + per-level scaling), skills (flattened by id),
# exp table, survive/loot data.
import json, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# decode products (container.json / skills.json, from the game client dump)
# are expected next to this script
T = os.path.dirname(os.path.abspath(__file__))
ct = json.load(open(os.path.join(T, "container.json"), encoding="utf8"))
sk = json.load(open(os.path.join(T, "skills.json"), encoding="utf8"))

# flatten skills by id
skills = {}
for lname, arr in sk.items():
    for s in arr:
        sid = s["id"]
        e = {
            "type": lname,
            "name": s.get("name"),
            "damageRate": s.get("damageRate", 1.0),
            "minRange": s.get("minAttackRange", 0.0),
            "range": s.get("attackRange", 0.0),
            "attackDuration": s.get("attackDuration", 0.0),
            "attackDurationRandomValue": s.get("attackDurationRandomValue", 0.0),
            "splashRange": s.get("splashRange", 0.0),
            "useSelfSplash": s.get("useSelfSplash", False),
            "canAttackGround": s.get("canAttackGround", False),
            "canAttackAir": s.get("canAttackAir", False),
            "isLockTarget": s.get("isLockTarget", False),
            "initialCoolDown": s.get("initialCoolDownTime", 0.0),
            "prepareTime": s.get("prepareTime", 0.0),
            "coolingTime": s.get("coolingTime", 0.0),
            "attackPoint": s.get("attackPoint", 0.0),
            "attackBackswing": s.get("attackBackswing", 0.0),
            "isMelee": s.get("isMeleeAttack", False),
            "damage": s.get("damage", []),
            "weapons": [
                {"skillID": w["skillID"], "defaultAngle": w["defaultAngle"]}
                for w in (s.get("weapons") or [])
            ],
            # '?int_extra' at SkillData+0x48 = weaponCountPerSkill
            # (weapons fired per attack; Normal mode = weapon count)
            "weaponCountPerSkill": max(1, int(s.get("?int_extra") or 1)),
            "bulletSpeed": s.get("bulletSpeed", 0.0),
            "projectileCount": s.get("projectileCount", 1),
            "randomTargetRange": s.get("randomTargetRange", 0.0),
            "attackStrength": s.get("attackStrength", 0),
            "maxLife": s.get("maxLife", []),
            "damageMultiplier": s.get("damageMultiplier", []),
            "unitID": s.get("unitID", 0),
            "maxBatch": s.get("maxBatch", 0),
            "maxCount": s.get("maxCount", 0),
            "createDuration": s.get("createDuration", 0.0),
            "productTime": s.get("productTime", 0.0),
            "createCountPerTime": s.get("createCountPerTime", 0),
            "startTime": s.get("startTime", 0),
            "positions": s.get("positions", []),
            "damageTimes": s.get("damageTimes", 0),
            "sweepLength": s.get("sweepLength", 0),
            "sweepWidth": s.get("sweepWidth", 0),
            "sweepTimes": s.get("sweepTimes", 0),
        }
        skills[sid] = e

# wiki-verified attack capability (base units; test-tier variants inherit)
# value: 0=ground only, 1=air only, 2=air & ground
WIKI_TARGET = {
    1: 0, 2: 2, 3: 0, 4: 0, 5: 0, 6: 2, 7: 2, 8: 0, 9: 2, 10: 0,
    11: 2, 12: 0, 13: 0, 14: 0, 15: 0, 16: 2, 17: 0, 18: 2, 19: 0,
    20: 0, 21: 0, 22: 2, 23: 0, 24: 0, 25: 2, 26: 2, 27: 2, 28: 0,
    29: 2, 30: 0, 31: 0,
}
def wiki_target(mid):
    if mid in WIKI_TARGET:
        return WIKI_TARGET[mid]
    if 51 <= mid <= 73:
        base = {51: 1, 52: 2, 53: 3, 54: 54, 55: 5, 56: 6, 57: 7, 58: 8, 59: 9,
                60: 10, 61: 11, 62: 12, 63: 13, 64: 14, 65: 15, 66: 16, 67: 17,
                68: 18, 69: 19, 70: 20, 71: 21, 72: 22, 73: 23}
        return WIKI_TARGET.get(base.get(mid), 0)
    if mid == 153: return WIKI_TARGET[3]
    if mid == 167: return 0
    if mid == 168: return WIKI_TARGET[18]
    if mid in (2001, 4001): return 0   # 丧钟 Death Knell: ground
    if mid == 2002: return 0           # 泰山 Mountain: ground
    if mid in (1001, 1002): return 0   # summons
    return 0

# mech table
mechs = {}
for m in ct["mechDatas"]:
    mid = m["id"]
    tgt = wiki_target(mid)
    mechs[mid] = {
        "id": mid,
        "name": m.get("name"),
        "life": m.get("life", 0),
        "damage": m.get("damage", 0),
        "moveSpeed": m.get("moveSpeed", 0),
        "isFly": m.get("isFly", False),
        "radius": m.get("radius", 5),
        "attackAngle": m.get("attackAngle", 0),
        "rotateSpeed": m.get("rotateSpeed", 0),
        "mainSkillID": m.get("mainSkillID", 0),
        "attackStrength": m.get("attackStrength", 0),
        "sizeScale": m.get("sizeScale", 1),
        "canAttackAir": tgt >= 1,
        "canAttackGround": tgt != 1,
    }

# card table (multi-module deployment: mechCount units per card)
cards = {}
for c in ct["cardDatas"]:
    mid = c.get("mechID", 0)
    cards[mid] = {
        "mechID": mid,
        "name": c.get("name"),
        "mechCount": c.get("mechCount", 1),
        "baseMoney": c.get("baseMoney", 0),
        "slotSize": c.get("slotSize", 0),
        "cardBaseSize": c.get("cardBaseSize", [0, 0]),
        "maintenanceSupply": c.get("maintenanceSupply", 0),
        "unlockPrice": c.get("unlockPrice", 0),
        "canBeSold": c.get("canBeSold", False),
        "canAddEquipment": c.get("canAddEquipment", False),
        "specialUnit": c.get("specialUnit", False),
        "group": c.get("group", 0),
        "sort": c.get("sort", 0),
        "technologies": c.get("technologies", []),
        "defaultTechnologies": c.get("defaultTechnologies", []),
    }

# exp table by mech id
exps = {}
for e in ct["mechExpDatas"]:
    exps[e["id"]] = {k: e.get(k, 0) for k in (
        "lootExpLv1", "lootExpLv2", "lootExpLv3", "lootExpLv4", "lootExpLv5",
        "lootExpLv6", "lootExpLv7", "lootExpLv8", "lootExpLv9",
        "upgradeLv2", "upgradeLv3", "upgradeLv4", "upgradeLv5",
        "upgradeLv6", "upgradeLv7", "upgradeLv8", "upgradeLv9",
        "upgradeSupply", "score")}

# tech main table (step5: engine-bakeable modifiers; sub-tables iterated later)
tj = json.load(open(T + r"\tech.json", encoding="utf8"))
techs = {}
for e in tj["technologyDatas"]:
    techs[e["id"]] = {
        "id": e["id"],
        "name": e.get("name"),
        "description": e.get("description"),
        "descParams": e.get("descParams"),
        "targetSkillID": e.get("targetSkillID", 0),
        "previousTechID": e.get("previousTechID", 0),
        "supply": e.get("supply", 0),
        "unlockCost": e.get("unlockCost", 0),
        "mainSkillEffect": e.get("mainSkillEffect", False),
        "lifeChangeRate": e.get("lifeChangeRate") or [],
        "damageChangeRate": e.get("damageChangeRate") or [],
        "speedChangeValue": e.get("speedChangeValue") or [],
        "minAttackRangeChangeValue": e.get("minAttackRangeChangeValue") or [],
        "attackRangeChangeValue": e.get("attackRangeChangeValue") or [],
        "attackRangeChangeRate": e.get("attackRangeChangeRate") or [],
        "attackIntervalChangeValue": e.get("attackIntervalChangeValue") or [],
        "attackIntervalChangeRate": e.get("attackIntervalChangeRate") or [],
        "splashRangeChangeValue": e.get("splashRangeChangeValue") or [],
        "projectileSpeedChangeValue": e.get("projectileSpeedChangeValue") or [],
    }

# step12 buildings: Construction table decoded from level0 ConfigDataContainer
# (tools/step12_decode3.py, byte-verified against wiki HP/exp/reload values).
# Snapshot construction IDs 1-4 are the player-deployable base types; the
# player variants are table rows 9 (wall: 1446/module, durability 2),
# 7/8 (cannons: radius 5, durability 1), 4 (magnet: 10 modules x 300).
ctab = json.load(open(T + r"\step12_constructionDatas.json", encoding="utf8"))
def bld_row(tid):
    r = ctab[str(tid)]
    dp = (r.get("descParams") or "").split(";")
    return {
        "tableId": tid,
        "name": r["name"],
        "life": r["maxLife"],            # per module
        "damage": r["damage"],
        "exp": r["exp"],
        "count": r["count"],             # modules per placement
        "radius": r["radius"],
        "skillId": r["skillID"],
        "durability": r["durability"] if r["hasDurability"] else 1,
        "blockWidth": r["blockWidth"],
        "space": r["space"],
        "reloadShots": int(dp[0]) if len(dp) == 2 else 0,
        "reloadTime": float(dp[1]) if len(dp) == 2 else 0.0,
        "selfDestruct": float(dp[0]) if len(dp) == 1 and dp[0] else 0.0,  # magnet 5s
    }
buildings = {
    "byCid": {str(c): bld_row(t) for c, t in ((1, 9), (2, 7), (3, 8), (4, 4))},
    "table": ctab,
}

out = {"mechs": mechs, "skills": skills, "cards": cards, "exps": exps,
       "techs": techs, "buildings": buildings,
       "levelScale": {str(k): v for k, v in
                      ((d.get("level"), {"life": d.get("lifeRating"), "damage": d.get("damageRating")})
                       for d in ct["attributeUpgradeDatas"]) if k}}

_out = os.path.join(os.path.dirname(T), "data", "gamedata.json")
json.dump(out, open(_out, "w", encoding="utf8"),
          ensure_ascii=False)
print(f"gamedata.json: {len(mechs)} mechs, {len(skills)} skills, {len(cards)} cards, {len(exps)} exp tables, {len(techs)} techs, {len(buildings['byCid'])} buildings")
# quick sanity
m1 = mechs.get(3)
print("Vulkan(id3):", m1)
print("skill 3001 range/dur:", skills[3001]["range"], skills[3001]["attackDuration"], "bullet", skills[3001].get("bulletSpeed"))
print("crawler card:", cards.get(10))
print("crawler radius:", mechs.get(10, {}).get("radius"), "fang radius:", mechs.get(9, {}).get("radius"),
      "hound radius:", mechs.get(28, {}).get("radius"))
for c, b in buildings["byCid"].items():
    print("bld cid%s -> table%d %s life=%d dmg=%d cnt=%d reload=%s/%s selfD=%s" % (
        c, b["tableId"], b["name"], b["life"], b["damage"], b["count"],
        b["reloadShots"], b["reloadTime"], b["selfDestruct"]))
