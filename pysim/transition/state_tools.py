# canonical serialization, digest, diff, invariants for EnvironmentState.
import hashlib
import json
import math
from dataclasses import is_dataclass
from enum import Enum

from .model import (EnvironmentState, PlayerState, UnitCard, Phase,
                    SCHEMA_VERSION)


def _canon(obj):
    """Structured value -> JSON-safe canonical structure.

    Floats must be finite; sets/maps are ordered deterministically."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("non-finite float in state: %r" % obj)
        return round(obj, 4)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (tuple, list)):
        return [_canon(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_canon(v) for v in obj)
    if isinstance(obj, dict):
        return {str(k): _canon(obj[k]) for k in sorted(obj, key=str)}
    if is_dataclass(obj):
        out = {"__type__": type(obj).__name__}
        for f in obj.__dataclass_fields__:
            out[f] = _canon(getattr(obj, f))
        return out
    raise TypeError("cannot canonicalize %r" % (obj,))


def canonical_dict(state: EnvironmentState) -> dict:
    """Canonical, deterministic dict of a state.

    Unit order inside `units` is normalized by observable identity so that
    semantically equal states (same units, different tuple order) digest the
    same; replay_index stays inside the unit so provenance survives."""
    d = _canon(state)
    for p in d["players"]:
        p["units"] = sorted(
            p["units"],
            key=lambda u: (u["mech_id"], u["level"], u["x"], u["y"],
                           u["is_rotate"], u["exp"], u["equipment_id"],
                           u["entity_id"]))
        p["tech_map"] = sorted(p["tech_map"], key=lambda kv: kv[0])
    return d


def state_digest(state: EnvironmentState) -> str:
    blob = json.dumps(canonical_dict(state), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def diff_state(expected: EnvironmentState, actual: EnvironmentState) -> dict:
    """First divergence + per-field mismatch counts between two states."""
    e, a = canonical_dict(expected), canonical_dict(actual)
    out = {"first_divergence": None, "mismatch_counts": {}}

    def walk(pe, pa, path):
        if out["first_divergence"] and out["mismatch_counts"].get("_cut"):
            return
        if type(pe) is not type(pa):
            out["mismatch_counts"][path] = 1
            if not out["first_divergence"]:
                out["first_divergence"] = {"path": path, "expected": pe,
                                           "actual": pa}
            return
        if isinstance(pe, dict):
            for k in sorted(set(pe) | set(pa)):
                if k not in pe or k not in pa:
                    out["mismatch_counts"]["%s.%s" % (path, k)] = 1
                    if not out["first_divergence"]:
                        out["first_divergence"] = {
                            "path": "%s.%s" % (path, k),
                            "expected": pe.get(k, "<missing>"),
                            "actual": pa.get(k, "<missing>")}
                else:
                    walk(pe[k], pa[k], "%s.%s" % (path, k))
        elif isinstance(pe, list):
            if len(pe) != len(pa):
                out["mismatch_counts"]["%s#len" % path] = 1
            for i, (ve, va) in enumerate(zip(pe, pa)):
                walk(ve, va, "%s[%d]" % (path, i))
        elif pe != pa:
            out["mismatch_counts"][path] = out["mismatch_counts"].get(path, 0) + 1
            if not out["first_divergence"]:
                out["first_divergence"] = {"path": path, "expected": pe,
                                           "actual": pa}

    walk(e, a, "state")
    return out


def assert_state_invariants(state: EnvironmentState) -> None:
    """Raise AssertionError on structural violations (docs §8.3)."""
    ids = [u.entity_id for p in state.players for u in p.units]
    assert len(ids) == len(set(ids)), "duplicate entity ids: %s" % (
        sorted({i for i in ids if ids.count(i) > 1}))
    for p in state.players:
        idxs = [u.replay_index for u in p.units if u.replay_index is not None]
        assert len(idxs) == len(set(idxs)), "duplicate replay_index in player"
        assert p.supply >= 0, "negative supply %d" % p.supply
        assert p.hp >= 0, "negative hp %d" % p.hp
        for u in p.units:
            assert 1 <= u.level <= 9, "level out of range: %r" % (u,)
            assert u.exp >= 0, "negative exp: %r" % (u,)
            assert math.isfinite(u.x) and math.isfinite(u.y), "bad pos: %r" % (u,)
    assert state.next_entity_id > max(ids, default=0), "next_entity_id too small"
    assert isinstance(state.phase, Phase)
    if state.phase is Phase.TERMINAL:
        assert state.terminal_reason, "terminal without reason"


def state_to_dict(state: EnvironmentState) -> dict:
    """Serializable dict for save(); inverse of state_from_dict."""
    return canonical_dict(state)


def state_from_dict(d: dict) -> EnvironmentState:
    """Rebuild a state from canonical_dict output (save/load round-trip)."""

    def unit(u):
        return UnitCard(**{k: v for k, v in u.items() if k != "__type__"})

    def player(p):
        p = {k: v for k, v in p.items() if k != "__type__"}
        p["units"] = tuple(unit(u) for u in p["units"])
        p["unlocked_mechs"] = frozenset(p["unlocked_mechs"])
        p["tech_map"] = tuple((int(m), tuple(t)) for m, t in p["tech_map"])
        for k in ("officers", "blueprints", "commander_skills_raw",
                  "constructions_raw"):
            p[k] = tuple(p.get(k) or ())
        p["tower_strengthen"] = tuple(p.get("tower_strengthen") or (0, 0))
        p["pre_round_fight_result"] = p.get("pre_round_fight_result")
        return PlayerState(**p)

    st = dict(d)
    st["players"] = tuple(player(p) for p in d["players"])
    st["phase"] = Phase(d["phase"])
    st["provenance"] = tuple((k, v) for k, v in d.get("provenance", []))
    st["finished_deploy"] = tuple(d.get("finished_deploy", (False, False)))
    keep = set(EnvironmentState.__dataclass_fields__)
    return EnvironmentState(**{k: v for k, v in st.items()
                               if k in keep and k != "__type__"})


def copy_state(state: EnvironmentState) -> EnvironmentState:
    """Deep copy via the canonical serializer (immutable dataclasses anyway)."""
    return state_from_dict(canonical_dict(state))


def with_player(state: EnvironmentState, idx: int, player: PlayerState,
                **updates) -> EnvironmentState:
    """Functional replace of one player (and optional top-level fields)."""
    players = list(state.players)
    players[idx] = player
    return EnvironmentState(
        schema_version=state.schema_version, ruleset_version=state.ruleset_version,
        engine_version=state.engine_version, round=state.round,
        phase=state.phase, players=tuple(players),
        finished_deploy=updates.get("finished_deploy", state.finished_deploy),
        next_entity_id=updates.get("next_entity_id", state.next_entity_id),
        terminal_reason=updates.get("terminal_reason", state.terminal_reason),
        provenance=updates.get("provenance", state.provenance))
