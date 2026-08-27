# Capability registry: which replay-driven mechanics the transition surface
# can execute faithfully, shared by the shard scanner (build time) and the
# game session service (runtime) — one rule source, never two (任务书 G5).
#
# 重构计划 M1/B1: support judgments derive from the battlefield mechanic
# registry (pysim/battlefield/registry.py) — six stages + confidence +
# evidence per (mechanism, id). This module adds the NORM-entry/raw-record
# classification the scanner needs; it must NOT keep a second mechanic table.
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
    "APPROXIMATE_REINFORCEMENT_EFFECT",   # step3: strict-only (equipment)
    "UNSUPPORTED_RAW_ACTION",
    "UNSUPPORTED_ACTION_FIELD",
    "MISSING_RULE_DATA",
    "MALFORMED_REPLAY_REFERENCE",
    "POSITION_OUT_OF_DEPLOY_ZONE",
)

# canonical kinds deploy_transition executes with full state effects
SUPPORTED_NORM_KINDS = {"buy", "gift", "unlock", "move", "upgrade", "tech",
                        "sell", "finish", "reinforce", "release", "equip",
                        "surrender", "tower_skill"}

# raw passthrough types deploy executes with a COMPLETE modeled effect
# (supply + persistent state + battle side). Everything else is a blocker.
# Blueprint semantics (step4 user ruling): 1/2/3 = commander-skill research
# (黏油弹400002/战地回收900001/移动信标1500001, slot granted next round),
# 4/5/401/501 = permanent officers.
_FULLY_MODELED_RAW = {"StrengthenTower", "GiveUp"}
_FULLY_MODELED_BLUEPRINTS = {1, 2, 3, 4, 5, 401, 501}


def _registry():
    from ..battlefield import registry
    return registry


def _mapped_skill_ids():
    from ..skills import COMMANDER_SKILLS, TRANSITION_SKILLS
    return set(COMMANDER_SKILLS) | set(TRANSITION_SKILLS)


def mechanism_support(mechanism: str, ident):
    """step3 任务书 §7.1 two-axis support for one mechanism id, now derived
    from the battlefield registry (M1): transition_complete, battle_fidelity,
    plus confidence and effect_complete (重构计划 §10.1 — effect_complete
    additionally requires six-stage closure AND verified confidence).

    transition_complete: deploy can change economy + persistent state with
    the real rule. battle_fidelity: exact | approximate | unsupported (does
    pysim consume the effect? "exact" means the implementation path is
    complete, NOT that the numbers are oracle-verified — check confidence)."""
    return _registry().mechanism_support(mechanism, ident).two_axis()


def classify_norm_entry(e, rec=None, eco=None, gd=None, side=None):
    """Norm entry -> blocker code or None when fully supported.

    `rec` is the round record (commanderSkills_raw needed to resolve
    ID=0 commander releases, mirroring deploy._resolve_release_id).
    `side` (when given) applies the deploy-zone rule with the same
    orientation as the runtime (deploy.in_own_half): one rule source.

    step3 §7: equipment entries (typed `equip` / 装备 reinforce offers) are
    transition-complete and APPROXIMATE — they classify as supported here
    (runtime playable) and separately shorten the strict-effect prefix."""
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
        if info.get("kind") == "装备":
            # step3 §6: known equipment stocks the inventory (approximate
            # battle fidelity, no runtime blocker); unknown ids block
            if mechanism_support("equipment", item_id)["transition_complete"]:
                return None
            return "MISSING_RULE_DATA"
        return None
    if t == "release":
        sid = e.get("skill")
        if sid is not None and int(sid) in _mapped_skill_ids():
            return None
        return "UNSUPPORTED_ACTION_FIELD"     # precise blocker upstream
    if t == "surrender":
        # battlefield M1: typed GiveUp — deploy executes it terminally
        return None
    if t == "tower_skill":
        # step4 任务书 §1.4: typed 能量塔技能 5/6 (registry-backed support)
        sid = _as_int(e.get("skill"))
        if sid is not None and mechanism_support("tower_skill", sid)[
                "transition_complete"]:
            return None
        return "UNSUPPORTED_ACTION_FIELD"     # ids 1/3/4 unmapped
    if t == "equip":
        if mechanism_support("equipment", e.get("id", 0))[
                "transition_complete"]:
            return None
        return "MISSING_RULE_DATA"
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
        if sid in (1, 3, 4, 5, 6):
            return None                       # one-shot round skills (typed)
        return "UNSUPPORTED_ACTION_FIELD"     # id 2 (never observed) unmapped
    if raw_type == "ReleaseContraption":
        cid = _as_int(raw_rec.get("ContraptionID"))
        if cid in (10001, 20001):
            return None                       # turret / barrier battle events
        return "UNSUPPORTED_ACTION_FIELD"     # 30001 unmapped
    if raw_type == "ReleaseCommanderSkill":
        sid = _as_int(raw_rec.get("ID"))
        if not sid:
            sid = _resolve_skill_id(raw_rec, rec)
        if sid in _mapped_skill_ids():
            return None
        # unmapped (200001 EMP / 1000001 再部署 / ...) and unresolved ID=0
        # releases stay precise blockers — never a wrong approximation
        return "UNSUPPORTED_ACTION_FIELD"
    return "UNSUPPORTED_RAW_ACTION"


def _resolve_skill_id(raw_rec, rec):
    """SkillIndex -> skill id through the round's commanderSkills_raw
    (same-round snapshot, mirroring deploy._resolve_release_id's lookup)."""
    sidx = raw_rec.get("SkillIndex")
    if sidx is None or rec is None:
        return None
    for e in rec.get("commanderSkills_raw") or []:
        if str(e.get("index")) == str(sidx):
            return _as_int(e.get("id"))
    return None


def offer_fidelity(item_id, eco):
    """Two-axis support of one reinforcement offer (equipment approximate;
    confidence/effect_complete ride along from the registry — M1)."""
    info = (eco.items.get(int(item_id)) or {}) if eco else {}
    if info.get("kind") == "装备":
        return mechanism_support("equipment", item_id)
    b = classify_norm_entry({"t": "reinforce", "id": int(item_id)},
                            None, eco, eco.gd if eco else None)
    if b is None:
        return {"transition_complete": True, "battle_fidelity": "exact"}
    return {"transition_complete": False, "battle_fidelity": "unsupported"}


def scan_offers(offers, eco, strict_all_supported=False):
    """The 4 shared reinforcement candidates -> blocker or None.

    任务书 G5 strict rule (strict_all_supported=True): every candidate must
    be effect-complete (six-stage closure AND verified confidence — M1: an
    implemented-but-provisional equipment still fails strict), else the
    round is unplayable. v1 honest-choice rule (default): all four costs
    must be KNOWN and at least one card fully supported — the UI disables
    unsupported cards and offers the skip, so no unmodeled effect ever
    executes; the strict prefix is still reported in the manifest for
    audit."""
    if not offers:
        return "MISSING_REINFORCEMENT_OFFERS"
    if len(offers) != 4:
        return "MISSING_REINFORCEMENT_OFFERS"
    n_supported = 0
    for item_id in offers:
        if eco.item_cost(int(item_id)) is None:
            return "UNSUPPORTED_REINFORCEMENT"
        fid = offer_fidelity(int(item_id), eco)
        if not fid["transition_complete"]:
            if strict_all_supported:
                return "MISSING_REINFORCEMENT_EFFECT"
            continue
        if strict_all_supported and not fid.get("effect_complete"):
            # transition-executable but not effect-complete: battle path
            # partial OR confidence provisional (equipment without oracle
            # A/B) — runtime keeps playing; the strict-effect prefix stops
            return "APPROXIMATE_REINFORCEMENT_EFFECT"
        n_supported += 1
    if n_supported == 0:
        return "MISSING_REINFORCEMENT_EFFECT"
    return None


def scan_opponent_round(norm_entries, rec, eco, gd, side=None):
    """One opponent round -> (blocker, first offending norm entry) or (None, None).

    step4: the round's paid buys must respect the same buy limit as the
    runtime — base 2 + 能量塔技能3 批量征召 purchases earlier in the stream
    (typed `tower_skill` or legacy `ActiveEnergyTowerSkill` passthrough) +
    10004 held at the snapshot or picked mid-round. Blueprint 2 research
    grants NO quota (user ruling; corpus wall 0/16,512)."""
    from .rules import BASE_BUY_LIMIT, EXTRA_DEPLOY_OFFICER
    officers = [int(o) for o in ((rec or {}).get("officers") or [])]
    off_bonus = sum(1 for o in officers if o == EXTRA_DEPLOY_OFFICER)
    mass = 0
    used = 0
    for e in norm_entries or []:
        t = e.get("t")
        if t == "buy":
            used += 1
            if used > BASE_BUY_LIMIT + mass + off_bonus:
                return "BUY_LIMIT_REACHED", e
        elif t == "reinforce" and int(e.get("id", 0) or 0) == \
                EXTRA_DEPLOY_OFFICER:
            off_bonus += 1
        elif t == "tower_skill" and int(e.get("skill", 0) or 0) == 3:
            mass += 1
        elif t == "passthrough":
            rt = e.get("raw_type")
            if rt == "ActiveEnergyTowerSkill" and \
                    _as_int((e.get("raw_rec") or {}).get("SkillID")) == 3:
                mass += 1
        b = classify_norm_entry(e, rec, eco, gd, side=side)
        if b:
            return b, e
    return None, None


def scan_option(game, opponent_player, eco, gd, catalog_team_ids=None,
                opening_of=None):
    """Playable prefixes + blocker list for one opponent option.

    Two axes (step3 任务书 §7.3):
      - runtime_playable_through_round: rounds whose opponent actions and
        offers are transition-executable (approximate equipment INCLUDED);
      - strict_effect_through_round: additionally effect-complete — stops at
        the first round where an approximate mechanic enters (equipment
        offer or opponent UseEquipment);
      - approximate_from_round / approximate_mechanisms: the visibility
        contract for the UI badge (「从 Rn 起装备效果未模拟」).

    opening_of: {(side): team_id} recorded ChooseAdvanceTeam ids; both sides
    need a catalog package (the human keeps the recorded candidate of their
    side as one of the 4 opening offers)."""
    blockers = []
    approximations = []
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
                        "runtime_playable_through_round": 0,
                        "strict_effect_through_round": 0,
                        "approximate_from_round": None,
                        "approximate_mechanisms": [],
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
        # opponent equipment bindings are approximate (transition executes;
        # battle fidelity per registry id — implemented ids no longer mark
        # the round approximate, but stay strict-blocked while provisional)
        for e in entries:
            if e.get("t") == "equip":
                eid = int(e.get("id", 0) or 0)
                fid = mechanism_support("equipment", eid)
                if fid["battle_fidelity"] != "exact":
                    approximations.append({"round": rnd, "side": opp,
                                           "mechanism": "equipment",
                                           "id": eid})
                if fid.get("effect_complete"):
                    continue           # verified + six-stage: not approximate
                if not strict_blocked:
                    strict_blocked = True
                    blockers.append({
                        "code": "APPROXIMATE_REINFORCEMENT_EFFECT",
                        "round": rnd, "side": opp,
                        "detail": "opponent UseEquipment %s (%s)"
                                  % (e.get("id"), fid.get("confidence")),
                        "strict": True})
        if rnd >= 2:
            off = offers_by_round.get(rnd)
            ob = scan_offers(off, eco)
            if ob:
                blockers.append({"code": ob, "round": rnd, "side": None,
                                 "detail": "offers %s" % (off,)})
                break
            for item_id in (off or []):
                fid = offer_fidelity(int(item_id), eco)
                if fid["battle_fidelity"] == "approximate":
                    approximations.append({"round": rnd, "side": None,
                                           "mechanism": "equipment",
                                           "id": int(item_id)})
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
    approx_from = None
    for a in sorted(approximations, key=lambda x: x["round"]):
        approx_from = a["round"]
        break
    return {"playable_through_round": playable,
            "strict_playable_through_round": strict_playable,
            "runtime_playable_through_round": playable,
            "strict_effect_through_round": strict_playable,
            "approximate_from_round": approx_from,
            "approximate_mechanisms": approximations,
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
