# Norm action stream -> CanonicalActionPlan (transition v0.1).
#
# Input is the de-undoed norm stream (pysim/transition/normalize.py or the
# rounds_norm.json artifact): atomic, ordered, index-resolved. Undo and
# CancelReleaseCommanderSkill NEVER appear here — the normalizer folded them
# before this point (deploy_transition asserts this defensively). This module
# is a pure type mapping: norm dicts -> typed CanonicalActions; no reference
# heuristics, no counter inference (game indexes come straight from the
# normalizer's sequential counter).
from dataclasses import dataclass

from .errors import TransitionError
from .model import (ActionKind, CanonicalAction, CanonicalActionPlan,
                    BuyArgs, MoveArgs, UpgradeArgs, UnlockArgs, TechArgs,
                    ChooseReinforceArgs, SellArgs, GiftArgs, UnsupportedArgs,
                    ReleaseCommanderSkillArgs, UseEquipmentArgs, EntityRef)

# raw types that must have been folded away before deploy
FORBIDDEN_RAW_TYPES = ("Undo", "CancelReleaseCommanderSkill")


@dataclass
class CanonicalizeReport:
    n_norm: int = 0
    n_emitted: int = 0
    unsupported: tuple = ()       # passthrough raw types seen
    unresolved_refs: tuple = ()   # refs the normalizer could not resolve
    notes: tuple = ()


def canonicalize_plan(player: int, actions: list,
                      player_state=None, economy=None,
                      norm_report: dict | None = None
                      ) -> tuple[CanonicalActionPlan, CanonicalizeReport]:
    """Map a norm action stream to the executable canonical plan.

    `actions` is either a rounds_norm.json `actions_norm` list or the
    equivalent structure produced on the fly by the normalizer (ReplayAdapter
    fallback). Each entry carries the normalizer-assigned game unit indexes;
    unit references are game indexes resolved by deploy through
    UnitCard.replay_index."""
    rep = CanonicalizeReport(n_norm=len(actions or []))
    out = []
    next_new_ref = 0

    def take_ref():
        nonlocal next_new_ref
        r = next_new_ref
        next_new_ref += 1
        return r

    for e in actions or []:
        t = e.get("t")
        k = e.get("raw", [-1])[0] if e.get("raw") else -1
        if t == "buy":
            gi = e.get("game_index")
            out.append(CanonicalAction(
                ActionKind.BUY_UNIT,
                BuyArgs(mech_id=int(e["uid"]), x=float(e["x"]),
                        y=float(e["y"]), new_ref=take_ref(),
                        game_index=(None if gi is None else int(gi))), k))
        elif t == "gift":
            gi = e.get("game_index")
            out.append(CanonicalAction(
                ActionKind.GIFT_UNIT,
                GiftArgs(mech_id=int(e["mech"]),
                         game_index=(None if gi is None else int(gi))), k))
        elif t == "reinforce":
            specs = []
            for g in e.get("grants") or []:
                specs.append((take_ref(), int(g["game_index"])))
            out.append(CanonicalAction(
                ActionKind.CHOOSE_REINFORCE,
                ChooseReinforceArgs(item_id=int(e.get("id", 0) or 0),
                                    grant_specs=tuple(specs)), k))
        elif t == "unlock":
            out.append(CanonicalAction(
                ActionKind.UNLOCK_UNIT, UnlockArgs(mech_id=int(e["uid"])), k))
        elif t == "move":
            out.append(CanonicalAction(
                ActionKind.MOVE_UNIT,
                MoveArgs(ref=EntityRef(handle=int(e["unit"])),
                         x=float(e["x"]), y=float(e["y"]),
                         is_rotate=e.get("rot")), k))
        elif t == "upgrade":
            u = e.get("unit")
            out.append(CanonicalAction(
                ActionKind.UPGRADE_UNIT,
                UpgradeArgs(ref=EntityRef(handle=(None if u is None
                                                  else int(u)))), k))
        elif t == "tech":
            out.append(CanonicalAction(
                ActionKind.BUY_TECH,
                TechArgs(mech_id=int(e["uid"]), tech_id=int(e["tech"])), k))
        elif t == "sell":
            u = e.get("unit")
            out.append(CanonicalAction(
                ActionKind.SELL_UNIT,
                SellArgs(ref=EntityRef(handle=(None if u is None
                                               else int(u)))), k))
        elif t == "release":
            # typed battlefield-skill release (step3 任务书 §5.2): the
            # normalizer already resolved explicit-ID vs SkillIndex
            u = e.get("unit")
            cidx = e.get("construction")
            out.append(CanonicalAction(
                ActionKind.RELEASE_COMMANDER_SKILL,
                ReleaseCommanderSkillArgs(
                    skill_index=(None if e.get("skill_index") is None
                                 else int(e["skill_index"])),
                    skill_id=(None if e.get("skill") is None
                              else int(e["skill"])),
                    positions=tuple((float(x), float(y))
                                    for (x, y) in (e.get("positions") or ())),
                    unit_ref=(None if u is None
                              else EntityRef(handle=int(u))),
                    construction_index=(None if cidx is None
                                        else int(cidx))), k))
        elif t == "equip":
            u = e.get("unit")
            out.append(CanonicalAction(
                ActionKind.USE_EQUIPMENT,
                UseEquipmentArgs(
                    equipment_id=int(e.get("id", 0) or 0),
                    unit_ref=EntityRef(handle=(None if u is None
                                               else int(u)))), k))
        elif t == "finish":
            out.append(CanonicalAction(ActionKind.END_DEPLOY, None, k))
        elif t == "passthrough":
            rt = str(e.get("raw_type"))
            if rt in FORBIDDEN_RAW_TYPES:
                raise TransitionError(
                    "UNDO_IN_NORM_STREAM",
                    "raw %s must be folded by the normalizer, not deployed "
                    "(player %d, raw index %s)" % (rt, player, k))
            raw = tuple(sorted((str(kk), vv) for kk, vv in
                               (e.get("raw_rec") or {}).items()))
            out.append(CanonicalAction(
                ActionKind.RAW_UNSUPPORTED,
                UnsupportedArgs(raw_type=rt, raw=raw), k))
        else:
            rep.notes = rep.notes + ("unknown_norm_type:%s" % t,)

    rep.n_emitted = len(out)
    seen = []
    for a in out:
        if a.kind is ActionKind.RAW_UNSUPPORTED and a.args.raw_type not in seen:
            seen.append(a.args.raw_type)
    rep.unsupported = tuple(seen)
    if norm_report:
        rep.unresolved_refs = tuple(
            (u.get("t"), u.get("unit"), u.get("reason"))
            for u in norm_report.get("unresolved_refs") or ())
    return CanonicalActionPlan(player=player, actions=tuple(out)), rep
