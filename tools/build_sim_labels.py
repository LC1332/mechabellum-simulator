#!/usr/bin/env python
"""Generate battle_sim_v1 pysim labels over the stored state rows (task
§5.4): each state × its derived seeds runs one direct pysim battle in a
worker pool. Output: battle_sim_v1.jsonl.gz with per-seed outcomes and the
aggregated distribution (soft WDL target + mean damage)."""
import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GD_PATH = os.path.join(ROOT, "data", "gamedata.json")
_GD = None


def _gd():
    global _GD
    if _GD is None:
        from pysim.gamedata import GameData
        _GD = GameData(GD_PATH)
    return _GD


def sim_one(args):
    sample_id, obs, seeds, opts = args
    from pysim.rl.sim_bridge import simulate_observation
    gd = _gd()
    outcomes = []
    warnings = ()
    for seed in seeds:
        try:
            o = simulate_observation(obs, gd, int(seed), opts=opts)
            outcomes.append(o)
        except Exception as ex:
            outcomes.append({"seed": int(seed), "error": str(ex)[:120]})
    return sample_id, outcomes


def aggregate(outcomes, max_hp):
    """Soft WDL + mean damage over seeds (task §7.2 sim soft targets)."""
    ok = [o for o in outcomes if "error" not in o]
    n = len(ok)
    if not n:
        return None
    wins = sum(1 for o in ok if o["winner"] == 0)
    losses = sum(1 for o in ok if o["winner"] == 1)
    draws = n - wins - losses
    d_opp = sum(o["damage_to_player"][1] for o in ok) / (n * max(1, max_hp))
    d_self = sum(o["damage_to_player"][0] for o in ok) / (n * max(1, max_hp))
    return {
        "p_win": wins / n, "p_draw": draws / n, "p_loss": losses / n,
        "y_damage_to_opp": round(d_opp, 5),
        "y_damage_to_self": round(d_self, 5),
        "y_damage_diff": round(d_opp - d_self, 5),
        "mean_end_time": round(sum(o["end_time"] for o in ok) / n, 2),
        "n_seeds": n, "n_errors": len(outcomes) - n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--workers", type=int, default=192)
    ap.add_argument("--train-side-only", action="store_true",
                    help="sim labels are ego-0; the ego-1 row reuses them")
    args = ap.parse_args()
    ds = os.path.join(args.run_dir, "datasets")
    in_path = os.path.join(ds, "battle_sim_states_v1.jsonl.gz")
    out_path = os.path.join(ds, "battle_sim_v1.jsonl.gz")

    states = []
    with gzip.open(in_path, "rt") as f:
        for line in f:
            states.append(json.loads(line))
    by_id = {s["sample_id"]: s for s in states}
    print("states:", len(states))

    t0 = time.time()
    jobs = [(s["sample_id"], s["observation"], s["seeds"], None)
            for s in states if s["seeds"]]
    rows = []
    stats = Counter()
    with Pool(args.workers) as pool:
        done = 0
        for sid, outcomes in pool.imap_unordered(sim_one, jobs, chunksize=4):
            done += 1
            if done % 1000 == 0:
                print("%d/%d (%.0fs)" % (done, len(jobs),
                                         time.time() - t0), flush=True)
            state = by_id.get(sid)
            if state is None:
                continue
            max_hp = max(state["observation"]["self"]["max_hp"], 1)
            agg = aggregate(outcomes, max_hp=max_hp)
            if agg is None:
                stats["all_failed"] += 1
                continue
            rows.append({
                "sample_id": sid,
                "replay_hash": state["replay_hash"],
                "match_id_hash": state["match_id_hash"],
                "round": state["round"],
                "corpus": state["corpus"],
                "game_version": state["game_version"],
                "split": state["split"],
                "duplicate_group": state["duplicate_group"],
                "candidate_group_id": state.get("candidate_group_id"),
                "state_source": state["state_source"],
                "tier": state["tier"],
                "observation": state["observation"],
                "observation_digest": state["observation_digest"],
                "n_units": state["n_units"],
                "label_version": state.get("label_version"),
                "outcomes": outcomes,
                "agg": agg,
            })
            stats["ok"] += 1

    with gzip.open(out_path, "wt", encoding="utf8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("wrote %d sim rows -> %s (%.0fs)" % (len(rows), out_path,
                                               time.time() - t0))
    print(dict(stats))


if __name__ == "__main__":
    main()
