# Equipment battle definitions (重构计划 §4 E0/E2): the SINGLE source for
# equipment ids. The transition-side table (pysim/transition/equipment.py)
# re-exports from here; capability, the compiler and the battle warning all
# read this module through the registry - never a second copy.
#
# Numeric evidence tiers:
#   - cost/target restriction: survey 增援卡牌-回放全量信息.md (frozen
#     2026-08-27, descriptions are the game's own card text);
#   - battle modifiers: survey description text (e.g. 射程增加20米) is the
#     game's own claim, but the STACKING ORDER against tech/officer modifiers
#     is a pysim modeling choice (equipment_stage_v1: multiplicative on life/
#     damage after the tech+officer stage, flat adds after everything) with
#     no real-game oracle A/B yet -> every battle spec stays confidence
#     "provisional" until an equipment oracle library exists (E6 gate).
from dataclasses import dataclass

EQUIPMENT_REGISTRY_VERSION = "equipment_defs_v2"

GIANT_OFFICER_ID = 20005        # 巨型专家: its gamedata unitIds ARE the giants


@dataclass(frozen=True)
class EquipmentDef:
    equipment_id: int
    name: str
    cost: int                      # reinforcement card cost (survey 费用)
    target_restriction: str        # any | giant | ground_giant
    battle_fidelity: str           # derived: exact when a battle spec exists
    source: str = "增援卡牌-回放全量信息.md"


@dataclass(frozen=True)
class EquipmentBattleSpec:
    """Static battle modifiers of one equipment id (E2 pipeline stage
    `equipment`). hp/dmg multipliers apply AFTER the tech+officer stage;
    range/speed are flat adds applied last. Confidence stays provisional
    until the real-game equipment oracle A/B (E6)."""
    equipment_id: int
    hp_mult: float = 0.0           # +0.75 = 生命值增加75%
    dmg_mult: float = 0.0          # +0.65 = 攻击力增加65%
    range_add: float = 0.0         # meters
    speed_add: float = 0.0         # meters
    confidence: str = "provisional"
    evidence: tuple = ()

    def params(self) -> dict:
        return {"hp_mult": self.hp_mult, "dmg_mult": self.dmg_mult,
                "range_add": self.range_add, "speed_add": self.speed_add}


# id -> (name, cost, restriction); costs are the survey 费用 values verbatim
_EQUIPMENT_TABLE = {
    1305003: ("光子涂层", 0, "any"),
    1305005: ("先进寄生弹药", 0, "any"),
    1305009: ("先进酸性弹药", 0, "any"),
    1306001: ("坦克生产线", 200, "giant"),
    1306002: ("野马生产线", 200, "giant"),
    1306003: ("钢球生产线", 200, "giant"),
    1306004: ("深渊信标", 0, "giant"),
    1307001: ("保护屏障", 100, "ground_giant"),
    1307002: ("超级屏障", 0, "giant"),
    1308001: ("抗干扰模块", 0, "any"),
    1309001: ("汲取模块", 100, "any"),
    13010001: ("便携式护盾", 150, "any"),
    13020001: ("纳米维修包", 100, "any"),
    13030001: ("激光瞄具", 50, "any"),
    13030002: ("重型装甲", 50, "any"),
    13030003: ("改良火控系统", 50, "any"),
    13030004: ("强化模块", 200, "any"),
    13030005: ("速攻模块", 0, "any"),
    13030006: ("超重型装甲", 200, "any"),
    13030007: ("增幅核心", 150, "any"),
    13030009: ("次级增幅核心", 0, "any"),
    13030010: ("统御核心", 100, "any"),
    13040001: ("部署模块", 0, "any"),
    13100001: ("试验级巨山装甲", 0, "any"),
    13120001: ("应激电容", 0, "giant"),
}
# 13030009 次级增幅核心 is NOT a reinforcement card: 增幅专家 10013 grants
# three copies at round 1 (information/专家明细.md) -> cost 0, provenance
# differs from the survey-sourced entries.

# E2 static battle specs. Evidence = the survey card description verbatim +
# the equipment_stage_v1 stacking assumption (see module header). The four
# high-frequency items (激光瞄具 448 / 改良火控 305 / 重型装甲 240 / 速攻模块
# 175 = 1168/2318 = 50.4% of equipment picks) come first; 超重型装甲/增幅核心
# are the second static batch; 强化模块 carries ONLY its battle half here
# (attack/life +25%) - the -100 upgrade discount is a transition-side effect.
_SURVEY = "survey:增援卡牌-回放全量信息.md"
_STAGE = "stacking:equipment_stage_v1(post-tech mult, flat last; no oracle A/B)"

EQUIPMENT_BATTLE_SPECS = {
    int(eid): spec for eid, spec in {
        13030001: EquipmentBattleSpec(
            13030001, range_add=20.0,
            evidence=(_SURVEY + " 「装备此物品的单位射程增加20米」", _STAGE)),
        13030002: EquipmentBattleSpec(
            13030002, hp_mult=0.75,
            evidence=(_SURVEY + " 「装备此物品的单位生命值增加75%」", _STAGE)),
        13030003: EquipmentBattleSpec(
            13030003, dmg_mult=0.65,
            evidence=(_SURVEY + " 「装备此物品的单位攻击力增加65%」", _STAGE)),
        13030005: EquipmentBattleSpec(
            13030005, dmg_mult=0.35, speed_add=5.0,
            evidence=(_SURVEY + " 「移动速度增加5，攻击力上升35%」", _STAGE)),
        13030006: EquipmentBattleSpec(
            13030006, hp_mult=1.50,
            evidence=(_SURVEY + " 「装备此物品的单位生命值增加150%」", _STAGE)),
        13030007: EquipmentBattleSpec(
            13030007, hp_mult=0.50, dmg_mult=0.50,
            evidence=(_SURVEY + " 「攻击力上升50%，生命值上升50%」", _STAGE)),
        13030004: EquipmentBattleSpec(
            13030004, hp_mult=0.25, dmg_mult=0.25,
            evidence=(_SURVEY + " 「攻击力上升25%，生命值上升25%」(battle half; "
                                "-100 upgrade discount is transition-side)",
                      _STAGE)),
    }.items()
}


def _battle_fidelity(equipment_id: int) -> str:
    return "exact" if int(equipment_id) in EQUIPMENT_BATTLE_SPECS \
        else "approximate"


EQUIPMENT_DEFS = {
    int(eid): EquipmentDef(
        equipment_id=int(eid), name=name, cost=int(cost),
        target_restriction=restriction,
        battle_fidelity=_battle_fidelity(int(eid)),
        source=("information/专家明细.md" if int(eid) == 13030009
                else "增援卡牌-回放全量信息.md"))
    for eid, (name, cost, restriction) in _EQUIPMENT_TABLE.items()
}

RESTRICTIONS = ("any", "giant", "ground_giant")


def giant_mechs(gd):
    """The giant mech id set: gamedata officer 20005 (巨型专家) unitIds."""
    o = (gd.officers or {}).get(GIANT_OFFICER_ID)
    return frozenset(o.unit_ids) if o else frozenset()


def equipment_target_ok(gd, equipment_id: int, mech_id: int) -> tuple[bool, str]:
    """(ok, reason) for equipping `equipment_id` on a unit of `mech_id`.

    Reasons: "" | unknown_equipment | not_equippable | giant_only |
    ground_giant_only. `not_equippable` = gamedata card canAddEquipment=false
    (checked by the caller against the card; this function only sees mech)."""
    d = EQUIPMENT_DEFS.get(int(equipment_id))
    if d is None:
        return False, "unknown_equipment"
    m = gd.mechs.get(int(mech_id))
    if m is None:
        return False, "unknown_mech"
    if d.target_restriction == "any":
        return True, ""
    giants = giant_mechs(gd)
    if int(mech_id) not in giants:
        return False, "giant_only" if d.target_restriction == "giant" \
            else "ground_giant_only"
    if d.target_restriction == "ground_giant" and m.is_fly:
        return False, "ground_giant_only"
    return True, ""


# ---------------------------------------------------------------- officers
# round-start equipment grants beyond gamedata cmdSkills (shared round event,
# 任务书 §5.3/§6.4): officer -> round -> multiset of equipment ids.
OFFICER_EQUIPMENT_GRANTS = {
    10013: {1: (13030009, 13030009, 13030009)},   # 增幅专家 (专家明细.md)
}


def round_officer_equipment(officers, round_no: int) -> tuple[int, ...]:
    out = []
    have = set(int(o) for o in officers or ())
    for oid, table in OFFICER_EQUIPMENT_GRANTS.items():
        if oid in have:
            out.extend(table.get(int(round_no), ()))
    return tuple(out)
