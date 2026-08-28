#!/usr/bin/env python
"""Run the §6.4 architecture ablation matrix (validation-driven).

Uses the PRE-REGISTERED development seed (configs/rl/
transformer_ablation_v1.json: development_seed) and writes per-arm reports
into <run-dir>/ablation/<arm>/. NO test-set selection: arms differ only by
the config overrides; selection happens on validation metrics and the
final 3-seed run uses the FROZEN winner config (§6.4).
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PY = sys.executable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--plan", default="configs/rl/transformer_ablation_v1.json")
    ap.add_argument("--family", choices=["value", "policy", "all"],
                    default="all")
    ap.add_argument("--device", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="engineering smoke: cap rows")
    args = ap.parse_args()

    with open(args.plan, encoding="utf8") as f:
        plan = json.load(f)
    seed = plan.get("development_seed", 0)

    jobs = []
    if args.family in ("value", "all"):
        for arm in plan["value"]:
            if arm.get("use") == "phase1_tool":
                jobs.append((arm, "deepsets"))     # phase1 trainer
            else:
                jobs.append((arm, "tvalue"))
    if args.family in ("policy", "all"):
        for arm in plan["policy"]:
            jobs.append((arm, "tpolicy"))

    results = []
    for arm, kind in jobs:
        name = arm["name"]
        out_dir = os.path.join(args.run_dir, "ablation", name)
        os.makedirs(out_dir, exist_ok=True)
        if kind == "deepsets":
            cmd = [PY, os.path.join(ROOT, "tools", "train_battle_value.py"),
                   "--run-dir", os.path.join(args.run_dir, "phase1_data"),
                   "--seed", str(seed)]
        elif kind == "tvalue":
            train_tool = os.path.join(ROOT, "tools",
                                      "train_transformer_value.py")
            cmd = [PY, train_tool,
                   "--run-dir", args.run_dir,
                   "--config", os.path.join(ROOT, arm["config"]),
                   "--overrides", json.dumps(arm.get("overrides", {})),
                   "--seed", str(seed)]
            if args.limit:
                cmd += ["--limit", str(args.limit)]
            cmd += ["--allow-engineering"]
        else:
            train_tool = os.path.join(ROOT, "tools",
                                      "train_transformer_policy.py")
            cmd = [PY, train_tool,
                   "--run-dir", args.run_dir,
                   "--config", os.path.join(ROOT, arm["config"]),
                   "--overrides", json.dumps(arm.get("overrides", {})),
                   "--seed", str(seed)]
            if args.limit:
                cmd += ["--limit", str(args.limit)]
            cmd += ["--allow-engineering"]
        print(">>", " ".join(cmd))
        # keep each arm's stdout in the arm dir for the run manifest
        with open(os.path.join(out_dir, "train.log"), "w") as log:
            rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT,
                                 cwd=ROOT)
        results.append({"arm": name, "kind": kind, "rc": rc})
        if rc != 0:
            print("arm %s FAILED (rc=%d) — log: %s/train.log" %
                  (name, rc, out_dir))

    with open(os.path.join(args.run_dir, "ablation", "ablation_runs.json"),
              "w", encoding="utf8") as f:
        json.dump({"seed": seed, "results": results}, f, indent=1)
    print(json.dumps(results, indent=1))
    print("选择只在 validation 上进行; 正式配置冻结后跑 3 seed (§6.4)。")


if __name__ == "__main__":
    main()
