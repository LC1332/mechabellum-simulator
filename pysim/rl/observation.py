# Observation contracts (task §4): BattleObservationV1 (pre-battle, both
# sides public) and PolicyObservationV1 (deploy-phase ego view), plus the ego
# mirror and the observation-local handle map.
#
# Ego convention (task §4.3): the acting side always owns the LOWER half
# (y < 0), like replay side 0. For side 1 every y and tower/device/skill
# position is negated (y' = -y) and is_rotate flips; x stays. Mirroring twice
# is the identity. Unit order inside each side is canonical (sorted by
# (x, y, mech, level, replay-free tie-break)) so permutation tests pass and
# pooled encoders never see list order.
#
# Handles (task §4.4): PolicyObservation units carry observation-local handles
# 0..n-1 in canonical order. The mask/action layer translates a handle back to
# the transition engine's EntityRef via HandleMap (handle -> replay_index,
# unique + reversible within one observation). entity_id never leaves this
# module's audit fields; the training loader drops it.
from __future__ import annotations

from dataclasses import dataclass, field

from ..transition.model import (EnvironmentState, PlayerState, Phase, UnitCard)
from .contracts import OBSERVATION_VERSION, stable_digest

BOARD_X = 350.0      # replay board half-width
BOARD_Y = 300.0      # replay board half-height


def mirror_y(y: float) -> float:
    return -float(y)


def canonical_sort_key(rec: dict) -> tuple:
    return (rec["y"], rec["x"], rec["mech"], rec["level"], rec["exp"], rec["rot"])


def _as_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    try:
        return bool(int(v))
    except (TypeError, ValueError):
        return bool(v)


def _skills_state(cs_raw) -> list[dict]:
    out = []
    for entry in cs_raw or ():
        idx, sid, active, cd = entry
        out.append({"slot": int(idx), "skill": int(sid), "active": _as_bool(active),
                    "cd": int(cd)})
    return sorted(out, key=lambda s: s["slot"])


def _side_public(p: PlayerState, flip: bool, include_private: bool) -> dict:
    """Public board of one side in the EGO frame (own half y<0).

    The ego transform is ONE rigid mirror of the whole board applied when
    the ego side is 1: every y negates (own half y>0 -> y<0, and the
    opponent's half lands in y>0) and every rotation flips with it."""
    units = []
    for u in p.units:
        y = float(u.y) if not flip else mirror_y(u.y)
        units.append(unit_record_with_rot(u, y, not flip))
    units.sort(key=canonical_sort_key)
    d = {
        "hp": int(p.hp), "max_hp": int(p.max_hp),
        "units": units,
        "techs": {str(m): sorted(int(t) for t in ts)
                  for m, ts in sorted(p.tech_map, key=lambda kv: int(kv[0]))},
        "officers": sorted(int(o) for o in p.officers),
        "blueprints": sorted(int(b) for b in p.blueprints),
        "tower_strengthen": [int(p.tower_strengthen[0]), int(p.tower_strengthen[1])],
        "tower_mods": sorted(int(t) for t in
                             getattr(p, "tower_mods_raw", ()) or ()),
        "devices": sorted(
            ({"id": int(c[0]), "x": float(c[1]),
              "y": float(c[2]) if not flip else mirror_y(c[2])}
             for c in getattr(p, "devices_raw", ()) or ()),
            key=lambda c: (c["y"], c["x"], c["id"])),
        "skill_events": sorted(
            ({"id": int(s[0]), "x": float(s[1]),
              "y": float(s[2]) if not flip else mirror_y(s[2])}
             for s in getattr(p, "skill_events_raw", ()) or ()),
            key=lambda s: (s["y"], s["x"], s["id"])),
    }
    if include_private:
        d["skills"] = _skills_state(p.commander_skills_raw)
        d["equipment_inventory"] = sorted(int(e) for e in
                                          getattr(p, "equipment_inventory",
                                                  ()) or ())
        d["constructions"] = sorted(
            ({"id": int(c[1]), "x": float(c[2]),
              "y": float(c[3]) if ego else mirror_y(c[3])}
             for c in getattr(p, "constructions_raw", ()) or ()),
            key=lambda c: (c["y"], c["x"], c["id"]))
    return d


def unit_record_with_rot(u, y: float, unflipped: bool) -> dict:
    # side 1's frame mirrors to the ego frame -> rotation flips with it
    rot = bool(u.is_rotate) if unflipped else (not bool(u.is_rotate))
    return {
        "mech": int(u.mech_id), "level": int(u.level), "exp": int(u.exp),
        "x": round(float(u.x), 1), "y": round(y, 1),
        "rot": rot, "equip": int(u.equipment_id or 0),
    }


# ---------------------------------------------------------------- V1 battle
@dataclass
class BattleObservationV1:
    """Both sides finished deploying, battle not run (task §4.1)."""
    round: int
    ego: int                      # 0/1, the side the observation is from
    self_side: dict
    opp_side: dict
    version: str = OBSERVATION_VERSION

    def to_dict(self) -> dict:
        return {"version": self.version, "round": self.round, "ego": self.ego,
                "self": self.self_side, "opp": self.opp_side}

    def digest(self) -> str:
        return stable_digest(self.to_dict())


def battle_observation(state: EnvironmentState, ego: int) -> BattleObservationV1:
    if state.phase is not Phase.PRE_BATTLE:
        raise ValueError("battle_observation needs PRE_BATTLE, got %s"
                         % state.phase.value)
    p, o = state.players[ego], state.players[1 - ego]
    flip = (ego == 1)
    return BattleObservationV1(round=int(state.round), ego=int(ego),
                               self_side=_side_public(p, flip,
                                                      include_private=False),
                               opp_side=_side_public(o, flip,
                                                      include_private=False))


# ---------------------------------------------------------------- handle map
class HandleMap:
    """Observation-local handle -> transition EntityRef handle (replay_index).

    Built from the live PlayerState at observation time; unique + reversible
    within that observation. The canonical order here MUST match the unit
    order the observation reports, so masks emit actions that resolve to the
    exact same unit (task §4.4 100% rule)."""

    def __init__(self, player: PlayerState, side: int = 0):
        entries = [(u.replay_index, u) for u in player.units]
        # handle = canonical presentation index; _present_order maps handle ->
        # units-list position. replay_index is the engine's resolution key
        # (deploy._find_unit), looked up through the same presentation order.
        self._present_order = sorted(
            range(len(entries)),
            key=lambda pos: canonical_sort_key(
                unit_record_with_rot(entries[pos][1],
                                     float(entries[pos][1].y)
                                     if side == 0 else mirror_y(entries[pos][1].y),
                                     side == 0)))
        self._handle_to_ridx: list[int | None] = \
            [entries[pos][0] for pos in self._present_order]
        self._ridx_to_handle = {ri: h for h, ri in
                                enumerate(self._handle_to_ridx) if ri is not None}

    def __len__(self) -> int:
        return len(self._handle_to_ridx)

    def ridx(self, handle: int) -> int | None:
        return self._handle_to_ridx[handle]

    def resolve(self, handle: int) -> int:
        """Engine EntityRef.handle for an observation handle (KeyError when
        the snapshot unit lacks a game index — legacy corpora)."""
        ri = self._handle_to_ridx[handle]
        if ri is None:
            raise KeyError("unit without replay_index at handle %d" % handle)
        return ri

    def handle_of_ridx(self, ridx: int) -> int | None:
        return self._ridx_to_handle.get(int(ridx))

    def unit_at(self, player: PlayerState, handle: int) -> UnitCard:
        """The live UnitCard behind an observation handle."""
        return player.units[self._present_order[handle]]


def unit_handle_map(player: PlayerState, side: int = 0) -> HandleMap:
    return HandleMap(player, side)


# ---------------------------------------------------------------- V1 policy
@dataclass
class PolicyObservationV1:
    """Ego deploy-phase observation (task §4.2/§4.4)."""
    round: int
    ego: int
    hp: int
    max_hp: int
    supply: int
    buy_remaining: int                # buy-limit quote remaining
    finished_deploy: bool
    units: list[dict]                 # canonical order; handle == list index
    unit_move_ok: list[bool]          # parallel to units
    unit_move_reasons: list[list[str]]
    unlocked_mechs: list[int]
    techs: dict                       # mech -> owned tech ids
    officers: list[int] = None        # own officer ids (public in game)
    skills: list[dict] = None         # own commander skill slots
    equipment_inventory: list[int] = None
    opp: dict = None                  # public opponent board (mirror-adjusted)
    prefix_len: int = 0
    budget_left: int = 0
    version: str = OBSERVATION_VERSION
    _handle: HandleMap | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        d = {"version": self.version, "round": self.round, "ego": self.ego,
             "hp": self.hp, "max_hp": self.max_hp, "supply": self.supply,
             "buy_remaining": self.buy_remaining,
             "finished_deploy": self.finished_deploy, "units": self.units,
             "unit_move_ok": self.unit_move_ok,
             "unit_move_reasons": self.unit_move_reasons,
             "unlocked_mechs": self.unlocked_mechs, "techs": self.techs,
             "officers": self.officers or [], "skills": self.skills or [],
             "equipment_inventory": self.equipment_inventory, "opp": self.opp,
             "prefix_len": self.prefix_len, "budget_left": self.budget_left}
        return d

    def digest(self) -> str:
        return stable_digest(self.to_dict())

    @property
    def handle_map(self) -> HandleMap:
        if self._handle is None:
            raise ValueError("observation built without a HandleMap")
        return self._handle


def policy_observation(state: EnvironmentState, ego: int,
                       buy_remaining: int, prefix_len: int = 0,
                       budget_left: int = 0) -> PolicyObservationV1:
    from ..transition.rules import movement_permission
    if state.phase is not Phase.DEPLOYMENT:
        raise ValueError("policy_observation needs DEPLOYMENT, got %s"
                         % state.phase.value)
    p, o = state.players[ego], state.players[1 - ego]
    hm = HandleMap(p, ego)
    units, move_ok, move_reasons = [], [], []
    for pos in range(len(p.units)):
        u = p.units[pos]
        y = float(u.y) if ego == 0 else mirror_y(u.y)
        units.append(unit_record_with_rot(u, y, ego == 0))   # own side only
        perm = movement_permission(p, u)
        move_ok.append(bool(perm.allowed))
        move_reasons.append(list(perm.reasons))
    # present units in canonical order (handle order follows)
    order = hm._present_order
    units = [units[i] for i in order]
    move_ok = [move_ok[i] for i in order]
    move_reasons = [move_reasons[i] for i in order]
    opp = _side_public(o, ego == 1, include_private=False)
    return PolicyObservationV1(
        round=int(state.round), ego=int(ego), hp=int(p.hp), max_hp=int(p.max_hp),
        supply=int(p.supply), buy_remaining=int(buy_remaining),
        finished_deploy=bool(state.finished_deploy[ego]), units=units,
        unit_move_ok=move_ok, unit_move_reasons=move_reasons,
        unlocked_mechs=sorted(int(m) for m in p.unlocked_mechs),
        techs={str(m): sorted(int(t) for t in ts)
               for m, ts in sorted(p.tech_map, key=lambda kv: int(kv[0]))},
        officers=sorted(int(o) for o in p.officers),
        skills=_skills_state(p.commander_skills_raw),
        equipment_inventory=sorted(int(e) for e in
                                   getattr(p, "equipment_inventory", ()) or ()),
        opp=opp, prefix_len=int(prefix_len), budget_left=int(budget_left),
        _handle=hm)


# ---------------------------------------------------------------- mirrors
def ego_mirror_state(state: EnvironmentState) -> EnvironmentState:
    """Swap sides AND mirror geometry: the state side-1 player sees after
    sitting on the other end of the table. mirror(mirror(s)) == s up to
    entity_id renumbering (entity ids are audit-only)."""
    from ..transition.model import PlayerState, EnvironmentState
    import dataclasses

    def flip_player(p: PlayerState) -> PlayerState:
        units = [dataclasses.replace(u, y=mirror_y(u.y),
                                     is_rotate=(not u.is_rotate)) for u in p.units]
        units.sort(key=lambda u: (u.y, u.x, u.mech_id, u.level))
        devices = tuple((c[0], c[1], c[2], mirror_y(c[3]))
                        for c in getattr(p, "devices_raw", ()) or ())
        skills = tuple((s[0], s[1], s[2], mirror_y(s[3]))
                       for s in getattr(p, "skill_events_raw", ()) or ())
        cons = tuple((c[0], c[1], c[2], mirror_y(c[3]))
                     for c in getattr(p, "constructions_raw", ()) or ())
        return dataclasses.replace(
            p, units=tuple(units), devices_raw=devices,
            skill_events_raw=skills, constructions_raw=cons)

    a, b = state.players
    return dataclasses.replace(state, players=(flip_player(b), flip_player(a)))
