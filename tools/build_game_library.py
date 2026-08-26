# Build the sharded replay game library for the /game audit game
# (transition前后端审计游戏任务书 G1/G2/G5).
#
# local_data/replay_game/manifest.json        (list metadata, loaded at boot)
# local_data/replay_game/games/<replay_id>.json  (one game shard, lazy)
#
# The shard is a full replay2json game entry + pre-normalized action streams
# (actions_norm/norm_report per round, the auditable artifact) + per-round
# reinforcement offers + per-opponent capability scan results. Opaque
# replay ids are content hashes; no absolute paths are exposed.
#
# usage:
#   python tools/build_game_library.py --replay-dir local_data/humen_replay \
#       --out local_data/replay_game
#   python tools/build_game_library.py --replay-dir local_data/humen_replay \
#       --out data/samples/replay_game --only "2119_20260710--201334560*,2119_20260710--268443861*"
import argparse
import glob
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHARD_SCHEMA = "replay_game_shard_v1"
MANIFEST_SCHEMA = "replay_game_manifest_v1"


def convert_games(args):
    if args.replay_dir:
        from tools.replay2json import convert, load_tech_prev
        tech_prev = load_tech_prev()
        files = sorted(glob.glob(os.path.join(args.replay_dir, "*.grbr")))
        if args.only:
            pats = [p.strip() for p in args.only.split(",") if p.strip()]
            files = [f for f in files
                     if any(os.path.basename(f).startswith(p.rstrip("*"))
                            or p in os.path.basename(f) for p in pats)]
        out = []
        for f in files:
            raw = open(f, "rb").read()
            h = hashlib.sha256(raw).hexdigest()
            try:
                g = convert(f, tech_prev)
            except Exception as ex:
                print("  FAIL %s: %s" % (os.path.basename(f), ex))
                continue
            if g:
                g["_source_hash"] = h
                out.append(g)
        return out
    games = json.load(open(args.rounds, encoding="utf8"))
    for g in games:
        g["_source_hash"] = hashlib.sha256(
            json.dumps(g, sort_keys=True).encode()).hexdigest()
    return games


def normalize_game(g, norm):
    """Pre-normalize both players' rounds (writes actions_norm/norm_report)."""
    n = 0
    for pr in g["players"]:
        for rec in pr["rounds"]:
            res = norm.normalize_round(rec)
            rec["actions_norm"] = res.actions_norm
            rec["norm_report"] = res.report
            rec["unit_index_start"] = res.counter_start
            n += len(res.actions_norm)
    g["norm_version"] = "rounds_norm_v0.1"
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-dir")
    ap.add_argument("--rounds")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default=None,
                    help="comma list of file-name substrings (sample mode)")
    ap.add_argument("--catalog",
                    default=os.path.join("data", "game",
                                         "opening_catalog.json"))
    ap.add_argument("--min-rounds", type=int, default=5)
    args = ap.parse_args()
    if not args.replay_dir and not args.rounds:
        ap.error("need --replay-dir or --rounds")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from pysim.gamedata import GameData
    from pysim.transition import Economy, capability
    from pysim.transition.normalize import Normalizer
    from pysim.transition import opening as opening_mod

    gd = GameData(os.path.join(root, "data", "gamedata.json"))
    eco = Economy(gd)
    norm = Normalizer(eco)
    catalog = opening_mod.load_catalog(args.catalog)
    team_ids = {int(t) for t in catalog["packages"]}

    games = convert_games(args)
    print("games:", len(games))
    os.makedirs(os.path.join(args.out, "games"), exist_ok=True)

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "ruleset_version": "normal_1v1_replay_v0",
        "min_rounds_default": args.min_rounds,
        "corpus_label": (args.replay_dir or args.rounds),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "options": [],
    }
    n_norm = 0
    t0 = time.time()
    for gi, g in enumerate(games):
        info = g.get("info", {})
        if info.get("matchMode") != "VS_1_1" or info.get("gameMode") != "Normal":
            continue
        n_norm += normalize_game(g, norm)
        rid = g["_source_hash"][:12]
        opening_of = {}
        ok_opening = True
        for side in (0, 1):
            tid, _ = opening_mod.recorded_team_of(g, side)
            opening_of[side] = tid
            if tid is None or tid not in team_ids:
                ok_opening = False
        caps = {}
        for opp in (0, 1):
            scan = capability.scan_option(g, opp, eco, gd,
                                          catalog_team_ids=team_ids if ok_opening else set(),
                                          opening_of=opening_of)
            caps[str(opp)] = scan
        shard = {
            "schema_version": SHARD_SCHEMA,
            "replay_id": rid,
            "replay_hash": g.pop("_source_hash"),
            "source_file": g["file"],
            "game_version": g["file"].split("_")[0],
            "game": g,
            "capabilities": caps,
        }
        shard_path = os.path.join("games", "%s.json" % rid)
        json.dump(shard, open(os.path.join(args.out, shard_path), "w",
                              encoding="utf8"), ensure_ascii=False,
                  separators=(",", ":"))
        rounds0 = len(g["players"][0]["rounds"])
        for opp in (0, 1):
            hum = 1 - opp
            scan = caps[str(opp)]
            manifest["options"].append({
                "replay_id": rid,
                "option_id": "%s-%d" % (rid, opp),
                "game_version": shard["game_version"],
                "file_label": g["file"],
                "opponent_player": opp,
                "opponent_name": g["players"][opp]["name"],
                "human_player": hum,
                "human_name": g["players"][hum]["name"],
                "round_count": rounds0,
                "playable_through_round": scan["playable_through_round"],
                "strict_playable_through_round":
                    scan.get("strict_playable_through_round",
                             scan["playable_through_round"]),
                "blockers": scan["blockers"],
                "enabled": scan["playable_through_round"] >= args.min_rounds,
                "shard": shard_path,
            })
        if (gi + 1) % 100 == 0:
            print("  %d/%d games (%.0fs)" % (gi + 1, len(games),
                                             time.time() - t0))
    manifest["option_count"] = len(manifest["options"])
    manifest["enabled_count"] = sum(1 for o in manifest["options"] if o["enabled"])
    json.dump(manifest, open(os.path.join(args.out, "manifest.json"), "w",
                             encoding="utf8"), ensure_ascii=False, indent=1)
    print("done: %d options (%d enabled >=%d rounds), %d norm actions, "
          "%.0fs -> %s" % (len(manifest["options"]),
                           manifest["enabled_count"], args.min_rounds,
                           n_norm, time.time() - t0, args.out))


if __name__ == "__main__":
    main()
