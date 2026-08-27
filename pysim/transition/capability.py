# Capability registry: which replay-driven mechanics the transition surface
# can execute faithfully, shared by the shard scanner (build time) and the
# game session service (runtime) — one rule source, never two (任务书 G5).
#
# The classifier works on NORM entries (normalize.py output, the stream the
# runtime actually canonicalizes) plus the shared per-round reinforcement
# offers. Blocker codes are the frozen 任务书 §4.1 set.
from .errors import TransitionError

BLOCKER_CODES = (
    "UNSUPPORTED_OPENING",
    "MISSING_REINFORCEMENT_OFFERS",
    "UNSUPPORTED_REINFORCEMENT",
    "MISSING_REINFORCEMENT_EFFECT",
    "UNSUPPORTED_RAW_ACTION",
    "UNSUPPORTED_ACTION_FIELD",
    "MISSING_RULE_DATA",
    "MALFORMED_REPLAY_REFERENCE",
    "POSITION_OUT_OF_DEPLOY_ZONE",
)

# canonical kinds deploy_transition executes with full state effects
SUPPORTED_NORM_KINDS = {"buy", "gift", "unlock", "move", "upgrade", "tech",
                        "sell", "finish", "reinforce"}

# raw passthrough types deploy executes with a COMPLETE modeled effect
# (supply + persistent state + battle side). Everything else is a blocker.
# Blueprint semantics v1 (deploy.BLUEPRINT_OFFICERS / BLUEPRINT_COSTS):
# 1 快速补给, 2 批量征召(buy limit), 3 精英征召(buy level), 4/5/401/501 officers.
_FULLY_MODELED_RAW = {"StrengthenTower", "GiveUp"}
_FULLY_MODELED_BLUEPRINTS = {1, 2, 3, 4, 5, 401, 501}
_FULLY_MODELED_COMMANDER_SKILLS = {1100001}   # 强化训练 exp jump


def classify_norm_entry(e, rec=None, eco=None, gd=None, side=None):
    """Norm entry -> blocker code or None when fully supported.

    `rec` is the round record (commanderSkills_raw needed to resolve
    ID=0 commander releases, mirroring deploy._resolves_to).
    `side` (when given) applies the deploy-zone rule with the same
    orientation as the runtime (deploy.in_own_half): one rule source."""
    from .deploy import in_own_half
    t = e.get("t")
    if t in ("buy", "gift", "unlock", "move", "upgrade", "finish", "sell"):
        if t in ("buy", "gift") and gd is not None:
            mech = int(e.get("uid") or e.get("mech") or 0)
            if mech not in gd.mechs:
                return "MISSING_RULE_DATA"
        if side is not None and t == "buy":
            # buys are strictly own-half in the corpus (110/110); moves may
            # cross the midline (deploy keeps bounds-only for them)
            try:
                y = float(e.get("y"))
            except (TypeError, ValueError):
                y = None
            if y is not None and not in_own_half(int(side), y):
                return "POSITION_OUT_OF_DEPLOY_ZONE"
        return None
    if t == "tech":
        if gd is not None and int(e.get("tech", 0)) not in gd.techs:
            return "MISSING_RULE_DATA"
        return None
    if t == "reinforce":
        if eco is None:
            return None
        item_id = int(e.get("id", 0) or 0)
        if item_id == 0:
            return None                      # skip: handled (+50 bonus)
        if eco.item_cost(item_id) is None:
            return "UNSUPPORTED_REINFORCEMENT"
        grant = eco.item_grant(item_id)
        if grant and gd is not None and grant[0] not in gd.mechs:
            return "MISSING_REINFORCEMENT_EFFECT"
        info = eco.items.get(item_id) or {}
        # 装备 has no state handler AND no engine mechanic (G4 registry v1);
        # 舰长技能/战术 enters the inventory and releases map to battle events
        if info.get("kind") == "装备":
            return "MISSING_REINFORCEMENT_EFFECT"
        return None
    if t == "passthrough":
        return classify_raw(e.get("raw_type"), e.get("raw_rec") or {}, rec)
    return "UNSUPPORTED_RAW_ACTION"


def classify_raw(raw_type, raw_rec, rec=None):
    """Raw passthrough record -> blocker or None (fully modeled)."""
    if raw_type in _FULLY_MODELED_RAW:
        return None
    if raw_type == "ActiveBlueprint":
        rid = _as_int(raw_rec.get("ID"))
        if rid in _FULLY_MODELED_BLUEPRINTS:
            return None
        return "UNSUPPORTED_ACTION_FIELD"     # battle effect unmodeled
    if raw_type == "ActiveEnergyTowerSkill":
        sid = _as_int(raw_rec.get("SkillID"))
        if sid in (5, 6):
            return None                       # stacking round buffs, free
        return "UNSUPPORTED_ACTION_FIELD"     # ids 1/3/4 unmapped
    if raw_type == "ReleaseContraption":
        cid = _as_int(raw_rec.get("ContraptionID"))
        if cid in (10001, 20001):
            return None                       # turret / barrier battle events
        return "UNSUPPORTED_ACTION_FIELD"     # 30001 unmapped
    if raw_type == "ReleaseCommanderSkill":
        from ..skills import COMMANDER_SKILLS
        sid = _as_int(raw_rec.get("ID"))
        if sid in COMMANDER_SKILLS or sid in _FULLY_MODELED_COMMANDER_SKILLS:
            return None
        if sid == 0:
            resolved = _resolve_skill_id(raw_rec, rec)
            if resolved in COMMANDER_SKILLS:
                return None
            # unresolved ID=0 releases are blocked (deploy cannot resolve
            # them either once they reach the passthrough branch)
        return "UNSUPPORTED_ACTION_FIELD"
    return "UNSUPPORTED_RAW_ACTION"


def _resolve_skill_id(raw_rec, rec):
    """SkillIndex -> skill id through the round's commanderSkills_raw
    (same-round snapshot, mirroring deploy._resolves_to's lookup)."""
    sidx = raw_rec.get("SkillIndex")
    if sidx is None or rec is None:
        return None
    for e in rec.get("commanderSkills_raw") or []:
        if str(e.get("index")) == str(sidx):
            return _as_int(e.get("id"))
    return None


def scan_offers(offers, eco, strict_all_supported=False):
    """The 4 shared reinforcement candidates -> blocker or None.

    任务书 G5 strict rule (strict_all_supported=True): every candidate must
    be effect-complete, else the round is unplayable. v1 honest-choice rule
    (default): all four costs must be KNOWN and at least one card fully
    supported — the UI disables unsupported cards and offers the skip, so no
    unmodeled effect ever executes; the strict prefix is still reported in
    the manifest for audit."""
    if not offers:
        return "MISSING_REINFORCEMENT_OFFERS"
    if len(offers) != 4:
        return "MISSING_REINFORCEMENT_OFFERS"
    n_supported = 0
    for item_id in offers:
        if eco.item_cost(int(item_id)) is None:
            return "UNSUPPORTED_REINFORCEMENT"
        b = classify_norm_entry({"t": "reinforce", "id": int(item_id)},
                                None, eco, eco.gd)
        if b is None:
            n_supported += 1
        elif strict_all_supported:
            return b
    if n_supported == 0:
        return "MISSING_REINFORCEMENT_EFFECT"
    return None


def scan_opponent_round(norm_entries, rec, eco, gd, side=None):
    """One opponent round -> (blocker, first offending norm entry) or (None, None)."""
    for e in norm_entries or []:
        b = classify_norm_entry(e, rec, eco, gd, side=side)
        if b:
            return b, e
    return None, None


def scan_option(game, opponent_player, eco, gd, catalog_team_ids=None,
                opening_of=None):
    """Playable prefix (round 0 based count of playable rounds) for one
    opponent option + the full blocker list.

    opening_of: {(side): team_id} recorded ChooseAdvanceTeam ids; both sides
    need a catalog package (the human keeps the recorded candidate of their
    side as one of the 4 opening offers)."""
    blockers = []
    opp, hum = int(opponent_player), 1 - int(opponent_player)
    offers_by_round = {int(k): v for k, v in
                       (game.get("reinforce_offers") or {}).items()}
    # round_count: highest round number present for both players
    rounds0 = {int(r["round"]) for r in game["players"][0]["rounds"]}
    rounds1 = {int(r["round"]) for r in game["players"][1]["rounds"]}
    last_round = min(max(rounds0), max(rounds1)) if rounds0 and rounds1 else 0

    if catalog_team_ids is not None and opening_of:
        for side in (opp, hum):
            tid = opening_of.get(side)
            if tid is None or int(tid) not in catalog_team_ids:
                blockers.append({"code": "UNSUPPORTED_OPENING",
                                 "round": 0, "side": side,
                                 "detail": "team %s not in catalog" % tid})
                return {"playable_through_round": 0,
                        "strict_playable_through_round": 0,
                        "blockers": blockers}

    playable = 0
    strict_playable = 0
    strict_blocked = False
    for rnd in range(1, last_round + 1):
        rec = _round_rec(game, opp, rnd)
        hum_rec = _round_rec(game, hum, rnd)
        if rec is None or hum_rec is None:
            blockers.append({"code": "MALFORMED_REPLAY_REFERENCE",
                             "round": rnd, "side": opp, "detail": "missing round"})
            break
        entries = rec.get("actions_norm")
        if entries is None:
            blockers.append({"code": "MALFORMED_REPLAY_REFERENCE",
                             "round": rnd, "side": opp,
                             "detail": "shard not normalized"})
            break
        # unresolved refs would make the historical plan un-canonicalizable
        nrep = rec.get("norm_report") or {}
        if nrep.get("unresolved_refs"):
            blockers.append({"code": "MALFORMED_REPLAY_REFERENCE",
                             "round": rnd, "side": opp,
                             "detail": "unresolved refs: %s"
                                       % (nrep["unresolved_refs"][:2],)})
            break
        b, entry = scan_opponent_round(entries, rec, eco, gd, side=opp)
        if b:
            blockers.append({"code": b, "round": rnd, "side": opp,
                             "detail": _entry_detail(entry)})
            break
        if rnd >= 2:
            off = offers_by_round.get(rnd)
            ob = scan_offers(off, eco)
            if ob:
                blockers.append({"code": ob, "round": rnd, "side": None,
                                 "detail": "offers %s" % (off,)})
                break
            sb = scan_offers(off, eco, strict_all_supported=True)
            if sb and not strict_blocked:
                strict_blocked = True
                blockers.append({"code": sb, "round": rnd, "side": None,
                                 "detail": "strict: not all 4 offers "
                                           "effect-complete %s" % (off,),
                                 "strict": True})
        playable = rnd
        if not strict_blocked:
            strict_playable = rnd
    return {"playable_through_round": playable,
            "strict_playable_through_round": strict_playable,
            "blockers": blockers}


def _round_rec(game, side, round_no):
    for r in game["players"][side]["rounds"]:
        if int(r["round"]) == int(round_no):
            return r
    return None


def _entry_detail(e):
    if not e:
        return ""
    return "%s %s" % (e.get("t"), {k: v for k, v in e.items()
                                   if k in ("id", "uid", "mech", "tech",
                                            "raw_type")})


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
