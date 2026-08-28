# Structured token contract (任务书 §4): BattleTokenObservationV2 /
# PolicyTokenObservationV2 -> typed token arrays, plus the structured
# autoregressive action codec (§5).
#
# Hard rules enforced here:
#  * tokens are produced ONLY by this versioned adapter (tokenizer_version
#    = structured_token_v1); model layers never see replay XML/JSON, labels,
#    FightReport, winners or file identity (§4.1, assert_observation_clean);
#  * NO ordinal positional embedding over entities — order comes from the
#    canonical sort the observation carries; permutation changes nothing in
#    the token multiset (§4.1);
#  * semantic ids go through typed vocabs with an OOV bucket per kind —
#    collisions of the `%64`-hash flavor are impossible by construction
#    (§5.1, tests/rl_transformer::test_vocab_no_collisions);
#  * over-limit observations raise TokenizerError — no silent unit/skill
#    truncation (§4.5).
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .token_contract import (OBSERVATION_VERSION, MAX_ENTITY_TOKENS_HARD,
                             MAX_ACTION_HISTORY, stable_digest,
                             assert_observation_clean)
from . import relative_bias as rb

# ---------------------------------------------------------------- token types
TOKEN_TYPES = (
    "PAD", "VALUE_CLS", "GLOBAL", "SELF_TOWER", "OPP_TOWER", "SELF_UNIT",
    "OPP_UNIT", "SELF_TECH", "OPP_TECH", "CONSTRUCTION", "DEVICE",
    "SKILL_RELEASE", "GROUND_AREA", "INV_TECH", "INV_EQUIP", "INV_SKILL",
    "HISTORY",
)
TT = {name: i for i, name in enumerate(TOKEN_TYPES)}
N_TOKEN_TYPES = len(TOKEN_TYPES)

# relative-bias type groups (relative_bias.TYPE_GROUPS index)
TYPE_GROUP = {
    "PAD": "pad", "VALUE_CLS": "cls", "GLOBAL": "global",
    "SELF_TOWER": "tower", "OPP_TOWER": "tower", "SELF_UNIT": "unit",
    "OPP_UNIT": "unit", "SELF_TECH": "tech", "OPP_TECH": "tech",
    "CONSTRUCTION": "structure", "DEVICE": "structure",
    "SKILL_RELEASE": "skill", "GROUND_AREA": "area", "INV_TECH": "inventory",
    "INV_EQUIP": "inventory", "INV_SKILL": "inventory", "HISTORY": "action",
}
GROUP_INDEX = {g: i for i, g in enumerate(rb.TYPE_GROUPS)}

BOARD_X = 350.0
BOARD_Y = 300.0
N_FEAT = 16
# feat layout: x y side air rot level exp equip value hp cd radius ttl
#              npoints in_area flag
FEAT_SLICES = {"x": 0, "y": 1, "side": 2, "air": 3, "rot": 4, "level": 5,
               "exp": 6, "equip": 7, "value": 8, "hp": 9, "cd": 10,
               "radius": 11, "ttl": 12, "npoints": 13, "in_area": 14,
               "flag": 15}

SIDE_SELF, SIDE_OPP, SIDE_NEUTRAL = 0, 1, -1


class TokenizerError(ValueError):
    pass


# ---------------------------------------------------------------- config
@dataclass
class TokenizerConfig:
    max_entity_tokens: int = 320      # frozen per-run from v2 length stats
    max_history: int = MAX_ACTION_HISTORY
    grid_nx: int = 28                 # coarse-to-fine x buckets (§5.2)
    grid_ny: int = 24
    residual_bins: int = 8
    dx_edges: tuple = rb.DEFAULT_DX_EDGES
    dy_edges: tuple = rb.DEFAULT_DY_EDGES
    dist_edges: tuple = rb.DEFAULT_DIST_EDGES

    def to_dict(self) -> dict:
        return {
            "max_entity_tokens": self.max_entity_tokens,
            "max_history": self.max_history,
            "xy_grid": {"nx": self.grid_nx, "ny": self.grid_ny,
                        "residual_bins": self.residual_bins},
            "bias_buckets": {"dx": list(self.dx_edges),
                             "dy": list(self.dy_edges),
                             "dist": list(self.dist_edges)},
        }

    @staticmethod
    def from_dict(d: dict) -> "TokenizerConfig":
        d = dict(d or {})
        bb = d.pop("bias_buckets", None) or {}
        xg = d.pop("xy_grid", None) or {}
        return TokenizerConfig(
            max_entity_tokens=int(d.get("max_entity_tokens", 320)),
            max_history=int(d.get("max_history", MAX_ACTION_HISTORY)),
            grid_nx=int(xg.get("nx", 28)), grid_ny=int(xg.get("ny", 24)),
            residual_bins=int(xg.get("residual_bins", 8)),
            dx_edges=tuple(bb.get("dx", rb.DEFAULT_DX_EDGES)),
            dy_edges=tuple(bb.get("dy", rb.DEFAULT_DY_EDGES)),
            dist_edges=tuple(bb.get("dist", rb.DEFAULT_DIST_EDGES)))

    def digest(self) -> str:
        return stable_digest(self.to_dict())


# ---------------------------------------------------------------- vocab
class SemanticVocab:
    """Typed id spaces (mech/tech/equip/skill/tower/blueprint/contraption/
    construction). One dict per kind + one shared OOV bucket per kind — no
    cross-kind or same-kind collisions, no modulo hashing (§5.1)."""

    KINDS = ("mech", "tech", "equip", "skill", "tower", "blueprint",
             "contraption", "construction")

    def __init__(self, known: dict | None = None):
        # kind -> {raw_id: compact_id}; compact ids start at 1, 0 = OOV/PAD
        self.tables: dict[str, dict[int, int]] = {
            k: {} for k in self.KINDS}
        for kind, items in (known or {}).items():
            if kind not in self.KINDS:
                raise TokenizerError("unknown vocab kind %s" % kind)
            for rank, raw in enumerate(sorted(int(v) for v in items)):
                self.tables[kind][raw] = rank + 1

    # -- build from gamedata (train-only fitting, §11)
    @classmethod
    def from_gamedata(cls, gd) -> "SemanticVocab":
        v = cls()
        v.register("mech", [int(m) for m in gd.mechs])
        v.register("tech", [int(t) for t in gd.techs])
        cards = getattr(gd, "cards", {}) or {}
        equips, skills, towers, bps, cons = set(), set(), set(), set(), set()
        for c in cards.values():
            if getattr(c, "equipment_id", 0):
                equips.add(int(c.equipment_id))
        try:
            from ...skills import (COMMANDER_SKILLS, TOWER_SKILL_COSTS,
                                   BLUEPRINT_COSTS, CONTRAPTION_COSTS)
            skills |= set(int(s) for s in COMMANDER_SKILLS)
            towers |= set(int(t) for t in TOWER_SKILL_COSTS)
            bps |= set(int(b) for b in BLUEPRINT_COSTS)
            cons |= set(int(c) for c in CONTRAPTION_COSTS)
        except ImportError:
            pass
        v.register("equip", equips)
        v.register("skill", skills)
        v.register("tower", towers)
        v.register("blueprint", bps)
        v.register("contraption", cons)
        return v

    def register(self, kind: str, raw_ids) -> None:
        tab = self.tables[kind]
        for raw in sorted(int(v) for v in raw_ids):
            if raw not in tab:
                tab[raw] = len(tab) + 1

    def id(self, kind: str, raw) -> int:
        return self.tables[kind].get(int(raw), 0)   # 0 = OOV bucket

    def n(self, kind: str) -> int:
        return len(self.tables[kind]) + 1           # + OOV slot

    def sizes(self) -> dict:
        return {k: self.n(k) for k in self.KINDS}

    def to_dict(self) -> dict:
        return {k: {str(raw): i for raw, i in t.items()}
                for k, t in self.tables.items()}

    @staticmethod
    def from_dict(d: dict) -> "SemanticVocab":
        v = SemanticVocab()
        for kind, tab in d.items():
            v.tables[kind] = {int(raw): int(i) for raw, i in tab.items()}
        return v

    def digest(self) -> str:
        return stable_digest(self.to_dict())


# ------------------------------------------------- observation v2 adapters
def battle_token_obs_from_v1(v1: dict) -> dict:
    """BattleObservationV1 dict -> BattleTokenObservationV2 (§4.2).

    v1 fields carry no skill-shape/area detail; the adapter fills the v2
    schema with explicit UNKNOWN markers (never silent absence, §4.2).
    The post-T0 v2 dataset builder replaces the heuristic fields."""
    assert_observation_clean(v1)

    def units(side: dict, side_id: int) -> list:
        out = []
        for u in side.get("units") or []:
            out.append({
                "kind": "self_unit" if side_id == 0 else "opp_unit",
                "mech": int(u["mech"]), "level": int(u["level"]),
                "exp": int(u["exp"]), "x": float(u["x"]), "y": float(u["y"]),
                "rot": bool(u["rot"]), "equip": int(u.get("equip", 0) or 0),
                "air": 0, "value": 0.0, "status": "known", "fidelity": 1.0,
            })
        return out

    def techs(side: dict, side_id: int) -> list:
        out = []
        for m_str, ts in sorted((side.get("techs") or {}).items()):
            for t in ts:
                out.append({"kind": "self_tech" if side_id == 0 else "opp_tech",
                            "mech": int(m_str), "tech": int(t)})
        return out

    def flat(side: dict, kind: str) -> list:
        out = []
        for d in side.get(kind) or []:
            out.append({"kind": kind, "id": int(d["id"]),
                        "x": float(d["x"]), "y": float(d["y"]),
                        "shape": "unknown", "radius": 0.0,
                        "confidence": 0.5, "unknown_mechanism": True})
        return out

    ents = (units(v1["self"], 0) + units(v1["opp"], 1)
            + techs(v1["self"], 0) + techs(v1["opp"], 1)
            + [{"kind": "self_tower", "x": 0.0, "y": -295.0},
               {"kind": "opp_tower", "x": 0.0, "y": 295.0}]
            + flat(v1["self"], "devices") + flat(v1["opp"], "devices")
            + flat(v1["self"], "skill_events")
            + flat(v1["opp"], "skill_events"))
    # ground areas: v1 has none; v2 rows carry ordered points + ttl
    obs = {
        "version": OBSERVATION_VERSION,
        "round": int(v1["round"]), "ego": int(v1["ego"]),
        "global": {
            "self_hp": int(v1["self"]["hp"]),
            "self_max_hp": int(v1["self"]["max_hp"]),
            "opp_hp": int(v1["opp"]["hp"]),
            "opp_max_hp": int(v1["opp"]["max_hp"]),
            "self_tower_strengthen": [int(t) for t in
                                      v1["self"]["tower_strengthen"]],
            "opp_tower_strengthen": [int(t) for t in
                                     v1["opp"]["tower_strengthen"]],
            "self_officers": len(v1["self"].get("officers") or []),
            "opp_officers": len(v1["opp"].get("officers") or []),
            "self_blueprints": len(v1["self"].get("blueprints") or []),
            "opp_blueprints": len(v1["opp"].get("blueprints") or []),
        },
        "entities": ents, "ground_areas": [],
    }
    assert_observation_clean(obs)
    return obs


def policy_token_obs_from_live(obs_v1, space_dict: dict,
                               history: list[dict] | None = None) -> dict:
    """Live PolicyObservationV1 + LegalActionSpace + executed-action history
    -> PolicyTokenObservationV2 (§4.3). `history` entries are the structured
    receipts of THIS prefix only (no human future, §11)."""
    assert_observation_clean(obs_v1.to_dict())
    history = (history or [])[-MAX_ACTION_HISTORY:]
    units = [{
        "kind": "self_unit", "mech": int(u["mech"]), "level": int(u["level"]),
        "exp": int(u["exp"]), "x": float(u["x"]), "y": float(u["y"]),
        "rot": bool(u["rot"]), "equip": int(u.get("equip", 0) or 0),
        "air": 0, "value": 0.0, "status": "known", "fidelity": 1.0,
        "move_ok": bool(obs_v1.unit_move_ok[h]),
    } for h, u in enumerate(obs_v1.units)]
    ents = units + [{"kind": "self_tower", "x": 0.0, "y": -295.0}]
    for d in obs_v1.opp.get("units") or []:
        ents.append({
            "kind": "opp_unit", "mech": int(d["mech"]),
            "level": int(d["level"]), "exp": int(d["exp"]),
            "x": float(d["x"]), "y": float(d["y"]), "rot": bool(d["rot"]),
            "equip": int(d.get("equip", 0) or 0), "air": 0, "value": 0.0,
            "status": "known", "fidelity": 1.0})
    ents.append({"kind": "opp_tower", "x": 0.0, "y": 295.0})
    for m_str, ts in sorted((obs_v1.techs or {}).items()):
        for t in ts:
            ents.append({"kind": "self_tech", "mech": int(m_str),
                         "tech": int(t)})
    for m_str, ts in sorted(((obs_v1.opp or {}).get("techs") or {}).items()):
        for t in ts:
            ents.append({"kind": "opp_tech", "mech": int(m_str),
                         "tech": int(t)})
    for c in (obs_v1.opp or {}).get("devices") or []:
        ents.append({"kind": "device", "id": int(c["id"]),
                     "x": float(c["x"]), "y": float(c["y"]),
                     "shape": "unknown", "radius": 0.0, "confidence": 0.5,
                     "unknown_mechanism": True})
    for s in (obs_v1.opp or {}).get("skill_events") or []:
        ents.append({"kind": "skill_release", "id": int(s["id"]),
                     "x": float(s["x"]), "y": float(s["y"]),
                     "shape": "unknown", "radius": 0.0, "confidence": 0.5,
                     "unknown_mechanism": True})
    obs = {
        "version": OBSERVATION_VERSION,
        "round": int(obs_v1.round), "ego": int(obs_v1.ego),
        "global": {
            "self_hp": int(obs_v1.hp), "self_max_hp": int(obs_v1.max_hp),
            "opp_hp": int(obs_v1.opp["hp"]),
            "opp_max_hp": int(obs_v1.opp["max_hp"]),
            "self_tower_strengthen": [0, 0],
            "opp_tower_strengthen": [0, 0],
            "supply": int(obs_v1.supply),
            "buy_remaining": int(obs_v1.buy_remaining),
            "budget_left": int(obs_v1.budget_left),
            "prefix_len": int(obs_v1.prefix_len),
            "finished_deploy": bool(obs_v1.finished_deploy),
        },
        "entities": ents, "ground_areas": [],
        "policy": {
            "unlocked_mechs": [int(m) for m in obs_v1.unlocked_mechs],
            "skills": [{"slot": int(s["slot"]), "skill": int(s["skill"]),
                        "active": bool(s["active"]), "cd": int(s["cd"])}
                       for s in (obs_v1.skills or [])],
            "equipment_inventory": [int(e) for e in
                                    (obs_v1.equipment_inventory or [])],
            "history": [dict(h) for h in history],
        },
        "space": space_dict,
    }
    assert_observation_clean(obs)
    return obs


def mirror_battle_obs(obs: dict) -> dict:
    """Side swap at observation level (§6.1): sides exchange AND geometry
    mirrors (y negates, rotation flips) — the same rigid transform as
    rl.observation.ego_mirror_state. mirror(mirror(obs)) == obs."""
    def flip_unit(u: dict) -> dict:
        u = dict(u)
        u["y"] = -float(u["y"])
        u["rot"] = not bool(u.get("rot"))
        u["kind"] = ("self_unit" if u["kind"] == "opp_unit" else "opp_unit")
        return u

    def flip_plain(e: dict) -> dict:
        e = dict(e)
        e["y"] = -float(e.get("y", 0.0))
        return e

    def flip_points(e: dict) -> dict:
        e = flip_plain(e)
        e["points"] = [(float(px), -float(py))
                       for (px, py) in (e.get("points") or [])]
        return e

    def side_entities(side_key_for, kinds) -> list:
        return [e for e in obs["entities"] if e["kind"] in kinds]

    self_kinds = ("self_unit", "self_tech")
    opp_kinds = ("opp_unit", "opp_tech")
    ents = []
    ents += [flip_unit(e) for e in side_entities(None, opp_kinds)]
    ents += [flip_unit(e) for e in side_entities(None, self_kinds)]
    for e in obs["entities"]:
        k = e["kind"]
        if k in self_kinds or k in opp_kinds:
            continue
        if k == "self_tower":
            e2 = flip_plain(e); e2["kind"] = "opp_tower"
        elif k == "opp_tower":
            e2 = flip_plain(e); e2["kind"] = "self_tower"
        elif k == "GROUND_AREA" or k == "ground_area":
            e2 = flip_points(e)
        elif k in ("ground_areas",):
            continue
        else:
            e2 = flip_plain(e)
        ents.append(e2)
    g = dict(obs["global"])
    swaps = (("self_hp", "opp_hp"), ("self_max_hp", "opp_max_hp"),
             ("self_officers", "opp_officers"),
             ("self_blueprints", "opp_blueprints"))
    for a, b in swaps:
        if a in g or b in g:
            g[a], g[b] = g.get(b, g.get(a)), g.get(a, g.get(b))
    for k in ("self_tower_strengthen", "opp_tower_strengthen"):
        if k in g:
            g[k] = list(g[k])
    g["self_tower_strengthen"], g["opp_tower_strengthen"] = \
        g.get("opp_tower_strengthen", [0, 0]), g.get("self_tower_strengthen",
                                                     [0, 0])
    out = dict(obs)
    out["global"] = g
    out["entities"] = ents
    out["ground_areas"] = [flip_points(a) for a in
                           (obs.get("ground_areas") or [])]
    return out


def mirror_points(points) -> list:
    """Ordered multi-point mirror (§4.4): order preserved, y negates."""
    return [(float(x), -float(y)) for (x, y) in points]


# ---------------------------------------------------------------- encoding
@dataclass
class TokenArrays:
    """Encoded observation (per sample, no batch dim)."""
    type: np.ndarray            # int64 [T]
    sem: np.ndarray             # int64 [T] semantic id within type
    feat: np.ndarray            # float32 [T, N_FEAT]
    x: np.ndarray               # float32 [T] (0 for non-spatial)
    y: np.ndarray
    side: np.ndarray            # int64 [T] (0/1/-1 neutral)
    group: np.ndarray           # int64 [T] bias type-group
    air: np.ndarray             # int64 [T] (1 air / 0 ground / -1 n/a)
    area: np.ndarray            # int64 [T] first containing area or -1
    mask: np.ndarray            # float32 [T] 1=real token
    index: dict = field(default_factory=dict)   # kind -> token positions
    n_tokens: int = 0

    def digest(self) -> str:
        return stable_digest({
            "type": self.type[:self.n_tokens].tolist(),
            "sem": self.sem[:self.n_tokens].tolist(),
            "feat": np.round(self.feat[:self.n_tokens], 5).tolist(),
        })


def _area_of(x: float, y: float, areas: list) -> int:
    for ai, a in enumerate(areas):
        pts = a.get("points") or []
        if len(pts) >= 2:
            (ax, ay), (bx, by) = pts[0], pts[1]
            if min(ax, bx) <= x <= max(ax, bx) and \
                    min(ay, by) <= y <= max(ay, by):
                return ai
        elif a.get("radius"):
            r = float(a["radius"])
            if (x - float(pts[0][0])) ** 2 + (y - float(pts[0][1])) ** 2 \
                    <= r * r:
                return ai
    return -1


def _unit_feats(e: dict, side: int) -> tuple:
    f = np.zeros(N_FEAT, dtype=np.float32)
    f[FEAT_SLICES["side"]] = float(side)
    f[FEAT_SLICES["air"]] = float(e.get("air", 0))
    f[FEAT_SLICES["rot"]] = 1.0 if e.get("rot") else 0.0
    f[FEAT_SLICES["level"]] = float(e.get("level", 0)) / 9.0
    f[FEAT_SLICES["exp"]] = min(float(e.get("exp", 0)), 20000.0) / 20000.0
    f[FEAT_SLICES["equip"]] = float(e.get("equip", 0) or 0) / 1e7
    f[FEAT_SLICES["value"]] = float(e.get("value", 0.0)) / 100.0
    f[FEAT_SLICES["flag"]] = float(e.get("fidelity", 1.0))
    if "move_ok" in e:
        f[FEAT_SLICES["flag"]] = 1.0 if e["move_ok"] else 0.5
    return f, float(e.get("x", 0.0)), float(e.get("y", 0.0)), side, 0


def _t(type_name: str):
    return TT[type_name]


def encode_battle_tokens(obs: dict, vocab: SemanticVocab,
                         cfg: TokenizerConfig) -> TokenArrays:
    """ObservationV2 -> tokens: [VALUE_CLS, GLOBAL, entities..., areas...]
    (§4.2). Deterministic; entity ORDER follows the input list (already
    canonical upstream); no ordinal position embedding exists."""
    if obs.get("version") != OBSERVATION_VERSION:
        raise TokenizerError("observation version %r != %s" %
                             (obs.get("version"), OBSERVATION_VERSION))
    assert_observation_clean(obs)
    g = obs["global"]
    types, sems, feats, xs, ys, sides, airs = [], [], [], [], [], [], []

    def push(tname, sem_id, feat, x=0.0, y=0.0, side=SIDE_NEUTRAL, air=-1):
        types.append(_t(tname)); sems.append(int(sem_id))
        feats.append(feat); xs.append(float(x)); ys.append(float(y))
        sides.append(side); airs.append(air)

    push("VALUE_CLS", 0, np.zeros(N_FEAT, dtype=np.float32))
    gf = np.zeros(N_FEAT, dtype=np.float32)
    gf[FEAT_SLICES["hp"]] = g["self_hp"] / max(1, g["self_max_hp"])
    gf[FEAT_SLICES["flag"]] = g["opp_hp"] / max(1, g["opp_max_hp"])
    gf[FEAT_SLICES["level"]] = obs["round"] / 40.0
    gf[FEAT_SLICES["value"]] = sum(g.get("self_tower_strengthen",
                                         (0, 0))) / 18.0
    gf[FEAT_SLICES["cd"]] = sum(g.get("opp_tower_strengthen", (0, 0))) / 18.0
    push("GLOBAL", 0, gf)
    areas = obs.get("ground_areas") or []

    for e in obs["entities"]:
        k = e["kind"]
        if k in ("self_unit", "opp_unit"):
            side = SIDE_SELF if k == "self_unit" else SIDE_OPP
            f, x, y, s, a = _unit_feats(e, side)
            push(k, vocab.id("mech", e["mech"]), f, x, y, s, int(e.get("air", 0)))
        elif k in ("self_tech", "opp_tech"):
            side = SIDE_SELF if k == "self_tech" else SIDE_OPP
            f = np.zeros(N_FEAT, dtype=np.float32)
            f[FEAT_SLICES["side"]] = float(side)
            push(k, vocab.id("tech", e["tech"]), f, side=side)
        elif k in ("self_tower", "opp_tower"):
            side = SIDE_SELF if k == "self_tower" else SIDE_OPP
            f = np.zeros(N_FEAT, dtype=np.float32)
            f[FEAT_SLICES["side"]] = float(side)
            push(k, 0, f, float(e.get("x", 0.0)), float(e.get("y", 0.0)),
                 side)
        elif k in ("construction", "device"):
            f = np.zeros(N_FEAT, dtype=np.float32)
            push(k, vocab.id("construction" if k == "construction"
                             else "contraption", e["id"]), f,
                 float(e["x"]), float(e["y"]))
        elif k in ("skill_release",):
            f = np.zeros(N_FEAT, dtype=np.float32)
            f[FEAT_SLICES["radius"]] = float(e.get("radius", 0.0)) / 100.0
            f[FEAT_SLICES["flag"]] = float(e.get("confidence", 0.5))
            f[FEAT_SLICES["npoints"]] = float(len(e.get("points") or ()))
            push(k, vocab.id("skill", e["id"]), f, float(e["x"]),
                 float(e["y"]))
        else:
            raise TokenizerError("unknown entity kind %r" % k)

    for ai, a in enumerate(areas):
        f = np.zeros(N_FEAT, dtype=np.float32)
        pts = a.get("points") or []
        f[FEAT_SLICES["radius"]] = float(a.get("radius", 0.0)) / 100.0
        f[FEAT_SLICES["ttl"]] = float(a.get("ttl", 0))
        f[FEAT_SLICES["npoints"]] = float(len(pts))
        f[FEAT_SLICES["flag"]] = float(a.get("confidence", 0.5))
        x0 = float(pts[0][0]) if pts else 0.0
        y0 = float(pts[0][1]) if pts else 0.0
        push("GROUND_AREA", vocab.id("skill", a.get("skill", 0)), f, x0, y0)

    area_ids = np.asarray(
        [_area_of(x, y, areas) if types[i] in (
            _t("SELF_UNIT"), _t("OPP_UNIT"), _t("CONSTRUCTION"),
            _t("DEVICE")) else -1
         for i, (x, y) in enumerate(zip(xs, ys))], dtype=np.int64)

    return _finalize(types, sems, feats, xs, ys, sides, airs, area_ids, cfg,
                     index={})


def _finalize(types, sems, feats, xs, ys, sides, airs, area_ids, cfg,
              index) -> TokenArrays:
    n = len(types)
    if n > min(cfg.max_entity_tokens, MAX_ENTITY_TOKENS_HARD):
        raise TokenizerError(
            "%d tokens > max_entity_tokens=%d (§4.5: 超限必须精确报错,"
            "不得静默截断)" % (n, cfg.max_entity_tokens))
    ta = TokenArrays(
        type=np.asarray(types, dtype=np.int64),
        sem=np.asarray(sems, dtype=np.int64),
        feat=np.asarray(feats, dtype=np.float32).reshape(n, N_FEAT),
        x=np.asarray(xs, dtype=np.float32),
        y=np.asarray(ys, dtype=np.float32),
        side=np.asarray(sides, dtype=np.int64),
        group=np.asarray([GROUP_INDEX[TOKEN_TYPES[t]] for t in types],
                         dtype=np.int64),
        air=np.asarray(airs, dtype=np.int64),
        area=np.asarray(area_ids, dtype=np.int64),
        mask=np.ones(n, dtype=np.float32),
        index=index, n_tokens=n)
    return ta


# ------------------------------------------- policy: candidate tables
def build_candidate_tables(space: dict, cfg: TokenizerConfig) -> dict:
    """LegalActionSpace dict -> flat object/pointer candidate tables with
    per-verb legality masks (§5.1). Object = PRIMARY_OBJECT; pointer =
    observation token (own units). Arity per skill object comes from the
    registry the tokenizer shares with the engine (§5.3)."""
    from ...skills import RELEASE_POINT_COUNTS
    from .policy_arity import release_arity
    verbs = list(space["verbs"])
    verb_ix = {v: i for i, v in enumerate(verbs)}
    obj_entries, obj_mask_rows = [], []

    def add_pool(pool, entries, mask_by_verb):
        start = len(obj_entries)
        obj_entries.extend({"pool": pool, "value": v} for v in entries)
        for v in verbs:
            m = mask_by_verb.get(v)
            if m is None:
                obj_mask_rows.extend([False] * len(entries))
        # per-verb mask columns appended below

    def add_pool2(pool, entries, mask_by_verb):
        start = len(obj_entries)
        obj_entries.extend({"pool": pool, "value": v} for v in entries)
        col = {}
        for v in verbs:
            m = mask_by_verb.get(v)
            col[v] = [bool(x) for x in m] if m is not None \
                else [False] * len(entries)
        return start, col

    columns = []
    mech = space.get("mech_cands") or []
    columns.append(add_pool2("mech", mech, {
        "BUY_UNIT": space.get("mech_mask", {}).get("BUY_UNIT"),
        "UNLOCK_UNIT": space.get("mech_mask", {}).get("UNLOCK_UNIT")}))
    tech = [tuple(t) for t in space.get("tech_cands") or []]
    columns.append(add_pool2("tech", tech, {
        "BUY_TECH": space.get("tech_mask")}))
    equip = space.get("equip_cands") or []
    columns.append(add_pool2("equip", equip, {"USE_EQUIPMENT":
                                              space.get("equip_mask")}))
    skill = [tuple(s) for s in space.get("skill_cands") or []]
    skill_mask = {}
    if skill:
        sm = space.get("skill_mask") or [True] * len(skill)
        kinds = space.get("skill_target") or ["none"] * len(skill)
        skill_mask["RELEASE_COMMANDER_SKILL"] = [
            bool(sm[i]) and kinds[i] in ("position", "unit", "none")
            for i in range(len(skill))]
    columns.append(add_pool2("skill", skill, skill_mask))
    tower = space.get("tower_cands") or []
    columns.append(add_pool2("tower", tower, {
        "ACTIVATE_ENERGY_TOWER_SKILL": space.get("tower_mask")}))
    bp = space.get("blueprint_cands") or []
    columns.append(add_pool2("blueprint", bp, {
        "ACTIVE_BLUEPRINT": space.get("blueprint_mask")}))
    contr = space.get("contraption_cands") or []
    columns.append(add_pool2("contraption", contr, {
        "RELEASE_CONTRAPTION": space.get("contraption_mask")}))
    columns.append(add_pool2("strengthen", [0, 1], {
        "STRENGTHEN_TOWER": space.get("strengthen_mask")}))

    obj_mask = np.zeros((len(verbs), 0), dtype=bool)
    cols = []
    for start, col in columns:
        cols.append((start, col))
    n_obj = len(obj_entries)
    obj_mask = np.zeros((len(verbs), n_obj), dtype=bool)
    for (start, col) in cols:
        for v, m in col.items():
            if m is not None:
                obj_mask[verb_ix[v], start:start + len(m)] = m

    # pointer candidates: own units in handle order == token order (the
    # policy encoder emits self units first, canonical order, §4.3)
    n_units = sum(1 for u in ((space.get("_n_own_units"),)) for _ in range(u)) \
        if space.get("_n_own_units") else None
    ptr_mask = np.zeros((len(verbs), 1), dtype=bool)
    um = space.get("unit_mask") or {}
    n_pool = max([len(v) for v in um.values()] or [0])
    ptr_mask = np.zeros((len(verbs), n_pool), dtype=bool)
    for v, m in um.items():
        if v in verb_ix:
            ptr_mask[verb_ix[v], :len(m)] = [bool(x) for x in m]
    if "USE_EQUIPMENT" in verb_ix:
        ptr_mask[verb_ix["USE_EQUIPMENT"], :n_pool] = True
    if "RELEASE_COMMANDER_SKILL" in verb_ix:
        unit_target = [kinds[i] == "unit" for i in range(len(skill))] \
            if skill else []
        ptr_mask[verb_ix["RELEASE_COMMANDER_SKILL"], :len(unit_target)] = \
            unit_target

    # per-verb coarse xy legality (§5.2: bounds from the rule layer)
    nx, ny = cfg.grid_nx, cfg.grid_ny
    xy_legal = np.zeros((len(verbs), nx * ny), dtype=bool)
    for v in verbs:
        lo_x, hi_x, lo_y, hi_y = (347.0, -347.0, -3.0, -297.0) \
            if v == "BUY_UNIT" else (347.0, -347.0, 297.0, -297.0)
        lo_x, hi_x = -min(347.0, abs(lo_x)), min(347.0, abs(hi_x))
        lo_y, hi_y = -min(297.0, abs(lo_y)), min(297.0, abs(hi_y))
        if v == "BUY_UNIT":
            lo_y, hi_y = -297.0, -3.0
        cw, ch = 700.0 / nx, 600.0 / ny
        for gy in range(ny):
            y0 = -300.0 + gy * ch
            if y0 > hi_y or y0 + ch < lo_y:
                continue
            for gx in range(nx):
                x0 = -350.0 + gx * cw
                if x0 > hi_x or x0 + cw < lo_x:
                    continue
                xy_legal[verb_ix[v], gy * nx + gx] = True

    # arity per skill object (registry, §5.3) — never guessed by the model
    arities = np.zeros(n_obj, dtype=np.int64)
    for i, e in enumerate(obj_entries):
        if e["pool"] == "skill":
            sid = e["value"][1]
            arities[i] = release_arity(sid)
        elif e["pool"] in ("mech", "contraption"):
            arities[i] = 1
    return {
        "verbs": verbs,
        "obj_entries": obj_entries, "obj_mask": obj_mask,
        "n_ptr": n_pool, "ptr_mask": ptr_mask,
        "xy_legal": xy_legal, "arities": arities,
        "grid": {"nx": nx, "ny": ny,
                 "residual_bins": cfg.residual_bins},
    }


def encode_policy_tokens(obs: dict, vocab: SemanticVocab,
                         cfg: TokenizerConfig) -> tuple:
    """PolicyTokenObservationV2 -> (TokenArrays with index metadata, candidate
    tables). Adds inventory tokens + capped action history (§4.3)."""
    base = obs.get("policy") or {}
    ta = encode_battle_tokens(
        {k: obs[k] for k in ("version", "round", "ego", "global",
                             "entities", "ground_areas")},
        vocab, cfg)
    types, sems, feats, xs, ys, sides, airs = (
        [ta.type.tolist()], [ta.sem.tolist()], [ta.feat.tolist()],
        [ta.x.tolist()], [ta.y.tolist()], [ta.side.tolist()],
        [ta.air.tolist()])
    index = dict(ta.index)
    area_ids = [ta.area.tolist()]

    def push(tname, sem_id, feat, x=0.0, y=0.0, side=SIDE_NEUTRAL, air=-1,
             area=-1):
        types[0].append(_t(tname)); sems[0].append(int(sem_id))
        feats[0].append(list(feat)); xs[0].append(float(x))
        ys[0].append(float(y)); sides[0].append(side); airs[0].append(air)
        area_ids[0].append(area)

    # own-unit token index metadata (pointer candidates point here, §5.1)
    own_unit_pos = [i for i, t in enumerate(types[0])
                    if t == _t("SELF_UNIT")]
    index["self_unit"] = np.asarray(own_unit_pos, dtype=np.int64)
    index["self_tech"] = np.asarray(
        [i for i, t in enumerate(types[0]) if t == _t("SELF_TECH")],
        dtype=np.int64)

    g = obs["global"]
    invf = np.zeros(N_FEAT, dtype=np.float32)
    invf[FEAT_SLICES["value"]] = g.get("supply", 0.0) / 3000.0
    for m in base.get("unlocked_mechs") or []:
        push("INV_TECH" if False else "INV_EQUIP", 0, invf)  # placeholder
    # (unlocked mechs are implicit in own units + inventory tokens below)
    types[0] = types[0][:ta.n_tokens]
    sems[0] = sems[0][:ta.n_tokens]
    feats[0] = feats[0][:ta.n_tokens]
    xs[0] = xs[0][:ta.n_tokens]
    ys[0] = ys[0][:ta.n_tokens]
    sides[0] = sides[0][:ta.n_tokens]
    airs[0] = airs[0][:ta.n_tokens]
    area_ids[0] = area_ids[0][:ta.n_tokens]

    for t in base.get("skills") or []:
        if not t.get("active", True):
            continue
        f = np.zeros(N_FEAT, dtype=np.float32)
        f[FEAT_SLICES["cd"]] = float(t.get("cd", 0)) / 8.0
        push("INV_SKILL", vocab.id("skill", t["skill"]), f)
    for t in base.get("equipment_inventory") or []:
        push("INV_EQUIP", vocab.id("equip", t), np.zeros(
            N_FEAT, dtype=np.float32))
    for e in base.get("history") or []:
        pass  # history appended below through the same push path

    hist = (base.get("history") or [])[-cfg.max_history:]
    for i, h in enumerate(hist):
        from .policy_arity import VERB_SEM
        f = np.zeros(N_FEAT, dtype=np.float32)
        f[FEAT_SLICES["x"]] = float(h.get("x", 0.0)) / BOARD_X
        f[FEAT_SLICES["y"]] = float(h.get("y", 0.0)) / BOARD_Y
        f[FEAT_SLICES["flag"]] = 1.0 if h.get("receipt_ok", True) else 0.0
        f[FEAT_SLICES["npoints"]] = float(len(h.get("points") or ()))
        f[FEAT_SLICES["level"]] = i / float(cfg.max_history)
        push("HISTORY", VERB_SEM.get(str(h.get("verb", "")), 0), f)

    ta2 = _finalize(types[0], sems[0], feats[0], xs[0], ys[0], sides[0],
                    airs[0], area_ids[0], cfg, index)
    tables = build_candidate_tables(obs.get("space") or {}, cfg)
    # pointer candidate p -> encoder token position (own unit p == handle p)
    tables["ptr_token_pos"] = index["self_unit"]
    return ta2, tables


# ------------------------------------------------ structured action codec
VERBS_13 = ("END_DEPLOY", "BUY_UNIT", "UNLOCK_UNIT", "UPGRADE_UNIT",
            "BUY_TECH", "MOVE_UNIT", "SELL_UNIT", "USE_EQUIPMENT",
            "RELEASE_COMMANDER_SKILL", "ACTIVATE_ENERGY_TOWER_SKILL",
            "STRENGTHEN_TOWER", "ACTIVE_BLUEPRINT", "RELEASE_CONTRAPTION")
VERB_INDEX = {v: i for i, v in enumerate(VERBS_13)}
MAX_POINTS = 3
# decoder slot sequence (§5): one structured chain per atomic action
DECODE_SLOTS = ("BOS", "VERB", "OBJ", "PTR", "P1C", "P1X", "P1Y",
                "P2C", "P2X", "P2Y", "P3C", "P3X", "P3Y", "ORI")
SLOT_INDEX = {s: i for i, s in enumerate(DECODE_SLOTS)}


def grid_encode(x: float, y: float, cfg: TokenizerConfig) -> tuple:
    """ego (x, y) -> (coarse, rx, ry) with edges pinned in config (§5.2)."""
    nx, ny, r = cfg.grid_nx, cfg.grid_ny, cfg.residual_bins
    gx = int(np.clip((float(x) + 350.0) / (700.0 / nx), 0, nx - 1))
    gy = int(np.clip((float(y) + 300.0) / (600.0 / ny), 0, ny - 1))
    rx = int(np.clip((float(x) + 350.0 - gx * (700.0 / nx))
                     / (700.0 / nx) * r, 0, r - 1))
    ry = int(np.clip((float(y) + 300.0 - gy * (600.0 / ny))
                     / (600.0 / ny) * r, 0, r - 1))
    return gy * nx + gx, rx, ry


def grid_decode(coarse: int, rx: int, ry: int, cfg: TokenizerConfig) -> tuple:
    nx, ny, r = cfg.grid_nx, cfg.grid_ny, cfg.residual_bins
    gy, gx = divmod(int(coarse), nx)
    x = -350.0 + (gx + (rx + 0.5) / r) * (700.0 / nx)
    y = -300.0 + (gy + (ry + 0.5) / r) * (600.0 / ny)
    return float(x), float(y)


@dataclass
class ActionFields:
    """Ground-truth/decoded structured fields of ONE atomic action.
    -100 marks an absent stage (masked out of the loss, §8.2)."""
    verb: int = -100
    obj: int = -100
    ptr: int = -100
    points: tuple = ()            # ((coarse, rx, ry), ...) arity 0..3
    orient: int = -100

    def to_list(self) -> list:
        out = [self.verb, self.obj, self.ptr]
        for i in range(MAX_POINTS):
            out.extend(self.points[i] if i < len(self.points)
                       else (-100, -100, -100))
        out.append(self.orient)
        return [int(v) for v in out]

    @staticmethod
    def from_list(vals: list) -> "ActionFields":
        pts = []
        for i in range(MAX_POINTS):
            c, rx, ry = vals[3 + 3 * i: 6 + 3 * i]
            if c != -100:
                pts.append((int(c), int(rx), int(ry)))
        return ActionFields(verb=int(vals[0]), obj=int(vals[1]),
                            ptr=int(vals[2]), points=tuple(pts),
                            orient=int(vals[-1]))


def find_obj_index(tables: dict, pool: str, value) -> int:
    for i, e in enumerate(tables["obj_entries"]):
        if e["pool"] != pool:
            continue
        v = e["value"]
        if pool == "tech" and tuple(v) == tuple(value):
            return i
        if pool == "skill" and tuple(v) == tuple(value):
            return i
        if pool not in ("tech", "skill") and v == value:
            return i
    return -1


def action_to_fields(a: dict, tables: dict, cfg: TokenizerConfig) -> ActionFields:
    """Target action dict (RLAction.to_dict + optional 'points') ->
    structured fields. Raises TokenizerError when the target is not in the
    current legal tables (teacher forcing demands 100% in-mask, §13.2)."""
    verb = a["verb"]
    if verb not in VERB_INDEX:
        raise TokenizerError("unknown verb %r" % verb)
    f = ActionFields(verb=VERB_INDEX[verb])
    points = [tuple(p) for p in (a.get("points") or [])]
    if points and a.get("y") is not None and not points:
        points = [(a["x"], a["y"])]
    if a.get("mech") is not None:
        f.obj = _require(find_obj_index(tables, "mech", a["mech"]),
                         "mech %s" % a["mech"], tables)
    elif a.get("tech") is not None:
        f.obj = _require(find_obj_index(tables, "tech", tuple(a["tech"])),
                         "tech %s" % (a["tech"],), tables)
    elif a.get("equip") is not None:
        f.obj = _require(find_obj_index(tables, "equip", a["equip"]),
                         "equip %s" % a["equip"], tables)
    elif a.get("skill_slot") is not None or a.get("skill_id") is not None:
        f.obj = _require(
            find_obj_index(tables, "skill",
                           (a.get("skill_slot"), a.get("skill_id"))),
            "skill %s" % (a.get("skill_slot"), a.get("skill_id")), tables)
    elif a.get("tower") is not None:
        f.obj = _require(find_obj_index(tables, "tower", a["tower"]),
                         "tower %s" % a["tower"], tables)
    elif a.get("blueprint") is not None:
        f.obj = _require(find_obj_index(tables, "blueprint", a["blueprint"]),
                         "blueprint %s" % a["blueprint"], tables)
    elif a.get("contraption") is not None:
        f.obj = _require(find_obj_index(tables, "contraption",
                                        a["contraption"]),
                         "contraption %s" % a["contraption"], tables)
    elif a.get("tower_index") is not None:
        f.obj = _require(find_obj_index(tables, "strengthen",
                                        int(a["tower_index"])),
                         "strengthen %s" % a["tower_index"], tables)
    if a.get("handle") is not None:
        f.ptr = int(a["handle"])
        if f.ptr >= tables["n_ptr"]:
            raise TokenizerError("handle %d outside pointer pool" % f.ptr)
        verb_name = VERBS_13[f.verb]
        if not tables["ptr_mask"][VERB_INDEX[verb_name], f.ptr]:
            raise TokenizerError("target handle %d not in %s mask"
                                 % (f.ptr, verb_name))
    if points:
        if verb == "RELEASE_COMMANDER_SKILL":
            arity = int(tables["arities"][f.obj]) if f.obj >= 0 else len(points)
            if len(points) != arity:
                raise TokenizerError(
                    "点数不符: target %d points != registry arity %d (§5.3)"
                    % (len(points), arity))
        for p in points[:MAX_POINTS]:
            f.points = f.points + (grid_encode(p[0], p[1], cfg),)
    elif a.get("y") is not None:
        f.points = (grid_encode(a["x"], a["y"], cfg),)
    if a.get("rot") is not None:
        f.orient = int(a["rot"])
    # legality spot-checks on the object column
    if f.obj >= 0 and not tables["obj_mask"][f.verb, f.obj]:
        raise TokenizerError("object %d not in %s mask" %
                             (f.obj, VERBS_13[f.verb]))
    return f


def _require(idx: int, what: str, tables) -> int:
    if idx < 0:
        raise TokenizerError("target %s 不在候选表 (target-in-mask 必须 100%%)"
                             % what)
    return idx


def fields_to_action(f: ActionFields, tables: dict,
                     cfg: TokenizerConfig) -> dict:
    """Structured fields -> action dict (ego terms, ordered points kept,
    §5.3). Pointer stays an observation-local handle."""
    verb = VERBS_13[f.verb]
    a = {"verb": verb}
    if f.obj >= 0:
        e = tables["obj_entries"][f.obj]
        pool, v = e["pool"], e["value"]
        if pool == "mech":
            a["mech"] = int(v)
        elif pool == "tech":
            a["tech"] = list(v)
        elif pool == "equip":
            a["equip"] = int(v)
        elif pool == "skill":
            a["skill_slot"], a["skill_id"] = int(v[0]), int(v[1])
        elif pool == "tower":
            a["tower"] = int(v)
        elif pool == "blueprint":
            a["blueprint"] = int(v)
        elif pool == "contraption":
            a["contraption"] = int(v)
        elif pool == "strengthen":
            a["tower_index"] = int(v)
    if f.ptr >= 0:
        a["handle"] = int(f.ptr)
    pts = [grid_decode(c, rx, ry, cfg) for (c, rx, ry) in f.points]
    if len(pts) == 1:
        a["x"], a["y"] = pts[0]
    elif len(pts) > 1:
        a["points"] = [(x, y) for (x, y) in pts]
        a["x"], a["y"] = pts[0]
    if f.orient >= 0:
        a["rot"] = int(f.orient)
    return a


# ------------------------------------------------- length stats (§4.5)
def token_length_stats(observations: list, vocab: SemanticVocab,
                       cfg: TokenizerConfig) -> dict:
    lens = []
    for obs in observations:
        try:
            if "policy" in obs:
                ta, _ = encode_policy_tokens(obs, vocab, cfg)
            else:
                ta = encode_battle_tokens(obs, vocab, cfg)
            lens.append(int(ta.n_tokens))
        except TokenizerError:
            lens.append(-1)          # counted as over-limit, never truncated
    arr = np.asarray([l for l in lens if l > 0], dtype=np.float64)
    n_over = int(sum(1 for l in lens if l < 0))
    return {
        "n": len(lens), "n_over_limit": n_over,
        "p50": float(np.percentile(arr, 50)) if len(arr) else 0.0,
        "p95": float(np.percentile(arr, 95)) if len(arr) else 0.0,
        "p99": float(np.percentile(arr, 99)) if len(arr) else 0.0,
        "max": float(arr.max()) if len(arr) else 0.0,
    }
