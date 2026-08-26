# RawActionLog -> CanonicalActionPlan.
#
# Raw records are the rounds.json `actions` lists (lossless re-serialization of
# the replay's PAD_* actionRecords). Canonicalization folds Undo into final
# intent, splits multi-move records into atomic moves and assigns plan-local
# `new_ref`s for bought/granted units. Raw order is preserved: buy level
# bonuses and grant placement are order-sensitive (精英征召 ordering, undo
# stack), so no re-sorting happens here.
#
# Game unit indexes: assigned sequentially from snapshot_max+1 over the
# SURVIVING (post-undo-fold) spawns — the game reuses indexes freed by Undo
# (corpus: buy,buy,undo,undo,buy,buy -> new units keep the first indexes).
from dataclasses import dataclass

from .model import (ActionKind, CanonicalAction, CanonicalActionPlan,
                    BuyArgs, MoveArgs, UpgradeArgs, UnlockArgs, TechArgs,
                    ChooseReinforceArgs, SellArgs, UnsupportedArgs, EntityRef,
                    PlayerState)

SELL_SKILL_IDS = {0, 900001}          # ID=0 + SkillIndex=0 resolves to sell in most records
UNIT_SKILL_KEEP = {1100001, 1000001}  # 强化训练/再部署 target a unit but keep it


@dataclass
class CanonicalizeReport:
    n_raw: int = 0
    n_emitted: int = 0
    n_undo_folded: int = 0
    undo_on_empty: int = 0
    adopted_moves: int = 0        # moves matched to spawns by uid order
    unsupported: tuple = ()       # raw types seen that v0 does not execute
    notes: tuple = ()


def _resolve_sell(raw: dict, skills_raw) -> bool:
    """ReleaseCommanderSkill targeting a unit = sell when the resolved skill
    is the recycle skill (900001) rather than a unit-targeting tactical."""
    sid = int(raw.get("ID", 0) or 0)
    if sid not in SELL_SKILL_IDS:
        return False
    if int(raw.get("UnitIndex", -1) or -1) < 0:
        return False
    if sid == 900001:
        return True
    idx = str(raw.get("SkillIndex", 0))
    for entry in skills_raw or ():
        if entry and str(entry[0]) == idx:
            try:
                return int(entry[1]) not in UNIT_SKILL_KEEP
            except (TypeError, ValueError):
                return True
    return True     # unresolved ID=0/SkillIndex=0 unit-target releases: majority are sells


def canonicalize_plan(player: int, raw_actions: list,
                      player_state: PlayerState,
                      economy=None,
                      first_new_index: int | None = None) -> tuple[CanonicalActionPlan, CanonicalizeReport]:
    """Fold a raw action log into the executable canonical plan.

    Two passes: (1) raw walk folds Undos (which revert the last deploy action
    of any kind) and builds the action skeleton with plan-local new_refs;
    (2) game indexes are assigned to surviving spawns in emission order and
    unit references resolve against them.

    first_new_index: oracle-harness hint for the game's unitIndex counter
    (sold/undone units in past rounds can burn indexes above the snapshot
    max). Sequential runners pass their tracked counter instead."""
    rep = CanonicalizeReport(n_raw=len(raw_actions or []))
    snap_idx = {u.replay_index: u for u in player_state.units
                if u.replay_index is not None}
    max_idx = max(snap_idx, default=-1)
    next_new_ref = 0
    stack = []          # (raw_index, spawn_new_refs | None) undoable steps
    skeleton = []       # [kind, args-kwargs, raw_index, spawn_refs]

    def new_refs(n):
        nonlocal next_new_ref
        refs = tuple(range(next_new_ref, next_new_ref + n))
        next_new_ref += n
        return refs

    for k, a in enumerate(raw_actions or []):
        t = a.get("type")
        if t == "ChooseReinforceItem":
            item_id = int(a.get("ID", 0) or 0)
            grant = economy.item_grant(item_id) if economy else None
            refs = new_refs(grant[1]) if grant else ()
            skeleton.append(["choose", {"item_id": item_id, "refs": refs},
                             k, refs])
            stack.append((k, refs))
        elif t == "BuyUnit":
            pos = a.get("position") or {}
            refs = new_refs(1)
            skeleton.append(["buy", {"mech_id": int(a.get("UID", 0) or 0),
                                     "x": float(pos.get("x", 0.0) or 0.0),
                                     "y": float(pos.get("y", 0.0) or 0.0),
                                     "ref": refs[0],
                                     "uid": int(a.get("UID", 0) or 0)}, k, refs])
            stack.append((k, refs))
        elif t == "MoveUnit":
            for m in a.get("moveUnitDatas") or []:
                pos = m.get("position") or {}
                rot = m.get("isRotate")
                skeleton.append(["move", {
                    "game_index": m.get("unitIndex"),
                    "uid": m.get("unitID"),
                    "x": float(pos.get("x", 0.0) or 0.0),
                    "y": float(pos.get("y", 0.0) or 0.0),
                    "rotate": (None if rot is None else bool(rot))}, k, ()])
                stack.append((k, ()))        # moves ARE undoable (one per record)
        elif t == "UpgradeUnit":
            skeleton.append(["upgrade", {"game_index": a.get("UIDX"),
                                         "uid": int(a.get("UID", 0) or 0)},
                             k, ()])
            stack.append((k, None))
        elif t == "UnlockUnit":
            skeleton.append(["unlock", {"mech_id": int(a.get("UID", 0) or 0)},
                             k, ()])
            stack.append((k, None))
        elif t == "UpgradeTechnology":
            skeleton.append(["tech", {"mech_id": int(a.get("UID", 0) or 0),
                                      "tech_id": int(a.get("TechID", 0) or 0)},
                             k, ()])
            stack.append((k, None))
        elif t == "ReleaseCommanderSkill":
            if _resolve_sell(a, player_state.commander_skills_raw):
                skeleton.append(["sell", {"game_index": a.get("UnitIndex"),
                                          "uid": None}, k, ()])
                stack.append((k, None))
            else:
                skeleton.append(["unsupported", {"raw": a}, k, ()])
        elif t == "FinishDeploy":
            skeleton.append(["finish", {}, k, ()])
        elif t == "Undo":
            # game truth: the undo arrow steps back through EVERY deploy
            # action (buys, upgrades, unlocks, techs, sells AND moves)
            if not stack:
                rep.undo_on_empty += 1
                continue
            k0, refs = stack.pop()
            _drop_skeleton(skeleton, k0, refs)
            rep.n_undo_folded += 1
        else:
            skeleton.append(["unsupported", {"raw": a}, k, ()])

    # ---- pass 2: assign game indexes to surviving spawns, resolve refs ----
    seq = (int(first_new_index) if first_new_index is not None
           else max_idx + 1)
    if seq <= max_idx:
        seq = max_idx + 1
    spawn_index_of_ref = {}      # new_ref -> assigned game index
    mech_of_ref = {}
    for entry in skeleton:
        kind, kw, k, refs = entry
        if kind == "choose":
            item_id = kw["item_id"]
            grant = economy.item_grant(item_id) if economy else None
            specs = []
            if grant:
                for r in kw["refs"]:
                    spawn_index_of_ref[r] = seq
                    mech_of_ref[r] = grant[0]
                    specs.append((r, seq))
                    seq += 1
            entry[1]["grant_specs"] = tuple(specs)
        elif kind == "buy":
            r = kw["ref"]
            spawn_index_of_ref[r] = seq
            mech_of_ref[r] = kw["mech_id"]
            entry[1]["game_index"] = seq
            seq += 1

    ref_of_index = {gi: r for r, gi in spawn_index_of_ref.items()}
    unclaimed = {r for r in spawn_index_of_ref}     # refs not referenced yet

    # reference-driven re-alignment: the game's new-unit indexes can contain
    # burned gaps (sold units, buys undone in EARLIER rounds), so predicted
    # indexes miss. Pair each referenced unknown index (ascending) with the
    # earliest same-uid spawn (spawns are monotonic in the game counter).
    refs = []                                        # (game_index, uid)
    for kind, kw, k, refs_ in skeleton:
        if kind == "move":
            gi, uid = kw["game_index"], kw["uid"]
            if gi is not None and uid is not None:
                refs.append((int(gi), int(uid)))
        elif kind == "upgrade":
            gi, uid = kw["game_index"], int(kw.get("uid") or 0)
            if gi is not None and uid:
                refs.append((int(gi), uid))
    queue_by_uid = {}
    for r in sorted(spawn_index_of_ref):
        queue_by_uid.setdefault(mech_of_ref[r], []).append(r)
    taken = set()
    for gi, uid in sorted(set(refs)):
        if gi <= max_idx or gi in ref_of_index:
            continue
        q = queue_by_uid.get(uid)
        while q and q[0] in taken:
            q.pop(0)
        if q:
            r = q.pop(0)
            taken.add(r)
            ref_of_index[gi] = r
            # retire the stale predicted index of this ref
            old = spawn_index_of_ref[r]
            if ref_of_index.get(old) == r and old not in {g for g, _ in refs}:
                del ref_of_index[old]
    # reflect realignment into the spawn actions' own game indexes
    index_of_ref_now = {}
    for gi, r in ref_of_index.items():
        index_of_ref_now.setdefault(r, gi)
    for entry in skeleton:
        if entry[0] == "buy" and entry[1]["ref"] in index_of_ref_now:
            entry[1]["game_index"] = index_of_ref_now[entry[1]["ref"]]
        elif entry[0] == "choose":
            entry[1]["grant_specs"] = tuple(
                (r, index_of_ref_now.get(r, gi))
                for r, gi in entry[1]["grant_specs"])

    actions = []
    for kind, kw, k, refs in skeleton:
        if kind == "choose":
            actions.append(CanonicalAction(
                ActionKind.CHOOSE_REINFORCE,
                ChooseReinforceArgs(item_id=kw["item_id"],
                                    grant_specs=kw["grant_specs"]), k))
        elif kind == "buy":
            actions.append(CanonicalAction(
                ActionKind.BUY_UNIT,
                BuyArgs(mech_id=kw["mech_id"], x=kw["x"], y=kw["y"],
                        new_ref=kw["ref"], game_index=kw["game_index"]), k))
        elif kind == "move":
            gi = kw["game_index"]
            gi = int(gi) if gi is not None else None
            uid = kw.get("uid")
            uid = int(uid) if uid is not None else None
            ref = None
            if gi is not None and gi in ref_of_index:
                cand = ref_of_index[gi]
                # uid consistency: burned/reused indexes can map this move to
                # the wrong spawn; fall through to adoption on mismatch
                if uid is None or mech_of_ref.get(cand) == uid:
                    ref = cand
            if ref is None and gi is not None and gi in snap_idx and \
                    (uid is None or snap_idx[gi].mech_id == uid):
                ref = None                        # snapshot unit: handle path
            if ref is None and uid is not None:
                # adopt an unclaimed same-type spawn in order (cross-round
                # index burns from sold/undone units make exact indexes
                # ambiguous in this corner)
                cand = next((r for r in sorted(unclaimed)
                             if mech_of_ref.get(r) == uid), None)
                if cand is not None:
                    ref = cand
                    rep.adopted_moves += 1
            if ref is not None:
                unclaimed.discard(ref)
                ent = EntityRef(new_ref=ref)
            else:
                ent = EntityRef(handle=gi)
            actions.append(CanonicalAction(
                ActionKind.MOVE_UNIT,
                MoveArgs(ref=ent, x=kw["x"], y=kw["y"],
                         is_rotate=kw["rotate"]), k))
        elif kind == "upgrade":
            gi = kw["game_index"]
            gi = int(gi) if gi is not None else None
            uid = int(kw.get("uid") or 0)
            ref = None
            if gi is not None and gi in ref_of_index:
                cand = ref_of_index[gi]
                if uid == 0 or mech_of_ref.get(cand) == uid:
                    ref = cand
            if ref is None and uid and gi is not None and gi > max_idx:
                # fresh spawn with a burned/shifted index: adopt by unit type
                cand = next((r for r in sorted(unclaimed)
                             if mech_of_ref.get(r) == uid), None)
                if cand is not None:
                    ref = cand
                    rep.adopted_moves += 1
            if ref is not None:
                unclaimed.discard(ref)
                ent = EntityRef(new_ref=ref)
            else:
                ent = EntityRef(handle=gi)
            actions.append(CanonicalAction(ActionKind.UPGRADE_UNIT,
                                           UpgradeArgs(ref=ent), k))
        elif kind == "unlock":
            actions.append(CanonicalAction(ActionKind.UNLOCK_UNIT,
                                           UnlockArgs(mech_id=kw["mech_id"]), k))
        elif kind == "tech":
            actions.append(CanonicalAction(
                ActionKind.BUY_TECH,
                TechArgs(mech_id=kw["mech_id"], tech_id=kw["tech_id"]), k))
        elif kind == "sell":
            gi = kw["game_index"]
            gi = int(gi) if gi is not None else None
            ref = ref_of_index.get(gi) if gi is not None else None
            if ref is None and gi is not None and gi > max_idx:
                # sell of a fresh spawn whose burned index is off: adopt the
                # lowest unclaimed spawn (no uid channel on skill releases)
                cand = next((r for r in sorted(unclaimed)), None)
                if cand is not None:
                    ref = cand
                    rep.adopted_moves += 1
            if ref is not None:
                unclaimed.discard(ref)
                ent = EntityRef(new_ref=ref)
            else:
                ent = EntityRef(handle=gi)
            actions.append(CanonicalAction(ActionKind.SELL_UNIT,
                                           SellArgs(ref=ent), k))
        elif kind == "finish":
            actions.append(CanonicalAction(ActionKind.END_DEPLOY, None, k))
        else:
            actions.append(_unsupported(kw["raw"], k))

    rep.n_emitted = len(actions)
    seen = []
    for a in actions:
        if a.kind is ActionKind.RAW_UNSUPPORTED and a.args.raw_type not in seen:
            seen.append(a.args.raw_type)
    rep.unsupported = tuple(seen)
    return CanonicalActionPlan(player=player, actions=tuple(actions)), rep


def _drop_skeleton(skeleton, raw_index, dead_refs):
    """Remove ALL skeleton entries of an undone raw record (a multi-unit
    MoveUnit record reverts as a whole)."""
    i = 0
    while i < len(skeleton):
        if skeleton[i][2] == raw_index:
            del skeleton[i]
        else:
            i += 1


def _unsupported(a: dict, k: int) -> CanonicalAction:
    raw = tuple(sorted((str(kk), vv) for kk, vv in a.items() if kk != "type"))
    return CanonicalAction(ActionKind.RAW_UNSUPPORTED,
                           UnsupportedArgs(raw_type=str(a.get("type")),
                                           raw=raw), raw_index=k)
