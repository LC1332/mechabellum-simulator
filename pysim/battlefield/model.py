# BattleInput contracts (battlefield v1, 重构计划 §2.2 第一版冻结对象).
#
# Everything is a frozen dataclass of JSON-safe primitives so the input can be
# digested, dumped and diffed without touching the engine. Digests are content
# digests: the same logical input always produces the same digest regardless
# of dict ordering.
import hashlib
import json
import math


def _canon(obj):
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("non-finite float in battle input: %r" % (obj,))
        return round(obj, 4)
    if isinstance(obj, (tuple, list)):
        return [_canon(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _canon(obj[k]) for k in sorted(obj, key=str)}
    raise TypeError("cannot canonicalize %r" % (obj,))


def _digest(obj) -> str:
    blob = json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


BATTLEFIELD_INPUT_VERSION = "battlefield-input-v1"

# pipeline stage names for compiled modifiers (重构计划 §5 B1 effect order)
MODIFIER_STAGES = ("base", "level", "technology", "officer", "blueprint",
                   "equipment", "tower_buff")


class UnitBattleInput:
    """One persistent unit card entering the battle.

    entity_id   - the transition-layer stable id (never a numpy row);
    spawn_at    - flank teleport seconds (0 = none; 快速传送 halves the base
                  delay at compile time, so the engine just consumes it);
    equipment_id/effect_ids - compiled battle sources (registry-resolved)."""
    __slots__ = ("entity_id", "side", "mech_id", "level", "exp", "position",
                 "rotation", "tech_ids", "equipment_id", "effect_ids",
                 "spawn_at", "source")

    def __init__(self, entity_id, side, mech_id, level, exp=0,
                 position=(0.0, 0.0), rotation=False, tech_ids=(),
                 equipment_id=0, effect_ids=(), spawn_at=0.0, source="state"):
        self.entity_id = int(entity_id)
        self.side = int(side)
        self.mech_id = int(mech_id)
        self.level = int(level)
        self.exp = int(exp)
        self.position = (float(position[0]), float(position[1]))
        self.rotation = bool(rotation)
        self.tech_ids = tuple(int(t) for t in tech_ids)
        self.equipment_id = int(equipment_id)
        self.effect_ids = tuple(int(e) for e in effect_ids)
        self.spawn_at = float(spawn_at)
        self.source = str(source)

    def as_dict(self):
        return {"entity_id": self.entity_id, "side": self.side,
                "mech_id": self.mech_id, "level": self.level, "exp": self.exp,
                "position": list(self.position), "rotation": self.rotation,
                "tech_ids": list(self.tech_ids),
                "equipment_id": self.equipment_id,
                "effect_ids": list(self.effect_ids),
                "spawn_at": self.spawn_at, "source": self.source}

    def __repr__(self):
        return "UnitBattleInput(eid=%s side=%s mech=%s lv=%s eq=%s spawn=%s)" % (
            self.entity_id, self.side, self.mech_id, self.level,
            self.equipment_id, self.spawn_at)


class WorldObject:
    """Towers, buildings and devices as one world-object stream.

    ref is a stable compile-time reference ("tower:<side>:<slot>",
    "bld:<index>", "device:<cid>:<n>") - NOT a numpy row. params is an ordered
    tuple of (name, value) pairs so the object carries its compiled numbers
    (device hp/damage after officer multipliers, tower strengthen, ...) and
    the input digest sees them."""
    __slots__ = ("kind", "side", "ref", "subtype", "position", "params",
                 "persistent", "source")

    def __init__(self, kind, side, ref, subtype=0, position=(0.0, 0.0),
                 params=(), persistent=False, source=""):
        self.kind = str(kind)          # tower | building | device
        self.side = int(side)
        self.ref = str(ref)
        self.subtype = int(subtype)
        self.position = (float(position[0]), float(position[1]))
        self.params = tuple((str(k), float(v)) for (k, v) in params)
        self.persistent = bool(persistent)
        self.source = str(source)

    def as_dict(self):
        return {"kind": self.kind, "side": self.side, "ref": self.ref,
                "subtype": self.subtype, "position": list(self.position),
                "params": [[k, v] for k, v in self.params],
                "persistent": self.persistent, "source": self.source}


class TimedEvent:
    """Pre-fight battlefield skill release compiled to one timed event.

    All corpus releases land at battle t=0 (tools/step8_probe6), so `at`
    stays 0.0 until mid-fight scheduling exists."""
    __slots__ = ("kind", "side", "ref", "skill_id", "position", "at",
                 "params", "source")

    def __init__(self, kind, side, ref, skill_id, position=(0.0, 0.0),
                 at=0.0, params=(), source=""):
        self.kind = str(kind)          # strike | burn | barrier | summon
        self.side = int(side)
        self.ref = str(ref)
        self.skill_id = int(skill_id)
        self.position = (float(position[0]), float(position[1]))
        self.at = float(at)
        self.params = tuple((str(k), float(v)) for (k, v) in params)
        self.source = str(source)

    def as_dict(self):
        return {"kind": self.kind, "side": self.side, "ref": self.ref,
                "skill_id": self.skill_id, "position": list(self.position),
                "at": self.at, "params": [[k, v] for k, v in self.params],
                "source": self.source}


class SideMods:
    """Per-side global buffs compiled for this round (能量塔技能 5/6 today)."""
    __slots__ = ("side", "range_add", "speed_add")

    def __init__(self, side, range_add=0.0, speed_add=0.0):
        self.side = int(side)
        self.range_add = float(range_add)
        self.speed_add = float(speed_add)

    def as_dict(self):
        return {"side": self.side, "range_add": self.range_add,
                "speed_add": self.speed_add}


class BattleInput:
    """Frozen compile output: EnvironmentState + this round's releases."""

    __slots__ = ("ruleset_version", "engine_version", "contract_version",
                 "seed", "units", "world_objects", "events", "side_mods",
                 "officers")

    def __init__(self, ruleset_version, engine_version, seed=0, units=(),
                 world_objects=(), events=(), side_mods=(), officers=((), ()),
                 contract_version=BATTLEFIELD_INPUT_VERSION):
        self.ruleset_version = str(ruleset_version)
        self.engine_version = str(engine_version)
        self.contract_version = str(contract_version)
        self.seed = int(seed)
        self.units = tuple(units)
        self.world_objects = tuple(world_objects)
        self.events = tuple(events)
        self.side_mods = tuple(side_mods)
        # post-bp-stack officer ids per side (blueprint II implies I); the
        # compile owns the stacking rule, the engine consumes final ids
        self.officers = (tuple(officers[0]) if len(officers) > 0 else (),
                         tuple(officers[1]) if len(officers) > 1 else ())

    def as_dict(self):
        return {"contract_version": self.contract_version,
                "ruleset_version": self.ruleset_version,
                "engine_version": self.engine_version, "seed": self.seed,
                "units": [u.as_dict() for u in self.units],
                "world_objects": [o.as_dict() for o in self.world_objects],
                "events": [e.as_dict() for e in self.events],
                "side_mods": [m.as_dict() for m in self.side_mods],
                "officers": [list(o) for o in self.officers]}

    def digest(self) -> str:
        """Content digest of the full compile (units incl. equipment_id,
        world objects with compiled params, timed events, side mods)."""
        return _digest(self.as_dict())
