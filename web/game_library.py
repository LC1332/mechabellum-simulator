# Replay game library: manifest + lazy per-game shards (任务书 G2/G12,
# step2 G4/G5/G6).
#
# Boot loads ONLY the manifest (never the full corpus); shards load on
# session creation. Corpus absence is a normal state (corpus_available=
# false), never a startup failure.
#
# Manifest schemas replay_game_manifest_v1/v2 are both accepted; option
# dicts are normalized to the step2 presentation contract (round range,
# split blockers, start_mode) in ONE place so the API, the session service
# and the frontend never re-derive it.
import json
import os
import threading

MANIFEST_SCHEMA = "replay_game_manifest_v2"
MANIFEST_SCHEMA_V1 = "replay_game_manifest_v1"
# limited start (受限开始) floor: the server owns every threshold; the
# frontend only reads min_rounds/limited_min_rounds from the summary
LIMITED_MIN_ROUNDS = 3


class GameLibrary:
    def __init__(self, root):
        self.root = root
        self.manifest = None
        self.manifest_path = None
        self.corpus_available = False
        self._shards = {}
        self._lock = threading.Lock()
        # MECHABELLUM_GAME_LIB pins the library dir (tests use the in-repo
        # fixture so a big local corpus cannot change test outcomes)
        env_dir = os.environ.get("MECHABELLUM_GAME_LIB")
        cands = []
        if env_dir:
            cands.append(env_dir if os.path.isabs(env_dir)
                         else os.path.join(root, env_dir))
        cands += [os.path.join(root, "local_data", "replay_game"),
                  os.path.join(root, "data", "samples", "replay_game")]
        for cand in cands:
            mp = os.path.join(cand, "manifest.json")
            if os.path.exists(mp):
                try:
                    m = json.load(open(mp, encoding="utf8"))
                except (OSError, ValueError):
                    continue
                if m.get("schema_version") in (MANIFEST_SCHEMA,
                                               MANIFEST_SCHEMA_V1):
                    self.manifest = m
                    self.manifest_path = mp
                    self.corpus_available = True
                    break
        self.catalog = self._load_catalog(root)

    @staticmethod
    def _load_catalog(root):
        from pysim.transition import opening as opening_mod
        # the committed catalog is repo-anchored (web/..), independent of the
        # library root so test temp libraries still find it
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_dir = os.environ.get("MECHABELLUM_GAME_LIB")
        cands = []
        if env_dir:
            cands.append(os.path.join(env_dir, "opening_catalog.json"))
        cands += [os.path.join(root, "local_data", "replay_game",
                               "opening_catalog.json"),
                  os.path.join(root, "data", "game", "opening_catalog.json"),
                  os.path.join(repo, "data", "game", "opening_catalog.json")]
        for cand in cands:
            if os.path.exists(cand):
                try:
                    return opening_mod.load_catalog(cand)
                except Exception:
                    continue
        return {"schema_version": "opening_catalog_v2", "packages": {}}

    # ---------------------------------------------------------------- normalize
    @staticmethod
    def norm_option(o, min_rounds):
        """Raw manifest option -> step2 presentation contract (G4/G5).

        v1 manifests carried `round_count` = number of round records
        including round 0 ([0..n-1]), so the source range is exactly
        R0..R(n-1); v2 manifests carry the explicit fields and win.
        """
        out = dict(o)
        round_count = int(o.get("round_record_count",
                                o.get("round_count", 0)) or 0)
        out["round_count"] = int(o.get("round_count", round_count) or 0)
        out["round_record_count"] = round_count
        out["round_min"] = int(o.get("round_min", 0) if
                               o.get("round_min") is not None else 0)
        out["round_max"] = int(o.get("round_max",
                                     round_count - 1 if round_count else -1)
                               if o.get("round_max") is not None
                               else (round_count - 1 if round_count else -1))
        ptr = int(o.get("playable_through_round", 0) or 0)
        sptr = int(o.get("strict_playable_through_round", ptr) or 0)
        out["playable_through_round"] = ptr
        out["strict_playable_through_round"] = sptr
        blockers = list(o.get("blockers") or [])
        out["blockers"] = blockers
        # strict blockers only shorten the strict prefix; the runtime stop
        # reason is the first NON-strict blocker (step2 §2.1)
        out["first_runtime_blocker"] = next(
            (b for b in blockers if not b.get("strict")), None)
        out["first_strict_blocker"] = next(
            (b for b in blockers if b.get("strict")), None)
        if ptr >= min_rounds:
            mode = "normal"
        elif ptr >= LIMITED_MIN_ROUNDS:
            mode = "limited"
        else:
            mode = "disabled"
        out["start_mode"] = mode
        out["enabled"] = mode == "normal"      # v1 compat field
        out["shard"] = "/".join(_safe_shard_parts(o.get("shard", "")))
        return out

    # ---------------------------------------------------------------- list
    def summary(self, min_rounds=5):
        opts = [self.norm_option(o, min_rounds)
                for o in (self.manifest or {}).get("options", [])]
        # G5: playable prefix desc, then source length, then name
        opts.sort(key=lambda o: (-(o["playable_through_round"] or 0),
                                 -(o["round_record_count"] or 0),
                                 o["replay_id"], o["opponent_player"]))
        return {
            "schema_version": "replay_library_v2",
            "corpus_available": self.corpus_available,
            "corpus_label": (self.manifest or {}).get("corpus_label"),
            "manifest_schema_version": (self.manifest or {}).get(
                "schema_version"),
            "ruleset_version": (self.manifest or {}).get("ruleset_version"),
            "opening_catalog": {
                "schema_version": self.catalog.get("schema_version"),
                "package_count": len(self.catalog.get("packages") or {}),
            },
            "min_rounds": min_rounds,
            "limited_min_rounds": LIMITED_MIN_ROUNDS,
            "options": opts,
        }

    def option(self, replay_id, opponent_player):
        """Normalized option (same contract as summary rows)."""
        for o in (self.manifest or {}).get("options", []):
            if o["replay_id"] == replay_id and \
                    int(o["opponent_player"]) == int(opponent_player):
                return self.norm_option(o, int((self.manifest or {}).get(
                    "min_rounds_default", 5)))
        return None

    def shard(self, option):
        key = option["replay_id"]
        with self._lock:
            if key in self._shards:
                return self._shards[key]
        rel = _safe_shard_parts(option.get("shard") or
                                "games/%s.json" % key)
        path = os.path.join(os.path.dirname(self.manifest_path), *rel)
        if not os.path.exists(path):
            try:
                from .game_service import GameError
            except ImportError:
                from game_service import GameError
            raise GameError("SHARD_MISSING", 409,
                            "shard for %s missing" % key)
        shard = json.load(open(path, encoding="utf8"))
        with self._lock:
            self._shards[key] = shard
        return shard


def _safe_shard_parts(raw):
    """Normalize a manifest shard path to '/'-separated parts; refuse
    absolute paths and .. traversal so Windows/Linux manifests behave
    identically."""
    rel = str(raw or "").replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or ".." in parts or os.path.isabs(rel) or ":" in rel:
        raise _shard_error(rel)
    return parts


def _shard_error(rel):
    try:
        from .game_service import GameError
    except ImportError:
        from game_service import GameError
    return GameError("SHARD_PATH_UNSAFE", 409,
                     "shard path %r rejected (absolute/traversal)" % rel)
