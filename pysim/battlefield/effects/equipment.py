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


# ---------------------------------------------------------------- runtime
# step32 动态装备与伤害管线 (pysim动态装备与伤害管线修正任务书-2026-08-28
# §T1): versioned RUNTIME effect specs. The static E2 table above keeps its
# semantics and digest untouched; every runtime id resolves to ONE composite
# spec that the engine consumes (shield / regen / lifesteal / immunity /
# timed modifier / summon scheduler). Numbers come from the task doc's card
# text + user notes; every spec stays confidence "provisional" until its
# per-id oracle A/B lands (任务书 §4/§8.5) - None/absent fields mean
# "unknown", never 0.
EQUIPMENT_RUNTIME_VERSION = "equipment_runtime_v1"

# Unified StatusKind vocabulary (任务书 T3). The engine checks these at the
# status-apply entry points, not per tech/skill branch.
STATUS_EMP = "emp"                      # 电磁弹 disable + slow
STATUS_BURN = "burn"                    # 引燃 / 燃烧 DoT
STATUS_ACID = "acid"                    # 酸液 (DoT + vulnerability)
STATUS_DEGENERATION = "degeneration"    # 退化光束 (not yet modeled in engine)
STATUS_HACKER = "hacker"                # 骇客控制 (beam + conversion)
STATUS_PARALYSIS = "paralysis"          # 核心建筑爆炸瘫痪
STATUS_KINDS = (STATUS_EMP, STATUS_BURN, STATUS_ACID, STATUS_DEGENERATION,
                STATUS_HACKER, STATUS_PARALYSIS)
STATUS_BITS = {k: (1 << i) for i, k in enumerate(STATUS_KINDS)}


def status_mask(*kinds) -> int:
    m = 0
    for k in kinds:
        m |= STATUS_BITS[k]
    return m


@dataclass(frozen=True)
class TimedModifier:
    """A timed modifier active from battle start (equipment exists before
    the fight, so there is no "grant clears old statuses" moment - that
    question is photon-projection-only, oracle Q4)."""
    kind: str                    # "photon" today; more families later
    duration: float              # seconds from t=0
    dmg_taken_mult: float = 1.0  # damage-taken channel (photon x0.70)
    immunity: tuple = ()         # StatusKinds suppressed while active

    @property
    def immunity_mask(self) -> int:
        return status_mask(*self.immunity)


@dataclass(frozen=True)
class BarrierSpec:
    """A carrier-following shield barrier device (保护/超级屏障). Ground
    coverage + follow semantics reuse the DEVICE_BARRIER channel; radius is
    provisional (BARRIER_RADIUS default) until oracle Q3."""
    hp: float
    radius: float
    follow: bool = True


@dataclass(frozen=True)
class SummonSchedule:
    """Periodic production-line summon (任务书 T6/T12). first_at defaults to
    `period` (first batch at t=period, NOT t=0) - oracle pending (T12)."""
    mech_id: int
    period: float
    count: int                   # units per batch
    batches: int                 # total batches over the fight
    first_at: float = -1.0       # -1 = resolve to `period`

    def resolved_first_at(self) -> float:
        return self.period if self.first_at < 0 else self.first_at


@dataclass(frozen=True)
class EquipmentRuntimeSpec:
    """Composite runtime effect spec of one equipment id (任务书 T1). The
    engine treats this table as the single runtime source; registry dump,
    warnings and evidence all read through it."""
    equipment_id: int
    static: EquipmentBattleSpec | None = None   # static modifier stage
    timed: tuple = ()            # TimedModifier...
    shield_self: str | None = None   # "max_hp" = per-row shield = own maxHP
    barrier: BarrierSpec | None = None          # follower barrier device
    regen_frac: float | None = None             # maxHP/s, REPLACES tech regen
    lifesteal_frac: float | None = None         # adds to tech lifesteal
    immunity: tuple = ()         # permanent StatusKinds (anti-jamming)
    summon: SummonSchedule | None = None
    confidence: str = "provisional"
    evidence: tuple = ()

    @property
    def immunity_mask(self) -> int:
        return status_mask(*self.immunity)

    @property
    def battle_implemented(self) -> bool:
        return bool(self.static is not None or self.timed or self.shield_self
                    or self.barrier is not None or self.regen_frac is not None
                    or self.lifesteal_frac is not None or self.immunity
                    or self.summon is not None)

    def params(self) -> dict:
        """JSON-safe canonical view (registry dump / digest input)."""
        return {
            "equipment_id": self.equipment_id,
            "static": self.static.params() if self.static else None,
            "timed": [{"kind": t.kind, "duration": t.duration,
                       "dmg_taken_mult": t.dmg_taken_mult,
                       "immunity": list(t.immunity)} for t in self.timed],
            "shield_self": self.shield_self,
            "barrier": ({"hp": self.barrier.hp, "radius": self.barrier.radius,
                         "follow": self.barrier.follow}
                        if self.barrier else None),
            "regen_frac": self.regen_frac,
            "lifesteal_frac": self.lifesteal_frac,
            "immunity": list(self.immunity),
            "summon": ({"mech_id": self.summon.mech_id,
                        "period": self.summon.period,
                        "count": self.summon.count,
                        "batches": self.summon.batches,
                        "first_at": self.summon.resolved_first_at()}
                       if self.summon else None),
            "confidence": self.confidence,
        }

    def digest(self) -> str:
        import hashlib
        import json
        blob = json.dumps(self.params(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_TASK = "task:pysim动态装备与伤害管线修正任务书-2026-08-28"
_TASK_NOTE = _TASK + " 用户注记"
_ORACLE_PENDING = "oracle A/B pending (任务书 §4/§8.5) -> provisional"

EQUIPMENT_RUNTIME_SPECS = {
    int(eid): spec for eid, spec in {
        # 次级增幅核心: 攻击力上升22% 生命上升22% (任务书用户注记, T7);
        # 数值仍未 oracle 复核, 叠加段 = equipment_stage_v1 (待证)
        13030009: EquipmentRuntimeSpec(
            13030009,
            static=EquipmentBattleSpec(
                13030009, hp_mult=0.22, dmg_mult=0.22,
                evidence=(_TASK_NOTE + " 「攻击力上升22% 生命上升22%」",
                          _STAGE, _ORACLE_PENDING)),
            evidence=(_TASK_NOTE + " 「攻击力上升22% 生命上升22%」",
                      _ORACLE_PENDING)),
        # 光子涂层: 开战30s 减伤x0.70 + 免疫 EMP/引燃/酸液/退化 (T8; 光子
        # 投射的 "获得时清除" 规则不适用 - 装备开战前已存在, oracle Q4)
        1305003: EquipmentRuntimeSpec(
            1305003,
            timed=(TimedModifier(
                "photon", 30.0, dmg_taken_mult=0.70,
                immunity=(STATUS_EMP, STATUS_BURN, STATUS_ACID,
                          STATUS_DEGENERATION)),),
            evidence=(_TASK + " 「相当于开战30s有光子状态」",
                      _ORACLE_PENDING)),
        # 抗干扰模块: 免疫 EMP / 骇客控制 / 核心爆炸瘫痪 (T8)
        1308001: EquipmentRuntimeSpec(
            1308001,
            immunity=(STATUS_EMP, STATUS_HACKER, STATUS_PARALYSIS),
            evidence=(_TASK + " 「EMP/骇客/核心瘫痪免疫未实现」",
                      _ORACLE_PENDING)),
        # 便携式护盾: 护盾 = 装备后 maxHP; "至少挡一次" 溢出语义按当前
        # 引擎口径 (min(shield, hit), 溢出入血), oracle Q2 待证 (T9)
        13010001: EquipmentRuntimeSpec(
            13010001,
            shield_self="max_hp",
            evidence=(_TASK + " 「同 maxHP 护盾与至少挡一次未实现」",
                      _ORACLE_PENDING)),
        # 保护屏障 / 超级屏障: 60000/180000 HP 跟随屏障 (T10); 半径沿用
        # BARRIER_RADIUS=30 (cal), 地面覆盖同空投护盾口径, oracle Q3 待证
        1307001: EquipmentRuntimeSpec(
            1307001,
            barrier=BarrierSpec(60000.0, 30.0, follow=True),
            evidence=(_TASK + " 「60000 护盾的位置/跟随/覆盖未实现」",
                      _ORACLE_PENDING)),
        1307002: EquipmentRuntimeSpec(
            1307002,
            barrier=BarrierSpec(180000.0, 30.0, follow=True),
            evidence=(_TASK + " 「与 1307001 共用跟随屏障原语」",
                      _ORACLE_PENDING)),
        # 汲取模块: HP +30% (静态段) + 90% 吸血 (T5/T11); 吸血基数沿用
        # 引擎 dealt(实际造成伤害) 口径, receipt 化待 oracle
        1309001: EquipmentRuntimeSpec(
            1309001,
            static=EquipmentBattleSpec(
                1309001, hp_mult=0.30,
                evidence=(_TASK + " 「HP +30% / 90% 吸血未实现」",
                          _STAGE, _ORACLE_PENDING)),
            lifesteal_frac=0.90,
            evidence=(_TASK + " 「HP +30% / 90% 吸血未实现」",
                      _ORACLE_PENDING)),
        # 纳米维修包: 4.5% maxHP/s, 覆盖单位自身战地维修 (任务书用户注记);
        # 被引燃/EMP 期间禁疗沿用现有 regen 门, 酸液禁疗 oracle Q5 待证
        13020001: EquipmentRuntimeSpec(
            13020001,
            regen_frac=0.045,
            evidence=(_TASK + " 「4.5% maxHP/s 未实现; 覆盖战地维修」",
                      _ORACLE_PENDING)),
        # 三类生产线 (T12): 首批 t=period (非 t=0), 批次上限 = 文案次数,
        # 召唤等级 1 / 无科技继承 - 全部 oracle Q6 待证
        1306001: EquipmentRuntimeSpec(
            1306001,
            summon=SummonSchedule(13, 13.0, 2, 7),
            evidence=(_TASK + " 「13s x 2 铁锤, 7 次」",
                      _ORACLE_PENDING)),
        1306002: EquipmentRuntimeSpec(
            1306002,
            summon=SummonSchedule(7, 11.0, 4, 8),
            evidence=(_TASK + " 「11s x 4 野马, 8 次」",
                      _ORACLE_PENDING)),
        1306003: EquipmentRuntimeSpec(
            1306003,
            summon=SummonSchedule(8, 16.0, 2, 6),
            evidence=(_TASK + " 「16s x 2 钢球, 6 次」",
                      _ORACLE_PENDING)),
    }.items()
}

# The selected-ID set of the 2026-08-28 task doc (§1.2; 联合覆盖 1628/7122
# = 22.9% 回合). 部署模块 13040001 / 统御核心 13030010 stay deferred (§2.3).
SELECTED_RUNTIME_EQUIPMENT_IDS = tuple(sorted(EQUIPMENT_RUNTIME_SPECS))
DEFERRED_EQUIPMENT_IDS = (13030010, 13040001)


def equipment_static_spec(equipment_id) -> EquipmentBattleSpec | None:
    """Static modifier stage lookup: the legacy E2 table first (unchanged
    semantics/digest), then a runtime spec's static block."""
    eid = int(equipment_id or 0)
    spec = EQUIPMENT_BATTLE_SPECS.get(eid)
    if spec is not None:
        return spec
    rt = EQUIPMENT_RUNTIME_SPECS.get(eid)
    return rt.static if rt is not None else None


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
