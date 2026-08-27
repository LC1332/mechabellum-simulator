# BattleOutcomeV2 (重构计划 §2.2): the versioned fight output carrying
# per-entity and per-world-object results with a content digest. Settlement
# keeps consuming the V1 transition.BattleOutcome during the migration; V2
# is the audit/frontend contract that the compiler->engine pipeline must
# reproduce deterministically.
import hashlib
import json
import math

BATTLEFIELD_OUTCOME_VERSION = "battle-outcome-v2"


def _canon(obj):
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("non-finite float in battle outcome: %r" % (obj,))
        return round(obj, 4)
    if isinstance(obj, (tuple, list)):
        return [_canon(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _canon(obj[k]) for k in sorted(obj, key=str)}
    raise TypeError("cannot canonicalize %r" % (obj,))


class EntityOutcome:
    """One persistent unit's fight result (entity-keyed, never card_idx)."""
    __slots__ = ("entity_id", "side", "damage", "kills", "survived",
                 "exp_after", "level_after")

    def __init__(self, entity_id, side, damage, kills, survived, exp_after,
                 level_after):
        self.entity_id = int(entity_id)
        self.side = int(side)
        self.damage = round(float(damage), 3)
        self.kills = int(kills)
        self.survived = bool(survived)
        self.exp_after = int(exp_after)
        self.level_after = int(level_after)

    def as_dict(self):
        return {"entity_id": self.entity_id, "side": self.side,
                "damage": self.damage, "kills": self.kills,
                "survived": self.survived, "exp_after": self.exp_after,
                "level_after": self.level_after}


class ObjectOutcome:
    """One world object's fight result (stable compile ref keyed)."""
    __slots__ = ("ref", "kind", "side", "alive", "hp_remaining", "killed_by",
                 "score_contribution")

    def __init__(self, ref, kind, side, alive, hp_remaining, killed_by,
                 score_contribution):
        self.ref = str(ref)
        self.kind = str(kind)
        self.side = int(side)
        self.alive = bool(alive)
        self.hp_remaining = round(float(hp_remaining), 3)
        self.killed_by = None if killed_by is None else int(killed_by)
        self.score_contribution = round(float(score_contribution), 3)

    def as_dict(self):
        return {"ref": self.ref, "kind": self.kind, "side": self.side,
                "alive": self.alive, "hp_remaining": self.hp_remaining,
                "killed_by": self.killed_by,
                "score_contribution": self.score_contribution}


class BattleOutcomeV2:
    __slots__ = ("outcome_version", "battle_seed", "winner", "score_by_team",
                 "damage_to_player", "entities", "objects", "end_time",
                 "engine_version", "fidelity_warnings")

    def __init__(self, battle_seed, winner, score_by_team, damage_to_player,
                 entities=(), objects=(), end_time=0.0, engine_version="",
                 fidelity_warnings=(),
                 outcome_version=BATTLEFIELD_OUTCOME_VERSION):
        self.outcome_version = str(outcome_version)
        self.battle_seed = int(battle_seed)
        self.winner = int(winner)
        self.score_by_team = (int(score_by_team[0]), int(score_by_team[1]))
        self.damage_to_player = (int(damage_to_player[0]),
                                 int(damage_to_player[1]))
        self.entities = tuple(entities)
        self.objects = tuple(objects)
        self.end_time = float(end_time)
        self.engine_version = str(engine_version)
        self.fidelity_warnings = tuple(fidelity_warnings)

    def as_dict(self):
        return {"outcome_version": self.outcome_version,
                "battle_seed": self.battle_seed, "winner": self.winner,
                "score_by_team": list(self.score_by_team),
                "damage_to_player": list(self.damage_to_player),
                "entities": [e.as_dict() for e in self.entities],
                "objects": [o.as_dict() for o in self.objects],
                "end_time": round(self.end_time, 4),
                "engine_version": self.engine_version,
                "fidelity_warnings": list(self.fidelity_warnings)}

    def digest(self) -> str:
        blob = json.dumps(_canon(self.as_dict()), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
