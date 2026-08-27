# Equipment transition helpers (step3 任务书 §6.2).
#
# The EQUIPMENT_DEFS table MOVED to pysim/battlefield/effects/equipment.py
# (重构计划 §4 E0: one table shared by capability, the compiler and the
# battle warnings through the battlefield registry). This module keeps the
# transition-side helpers (grants, skill-slot top-up) and re-exports the
# table for backward compatibility - importers must not assume a second
# copy exists here.
from ..battlefield.effects.equipment import (
    EQUIPMENT_REGISTRY_VERSION, GIANT_OFFICER_ID, EquipmentDef,
    EQUIPMENT_DEFS, RESTRICTIONS, giant_mechs, equipment_target_ok,
    OFFICER_EQUIPMENT_GRANTS, round_officer_equipment)


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


def slot_is_active(entry) -> bool:
    """commander_skills_raw entry -> usability (isActive)."""
    try:
        return str(entry[2]).lower() == "true"
    except (TypeError, ValueError, IndexError):
        return False


def slot_cd(entry) -> int:
    try:
        return int(float(entry[3]))
    except (TypeError, ValueError, IndexError):
        return 0
