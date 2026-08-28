#!/usr/bin/env python
"""自动训练驱动 (Transformer基线任务书 §14 R2-R4 + 用户 2026-08-28 授权).

State machine driven by <run-dir>/auto_state.json; each invocation runs the
NEXT stage and exits (cron-friendly). Stages:

  convert  v1_full -> v2 datasets (若尚未转换)
  cache    token cache + vocab.json (CPU, 长任务)
  train    TPolicy small 3 seed 并行 (GPU 1/2/3) + TValue small seed0 (GPU 4)
  arena    TPolicy vs 回放赢家 (dev roots, direct pysim) + teacher-forced 汇总
  done     全部完成

Usage: tools/run_auto_training.py --run-dir local_data/rl_transformer/auto_v1
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

POLICY_GPUS = [1, 2, 3]      # 每个 seed 一张卡 (§1.3 探索期模式)
VALUE_GPUS = [4]


def load_state(run_dir):
    p = os.path.join(run_dir, "auto_state.json")
    if os.path.exists(p):
        with open(p, encoding="utf8") as f:
            return json.load(f)
    return {"stage": "convert", "history": []}


def save_state(run_dir, st):
    st["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(run_dir, "auto_state.json"), "w",
              encoding="utf8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def sh(cmd, env=None, log_path=None):
    print(">>", " ".join(cmd), flush=True)
    e = dict(os.environ)
    if env:
        e.update(env)
    if log_path:
        with open(log_path, "a") as f:
            return subprocess.call(cmd, env=e, stdout=f,
                                   stderr=subprocess.STDOUT, cwd=ROOT)
    return subprocess.call(cmd, env=e, cwd=ROOT)


def stage_convert(run_dir, st):
    ds = os.path.join(run_dir, "datasets")
    if os.path.exists(os.path.join(ds, "policy_prefix_real_v2.jsonl.gz")):
        st["stage"] = "cache"
        return
    rc = sh([PY, "tools/convert_phase1_to_transformer.py",
             "--out-dir", run_dir],
            log_path=os.path.join(run_dir, "convert.log"))
    st["stage"] = "cache" if rc == 0 else "convert_failed"
    st["history"].append({"stage": "convert", "rc": rc,
                          "at": time.strftime("%H:%M:%S")})


def stage_cache(run_dir, st):
    if os.path.exists(os.path.join(run_dir, "token_cache",
                                   "manifest.json")):
        st["stage"] = "train"
        return
    rc = sh([PY, "tools/build_transformer_cache.py",
             "--run-dir", run_dir, "--policy"],
            log_path=os.path.join(run_dir, "cache.log"))
    st["stage"] = "train" if rc == 0 else "cache_failed"
    st["history"].append({"stage": "cache", "rc": rc,
                          "at": time.strftime("%H:%M:%S")})


def stage_train(run_dir, st, epochs):
    ck_dir = os.path.join(run_dir, "checkpoints")
    seeds = st.get("seeds", [0, 1, 2])
    running = []
    logdir = os.path.join(run_dir, "logs")
    os.makedirs(logdir, exist_ok=True)
    for i, seed in enumerate(seeds):
        ck = os.path.join(ck_dir, "tpolicy_seed%d.pt" % seed)
        done_mark = ck + ".done"
        if os.path.exists(done_mark):
            continue
        gpu = POLICY_GPUS[i % len(POLICY_GPUS)]
        cmd = [PY, "-m", "torch.distributed.run",
               "--nnodes=1", "--node-rank=0", "--nproc_per_node=1",
               "--master-port=%d" % (29800 + i * 7),
               "tools/train_transformer_policy.py",
               "--run-dir", run_dir,
               "--config", "configs/rl/transformer_policy_small_v1.json",
               "--epochs", str(epochs), "--seed", str(seed), "--device",
               "cuda"]
        env = {"CUDA_VISIBLE_DEVICES": str(gpu)}
        log = os.path.join(logdir, "policy_seed%d.log" % seed)
        print(">> seed %d on GPU %d -> %s" % (seed, gpu, log), flush=True)
        # stagger starts: each trainer holds ~20GB while loading the
        # cache; simultaneous loads tripped the node OOM killer (rc=-9)
        proc = subprocess.Popen(cmd, env={**os.environ, **env},
                                stdout=open(log, "a"),
                                stderr=subprocess.STDOUT, cwd=ROOT)
        running.append((seed, proc, done_mark))
        time.sleep(90)
    # value model (real+sim domains; sim 标签 provisional 已知)
    vgpu = VALUE_GPUS[0]
    vck = os.path.join(ck_dir, "tvalue_seed0.pt")
    if not os.path.exists(vck + ".done"):
        cmd_v = [PY, "tools/train_transformer_value.py",
                 "--run-dir", run_dir,
                 "--config", "configs/rl/transformer_value_small_v1.json",
                 "--epochs", str(max(8, epochs // 2)), "--seed", "0",
                 "--device", "cuda"]
        vlog = os.path.join(logdir, "value_seed0.log")
        vproc = subprocess.Popen(cmd_v, env={**os.environ,
                                             "CUDA_VISIBLE_DEVICES": str(vgpu)},
                                 stdout=open(vlog, "a"),
                                 stderr=subprocess.STDOUT, cwd=ROOT)
    else:
        vproc = None
    # wait for all
    rc_all = 0
    for seed, proc, done_mark in running:
        rc = proc.wait()
        if rc == 0:
            open(done_mark, "w").write("ok\n")
        else:
            rc_all = rc
        st["history"].append({"stage": "policy_seed%d" % seed, "rc": rc,
                              "at": time.strftime("%H:%M:%S")})
    if vproc is not None:
        rc = vproc.wait()
        if rc == 0:
            open(vck + ".done", "w").write("ok\n")
        st["history"].append({"stage": "value_seed0", "rc": rc,
                              "at": time.strftime("%H:%M:%S")})
    st["stage"] = "arena" if rc_all == 0 else "train_failed"


def stage_arena(run_dir, st, n_roots):
    ck = os.path.join(run_dir, "checkpoints", "tpolicy_seed0.pt")
    if not os.path.exists(ck):
        st["stage"] = "train"
        return
    out_dir = os.path.join(run_dir, "arena")
    os.makedirs(out_dir, exist_ok=True)
    rc = sh([PY, "tools/run_transformer_arena.py",
             "--checkpoint", ck,
             "--corpus-chunks", "local_data/rl_phase1/dev_small/corpus_chunks",
             "--out-dir", out_dir,
             "--n-roots", str(n_roots), "--rounds-per-root", "2",
             "--max-games", "6",
             "--opponent", "human_replay", "--baselines", "end_only",
             "--mode", "greedy", "--seed", "0"],
            env={"CUDA_VISIBLE_DEVICES": "1"},
            log_path=os.path.join(run_dir, "logs", "arena.log"))
    st["history"].append({"stage": "arena", "rc": rc,
                          "at": time.strftime("%H:%M:%S")})
    # aggregate the latest arena summary into state
    import glob
    arenas = sorted(glob.glob(os.path.join(out_dir, "arena_*.json")))
    if arenas:
        with open(arenas[-1], encoding="utf8") as f:
            rep = json.load(f)
        st["arena_summary"] = rep.get("summary", {})
        st["gate_10_4"] = rep.get("gate_10_4", {})
    sh([PY, "tools/build_transformer_report.py", "--run-dir", run_dir])
    st["stage"] = "done"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="local_data/rl_transformer/auto_v1")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--n-roots", type=int, default=8)
    ap.add_argument("--stage", default=None, help="force a stage")
    args = ap.parse_args()
    st = load_state(args.run_dir)
    if args.stage:
        st["stage"] = args.stage
    print("stage:", st["stage"], flush=True)
    if st["stage"] == "convert":
        stage_convert(args.run_dir, st)
    elif st["stage"] == "cache":
        stage_cache(args.run_dir, st)
    elif st["stage"] == "train":
        stage_train(args.run_dir, st, args.epochs)
    elif st["stage"] == "arena":
        stage_arena(args.run_dir, st, args.n_roots)
    elif st["stage"] == "done":
        print("ALL DONE — arena summary:",
              json.dumps(st.get("arena_summary", {}), ensure_ascii=False))
        return
    else:
        print("stage in failed state:", st["stage"], "— 手动排查后用 "
              "--stage 强制重跑")
        return
    save_state(args.run_dir, st)
    print("next stage:", st["stage"], flush=True)


if __name__ == "__main__":
    main()
