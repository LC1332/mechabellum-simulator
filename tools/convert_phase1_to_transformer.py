#!/usr/bin/env python
"""Convert Phase 1 v1 datasets into transformer v2 format (§7.1).

Real battle labels are engine-independent (§7.1: real 标签不因 engine 改变),
so battle_real_v1 rows carry over with the observation upgraded through the
versioned v2 adapter. Sim labels from pysim-step30 are carried over marked
provisional (sim_label regeneration with the frozen engine is queued
separately). Policy rows gain the structured history chain built from each
(match, round, seat) prefix sequence — teacher states only, no human future.

Output: <out-dir>/datasets/{battle_real_v2,battle_sim_v2,
policy_prefix_real_v2}.jsonl.gz (row schema = transformer_data_v2).
"""
import argparse
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.rl.transformer.tokenizer import (                     # noqa: E402
    battle_token_obs_from_v1, policy_token_obs_from_v1_dict)
from pysim.rl.transformer.token_contract import (                # noqa: E402
    OBSERVATION_VERSION, stable_digest)


def history_entry(target: dict) -> dict:
    return {
        "verb": str(target.get("verb", "")),
        "x": float(target.get("x", 0.0) or 0.0),
        "y": float(target.get("y", 0.0) or 0.0),
        "points": [tuple(p) for p in (target.get("points") or [])],
        "receipt_ok": True,        # teacher-walk receipts were accepted
    }


def convert_policy(rows_in, out_path, stats):
    """Group by (match, seat, round), order by prefix_len, build history."""
    groups = {}
    for r in rows_in:
        key = (r["match_id_hash"], int(r.get("ego_side", 0)),
               int(r.get("round", 0)))
        groups.setdefault(key, []).append(r)
    n = 0
    with gzip.open(out_path, "wt", encoding="utf8") as f:
        for key, rows in sorted(groups.items(), key=lambda kv: kv[0]):
            rows.sort(key=lambda r: int(r.get("prefix_len", 0)))
            history = []
            for r in rows:
                target = r["target_action"]
                obs2 = policy_token_obs_from_v1_dict(
                    r["obs"], r["space"], history=history)
                budget_left = int(r["obs"].get("budget_left", 64))
                out = {
                    "sample_id": r["sample_id"],
                    "split": r["split"], "tier": r.get("tier"),
                    "corpus": r.get("corpus", "v1_full"),
                    "match_id_hash": r["match_id_hash"],
                    "ego_side": r.get("ego_side"), "round": r.get("round"),
                    "prefix_len": r.get("prefix_len"),
                    "observation": obs2,
                    "target": target,
                    "end": 1 if target.get("verb") == "END_DEPLOY" else 0,
                    "rem_bucket": min(8, max(0, budget_left) // 8),
                }
                f.write(json.dumps(out, ensure_ascii=False, default=str)
                        + "\n")
                history.append(history_entry(target))
                n += 1
    stats["policy_prefix_real_v2"] = n


def convert_battle(rows_in, out_path, stats, name):
    n = 0
    with gzip.open(out_path, "wt", encoding="utf8") as f:
        for r in rows_in:
            obs2 = battle_token_obs_from_v1(r["observation"])
            out = dict(r)
            out["observation"] = obs2
            out["obs_v1_digest"] = r.get("observation_digest",
                                         stable_digest(r["observation"]))
            f.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
            n += 1
    stats[name] = n


def read_rows(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf8") as f:
        for line in f:
            yield json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-dir",
                    default="local_data/rl_phase1/v1_full/datasets")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--datasets", default="policy,real,sim")
    args = ap.parse_args()
    ds = os.path.join(args.out_dir, "datasets")
    os.makedirs(ds, exist_ok=True)
    stats = {}
    want = set(args.datasets.split(","))

    if "policy" in want:
        convert_policy(
            read_rows(os.path.join(args.v1_dir,
                                   "policy_prefix_real_v1.jsonl.gz")),
            os.path.join(ds, "policy_prefix_real_v2.jsonl.gz"), stats)
        print("policy:", stats)
    if "real" in want:
        convert_battle(
            read_rows(os.path.join(args.v1_dir, "battle_real_v1.jsonl.gz")),
            os.path.join(ds, "battle_real_v2.jsonl.gz"), stats,
            "battle_real_v2")
        print("real:", stats)
    if "sim" in want:
        convert_battle(
            read_rows(os.path.join(args.v1_dir, "battle_sim_v1.jsonl.gz")),
            os.path.join(ds, "battle_sim_v2.jsonl.gz"), stats,
            "battle_sim_v2")
        print("sim:", stats)
    print("converted ->", ds)
    print("注意: sim 标签为 pysim-step30 旧引擎产物 (provisional);"
          " real 标签引擎无关,可直接沿用 (§7.1)")


if __name__ == "__main__":
    main()
