# Replay game library: manifest + lazy per-game shards (任务书 G2/G12).
#
# Boot loads ONLY the manifest (never the full corpus); shards load on
# session creation. Corpus absence is a normal state (corpus_available=
# false), never a startup failure.
import json
import os
import threading

MANIFEST_SCHEMA = "replay_game_manifest_v1"


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
                if m.get("schema_version") == MANIFEST_SCHEMA:
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
        return {"schema_version": "opening_catalog_v1", "packages": {}}

    # ---------------------------------------------------------------- list
    def summary(self, min_rounds=5):
        opts = []
        for o in (self.manifest or {}).get("options", []):
            opts.append({
                "replay_id": o["replay_id"],
                "option_id": o["option_id"],
                "game_version": o.get("game_version", ""),
                "file_label": o.get("file_label", ""),
                "opponent_player": o["opponent_player"],
                "opponent_name": o.get("opponent_name", ""),
                "human_player": o["human_player"],
                "human_name": o.get("human_name", ""),
                "round_count": o.get("round_count", 0),
                "playable_through_round": o.get("playable_through_round", 0),
                "strict_playable_through_round":
                    o.get("strict_playable_through_round",
                          o.get("playable_through_round", 0)),
                "blockers": o.get("blockers", []),
                "enabled": o.get("playable_through_round", 0) >= min_rounds,
            })
        opts.sort(key=lambda o: (-(o["playable_through_round"] or 0),
                                 o["replay_id"], o["opponent_player"]))
        return {
            "schema_version": "replay_library_v1",
            "corpus_available": self.corpus_available,
            "corpus_label": (self.manifest or {}).get("corpus_label"),
            "manifest_schema_version": (self.manifest or {}).get("schema_version"),
            "ruleset_version": (self.manifest or {}).get("ruleset_version"),
            "opening_catalog": {
                "schema_version": self.catalog.get("schema_version"),
                "package_count": len(self.catalog.get("packages") or {}),
            },
            "min_rounds": min_rounds,
            "options": opts,
        }

    def option(self, replay_id, opponent_player):
        for o in (self.manifest or {}).get("options", []):
            if o["replay_id"] == replay_id and \
                    int(o["opponent_player"]) == int(opponent_player):
                return o
        return None

    def shard(self, option):
        key = option["replay_id"]
        with self._lock:
            if key in self._shards:
                return self._shards[key]
        path = os.path.join(os.path.dirname(self.manifest_path),
                            option.get("shard", "games/%s.json" % key))
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
