# 爬虫动力学与伤害标定修正任务书 (2026-08-29) T1: the unified calibration
# ledger. EXTENDS the step32 DamageReceipt channel — one damage truth source,
# never a second competing口径 (任务书 §5/T1). The engine records cheap
# per-event probes only when opts.calib_ledger is set:
#   fire_events     (t, row, card, uid)      one per _fire_one volley
#   target_since    row -> first lock time   (first_target_at)
#   walked          row -> metres moved      (distance_walked)
#   retarget_events (t, row, old, new, reason) — shared with T4 crawler_retarget
# Aggregate rows (CalibrationRow) are derived AFTER the battle from these
# probes + damage_receipts + kills; nothing here changes simulation results.
from dataclasses import dataclass, field, asdict


@dataclass
class CalibrationRow:
    """Per source card 120s output calibration row (任务书 §5/T1 field set).
    单位约定: 弹数/整轮伤害分开报告 — volleys = 齐射次数, projectiles =
    单轮弹数估计 (skill 表值 × weapon_count), impacts = 实际结算次数."""
    case_id: str = ""
    source_card: int = -1
    source_member: int = -1            # representative row (first member)
    mech_id: int = -1
    tech_ids: tuple = ()
    first_target_at: float = -1.0      # first lock acquisition (s)
    first_fire_at: float = -1.0        # first fire event (s)
    volleys: int = 0                   # _fire_one calls
    projectiles: int = 0               # per-volley projectile count estimate
    impacts: int = 0                   # damage receipts count
    raw_damage: float = 0.0
    actual_damage: float = 0.0         # hp_damage (post shield/barrier)
    overkill: float = 0.0
    shield_absorbed: float = 0.0
    barrier_absorbed: float = 0.0
    kills: int = 0
    target_switches: int = 0
    distance_walked: float = 0.0
    turn_time: float = -1.0            # oracle-pending: facing off -> -1
    unique_victims: int = 0            # distinct victim rows hit (清杂 AoE)

    def to_dict(self):
        d = asdict(self)
        d["tech_ids"] = list(self.tech_ids)
        return d


def _card_meta(b, ci):
    c = b.cards[ci] if 0 <= ci < len(b.cards) else {}
    members = [int(r) for r in b.card_by_row(ci)] if hasattr(
        b, "card_by_row") else []
    return c, members


def build_calibration_rows(b, case_id=""):
    """Aggregate CalibrationRow per source card from a finished Battle.

    Damage fields come from the DamageReceipt stream (the single damage
    truth); fire/target/turn probes are the opts.calib_ledger channels.
    Rows are produced for every card that fired or dealt damage."""
    rows = {}
    for r in b.damage_receipts:
        ci = int(r.get("source_card", -1))
        if ci < 0:
            continue
        row = rows.setdefault(ci, CalibrationRow(case_id=case_id,
                                                 source_card=ci))
        row.impacts += 1
        row.raw_damage += float(r.get("raw_damage", 0.0))
        row.actual_damage += float(r.get("hp_damage", 0.0))
        row.overkill += float(r.get("overkill", 0.0))
        row.shield_absorbed += float(r.get("shield_absorbed", 0.0))
        row.barrier_absorbed += float(r.get("barrier_absorbed", 0.0))
        row.unique_victims += 0   # recomputed below
    victims_by_card = {}
    for r in b.damage_receipts:
        ci = int(r.get("source_card", -1))
        if ci >= 0:
            victims_by_card.setdefault(ci, set()).add(
                int(r.get("victim_row", -1)))
    for ci, vset in victims_by_card.items():
        if ci in rows:
            rows[ci].unique_victims = len(vset)
    # fire probes
    if getattr(b, "fire_events", None):
        for (t, row_i, ci, _uid) in b.fire_events:
            if ci < 0:
                continue
            row = rows.setdefault(ci, CalibrationRow(case_id=case_id,
                                                     source_card=ci))
            row.volleys += 1
            if row.first_fire_at < 0 or t < row.first_fire_at:
                row.first_fire_at = t
            if row.source_member < 0:
                row.source_member = int(row_i)
    # target acquisition + walked + switches
    ts = getattr(b, "target_since", None)
    if ts is not None:
        for ci in rows:
            members = np_rows_of_card(b, ci)
            vals = [float(ts[r]) for r in members
                    if ts[r] is not None and ts[r] >= 0]
            if vals:
                rows[ci].first_target_at = min(vals)
    wk = getattr(b, "walked", None)
    if wk is not None:
        for ci in rows:
            members = np_rows_of_card(b, ci)
            if members:
                rows[ci].distance_walked = round(
                    float(sum(wk[r] for r in members)), 2)
    for (_t, row_i, _o, _n, _r) in (getattr(b, "retarget_events", ()) or ()):
        ci = int(b.card_idx[row_i]) if row_i < b.n else -1
        if ci >= 0 and ci in rows:
            rows[ci].target_switches += 1
    # kills by killer card
    for k in b.kills:
        killer_uid = k.get("killer", 0)
        row_i = int(b.uid_inv.get(killer_uid, -1)) \
            if getattr(b, "uid_inv", None) else -1
        if row_i < 0:
            # uid -> row fallback: uid array search (small battles only)
            import numpy as np
            hit = np.where(b.uid == killer_uid)[0]
            row_i = int(hit[0]) if len(hit) else -1
        if row_i >= 0:
            ci = int(b.card_idx[row_i])
            if ci >= 0 and ci in rows:
                rows[ci].kills += 1
    # static identity fields
    for ci, row in rows.items():
        c = b.cards[ci] if 0 <= ci < len(b.cards) else None
        if c is not None:
            row.mech_id = int(c.get("mech", -1))
            row.tech_ids = tuple(int(t) for t in (c.get("techs") or ()))
        members = np_rows_of_card(b, ci)
        if row.source_member < 0 and members:
            row.source_member = members[0]
        if row.projectiles <= 0 and row.mech_id > 0:
            row.projectiles = estimate_projectiles(b, row.mech_id)
    return dict(sorted(rows.items()))


def np_rows_of_card(b, ci):
    import numpy as np
    return [int(r) for r in np.where(b.card_idx == ci)[0]]


def estimate_projectiles(b, mech_id):
    """Per-volley projectile estimate: main skill projectile_count (表值)
    x weapon_count. Multi-volley audit stays event-based (volleys)."""
    try:
        md = b.gd.mechs.get(int(mech_id))
        sd = b.gd.skills.get(md.main_skill_id) if md else None
        if sd is None:
            return 0
        return int(getattr(sd, "projectile_count", 1)
                   or 1) * int(getattr(sd, "weapon_count", 1) or 1)
    except AttributeError:
        return 0


def summarize(b, case_id=""):
    """Public report: dict ready for JSON (rows + battle totals + the
    consistency checks the 任务书 gates require)."""
    rows = build_calibration_rows(b, case_id)
    return {
        "case_id": case_id,
        "engine_version": "pysim-calib-ledger-v1",
        "rows": {str(ci): r.to_dict() for ci, r in rows.items()},
        "totals": {
            "raw_damage": round(sum(r.raw_damage for r in rows.values()), 3),
            "actual_damage": round(sum(r.actual_damage
                                       for r in rows.values()), 3),
            "card_damage_sum": round(sum(b.card_damage.values()), 3)
            if b.card_damage else 0.0,
            "impacts": sum(r.impacts for r in rows.values()),
            "volleys": sum(r.volleys for r in rows.values()),
        },
    }
