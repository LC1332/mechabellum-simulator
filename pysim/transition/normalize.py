# Action-stream normalizer (transition v0.1).
#
# Folds Undo/CancelReleaseCommanderSkill OUT of a raw per-(player, round)
# action log and produces an atomic, undo-free norm stream:
#   - stack folding: Undo reverts the last undoable op. ALL deploy action
#     types are undoable (user ruling Q1/Q9: the undo arrow removes the
#     previous recorded operation). ChooseReinforceItem is NOT undoable
#     (Q4) and FinishDeploy ends the phase (Q2); GiveUp/ChooseAdvanceTeam
#     are terminal/round-0 markers.
#   - CancelReleaseCommanderSkill removes the matched prior release (same
#     SkillIndex) and is itself undoable (Q9): undoing a cancel restores
#     the release.
#   - multi-unit MoveUnit records split into atomic moves; one record is
#     one undoable op (atomic revert).
#   - sequential unit-index counter: starts at the snapshot's playerData/
#     unitIndex; buy/grant allocate & advance, Undo of a buy reclaims the
#     index, sell burns it (counter unchanged).
#   - reference resolution against snapshot-live units + surviving spawns;
#     failures land in norm_report.unresolved_refs (NO uid-adoption or
#     next-snapshot heuristics).
# The normalizer only ever reads the CURRENT round's record; oracle
# comparisons against next-round snapshots live in tools/normalize_actions.py
# (--diagnostic), never here.
from dataclasses import dataclass, field

# raw types carried through as `passthrough` entries (no modeled deploy
# effect in v0; still participants in undo folding unless noted).
# UseEquipment and MAPPED ReleaseCommanderSkill releases are emitted as
# typed entries instead (step3 任务书 §5.2/§6.3); only the unmapped residue
# stays passthrough here.
PASSTHROUGH_TYPES = {
    "ActiveEnergyTowerSkill",     # undoable (Q1)
    "ReleaseContraption",         # undoable (Q1)
    "UseEquipment",               # undoable (Q1) -> typed `equip` entry
    "StrengthenTower",            # undoable (Q1)
    "ActiveBlueprint",            # undoable (Q7/Q8)
    "ReleaseCommanderSkill",      # unmapped releases (undoable, Q1)
    "GiveUp",                     # terminal marker, not undoable
    "ChooseAdvanceTeam",          # round-0 marker, not undoable
}

SELL_SKILL_IDS = {0, 900001}
UNIT_SKILL_KEEP = {1100001, 1000001}   # 强化训练/再部署: unit-targeting, not sells


def _mapped_release_ids():
    """Commander skill ids with a trusted transition/battle effect."""
    from ..skills import COMMANDER_SKILLS, TRANSITION_SKILLS
    return set(COMMANDER_SKILLS) | set(TRANSITION_SKILLS)


def _release_skill_id(raw: dict, skills_raw):
    """ReleaseCommanderSkill record -> resolved skill id or None.

    Explicit non-zero ID wins; otherwise SkillIndex resolves through the
    round's commanderSkills snapshot entries (index -> id)."""
    rid = _int(raw.get("ID"), 0)
    if rid:
        return rid
    sidx = raw.get("SkillIndex")
    if sidx is None:
        return None
    for entry in skills_raw or ():
        if entry and str(entry.get("index")) == str(sidx):
            try:
                sid = int(entry.get("id"))
                return sid if sid else None
            except (TypeError, ValueError):
                return None
    return None


def _positions_of(raw: dict):
    """Positions field of a release record -> [(x, y), ...] (floats)."""
    v = raw.get("Positions")
    if isinstance(v, dict):
        v = [v]
    out = []
    for p in (v or []):
        try:
            out.append((float(p.get("x", 0.0) or 0.0),
                        float(p.get("y", 0.0) or 0.0)))
        except (AttributeError, TypeError, ValueError):
            continue
    return out

# Opening-team delayed gift units (corpus-frozen 2026-08-26, zero exceptions
# in 1106 games): officer present from round 1 -> one free unit arrives at a
# FIXED round, allocated at the round's first index (before all actions).
GIFT_OFFICERS = {
    20029: (2, 2),     # round 2, 长弓
    20036: (3, 21),    # round 3
    20038: (3, 20),    # round 3, 火獾
    20033: (4, 5),     # round 4
    20039: (4, 22),    # round 4
}


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


@dataclass
class _Op:
    op_id: int
    kind: str                       # buy/move/upgrade/unlock/tech/release/
                                    # cancel/tower_skill/passthrough
    raw_index: int
    emitted: list = field(default_factory=list)
    alloc: int = 0                  # unit indexes allocated (buy/grant)
    cancel_of: "_Op | None" = None  # for cancel ops: the release it killed
    cancelled: bool = False         # for release ops: killed by a cancel
    alive: bool = True


@dataclass
class NormResult:
    actions_norm: list
    report: dict
    counter_start: int
    counter_end: int
    spawn_indexes: dict             # game_index -> mech_id (surviving spawns)


class Normalizer:
    """Stateful fold of one (player, round) raw action log."""

    def __init__(self, eco=None):
        self.eco = eco

    # ------------------------------------------------------------------ api
    def normalize_round(self, rec: dict) -> NormResult:
        raw_actions = rec.get("actions") or []
        snap = {int(u["index"]): u for u in rec.get("units") or []}
        notes = []
        counter = rec.get("unit_index")
        if counter is None:
            counter = (max(snap) + 1) if snap else 0
            notes.append("unit_index_missing_derived_maxlive")
        counter_start = int(counter)

        emitted = []                # entry dicts (mutated in place)
        stack = []                  # undoable _Op stack, bottom..top
        folded = []
        n_undo = n_cancel = undo_on_empty = 0
        op_seq = 0
        skills_raw = rec.get("commanderSkills_raw") or ()
        sell_supply_of = {ix: int(u.get("sellSupply", 0) or 0)
                          for ix, u in snap.items()}
        mech_of = {ix: int(u["id"]) for ix, u in snap.items()}

        # opening-team gift: allocated before the first action of its round
        gifts = []
        for o in rec.get("officers") or []:
            try:
                arr_round, gift_mech = GIFT_OFFICERS.get(int(o), (None, None))
            except (TypeError, ValueError):
                continue
            if arr_round == int(rec.get("round", -1)):
                gi = counter
                counter += 1
                mech_of[gi] = gift_mech
                price = self.eco.buy_price(gift_mech) if self.eco else None
                sell_supply_of[gi] = price or 0
                gifts.append({"t": "gift", "mech": gift_mech,
                              "game_index": gi, "raw": [-1]})
        emitted.extend(gifts)

        def emit(entry):
            emitted.append(entry)
            return len(emitted) - 1

        def push(kind, raw_index, entry_ids, alloc=0) -> _Op:
            nonlocal op_seq
            op = _Op(op_id=op_seq, kind=kind, raw_index=raw_index,
                     emitted=list(entry_ids), alloc=alloc)
            op_seq += 1
            stack.append(op)
            return op

        for k, a in enumerate(raw_actions):
            t = a.get("type")
            if t == "BuyUnit":
                pos = a.get("position") or {}
                gi = counter
                counter += 1
                mech = _int(a.get("UID"))
                mech_of[gi] = mech
                price = self.eco.buy_price(mech) if self.eco else None
                sell_supply_of[gi] = price or 0
                ei = emit({"t": "buy", "uid": mech,
                           "x": float(pos.get("x", 0.0) or 0.0),
                           "y": float(pos.get("y", 0.0) or 0.0),
                           "game_index": gi, "cost": price, "raw": [k]})
                push("buy", k, [ei], alloc=1)
            elif t == "ChooseReinforceItem":
                item = _int(a.get("ID"))
                cost = self.eco.item_cost(item) if self.eco else None
                grant = self.eco.item_grant(item) if (self.eco and item) else None
                grants = []
                emit({"t": "reinforce", "id": item, "cost": cost,
                      "grants": grants, "raw": [k]})
                if grant:
                    mech, count, level = grant
                    for _ in range(count):
                        gi = counter
                        counter += 1
                        mech_of[gi] = mech
                        price = self.eco.buy_price(mech) if self.eco else None
                        sell_supply_of[gi] = price or 0
                        grants.append({"mech": mech, "level": int(level),
                                       "game_index": gi})
                # Q4: reinforce picks are NOT undoable -> never stacked
            elif t == "MoveUnit":
                ids = []
                for m in a.get("moveUnitDatas") or []:
                    pos = m.get("position") or {}
                    rot = m.get("isRotate")
                    ids.append(emit({
                        "t": "move",
                        "unit": (None if m.get("unitIndex") is None
                                 else _int(m.get("unitIndex"))),
                        "uid": (None if m.get("unitID") is None
                                else _int(m.get("unitID"))),
                        "x": float(pos.get("x", 0.0) or 0.0),
                        "y": float(pos.get("y", 0.0) or 0.0),
                        "rot": (None if rot is None else bool(rot)),
                        "raw": [k]}))
                push("move", k, ids)
            elif t == "UpgradeUnit":
                uidx = a.get("UIDX")
                uid = _int(a.get("UID"))
                ei = emit({"t": "upgrade",
                           "unit": None if uidx is None else _int(uidx),
                           "uid": uid,
                           "cost": None, "raw": [k]})     # cost filled pass 2
                push("upgrade", k, [ei])
            elif t == "UnlockUnit":
                mech = _int(a.get("UID"))
                cost = self.eco.unlock_price(
                    mech, tuple(int(o) for o in rec.get("officers") or ())
                ) if self.eco else None
                ei = emit({"t": "unlock", "uid": mech, "cost": cost,
                           "raw": [k]})
                push("unlock", k, [ei])
            elif t == "UpgradeTechnology":
                mech = _int(a.get("UID"))
                tid = _int(a.get("TechID"))
                ei = emit({"t": "tech", "uid": mech, "tech": tid,
                           "cost": None, "raw": [k]})     # cost filled pass 2
                push("tech", k, [ei])
            elif t == "ReleaseCommanderSkill":
                sidx = _int(a.get("SkillIndex"))
                if self._resolves_sell(a, skills_raw):
                    gi = a.get("UnitIndex")
                    gi = None if gi is None else _int(gi)
                    ei = emit({"t": "sell", "unit": gi, "refund": None,
                               "skill_index": sidx, "raw": [k]})
                    push("release", k, [ei])
                elif (_release_skill_id(a, skills_raw)
                        in _mapped_release_ids()):
                    sid = _release_skill_id(a, skills_raw)
                    uidx = a.get("UnitIndex")
                    cidx = a.get("ConstructionIndex")
                    ei = emit({"t": "release", "skill": int(sid),
                               "skill_index": sidx,
                               "positions": _positions_of(a),
                               "unit": (None if uidx is None else _int(uidx)),
                               "construction": (None if cidx is None
                                                else _int(cidx)),
                               "raw": [k]})
                    push("release", k, [ei])
                else:
                    ei = emit(self._passthrough(a, k))
                    push("release", k, [ei])
            elif t == "CancelReleaseCommanderSkill":
                sidx = str(a.get("SkillIndex"))
                target = None
                for op in reversed(stack):
                    if op.kind == "release" and not op.cancelled:
                        e = emitted[op.emitted[0]]
                        if str(e.get("skill_index")) == sidx:
                            target = op
                            break
                if target is not None:
                    target.cancelled = True
                    for ei in target.emitted:
                        emitted[ei]["_dead"] = True
                    n_cancel += 1
                    folded.append({"raw_index": target.raw_index,
                                   "undone_by": k, "kind": "cancel_release"})
                    op = push("cancel", k, [])
                    op.cancel_of = target
                else:
                    notes.append("cancel_no_match@%d" % k)
            elif t == "Undo":
                if not stack:
                    undo_on_empty += 1
                    continue
                op = stack.pop()
                if op.cancel_of is not None:         # undoing a cancel:
                    target = op.cancel_of             # restore the release
                    target.cancelled = False
                    for ei in target.emitted:
                        emitted[ei].pop("_dead", None)
                    folded.append({"raw_index": op.raw_index, "undone_by": k,
                                   "kind": "cancel_undone"})
                else:
                    for ei in op.emitted:
                        emitted[ei]["_dead"] = True
                    counter -= op.alloc
                    folded.append({"raw_index": op.raw_index, "undone_by": k,
                                   "kind": op.kind})
                n_undo += 1
            elif t == "UseEquipment":
                # typed equipment binding (step3 任务书 §6.3): one undoable op
                eid = _int(a.get("EquipmentID"))
                uidx = a.get("UnitIndex")
                ei = emit({"t": "equip", "id": eid,
                           "unit": (None if uidx is None else _int(uidx)),
                           "raw": [k]})
                push("equip", k, [ei])
            elif t == "FinishDeploy":
                emit({"t": "finish", "raw": [k]})
            elif t in PASSTHROUGH_TYPES:
                ei = emit(self._passthrough(a, k))
                if t not in ("GiveUp", "ChooseAdvanceTeam"):
                    push("passthrough", k, [ei])
            else:
                notes.append("unknown_type@%d:%s" % (k, t))

        stream = [e for e in emitted if not e.pop("_dead", False)]

        # ---- pass 2 over the folded stream: prices + reference resolution
        live = dict(mech_of)         # snapshot live -> mech (spawns added)
        live_sell = dict(sell_supply_of)
        unresolved = []
        # step3 任务书 §2.1: price estimates consume the SAME quote entry as
        # execution (expert discounts included, officers from the snapshot)
        round_officers = tuple(int(o) for o in rec.get("officers") or ())
        # tech staircase seed: ACTIVE techs from the snapshot techMap (the
        # game prices from the mech's active-tech count incl. defaults)
        tech_owned = {int(m): len(lst)
                      for m, lst in (rec.get("techMap") or {}).items()}
        for e in stream:
            t = e.get("t")
            if t == "gift":
                live[e["game_index"]] = e["mech"]
                price = self.eco.buy_price(e["mech"]) if self.eco else None
                live_sell[e["game_index"]] = price or 0
            elif t == "buy":
                live[e["game_index"]] = e["uid"]
                live_sell[e["game_index"]] = e["cost"] or 0
            elif t == "reinforce":
                for g in e.get("grants") or []:
                    live[g["game_index"]] = g["mech"]
                    price = self.eco.buy_price(g["mech"]) if self.eco else None
                    live_sell[g["game_index"]] = price or 0
            elif t == "unlock":
                if e.get("cost") is None and self.eco:
                    e["cost"] = self.eco.unlock_price(e["uid"],
                                                      round_officers)
            elif t == "upgrade":
                if self.eco:
                    mech = e.get("uid") or live.get(e.get("unit"))
                    e["cost"] = self.eco.upgrade_price(mech) \
                        if mech else None
                gi = e.get("unit")
                if gi is None or gi < 0:
                    # UIDX<0: the target's index was not assigned at record
                    # time; fall back to the newest live spawn of that type
                    cands = [ix for ix, m in live.items()
                             if m == e.get("uid") and ix >= counter_start]
                    e["unit"] = max(cands) if cands else None
                    gi = e["unit"]
                if gi is None or gi < 0:
                    unresolved.append({"raw": e["raw"][0], "t": t,
                                       "unit": None, "reason": "no_target"})
                    continue
                if gi not in live:
                    unresolved.append({"raw": e["raw"][0], "t": t,
                                       "unit": gi, "reason": "unknown_index"})
                    continue
                if e.get("uid") and live[gi] != e["uid"]:
                    notes.append("uid_mismatch@%d(idx=%d,got=%s,live=%s)"
                                 % (e["raw"][0], gi, e["uid"], live[gi]))
                    e["uid"] = live[gi]      # the game index is the truth
            elif t == "move":
                gi = e.get("unit")
                if gi is None or gi < 0:
                    unresolved.append({"raw": e["raw"][0], "t": t,
                                       "unit": None, "reason": "no_target"})
                    continue
                if gi not in live:
                    unresolved.append({"raw": e["raw"][0], "t": t,
                                       "unit": gi, "reason": "unknown_index"})
                    continue
                if e.get("uid") is not None and live[gi] != e["uid"]:
                    notes.append("uid_mismatch@%d(idx=%d,got=%s,live=%s)"
                                 % (e["raw"][0], gi, e["uid"], live[gi]))
                    e["uid"] = live[gi]
            elif t == "sell":
                gi = e.get("unit")
                if gi is None or gi not in live:
                    unresolved.append({"raw": e["raw"][0], "t": t,
                                       "unit": gi, "reason": "unknown_index"})
                    continue
                e["refund"] = live_sell.get(gi) or 0
                del live[gi]
                del live_sell[gi]
            elif t == "equip":
                gi = e.get("unit")
                if gi is None or gi < 0 or gi not in live:
                    unresolved.append({"raw": e["raw"][0], "t": t,
                                       "unit": gi, "reason": "unknown_index"})
                    continue
                if e.get("uid") is not None and live[gi] != e.get("uid"):
                    e["uid"] = live[gi]      # the game index is the truth
            elif t == "tech":
                if self.eco:
                    e["cost"] = self.eco.tech_price(
                        e["uid"], e["tech"], tech_owned.get(e["uid"], 0),
                        round_officers)
                    if e["cost"] is not None:
                        tech_owned[e["uid"]] = tech_owned.get(e["uid"], 0) + 1

        report = {
            "n_raw": len(raw_actions),
            "n_undo_folded": n_undo,
            "n_cancel_folded": n_cancel,
            "folded": folded,
            "unresolved_refs": unresolved,
            "counter_end": counter,
            "undo_on_empty": undo_on_empty,
            "notes": notes,
        }
        spawns = {e["game_index"]: e["uid"] for e in stream
                  if e.get("t") == "buy"}
        for e in stream:
            if e.get("t") == "gift":
                spawns[e["game_index"]] = e["mech"]
            elif e.get("t") == "reinforce":
                for g in e.get("grants") or []:
                    spawns[g["game_index"]] = g["mech"]
        return NormResult(actions_norm=stream, report=report,
                          counter_start=counter_start, counter_end=counter,
                          spawn_indexes=spawns)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _resolves_sell(raw: dict, skills_raw) -> bool:
        """ReleaseCommanderSkill targeting a unit = sell (skill 900001) when
        the resolved skill is the recycle skill, not a unit-targeting
        tactical (强化训练 1100001 / 再部署 1000001)."""
        sid = _int(raw.get("ID"), 0)
        if sid not in SELL_SKILL_IDS:
            return False
        if _int(raw.get("UnitIndex"), -1) < 0:
            return False
        if sid == 900001:
            return True
        idx = str(raw.get("SkillIndex", 0))
        for entry in skills_raw or ():
            if entry and str(entry.get("index")) == idx:
                try:
                    return int(entry.get("id")) not in UNIT_SKILL_KEEP
                except (TypeError, ValueError):
                    return True
        # unresolved ID=0/SkillIndex=0 unit releases: corpus-measured 398/480
        # are NOT sells (the unit survives to the next snapshot) -> default
        # to a passthrough skill release, not a sell
        return False

    @staticmethod
    def _passthrough(a: dict, k: int) -> dict:
        keep = {kk: vv for kk, vv in a.items() if kk != "type"}
        e = {"t": "passthrough", "raw_type": str(a.get("type")),
             "raw": [k], "raw_rec": keep}
        if a.get("SkillIndex") is not None:
            e["skill_index"] = _int(a.get("SkillIndex"))
        return e
