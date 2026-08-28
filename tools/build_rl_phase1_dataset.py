#!/usr/bin/env python
"""Build the three RL Phase 1 datasets from replay corpora (task §5).

Stages:
  chunks  - split corpora into per-chunk game files (parallel-friendly)
  real    - teacher-forced walks -> policy_prefix_real_v1 rows +
            battle_real_v1 rows (pre-battle board + FightReport labels)
  census  - dedup/split/exclusion report + coverage stats (data_report)

Datasets land in local_data/rl_phase1/<run_id>/datasets/*.jsonl.gz with a
manifest.json. Every row carries full provenance (sample_id, replay_hash,
round, ego_side, split, duplicate_group, fidelity flags, digests).
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.transition.economy import Economy                     # noqa: E402
from pysim.transition.replay_adapter import ReplayAdapter        # noqa: E402
from pysim.rl.prefix_env import (teacher_force_walk, apply_incomes,  # noqa: E402
                                 derive_round_incomes)
from pysim.rl.observation import battle_observation              # noqa: E402
from pysim.rl.masks import (build_action_space, action_from_norm_entry,  # noqa: E402
                            target_in_mask, space_to_dict, encode_target,
                            SKIP)
from pysim.rl.contracts import (stable_digest, sample_id, derive_seed,  # noqa: E402
                                SIM_LABEL_VERSION)

GD_PATH = os.path.join(ROOT, "data", "gamedata.json")

# sims per state: train roots get fewer seeds, val/test more (task §5.4)
SEEDS = {"train": (4, 8), "validation": (16, 32), "test": (16, 32),
         "stress": (16, 32)}
N_SEEDS = {"train": 6, "validation": 24, "test": 24, "stress": 24}

_GD = None
_ECO = None


def _gd():
    global _GD, _ECO
    if _GD is None:
        _GD = GameData(GD_PATH)
        _ECO = Economy(_GD)
    return _GD


def _eco():
    _gd()
    return _ECO


# ---------------------------------------------------------------- corpus
def game_fingerprint(g) -> str:
    """Canonical game digest for duplicate grouping (round digests only —
    independent of file name/ordering)."""
    h = hashlib.sha256()
    for side in (0, 1):
        for r in sorted(g["players"][side].get("rounds", []),
                        key=lambda r: int(r["round"])):
            h.update(json.dumps(
                {k: r.get(k) for k in ("round", "units", "techMap", "supply",
                                       "reactorCore", "officers")},
                sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def stage_chunks(args):
    """Split each corpus into chunk files of N games with metadata."""
    os.makedirs(args.chunk_dir, exist_ok=True)
    meta = []
    for corpus_name, path in args.corpora:
        data = json.load(open(path))
        for i in range(0, len(data), args.games_per_chunk):
            chunk_games = data[i:i + args.games_per_chunk]
            cid = "%s_%04d" % (corpus_name, i // args.games_per_chunk)
            cpath = os.path.join(args.chunk_dir, cid + ".json")
            if not os.path.exists(cpath) or args.overwrite:
                with open(cpath, "w") as f:
                    json.dump(chunk_games, f, ensure_ascii=False)
            meta.append({"chunk_id": cid, "corpus": corpus_name,
                         "path": cpath, "n_games": len(chunk_games)})
    with open(os.path.join(args.chunk_dir, "chunks.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print("chunks: %d (%s)" % (len(meta), ", ".join(
        "%s:%d games" % (m["corpus"], m["n_games"]) for m in meta[:3])))
    return meta


# ---------------------------------------------------------------- splits
def assign_splits(games_meta, new_corpus, history_corpus):
    """Time-based split for the new corpus; history goes to train.

    games_meta: list of dicts {corpus, file, fingerprint, players} sorted by
    (corpus, file) — the new corpus file names carry the game date."""
    fp_split = {}
    new_files = sorted(m["file"] for m in games_meta
                       if m["corpus"] == new_corpus)
    n = len(new_files)
    n_test = max(1, int(round(n * 0.10)))
    n_val = max(1, int(round(n * 0.10)))
    test_files = set(new_files[n - n_test:])
    val_files = set(new_files[n - n_test - n_val:n - n_test])
    for m in games_meta:
        if m["corpus"] == history_corpus:
            fp_split[m["fingerprint"]] = "train"
        elif m["file"] in test_files:
            fp_split[m["fingerprint"]] = "test"
        elif m["file"] in val_files:
            fp_split[m["fingerprint"]] = "validation"
        else:
            fp_split[m["fingerprint"]] = "train"
    return fp_split


def player_stress_flags(games_meta, fp_split):
    """Player-held-out stress marking: games in val/test where BOTH players
    never appear in any train game."""
    train_players = set()
    for m in games_meta:
        if fp_split[m["fingerprint"]] == "train":
            train_players |= set(m["players"])
    stress = {}
    for m in games_meta:
        if fp_split[m["fingerprint"]] in ("validation", "test") \
                and set(m["players"]) and set(m["players"]) <= train_players \
                is False:
            pass
    # simpler & correct: both players absent from train
    for m in games_meta:
        sp = set(m["players"])
        if fp_split[m["fingerprint"]] in ("validation", "test") \
                and sp and not (sp & train_players):
            stress[m["fingerprint"]] = True
    return stress


# ---------------------------------------------------------------- workers
def process_chunk(chunk):
    cpath = chunk["path"]
    corpus = chunk["corpus"]
    games = json.load(open(cpath))
    eco = _eco()
    gd = _gd()
    rows_policy, rows_battle, rows_state = [], [], []
    stats = Counter()
    for gi_local, g in enumerate(games):
        stats["games_seen"] += 1
        if g.get("info", {}).get("matchMode") != "VS_1_1":
            stats["skip_not_1v1"] += 1
            continue
        fname = g.get("file", "")
        replay_hash = hashlib.sha256(fname.encode()).hexdigest()[:16]
        fp = game_fingerprint(g)
        players = tuple(str(p.get("name", "")) for p in g["players"][:2])
        version = fname.split("_")[0] if "_" in fname else "unknown"
        adapter = ReplayAdapter([g])
        adapter._games = [g]           # avoid re-loading
        gid = 0
        income_tab, approx_tab = derive_round_incomes(g, eco)
        round_seqs = {}
        for side in (0, 1):
            prev = None
            for rec in g["players"][side].get("rounds", []):
                rnd = int(rec["round"])
                round_seqs[(side, rnd)] = prev
                prev = rnd
        max_round = max((int(r["round"]) for side in (0, 1)
                         for r in g["players"][side].get("rounds", [])),
                        default=0)
        for rnd in range(1, max_round):
            prev0 = round_seqs.get((0, rnd))
            prev1 = round_seqs.get((1, rnd))
            if (prev0 is not None and rnd - prev0 > 1) or \
                    (prev1 is not None and rnd - prev1 > 1):
                stats["skip_round_gap"] += 1
                continue
            try:
                base = adapter.environment_state(gid, rnd, economy=eco)
            except (KeyError, ValueError):
                stats["skip_no_snapshot"] += 1
                continue
            pair_inc = [int(income_tab.get((0, rnd), 0)),
                        int(income_tab.get((1, rnd), 0))]
            root = apply_incomes(base, (pair_inc[0], pair_inc[1]))
            approx = (side, rnd) in approx_tab

            walks = {}
            entries_by_side = {}
            for side in (0, 1):
                entries, _rep = adapter.norm_actions(g, side, rnd)
                entries_by_side[side] = entries
                if not entries:
                    stats["skip_empty_round"] += 1
                    continue
                w = teacher_force_walk(root, side, entries, eco, gd)
                walks[side] = w

            # ---- policy prefix rows
            for side, w in walks.items():
                base_sid = sample_id(replay_hash, rnd, side)
                tier = "gold"
                flags = []
                if w.end_reason not in ("human_end",):
                    tier = "silver"
                    flags.append("end:%s" % w.end_reason)
                if w.failure is not None:
                    flags.append("fail:%s:%s" % (w.failure.kind,
                                                 w.failure.step))
                if approx:
                    flags.append("income_approx")
                if w.noops:
                    flags.append("noop:" + ";".join(w.noops[:3]))
                for pi, (obs, space, target) in enumerate(w.samples):
                    rows_policy.append({
                        "sample_id": "%s|p%d" % (base_sid, pi),
                        "replay_hash": replay_hash,
                        "match_id_hash": fp,
                        "round": rnd, "ego_side": side,
                        "corpus": corpus, "game_version": version,
                        "prefix_len": pi, "split": "__SPLIT__",
                        "duplicate_group": fp, "tier": tier,
                        "fidelity_flags": flags,
                        "obs": obs.to_dict(),
                        "obs_digest": obs.digest(),
                        "space": space_to_dict(space),
                        "target": encode_target(target, space),
                        "target_action": target.to_dict(),
                    })

            # ---- joint pre-battle board (both walks complete)
            if all(w.end_reason == "human_end" and
                   w.final_state is not None for w in walks.values()) \
                    and len(walks) == 2:
                from pysim.transition.env import TransitionEnv
                from pysim.transition.model import CanonicalActionPlan
                env2 = TransitionEnv(gd, eco=eco)
                env2.reset(root)          # income-injected root
                ok = True
                noop_flags = []
                from pysim.transition.deploy import deploy_transition
                for side in (0, 1):
                    plan = CanonicalActionPlan(
                        player=side, actions=walks[side].engine_actions)
                    dep = deploy_transition(env2.state, (plan,), eco)
                    env2._state = dep.state
                    for rec in dep.receipts[0]:
                        if not rec.accepted:
                            ok = False
                            noop_flags.append("joint:%s:%s" % (
                                rec.kind, rec.reason_code))
                if ok and env2.state.phase.value == "pre_battle":
                    reports = adapter.fight_reports(g, rnd)
                    next_res = {}
                    for side in (0, 1):
                        nr = adapter.round_rec(g, side, rnd + 1)
                        next_res[side] = nr.get("preRoundFightResult") \
                            if nr is not None else None
                    if reports is not None and \
                            all(next_res[s] for s in (0, 1)):
                        d0 = int(reports[1].get("score", 0))
                        d1 = int(reports[0].get("score", 0))
                        dmg = (d0, d1)
                        max_hp = tuple(env2.state.players[s].max_hp
                                       for s in (0, 1))
                        tier = "gold"
                        if noop_flags or approx:
                            tier = "silver"
                        for ego in (0, 1):
                            bo = battle_observation(env2.state, ego)
                            res = next_res[ego]
                            y_wdl = {"Lose": 0, "Deuce": 1, "Win": 2}[res]
                            y_d_opp = dmg[1 - ego] / max(1, max_hp[1 - ego])
                            y_d_self = dmg[ego] / max(1, max_hp[ego])
                            rows_battle.append({
                                "sample_id": sample_id(replay_hash, rnd,
                                                       "real", ego),
                                "replay_hash": replay_hash,
                                "match_id_hash": fp,
                                "round": rnd, "ego_side": ego,
                                "corpus": corpus, "game_version": version,
                                "split": "__SPLIT__",
                                "duplicate_group": fp,
                                "tier": tier,
                                "fidelity_flags": noop_flags[:3],
                                "y_wdl": y_wdl,
                                "y_damage_to_opp": round(y_d_opp, 5),
                                "y_damage_to_self": round(y_d_self, 5),
                                "y_damage_diff": round(y_d_opp - y_d_self, 5),
                                "observation": bo.to_dict(),
                                "observation_digest": bo.digest(),
                                "state_digest": bo.digest(),
                            })
                        # state row for the sim-label stage
                        rows_state.append({
                            "sample_id": sample_id(replay_hash, rnd, "sim"),
                            "replay_hash": replay_hash,
                            "match_id_hash": fp,
                            "round": rnd, "corpus": corpus,
                            "game_version": version,
                            "split": "__SPLIT__", "duplicate_group": fp,
                            "tier": tier,
                            "candidate_group_id": fp,
                            "state_source": "human",
                            "observation": battle_observation(
                                env2.state, 0).to_dict(),
                            "observation_digest": battle_observation(
                                env2.state, 0).digest(),
                            "n_units": (len(env2.state.players[0].units),
                                        len(env2.state.players[1].units)),
                            "seeds": [derive_seed(
                                sample_id(replay_hash, rnd, "sim"), k)
                                for k in range(N_SEEDS.get("__SPLIT__", 6))],
                            "label_version": SIM_LABEL_VERSION,
                        })
                        stats["battle_states"] += 1
                    else:
                        stats["skip_no_label"] += 1
                else:
                    stats["skip_joint_board"] += 1
            stats["rounds_seen"] += 1
    return {"chunk": chunk["chunk_id"], "corpus": corpus,
            "rows_policy": rows_policy, "rows_battle": rows_battle,
            "rows_state": rows_state,
            "stats": dict(stats)}


# ---------------------------------------------------------------- driver
def write_shard(path, rows):
    with gzip.open(path, "wt", encoding="utf8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--new-corpus", default="local_data/humen_rounds.json")
    ap.add_argument("--history-corpus",
                    default="local_data/rl_phase1/history_rounds.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--games-per-chunk", type=int, default=16)
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument("--limit-chunks", type=int, default=0)
    ap.add_argument("--stage", choices=["chunks", "real", "all"],
                    default="all")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    run_id = args.run_id or time.strftime("run_%Y%m%d_%H%M%S")
    out = args.out or os.path.join(ROOT, "local_data", "rl_phase1", run_id)
    ds_dir = os.path.join(out, "datasets")
    chunk_dir = os.path.join(out, "corpus_chunks")
    os.makedirs(ds_dir, exist_ok=True)

    corpora = [("new", os.path.join(ROOT, args.new_corpus))]
    if os.path.exists(os.path.join(ROOT, args.history_corpus)):
        corpora.append(("history",
                        os.path.join(ROOT, args.history_corpus)))
    else:
        print("NOTE: history corpus not found, new corpus only")
    args.chunk_dir = chunk_dir
    args.corpora = corpora

    if args.stage in ("chunks", "all"):
        stage_chunks(args)
    meta = json.load(open(os.path.join(chunk_dir, "chunks.json")))
    if args.limit_chunks:
        meta = meta[:args.limit_chunks]

    t0 = time.time()
    with Pool(args.workers) as pool:
        results = []
        for i, res in enumerate(pool.imap_unordered(process_chunk, meta)):
            results.append(res)
            n_p = len(res["rows_policy"])
            n_b = len(res["rows_battle"])
            if (i + 1) % 10 == 0 or i + 1 == len(meta):
                print("chunk %d/%d policy=%d battle=%d (%.0fs)" % (
                    i + 1, len(meta), n_p, n_b, time.time() - t0), flush=True)

    # ---- assemble + splits: rebuild game-level metadata from chunk files
    corpus_fp_file = {}
    corpus_fp_players = {}
    for chunk in meta:
        games = json.load(open(chunk["path"]))
        for g in games:
            if g.get("info", {}).get("matchMode") != "VS_1_1":
                continue
            fp = game_fingerprint(g)
            corpus_fp_file[fp] = (chunk["corpus"], g.get("file", ""))
            corpus_fp_players[fp] = tuple(str(p.get("name", ""))
                                          for p in g["players"][:2])
    games_meta = [{"corpus": c, "file": f, "fingerprint": fp,
                   "players": list(corpus_fp_players[fp])}
                  for fp, (c, f) in corpus_fp_file.items()]
    fp_split = assign_splits(games_meta, "new", "history")
    stress = player_stress_flags(games_meta, fp_split)

    # ---- write shards with split resolved
    n_dup_groups = len(set(m["fingerprint"] for m in games_meta))
    split_of = lambda fp: fp_split.get(fp, "train")
    pol_rows, bat_rows, st_rows = [], [], []
    for r in results:
        for row in r["rows_policy"]:
            sp = split_of(row["duplicate_group"])
            row["split"] = sp
            if sp in ("validation", "test") and stress.get(
                    row["duplicate_group"]):
                row["stress"] = True
            pol_rows.append(row)
        for row in r["rows_battle"]:
            sp = split_of(row["duplicate_group"])
            row["split"] = sp
            if sp in ("validation", "test") and stress.get(
                    row["duplicate_group"]):
                row["stress"] = True
            bat_rows.append(row)
        for row in r["rows_state"]:
            sp = split_of(row["duplicate_group"])
            row["split"] = sp
            row["seeds"] = [derive_seed(row["sample_id"], k)
                            for k in range(N_SEEDS.get(sp, 6))]
            st_rows.append(row)

    write_shard(os.path.join(ds_dir, "policy_prefix_real_v1.jsonl.gz"),
                pol_rows)
    write_shard(os.path.join(ds_dir, "battle_real_v1.jsonl.gz"), bat_rows)
    write_shard(os.path.join(ds_dir, "battle_sim_states_v1.jsonl.gz"),
                st_rows)

    # ---- data report
    def count_by(rows, key):
        c = Counter()
        for r in rows:
            c[(r.get(key), r.get("split"), r.get("tier"))] += 1
        return {("%s|%s|%s" % k): v for k, v in sorted(c.items())}

    report = {
        "run_id": run_id,
        "n_games": len(games_meta),
        "n_duplicate_groups": n_dup_groups,
        "splits": Counter(m and fp_split[m["fingerprint"]]
                          for m in games_meta),
        "stress_games": len(stress),
        "rows": {
            "policy": len(pol_rows), "battle_real": len(bat_rows),
            "sim_states": len(st_rows)},
        "tier_split_policy": count_by(pol_rows, "corpus"),
        "tier_split_battle": count_by(bat_rows, "corpus"),
        "chunk_stats": {r["chunk"]: r["stats"] for r in results},
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open(os.path.join(out, "data_report.json"), "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)
    print(json.dumps({k: v for k, v in report.items() if k != "chunk_stats"},
                     ensure_ascii=False, indent=1, default=str)[:1500])


if __name__ == "__main__":
    main()
