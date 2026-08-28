# Legal action space + mask generation for pi_BC (task §4.4/§4.5).
#
# This is the model adapter over the transition rule source: every legality
# check here mirrors deploy_transition's checks through the same public
# helpers (rules.buy_limit_quote / movement_permission / Economy quotes /
# EQUIPMENT_DEFS), and the acceptance agreement is asserted by tests + the
# teacher-forcing recall gate (task T1: target-in-mask recall 100% on Gold).
#
# Coordinates are EGO (own half y<0); emission to the engine flips y and
# is_rotate for side 1. Handles are observation-local; to_engine_action
# translates them through the observation's HandleMap (replay_index), so the
# model never sees entity ids.
from __future__ import annotations

from dataclasses import dataclass, field

from ..transition.model import (EnvironmentState, PlayerState, Phase,
                                ActionKind, CanonicalAction, EntityRef,
                                BuyArgs, MoveArgs, UpgradeArgs, UnlockArgs,
                                TechArgs, SellArgs, ReleaseCommanderSkillArgs,
                                UseEquipmentArgs, ActivateEnergyTowerSkillArgs,
                                UnsupportedArgs)
from .contracts import PROFILE_VERBS
from .observation import PolicyObservationV1, HandleMap, BOARD_X, BOARD_Y

# emission margins: the engine's bounds/own-half checks are strict, so the
# sampled/decoded coordinates keep a small distance from the board edges
BOARD_X_MIN, BOARD_X_MAX = -347.0, 347.0
BOARD_Y_MIN, BOARD_Y_MAX = -297.0, 297.0
BUY_Y_MAX = -3.0             # ego own-half STRICT upper bound for buys

# orientation encodings: MOVE 0=keep 1=rotate 2=standard; BUY 0/1 boolean
ORIENTATION_MOVE = ("KEEP", "ROTATE", "STANDARD")

# fully-modeled passthrough verbs added beyond the task's core profile
# (contract notes document the extension; deploy executes both completely)
EXTRA_VERBS = ("ACTIVE_BLUEPRINT", "RELEASE_CONTRAPTION")
ALL_VERBS = tuple(PROFILE_VERBS) + EXTRA_VERBS
VERB_INDEX = {v: i for i, v in enumerate(ALL_VERBS)}

MAPPED_POSITION_SKILLS = (300001, 300003, 300004, 300007, 100002, 1200001,
                          1200002, 1200003, 1200004, 1200005, 400002,
                          1500001, 1500002)
UNIT_TARGET_SKILLS = (1100001, 1000001)


def mapped_skill_target_kind(sid: int) -> str:
    """How the policy parameterizes a release of skill `sid`:
    'position' | 'unit' | 'none' (unmapped -> RL noop, 执行了但没有效果)."""
    if sid in UNIT_TARGET_SKILLS:
        return "unit"
    if sid in MAPPED_POSITION_SKILLS:
        return "position"
    return "none"


def _raw_passthrough(raw_type: str, fields: dict) -> CanonicalAction:
    return CanonicalAction(
        ActionKind.RAW_UNSUPPORTED,
        UnsupportedArgs(raw_type=raw_type,
                        raw=tuple(sorted((str(k), v) for k, v in fields.items()))))


# ---------------------------------------------------------------- space
@dataclass
class LegalActionSpace:
    """Candidate sets + legality masks for one policy observation."""
    obs: PolicyObservationV1
    verbs: tuple = ALL_VERBS
    # candidate pools (shared across verbs, indexed by pointer heads)
    mech_cands: list[int] = field(default_factory=list)       # mech ids
    unit_cands: list[int] = field(default_factory=list)       # handles
    tech_cands: list[tuple[int, int]] = field(default_factory=list)
    equip_cands: list[int] = field(default_factory=list)      # distinct ids
    skill_cands: list[tuple[int, int]] = field(default_factory=list)  # (slot,id)
    tower_cands: list[int] = field(default_factory=list)
    blueprint_cands: list[int] = field(default_factory=list)
    contraption_cands: list[int] = field(default_factory=list)
    # masks
    verb_mask: list[bool] = field(default_factory=list)
    mech_mask: dict[str, list[bool]] = field(default_factory=dict)
    unit_mask: dict[str, list[bool]] = field(default_factory=dict)
    tech_mask: list[bool] = field(default_factory=list)
    equip_mask: list[bool] = field(default_factory=list)
    equip_unit_mask: dict[int, list[bool]] = field(default_factory=dict)
    skill_mask: list[bool] = field(default_factory=list)
    skill_target: list[str] = field(default_factory=list)
    tower_mask: list[bool] = field(default_factory=list)
    blueprint_mask: list[bool] = field(default_factory=list)
    contraption_mask: list[bool] = field(default_factory=list)
    strengthen_mask: list[bool] = field(default_factory=list)  # tower 0/1

    def verb_allowed(self, verb: str) -> bool:
        return self.verb_mask[VERB_INDEX[verb]]

    def xy_bounds(self, verb: str) -> tuple:
        if verb == "BUY_UNIT":
            return (BOARD_X_MIN, BOARD_X_MAX, BOARD_Y_MIN, BUY_Y_MAX)
        return (BOARD_X_MIN, BOARD_X_MAX, BOARD_Y_MIN, BOARD_Y_MAX)


def build_action_space(state: EnvironmentState, ego: int,
                       obs: PolicyObservationV1,
                       eco) -> LegalActionSpace:
    """Enumerate every verb/candidate the acting side could legally emit next.

    Mirrors deploy_transition checks via the public rule helpers (one rule
    source: rules.py / Economy / EQUIPMENT_DEFS)."""
    if state.phase is not Phase.DEPLOYMENT or state.finished_deploy[ego]:
        raise ValueError("build_action_space needs an unfinished DEPLOYMENT")
    from ..transition.rules import buy_limit_quote
    from ..transition.deploy import (BLUEPRINT_COSTS, BLUEPRINT_SKILLS,
                                     CONTRAPTION_COSTS, TOWER_SKILL_COSTS,
                                     TOWER_STRENGTHEN_COST)
    from ..transition.equipment import EQUIPMENT_DEFS, equipment_target_ok
    from ..skills import COMMANDER_SKILLS, TRANSITION_SKILLS

    p: PlayerState = state.players[ego]
    hm: HandleMap = obs.handle_map
    sp = obs.supply
    quote = buy_limit_quote(p)

    sp_mechs = sorted(int(m) for m in p.unlocked_mechs)
    buyable = [m for m in sp_mechs
               if eco.buy_price(m) is not None
               and eco.buy_price(m) <= sp and quote.remaining > 0]
    unlockable = [m for m in sorted(set(eco.gd.mechs) - set(sp_mechs))
                  if eco.unlock_price(m, p.officers) is not None
                  and eco.unlock_price(m, p.officers) <= sp]
    mech_cands = sorted(set(buyable) | set(unlockable))
    mech_mask = {
        "BUY_UNIT": [m in buyable for m in mech_cands],
        "UNLOCK_UNIT": [m in unlockable for m in mech_cands],
    }

    unit_cands = list(range(len(obs.units)))
    up_mask, sell_mask, move_mask = [], [], []
    for h in unit_cands:
        u = hm.unit_at(p, h)
        price = eco.upgrade_price(u.mech_id) or 0
        price = max(0, price + eco.upgrade_price_mod(u.mech_id, p.officers))
        up_mask.append(u.level < 9 and eco.upgrade_price(u.mech_id) is not None
                       and sp >= price)
        sell_mask.append(True)                 # any field unit is sellable
        move_mask.append(bool(obs.unit_move_ok[h]))

    # tech candidates: FIELD mechs only (same rule as env legal candidates)
    tech_cands = []
    tech_owned = {int(m): set(int(t) for t in ts)
                  for m, ts in ((int(k), v) for k, v in obs.techs.items())}
    for mech in sorted({u.mech_id for u in p.units}):
        card = eco.gd.cards.get(mech)
        techs = tech_owned.get(mech, set())
        for tid in (card.technologies if card else ()):
            if tid in techs:
                continue
            td = eco.gd.techs.get(tid)
            if td is None or (td.previous_tech_id
                              and td.previous_tech_id not in techs):
                continue
            price = eco.tech_price(mech, tid, len(techs), p.officers)
            if price is not None and sp >= price:
                tech_cands.append((mech, tid))

    # equipment: distinct stocked ids. Compatibility is NOT masked here —
    # the restriction tables lag corpus versions, so any (id, unit) pair is
    # emit-able and a refused binding lands in the RL noop path (执行了但
    # 没有效果, NOOP_REASON_CODES); only engine-accepted bindings mutate state
    equip_cands = sorted(set(int(e) for e in obs.equipment_inventory))
    equip_mask = [True] * len(equip_cands)
    equip_unit_mask = {eid: [True] * len(unit_cands) for eid in equip_cands}

    # commander skill releases: ACTIVE inventory slots (engine _find_release_slot)
    skill_cands, skill_mask, skill_target = [], [], []
    for s in obs.skills:
        if not s["active"]:
            continue
        sid = int(s["skill"])
        kind = mapped_skill_target_kind(sid)
        if sid not in COMMANDER_SKILLS and sid not in TRANSITION_SKILLS:
            kind = "none"          # unmapped -> RL noop (执行了但没有效果)
        skill_cands.append((int(s["slot"]), sid))
        skill_mask.append(True)
        skill_target.append(kind)

    # energy tower skills: single purchase per id per round + affordability
    tower_cands = sorted(TOWER_SKILL_COSTS)
    used = {int(t) for t in getattr(p, "tower_mods_raw", ()) or ()}
    sentinels = {int(b) for b in getattr(p, "blueprints_round", ()) or ()}
    tower_mask = []
    for sid in tower_cands:
        if sid in (5, 6):
            ok = sid not in used
        else:
            sent = {1: 101, 3: 102, 4: 103}.get(sid)
            ok = sent is not None and sent not in sentinels
        tower_mask.append(ok and sp >= TOWER_SKILL_COSTS[sid])

    # blueprint research/officers: skill-research ids (1/2/3) accept an
    # owned re-research with no charge; OFFICER ids (4/5/401/501) CHARGE on
    # every purchase, so owned officer ids are not re-emittable
    bp_owned = {int(b) for b in p.blueprints}
    from ..transition.deploy import BLUEPRINT_SKILLS
    blueprint_cands = sorted(BLUEPRINT_COSTS)
    blueprint_mask = [
        (sp >= BLUEPRINT_COSTS[b])
        or (b in bp_owned and b in BLUEPRINT_SKILLS)
        for b in blueprint_cands]

    contraption_cands = sorted(int(c) for c in CONTRAPTION_COSTS)
    contraption_mask = [sp >= CONTRAPTION_COSTS[str(c)]
                        for c in contraption_cands]

    strengthen_mask = []
    for tidx in (0, 1):
        lvl = int(p.tower_strengthen[tidx]) if tidx < len(p.tower_strengthen) else 0
        strengthen_mask.append(lvl < 9 and sp >= TOWER_STRENGTHEN_COST)

    verb_mask = [
        True,                                        # END_DEPLOY
        len(buyable) > 0,                            # BUY_UNIT
        len(unlockable) > 0,                         # UNLOCK_UNIT
        any(up_mask),                                # UPGRADE_UNIT
        len(tech_cands) > 0,                         # BUY_TECH
        any(move_mask),                              # MOVE_UNIT
        bool(obs.units),                             # SELL_UNIT
        any(equip_mask) and any(any(v) for v in equip_unit_mask.values()),
        len(skill_cands) > 0,                        # RELEASE_COMMANDER_SKILL
        any(tower_mask),                             # ACTIVATE_ENERGY_TOWER_SKILL
        any(strengthen_mask),                        # STRENGTHEN_TOWER
        any(blueprint_mask),                         # ACTIVE_BLUEPRINT
        any(contraption_mask),                       # RELEASE_CONTRAPTION
    ]
    return LegalActionSpace(
        obs=obs, mech_cands=mech_cands, unit_cands=unit_cands,
        tech_cands=tech_cands, equip_cands=equip_cands,
        skill_cands=skill_cands, tower_cands=tower_cands,
        blueprint_cands=blueprint_cands, contraption_cands=contraption_cands,
        verb_mask=verb_mask, mech_mask=mech_mask, unit_mask={
            "UPGRADE_UNIT": up_mask, "SELL_UNIT": sell_mask,
            "MOVE_UNIT": move_mask},
        tech_mask=[True] * len(tech_cands), equip_mask=equip_mask,
        equip_unit_mask=equip_unit_mask, skill_mask=skill_mask,
        skill_target=skill_target, tower_mask=tower_mask,
        blueprint_mask=blueprint_mask, contraption_mask=contraption_mask,
        strengthen_mask=strengthen_mask)


# ---------------------------------------------------------------- actions
@dataclass
class RLAction:
    """Policy-level action in observation-local EGO terms (own half y<0)."""
    verb: str
    mech: int | None = None
    handle: int | None = None
    tech: tuple[int, int] | None = None
    equip: int | None = None
    skill_slot: int | None = None
    skill_id: int | None = None
    tower: int | None = None
    tower_index: int | None = None
    blueprint: int | None = None
    contraption: int | None = None
    x: float | None = None
    y: float | None = None
    rot: int | None = None        # MOVE 0=keep/1=rotate/2=standard; BUY 0/1

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None}
        if isinstance(d.get("tech"), tuple):
            d["tech"] = list(d["tech"])
        return d

    @staticmethod
    def from_dict(d: dict) -> "RLAction":
        d = dict(d)
        if isinstance(d.get("tech"), list):
            d["tech"] = tuple(d["tech"])
        return RLAction(**d)


def to_engine_action(a: RLAction, ego: int, hm: HandleMap) -> CanonicalAction:
    """RLAction (ego coords) -> typed CanonicalAction in ENGINE coordinates.

    Side-1 emission flips y and rotation; handles resolve to replay_index."""
    flip = (ego == 1)

    def ey(y):
        return (-float(y)) if flip else float(y)

    def erot(rot):
        if rot is None or rot == 0:
            return None if a.verb == "MOVE_UNIT" else False
        val = (rot == 1)
        return (not val) if flip else val

    v = a.verb
    if v == "END_DEPLOY":
        return CanonicalAction(ActionKind.END_DEPLOY, None)
    if v == "BUY_UNIT":
        return CanonicalAction(ActionKind.BUY_UNIT, BuyArgs(
            mech_id=int(a.mech), x=float(a.x), y=ey(a.y),
            is_rotate=bool(erot(a.rot))))
    if v == "UNLOCK_UNIT":
        return CanonicalAction(ActionKind.UNLOCK_UNIT,
                               UnlockArgs(mech_id=int(a.mech)))
    if v == "UPGRADE_UNIT":
        return CanonicalAction(ActionKind.UPGRADE_UNIT, UpgradeArgs(
            ref=EntityRef(handle=hm.resolve(int(a.handle)))))
    if v == "SELL_UNIT":
        return CanonicalAction(ActionKind.SELL_UNIT, SellArgs(
            ref=EntityRef(handle=hm.resolve(int(a.handle)))))
    if v == "MOVE_UNIT":
        return CanonicalAction(ActionKind.MOVE_UNIT, MoveArgs(
            ref=EntityRef(handle=hm.resolve(int(a.handle))),
            x=float(a.x), y=ey(a.y), is_rotate=erot(a.rot)))
    if v == "BUY_TECH":
        return CanonicalAction(ActionKind.BUY_TECH, TechArgs(
            mech_id=int(a.tech[0]), tech_id=int(a.tech[1])))
    if v == "USE_EQUIPMENT":
        return CanonicalAction(ActionKind.USE_EQUIPMENT, UseEquipmentArgs(
            equipment_id=int(a.equip),
            unit_ref=EntityRef(handle=hm.resolve(int(a.handle)))))
    if v == "RELEASE_COMMANDER_SKILL":
        pos = ((float(a.x), ey(a.y)) if a.y is not None else None)
        return CanonicalAction(ActionKind.RELEASE_COMMANDER_SKILL,
                               ReleaseCommanderSkillArgs(
                                   skill_index=(None if a.skill_slot is None
                                                else int(a.skill_slot)),
                                   skill_id=(None if a.skill_id is None
                                             else int(a.skill_id)),
                                   positions=(pos,) if pos is not None else (),
                                   unit_ref=(None if a.handle is None else
                                             EntityRef(handle=hm.resolve(
                                                 int(a.handle))))))
    if v == "ACTIVATE_ENERGY_TOWER_SKILL":
        return CanonicalAction(ActionKind.ACTIVATE_ENERGY_TOWER_SKILL,
                               ActivateEnergyTowerSkillArgs(skill_id=int(a.tower)))
    if v == "STRENGTHEN_TOWER":
        return _raw_passthrough("StrengthenTower", {"Index": int(a.tower_index)})
    if v == "ACTIVE_BLUEPRINT":
        return _raw_passthrough("ActiveBlueprint", {"ID": int(a.blueprint)})
    if v == "RELEASE_CONTRAPTION":
        return _raw_passthrough("ReleaseContraption", {
            "ContraptionID": str(int(a.contraption)),
            "Position": {"x": float(a.x), "y": ey(a.y)}})
    raise ValueError("unknown verb %s" % v)


def action_from_norm_entry(e: dict, obs: PolicyObservationV1) -> RLAction | None:
    """Norm entry (human, ENGINE coordinates) -> RLAction target (EGO terms)
    for teacher forcing. Returns None for exogenous entries (gift/reinforce)
    and SKIP for no-effect entries the walk should bypass without a sample."""
    t = e.get("t")
    hm = obs.handle_map
    flip = (obs.ego == 1)

    def ey(y):
        return (-float(y)) if flip else float(y)

    def erot(v):
        if v is None:
            return None
        b = bool(v)
        return (1 if ((not b) if flip else b) else 2)

    if t == "finish":
        return RLAction("END_DEPLOY")
    if t == "buy":
        rot = e.get("rot")
        return RLAction("BUY_UNIT", mech=int(e["uid"]), x=float(e["x"]),
                        y=ey(e["y"]),
                        rot=(0 if rot is None else (1 if bool(rot) != flip else 2)))
    if t == "move":
        rot = e.get("rot")
        return RLAction("MOVE_UNIT", handle=hm.handle_of_ridx(int(e["unit"])),
                        x=float(e["x"]), y=ey(e["y"]), rot=erot(rot))
    if t == "upgrade":
        return RLAction("UPGRADE_UNIT",
                        handle=hm.handle_of_ridx(int(e["unit"])))
    if t == "tech":
        # corpus quirk (verified on 896 buys): round-rec techMap ALREADY
        # contains the round's own tech purchases — those entries are
        # pre-applied and their cost lands in the snapshot-derived income,
        # so re-executing would double-charge. Skip owned; buy the rest.
        tid = int(e["tech"])
        mech = int(e["uid"])
        owned = set()
        for mm, ts in ((int(k), v) for k, v in obs.techs.items()):
            owned.update(int(x) for x in ts)
        if tid in owned:
            return SKIP
        return RLAction("BUY_TECH", tech=(mech, tid))
    if t == "unlock":
        return RLAction("UNLOCK_UNIT", mech=int(e["uid"]))
    if t == "sell":
        return RLAction("SELL_UNIT",
                        handle=hm.handle_of_ridx(int(e["unit"])))
    if t == "release":
        slot = e.get("skill_index")
        sid = e.get("skill")
        h = (None if e.get("unit") is None
             else hm.handle_of_ridx(int(e["unit"])))
        xy = e.get("positions") or []
        active_slots = {s["slot"] for s in obs.skills if s["active"]}
        # when the entry's slot is not an ACTIVE inventory slot, emit by
        # skill_id only: the engine resolves the first active slot with the
        # id, or proceeds without slot consumption (deploy _find_release_slot)
        use_slot = (None if slot is None else int(slot))
        if use_slot is not None and sid is not None and use_slot not in active_slots:
            use_slot = None
        return RLAction("RELEASE_COMMANDER_SKILL",
                        skill_slot=use_slot,
                        skill_id=(None if sid is None else int(sid)),
                        handle=h,
                        x=(float(xy[0][0]) if xy else None),
                        y=(ey(xy[0][1]) if xy else None),
                        rot=(1 if xy else None))
    if t == "equip":
        return RLAction("USE_EQUIPMENT", equip=int(e.get("id", 0) or 0),
                        handle=hm.handle_of_ridx(int(e["unit"])))
    if t == "tower_skill":
        return RLAction("ACTIVATE_ENERGY_TOWER_SKILL",
                        tower=int(e.get("skill", 0) or 0))
    if t == "passthrough":
        rt = str(e.get("raw_type"))
        raw = {str(k): v for k, v in (e.get("raw_rec") or {}).items()}
        if rt == "StrengthenTower":
            return RLAction("STRENGTHEN_TOWER",
                            tower_index=int(raw.get("Index", 0) or 0))
        if rt == "ActiveBlueprint":
            return RLAction("ACTIVE_BLUEPRINT",
                            blueprint=int(raw.get("ID", 0) or 0))
        if rt == "ReleaseContraption":
            pos = raw.get("Position") or {}
            return RLAction("RELEASE_CONTRAPTION",
                            contraption=int(raw.get("ContraptionID", 0) or 0),
                            x=float(pos.get("x", 0.0) or 0.0),
                            y=ey(pos.get("y", 0.0) or 0.0))
        if rt == "ReleaseCommanderSkill":
            # ID=0/unmapped releases: the normalizer keeps the raw record as
            # passthrough with the game slot index — the policy target is the
            # slot; execution lands in the RL noop path (执行了但没有效果)
            slot = e.get("skill_index")
            sid = (None if raw.get("ID") in (None, "", 0)
                   else int(raw.get("ID")))
            uid = raw.get("UnitIndex")
            active_slots = {s["slot"] for s in obs.skills if s["active"]}
            if raw.get("ConstructionIndex") not in (None, "", -1):
                # construction recycles have no v0 effect — bypass without a
                # sample (any refund lands in the snapshot-derived income)
                return SKIP
            use_slot = (None if slot is None else int(slot))
            if use_slot is not None and use_slot not in active_slots:
                use_slot = None          # emit by id (or noop) instead
            if sid is None and use_slot is None:
                return SKIP              # no resolvable skill -> no effect
            pos = raw.get("Position") or {}
            return RLAction("RELEASE_COMMANDER_SKILL",
                            skill_slot=use_slot,
                            skill_id=sid,
                            handle=(None if uid in (None, "", -1)
                                    else hm.handle_of_ridx(int(uid))),
                            x=(float(pos.get("x", 0.0) or 0.0)
                               if pos else None),
                            y=(ey(pos.get("y", 0.0) or 0.0)
                               if pos else None))
        return None                    # ChooseAdvanceTeam/GiveUp — not BC
    return None                        # gift/reinforce/surrender: exogenous


SKIP = RLAction(verb="SKIP")


# ---------------------------------------------------------------- serialize
def space_to_dict(space: LegalActionSpace) -> dict:
    """Serializable snapshot of a LegalActionSpace (dataset rows carry this;
    the training loader never touches the transition engine)."""
    return {
        "verbs": list(space.verbs),
        "verb_mask": list(map(int, space.verb_mask)),
        "mech_cands": list(space.mech_cands),
        "mech_mask": {k: list(map(int, v)) for k, v in space.mech_mask.items()},
        "unit_cands": list(space.unit_cands),
        "unit_mask": {k: list(map(int, v)) for k, v in space.unit_mask.items()},
        "tech_cands": [list(t) for t in space.tech_cands],
        "equip_cands": list(space.equip_cands),
        "skill_cands": [list(s) for s in space.skill_cands],
        "skill_target": list(space.skill_target),
        "tower_cands": list(space.tower_cands),
        "blueprint_cands": list(space.blueprint_cands),
        "contraption_cands": list(space.contraption_cands),
        "tech_mask": list(map(int, space.tech_mask)),
        "equip_mask": list(map(int, space.equip_mask)),
        "skill_mask": list(map(int, space.skill_mask)),
        "tower_mask": list(map(int, space.tower_mask)),
        "blueprint_mask": list(map(int, space.blueprint_mask)),
        "contraption_mask": list(map(int, space.contraption_mask)),
        "strengthen_mask": list(map(int, space.strengthen_mask)),
    }


def encode_target(a: RLAction, space: LegalActionSpace) -> dict:
    """Target action -> per-head indices into the space's candidate pools
    (teacher-forcing labels; -1 = head unused for this verb)."""
    v = a.verb
    out = {"verb": VERB_INDEX[v], "mech": -1, "unit": -1, "tech": -1,
           "equip": -1, "skill": -1, "tower": -1, "bp": -1, "contr": -1,
           "tower_index": -1, "x": 0.0, "y": 0.0, "rot": -1}
    if v in ("BUY_UNIT", "UNLOCK_UNIT") and a.mech in space.mech_cands:
        out["mech"] = space.mech_cands.index(a.mech)
    if v in ("UPGRADE_UNIT", "SELL_UNIT", "MOVE_UNIT", "USE_EQUIPMENT",
             "RELEASE_COMMANDER_SKILL") and a.handle is not None \
            and a.handle in space.unit_cands:
        out["unit"] = space.unit_cands.index(a.handle)
    if v == "BUY_TECH":
        for i, t in enumerate(space.tech_cands):
            if tuple(t) == tuple(a.tech):
                out["tech"] = i
                break
    if v == "USE_EQUIPMENT" and a.equip in space.equip_cands:
        out["equip"] = space.equip_cands.index(a.equip)
    if v == "RELEASE_COMMANDER_SKILL":
        for i, (slot, sid) in enumerate(space.skill_cands):
            if (a.skill_slot is not None and slot == a.skill_slot) or \
                    (a.skill_slot is None and sid == a.skill_id):
                out["skill"] = i
                break
    if v == "ACTIVATE_ENERGY_TOWER_SKILL" and a.tower in space.tower_cands:
        out["tower"] = space.tower_cands.index(a.tower)
    if v == "ACTIVE_BLUEPRINT" and a.blueprint in space.blueprint_cands:
        out["bp"] = space.blueprint_cands.index(a.blueprint)
    if v == "RELEASE_CONTRAPTION" and a.contraption in space.contraption_cands:
        out["contr"] = space.contraption_cands.index(a.contraption)
    if v == "STRENGTHEN_TOWER":
        out["tower_index"] = 0 if a.tower_index not in (0, 1) else int(a.tower_index)
    if a.x is not None:
        out["x"] = float(a.x)
        out["y"] = float(a.y)
    if a.rot is not None:
        out["rot"] = int(a.rot)
    return out


def target_in_mask(a: RLAction, space: LegalActionSpace) -> bool:
    """Teacher-forcing gate: is the human target inside the emitted mask?"""
    v = a.verb
    if v == "END_DEPLOY":
        return True
    if not space.verb_allowed(v):
        return False
    if v in ("BUY_UNIT", "UNLOCK_UNIT"):
        if a.mech not in space.mech_cands:
            return False
        return space.mech_mask[v][space.mech_cands.index(a.mech)]
    if v in ("UPGRADE_UNIT", "SELL_UNIT", "MOVE_UNIT"):
        if a.handle not in space.unit_cands:
            return False
        return space.unit_mask[v][space.unit_cands.index(a.handle)]
    if v == "BUY_TECH":
        return tuple(a.tech) in [tuple(t) for t in space.tech_cands]
    if v == "USE_EQUIPMENT":
        if a.equip not in space.equip_cands:
            return False
        i = space.equip_cands.index(a.equip)
        if not space.equip_mask[i]:
            return False
        j = space.unit_cands.index(a.handle) if a.handle in space.unit_cands else -1
        return j >= 0 and space.equip_unit_mask[a.equip][j]
    if v == "RELEASE_COMMANDER_SKILL":
        for k, (slot, sid) in enumerate(space.skill_cands):
            if slot == a.skill_slot or (a.skill_slot is None
                                        and sid == a.skill_id):
                return space.skill_mask[k]
        return False
    if v == "ACTIVATE_ENERGY_TOWER_SKILL":
        return a.tower in space.tower_cands and \
            space.tower_mask[space.tower_cands.index(a.tower)]
    if v == "STRENGTHEN_TOWER":
        ti = 0 if a.tower_index not in (0, 1) else int(a.tower_index)
        return bool(space.strengthen_mask[ti])
    if v == "ACTIVE_BLUEPRINT":
        return a.blueprint in space.blueprint_cands and \
            space.blueprint_mask[space.blueprint_cands.index(a.blueprint)]
    if v == "RELEASE_CONTRAPTION":
        return a.contraption in space.contraption_cands and \
            space.contraption_mask[space.contraption_cands.index(a.contraption)]
    return False
