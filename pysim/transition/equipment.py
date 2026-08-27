# Equipment registry (step3 任务书 §6.2): versioned EquipmentDef table.
#
# Transition completeness vs battle fidelity (任务书 §7):
#   - every registered equipment is transition_complete=True: choosing the
#     reinforcement card charges and stocks it, UseEquipment binds it to a
#     unit, save/load/digest/settlement keep the binding;
#   - battle_fidelity="approximate": pysim does NOT consume the combat
#     modifier. Battle results must carry a fidelity warning, never silently
#     drop the effect.
#   - unknown equipment ids stay hard blockers (MISSING_RULE_DATA).
#
# The target restriction table is FROZEN HERE (information/增援卡牌-回放全量
# 信息.md descriptions, 2026-08-27): the runtime must not parse Chinese card
# descriptions to decide rules. Giant membership comes from gamedata officer
# 20005 (巨型专家) unitIds — the explicit giant list, not a slot/price
# heuristic (任务书 §2.2); ground_giant additionally requires !is_fly.
from dataclasses import dataclass

EQUIPMENT_REGISTRY_VERSION = "equipment_defs_v1"

GIANT_OFFICER_ID = 20005        # 巨型专家: its gamedata unitIds ARE the giants


@dataclass(frozen=True)
class EquipmentDef:
    equipment_id: int
    name: str
    cost: int                      # reinforcement card cost (survey 费用)
    target_restriction: str        # any | giant | ground_giant
    battle_fidelity: str           # "approximate" for every v1 entry
    source: str = "增援卡牌-回放全量信息.md"


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

EQUIPMENT_DEFS = {
    int(eid): EquipmentDef(
        equipment_id=int(eid), name=name, cost=int(cost),
        target_restriction=restriction, battle_fidelity="approximate",
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


def round_officer_skills(gd, officers, round_no: int) -> tuple[int, ...]:
    """Commander-skill slots granted at the start of `round_no` (gamedata
    officer cmdSkills + activeRound; e.g. 导弹专家 10011 -> two 300001 at
    round 2, 训练专家 10014 -> one 1100001 at round 1)."""
    out = []
    have = set(int(o) for o in officers or ())
    for oid in have:
        o = (gd.officers or {}).get(oid) if gd is not None else None
        if o is None:
            continue
        if int(o.active_round or 0) == int(round_no):
            out.extend(int(s) for s in o.cmd_skills or ())
    return tuple(out)


def top_up_skill_slots(slots, grants):
    """commander_skills_raw tuples + granted ids -> extended slot list.

    Grants are idempotent per id multiplicity (the round tick runs once, but
    opening snapshots may already carry the round-1 slots): each granted id
    tops the count up to the grant multiplicity. Slot indexes stay stable:
    new slots append after the current max index."""
    out = [tuple(str(x) for x in e) for e in slots]

    def max_idx():
        best = -1
        for e in out:
            try:
                best = max(best, int(e[0]))
            except (TypeError, ValueError):
                continue
        return best

    def count_of(sid):
        n = 0
        for e in out:
            try:
                if int(e[1]) == int(sid):
                    n += 1
            except (TypeError, ValueError):
                continue
        return n

    want = {}
    for sid in grants:
        want[int(sid)] = want.get(int(sid), 0) + 1
    for sid, n in want.items():
        while count_of(sid) < n:
            out.append((str(max_idx() + 1), str(sid), "true", "0"))
    return out
