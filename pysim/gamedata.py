# GameData loader: gamedata.json -> typed dicts with int keys.
# Mirrors engine/Config.cs, plus cardDatas (multi-module deployment)
# and technologyDatas (step5 bakeable modifiers).
import json


def tech_pick(values, level):
    """Pick the per-level element of a tech Change list (1-indexed, capped)."""
    if not values:
        return 0.0
    return float(values[min(level, len(values)) - 1])


class TechDef:
    __slots__ = ("id", "name", "description", "desc_params", "target_skill_id",
                 "previous_tech_id", "supply", "unlock_cost", "main_skill_effect",
                 "life_rate", "dmg_rate", "speed_val", "min_range_val",
                 "range_rate", "range_val", "interval_rate", "interval_val",
                 "splash_val", "bullet_val", "family",
                 # step19 behavior-tech payload (tools/step19_mkgd.py merge):
                 # skillID / buffID / delay / deadLineValue / explosion* etc,
                 # plus merged buffDatas entry under extra["buff"]
                 "extra",
                 # step16 sub-table fields (verbatim tech.json names)
                 "air_rate", "gnd_rate",          # layer damage rates
                 "aa_off", "gnd_off",             # targeting score offsets (m)
                 "grant_air",                     # airAttackTechnologyDatas
                 "armor_val",                     # flat per-hit damage reduction
                 "regen_rate",                    # fraction of maxHP per second
                 "lifesteal",                     # heal fraction of damage dealt
                 "splash_add",                    # splash family 'range' field
                 "multi_count",                   # extra targets per attack
                 "sec_dmg", "sec_rng",            # on-hit secondary splash
                 # step21 T2: missile HP rate (projectileLifeChangeRate,
                 # 10912 重型导弹 [2.0] — merged as "proj_life" by step21_mkgd)
                 "proj_life_rate")


class MechDef:
    __slots__ = ("id", "name", "life", "damage", "move_speed", "is_fly", "radius",
                 "attack_angle", "rotate_speed", "main_skill_id", "attack_strength",
                 "can_attack_air", "can_attack_ground")


class SkillDef:
    __slots__ = ("type", "name", "damage_rate", "min_range", "range", "attack_duration",
                 "attack_duration_rnd", "splash_range", "use_self_splash",
                 "can_attack_ground", "can_attack_air", "is_lock_target", "is_melee",
                 "initial_cool_down", "prepare_time", "cooling_time", "attack_point",
                 "attack_backswing", "bullet_speed", "projectile_count",
                 "random_target_range", "attack_strength", "damage", "max_life",
                 "damage_multiplier", "unit_id", "max_batch", "max_count",
                 "create_count_per_time", "start_time", "create_duration",
                 "product_time", "positions", "damage_times", "sweep_length",
                 "sweep_width", "sweep_times", "weapon_count", "weapon_ids")


class ExpDef:
    # loot_exp[lv] = exp for killing a level-lv unit of this mech
    # upgrade_at[lv] = exp needed to reach level lv (2..9)
    __slots__ = ("loot_exp", "upgrade_at")


class CardDef:
    __slots__ = ("mech_id", "name", "mech_count", "base_money", "slot_size",
                 "card_base_size", "maintenance_supply", "unlock_price",
                 "can_be_sold", "can_add_equipment", "special_unit", "group",
                 "sort", "technologies", "default_technologies")


class BuildingDef:
    # step12 battlefield construction (snapshot cid 1-4); values from the
    # Construction table decode (tools/step12_decode3.py, player rows 9/7/8/4)
    __slots__ = ("cid", "table_id", "name", "life", "damage", "exp", "count",
                 "radius", "skill_id", "durability", "block_width", "space",
                 "reload_shots", "reload_time", "self_destruct")


class OfficerDef:
    # step14 officer table (level0 decode, tools/step14_offdecode/offfull.py):
    # global per-player modifiers. target = UnitEffectTargetType (All/Air/
    # Ground/Melee/Ranged/Custom=unitIds); mods keys: life/dmg (rates),
    # rngV/rngR/intV/intR/splash/minRng (values/rates), speed (flat m/s).
    # cmdSkills = commander skills granted (released via skill_actions).
    __slots__ = ("id", "name", "target", "unit_ids", "mods", "cmd_skills",
                 "active_round")


class GameData:
    def __init__(self, path):
        root = json.load(open(path, encoding="utf8"))
        self.mechs = {}
        for k, m in root["mechs"].items():
            d = MechDef()
            d.id = int(m["id"])
            d.name = m.get("name")
            d.life = float(m.get("life", 0))
            d.damage = float(m.get("damage", 0))
            d.move_speed = float(m.get("moveSpeed", 0))
            d.is_fly = bool(m.get("isFly", False))
            d.radius = float(m.get("radius", 5))
            d.attack_angle = float(m.get("attackAngle", 0))
            d.rotate_speed = float(m.get("rotateSpeed", 0))
            d.main_skill_id = int(m.get("mainSkillID", 0))
            d.attack_strength = int(m.get("attackStrength", 0))
            d.can_attack_air = bool(m.get("canAttackAir", False))
            d.can_attack_ground = bool(m.get("canAttackGround", True))
            self.mechs[d.id] = d

        self.skills = {}
        for k, s in root["skills"].items():
            d = SkillDef()
            sid = int(k)
            d.type = s.get("type")
            d.name = s.get("name")
            d.damage_rate = float(s.get("damageRate", 1.0))
            d.min_range = float(s.get("minRange", 0))
            d.range = float(s.get("range", 0))
            d.attack_duration = float(s.get("attackDuration", 0))
            d.attack_duration_rnd = float(s.get("attackDurationRandomValue", 0))
            d.splash_range = float(s.get("splashRange", 0))
            d.use_self_splash = bool(s.get("useSelfSplash", False))
            d.can_attack_ground = bool(s.get("canAttackGround", False))
            d.can_attack_air = bool(s.get("canAttackAir", False))
            d.is_lock_target = bool(s.get("isLockTarget", False))
            d.is_melee = bool(s.get("isMelee", False))
            d.initial_cool_down = float(s.get("initialCoolDown", 0))
            d.prepare_time = float(s.get("prepareTime", 0))
            d.cooling_time = float(s.get("coolingTime", 0))
            d.attack_point = float(s.get("attackPoint", 0))
            d.attack_backswing = float(s.get("attackBackswing", 0))
            d.bullet_speed = float(s.get("bulletSpeed", 0))
            d.projectile_count = int(s.get("projectileCount", 1))
            d.random_target_range = float(s.get("randomTargetRange", 0))
            d.attack_strength = int(s.get("attackStrength", 0))
            d.damage = s.get("damage") or []
            d.max_life = s.get("maxLife") or []
            d.damage_multiplier = s.get("damageMultiplier") or []
            d.unit_id = int(s.get("unitID", 0))
            d.max_batch = int(s.get("maxBatch", 0))
            d.max_count = int(s.get("maxCount", 0))
            d.create_count_per_time = int(s.get("createCountPerTime", 0))
            d.start_time = int(s.get("startTime", 0))
            d.create_duration = float(s.get("createDuration", 0))
            d.product_time = float(s.get("productTime", 0))
            d.positions = s.get("positions") or []
            d.damage_times = int(s.get("damageTimes", 0))
            d.sweep_length = int(s.get("sweepLength", 0))
            d.sweep_width = int(s.get("sweepWidth", 0))
            d.sweep_times = int(s.get("sweepTimes", 0))
            d.weapon_count = max(1, int(s.get("weaponCountPerSkill", 1)))
            # weapons[].skillID list (step15 multi-gun units: Wraith 4
            # distinct ids, Typhoon 2 identical ids)
            d.weapon_ids = [int(w.get("skillID", 0)) for w in (s.get("weapons") or [])
                            if isinstance(w, dict)] or None
            self.skills[sid] = d

        self.exps = {}
        for k, e in root["exps"].items():
            d = ExpDef()
            d.loot_exp = [0, 0] + [0] * 9   # index by level 1..9
            d.upgrade_at = [0, 0] + [0] * 9  # upgrade_at[L] = exp to reach level L
            for lv in range(1, 10):
                d.loot_exp[lv] = int(e.get(f"lootExpLv{lv}", 0) or 0)
            for lv in range(2, 10):
                d.upgrade_at[lv] = int(e.get(f"upgradeLv{lv}", 0) or 0)
            self.exps[int(k)] = d

        self.cards = {}
        for k, c in root.get("cards", {}).items():
            d = CardDef()
            d.mech_id = int(c.get("mechID", 0))
            d.name = c.get("name")
            d.mech_count = int(c.get("mechCount", 1) or 1)
            d.base_money = int(c.get("baseMoney", 0) or 0)
            d.slot_size = int(c.get("slotSize", 0) or 0)
            d.card_base_size = c.get("cardBaseSize") or [0, 0]
            d.maintenance_supply = int(c.get("maintenanceSupply", 0) or 0)
            d.unlock_price = int(c.get("unlockPrice", 0) or 0)
            d.can_be_sold = bool(c.get("canBeSold", False))
            d.can_add_equipment = bool(c.get("canAddEquipment", False))
            d.special_unit = bool(c.get("specialUnit", False))
            d.group = int(c.get("group", 0) or 0)
            d.sort = int(c.get("sort", 0) or 0)
            d.technologies = c.get("technologies") or []
            d.default_technologies = c.get("defaultTechnologies") or []
            self.cards[d.mech_id] = d

        self.buildings = {}
        for cid, b in (root.get("buildings") or {}).get("byCid", {}).items():
            d = BuildingDef()
            d.cid = int(cid)
            d.table_id = int(b.get("tableId", 0))
            d.name = b.get("name")
            d.life = float(b.get("life", 0))
            d.damage = float(b.get("damage", 0))
            d.exp = int(b.get("exp", 0))
            d.count = int(b.get("count", 1) or 1)
            d.radius = float(b.get("radius", 4))
            d.skill_id = int(b.get("skillId", 0))
            d.durability = int(b.get("durability", 1) or 1)
            d.block_width = float(b.get("blockWidth", 0) or 0)
            d.space = float(b.get("space", 0) or 0)
            d.reload_shots = int(b.get("reloadShots", 0) or 0)
            d.reload_time = float(b.get("reloadTime", 0) or 0)
            d.self_destruct = float(b.get("selfDestruct", 0) or 0)
            self.buildings[d.cid] = d

        self.officers = {}
        for oid, o in (root.get("officers") or {}).items():
            d = OfficerDef()
            d.id = int(o.get("id", oid))
            d.name = o.get("name")
            d.target = o.get("target", "None")
            d.unit_ids = set(int(u) for u in o.get("unitIds") or [])
            d.mods = {k: float(v) for k, v in (o.get("mods") or {}).items()}
            d.cmd_skills = [int(s) for s in o.get("cmdSkills") or []]
            d.active_round = int(o.get("activeRound", 0) or 0)
            self.officers[d.id] = d

        self.techs = {}
        self.tech_families = None   # None = bake all; set = only these families
        for tid, t in root.get("techs", {}).items():
            d = TechDef()
            d.id = int(t.get("id", tid))
            d.name = t.get("name")
            d.description = t.get("description")
            d.desc_params = t.get("descParams")
            d.target_skill_id = int(t.get("targetSkillID", 0) or 0)
            d.previous_tech_id = int(t.get("previousTechID", 0) or 0)
            d.supply = int(t.get("supply", 0) or 0)
            d.unlock_cost = int(t.get("unlockCost", 0) or 0)
            d.main_skill_effect = bool(t.get("mainSkillEffect", False))
            d.life_rate = t.get("lifeChangeRate") or []
            d.dmg_rate = t.get("damageChangeRate") or []
            d.speed_val = t.get("speedChangeValue") or []
            d.min_range_val = t.get("minAttackRangeChangeValue") or []
            d.range_rate = t.get("attackRangeChangeRate") or []
            d.range_val = t.get("attackRangeChangeValue") or []
            d.interval_rate = t.get("attackIntervalChangeRate") or []
            d.interval_val = t.get("attackIntervalChangeValue") or []
            d.splash_val = t.get("splashRangeChangeValue") or []
            d.bullet_val = t.get("projectileSpeedChangeValue") or []
            # step16 sub-table fields
            d.family = t.get("family") or "technologyDatas"
            d.air_rate = t.get("airDamageChangeRate") or []
            d.gnd_rate = t.get("groundDamageChangeRate") or []
            d.aa_off = float(t.get("airTargetScoreOffset") or 0)
            d.gnd_off = float(t.get("groundTargetScoreOffset") or 0)
            d.grant_air = d.family == "airAttackTechnologyDatas"
            d.armor_val = t.get("reduceDamageValue") or []
            # recoveryLifeRate per recoveryDuration seconds -> /s fraction
            rlv = t.get("recoveryLifeRate") or []
            dur = t.get("recoveryDuration") or []
            d.regen_rate = [float(r) / max(float(du), 1e-6)
                            for r, du in zip(rlv, dur or [0.1] * len(rlv))]
            d.lifesteal = t.get("lifestealMultiplier") or []
            d.splash_add = t.get("range") or []
            d.multi_count = t.get("countIncrease") or []
            d.sec_dmg = float(t.get("damage") or 0) if d.family == \
                "secondaryDamageIntensifyTechDatas" else 0.0
            d.sec_rng = float(t.get("splashRange") or 0)
            # step21: merged by tools/step21_mkgd.py from tech.json
            d.proj_life_rate = t.get("proj_life") or []
            d.extra = t.get("extra") or {}
            self.techs[d.id] = d

    def sum_tech_mods(self, tech_ids, level):
        """Aggregate modifier sums of tech_ids at unit level (Rates sum,
        Values sum; per-level lists pick by level). tech_families filters
        which modifier families are baked (ablation switch).
        step16 sub-table keys: aa (layer dmg rates + targeting offsets +
        grant_air), armor, regen, steal (lifesteal), splashadd, multi, sec
        (on-hit splash). Offsets/lifesteal take max (tiered techs chain,
        they must not stack); counts and flat values sum."""
        fam = self.tech_families
        agg = {"life_rate": 0.0, "dmg_rate": 0.0, "range_rate": 0.0,
               "range_val": 0.0, "min_range": 0.0, "interval_rate": 0.0,
               "interval_val": 0.0, "speed": 0.0, "splash": 0.0, "bullet": 0.0,
               "air_rate": 0.0, "gnd_rate": 0.0, "aa_off": 0.0, "gnd_off": 0.0,
               "grant_air": False, "armor": 0.0, "regen": 0.0, "lifesteal": 0.0,
               "splash_add": 0.0, "multi": 0.0, "sec_dmg": 0.0, "sec_rng": 0.0,
               "proj_life": 0.0}
        for tid in tech_ids or ():
            td = self.techs.get(int(tid))
            if td is None:
                continue
            if fam is None or "life" in fam:
                agg["life_rate"] += tech_pick(td.life_rate, level)
            if fam is None or "dmg" in fam:
                agg["dmg_rate"] += tech_pick(td.dmg_rate, level)
            if fam is None or "range" in fam:
                agg["range_rate"] += tech_pick(td.range_rate, level)
                agg["range_val"] += tech_pick(td.range_val, level)
            if fam is None or "minrng" in fam:
                agg["min_range"] += tech_pick(td.min_range_val, level)
            if fam is None or "interval" in fam:
                agg["interval_rate"] += tech_pick(td.interval_rate, level)
                agg["interval_val"] += tech_pick(td.interval_val, level)
            if fam is None or "speed" in fam:
                agg["speed"] += tech_pick(td.speed_val, level)
            if fam is None or "splash" in fam:
                agg["splash"] += tech_pick(td.splash_val, level)
            if fam is None or "bullet" in fam:
                agg["bullet"] += tech_pick(td.bullet_val, level)
            if fam is None or "aa" in fam:
                agg["air_rate"] += tech_pick(td.air_rate, level)
                agg["gnd_rate"] += tech_pick(td.gnd_rate, level)
                agg["aa_off"] = max(agg["aa_off"], td.aa_off)
                agg["gnd_off"] = max(agg["gnd_off"], td.gnd_off)
                agg["grant_air"] = agg["grant_air"] or td.grant_air
            if fam is None or "armor" in fam:
                agg["armor"] += tech_pick(td.armor_val, level)
            if fam is None or "regen" in fam:
                agg["regen"] += tech_pick(td.regen_rate, level)
            if fam is None or "steal" in fam:
                agg["lifesteal"] = max(agg["lifesteal"],
                                        tech_pick(td.lifesteal, level))
            if fam is None or "splashadd" in fam:
                agg["splash_add"] += tech_pick(td.splash_add, level)
            if fam is None or "multi" in fam:
                agg["multi"] += tech_pick(td.multi_count, level)
            if fam is None or "sec" in fam:
                agg["sec_dmg"] = max(agg["sec_dmg"], td.sec_dmg)
                agg["sec_rng"] = max(agg["sec_rng"], td.sec_rng)
            if fam is None or "bullet" in fam:
                agg["proj_life"] += tech_pick(td.proj_life_rate, level)
        return agg

    def mech_count(self, mech_id):
        c = self.cards.get(mech_id)
        return c.mech_count if c else 1
