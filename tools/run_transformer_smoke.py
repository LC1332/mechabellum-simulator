#!/usr/bin/env python
"""End-to-end engineering smoke (任务书 §13.4 CPU path).

One command: contract -> toy v2 datasets -> token cache -> tiny TValue
train+eval -> tiny TPolicy-BC train+eval -> (optional) arena -> report.md.
Everything lands in local_data/rl_transformer/<run-id>/.

  tools/run_transformer_smoke.py --run-id smoke_$(date +%s)
  # add --gpus 2 --ddp to exercise the torchrun path inside the allowlist
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PY = sys.executable


def sh(cmd, env=None, log_to=None):
    print(">>", " ".join(cmd))
    e = dict(os.environ)
    if env:
        e.update(env)
    if log_to:
        with open(log_to, "w") as f:
            return subprocess.call(cmd, env=e, stdout=f,
                                   stderr=subprocess.STDOUT, cwd=ROOT)
    return subprocess.call(cmd, env=e, cwd=ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="smoke_%d" % int(time.time()))
    ap.add_argument("--games", type=int, default=18,
                    help="toy games (3 splits × n)")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--gpus", type=int, default=0,
                    help="0 = CPU single process; N>0 = torchrun N ranks "
                         "on physical GPUs 1..N (allowlist enforced)")
    ap.add_argument("--skip-arena", action="store_true")
    args = ap.parse_args()

    run_dir = os.path.join(ROOT, "local_data", "rl_transformer",
                           args.run_id)
    datasets = os.path.join(run_dir, "datasets")
    os.makedirs(datasets, exist_ok=True)
    fails = []

    def step(name, cmd, env=None):
        rc = sh(cmd, env=env, log_to=os.path.join(run_dir, name + ".log"))
        if rc != 0:
            fails.append((name, rc))
            print("[FAIL] %s rc=%d — log: %s/%s.log" % (name, rc, run_dir,
                                                        name))
        else:
            print("[ok] %s" % name)
        return rc

    # 1. toy v2 datasets (§3.2 允许的工程产物)
    from pysim.rl.transformer import toydata
    counts = toydata.write_toy_datasets(datasets, seed=0,
                                        n_games=args.games)
    print("toy datasets:", json.dumps(counts))

    # 2. contract (provisional, engineering mode)
    step("contract", [PY, "tools/build_rl_transformer_contract.py",
                      "--path", os.path.join(run_dir, "contract.json"),
                      "--force"])

    # 3. token cache
    step("cache", [PY, "tools/build_transformer_cache.py",
                   "--run-dir", run_dir, "--policy"])

    # 4-5. tiny training runs
    launcher = ["--epochs", str(args.epochs), "--limit", "400",
                "--allow-engineering"]
    env = None
    torchrun = None
    if args.gpus > 0:
        torchrun = ["torchrun", "--standalone",
                    "--nproc_per_node=%d" % args.gpus]
        env = {"CUDA_VISIBLE_DEVICES":
               ",".join(str(i) for i in range(1, args.gpus + 1))}
        base = torchrun + ["tools/train_transformer_value.py"]
    else:
        base = [PY, "tools/train_transformer_value.py"]

    step("train_value", base + [
        "--run-dir", run_dir,
        "--config", "configs/rl/transformer_value_tiny_v1.json"] + launcher,
        env=env)

    if args.gpus > 0:
        base_p = torchrun + ["tools/train_transformer_policy.py"]
    else:
        base_p = [PY, "tools/train_transformer_policy.py"]
    step("train_policy", base_p + [
        "--run-dir", run_dir,
        "--config", "configs/rl/transformer_policy_tiny_v1.json"] + launcher,
        env=env)

    # 6. report
    step("report", [PY, "tools/build_transformer_report.py",
                    "--run-dir", run_dir])

    print("")
    if fails:
        print("SMOKE FAILED:", fails)
        return 1
    print("SMOKE OK — artifacts in %s" % run_dir)
    print("注: T0 pending → 全部产物为 engineering/toy, 不得作为正式结论 (§3.2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
