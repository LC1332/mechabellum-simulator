#!/usr/bin/env python
"""Build the rl_transformer_contract_v1 JSON (Transformer基线任务书 §3.1).

The contract pins code commit + engine/schema/battlefield versions +
tokenizer/grid/bucket config + the GPU allowlist. It ALSO records the T0
backtest gate: while t0_backtest.status is "pending", tools refuse to
produce formal artifacts (labels/training/test/arena verdicts, §3.2) —
engineering artifacts (toy data, smoke, unit tests, probes) stay allowed.

Refuses to overwrite an existing incompatible contract; `--force` is for a
version bump after the backtest changes the engine (§T0).
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer.tokenizer import TokenizerConfig       # noqa: E402


def git_commit() -> tuple:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            stderr=subprocess.DEVNULL).decode().strip()
        dirty = subprocess.call(
            ["git", "diff", "--quiet"], cwd=ROOT,
            stderr=subprocess.DEVNULL) != 0
        return commit, dirty
    except Exception:
        return None, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=tc.CONTRACT_PATH)
    ap.add_argument("--max-entity-tokens", type=int, default=None,
                    help="freeze from v2 corpus length stats (§4.5); "
                         "omit = keep unfrozen (engineering mode)")
    ap.add_argument("--stats-from", default=None,
                    help="v2 dataset dir to compute token length stats from")
    ap.add_argument("--t0-status", default=tc.T0_PENDING,
                    choices=[tc.T0_PENDING, tc.T0_ACCEPTED])
    ap.add_argument("--t0-commit", default=None)
    ap.add_argument("--t0-replay-set-hash", default=None)
    ap.add_argument("--t0-metrics", default=None,
                    help="path of the 1000-replay backtest metrics file")
    ap.add_argument("--t0-decision", default=None)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing contract (version bump)")
    args = ap.parse_args()

    commit, dirty = git_commit()
    tok = TokenizerConfig()
    xy_grid = {"nx": tok.grid_nx, "ny": tok.grid_ny,
               "residual_bins": tok.residual_bins}
    bias_buckets = {"dx": list(tok.dx_edges), "dy": list(tok.dy_edges),
                    "dist": list(tok.dist_edges)}

    if args.stats_from:
        from pysim.gamedata import GameData
        from pysim.rl.transformer.tokenizer import SemanticVocab, \
            token_length_stats
        from pysim.rl.transformer.data import load_rows
        gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
        vocab = SemanticVocab.from_gamedata(gd)
        rows = []
        for name in ("battle_sim_v2", "battle_real_v2",
                     "policy_prefix_real_v2"):
            p = os.path.join(args.stats_from, name + ".jsonl.gz")
            if os.path.exists(p):
                rows += load_rows(p, limit=20000)
        stats = token_length_stats([r["observation"] for r in rows],
                                   vocab, tok)
        print("token length stats:", json.dumps(stats))
        print("建议: max_entity_tokens >= p99, 超限样本进 excluded (§4.5)")
        if args.max_entity_tokens is None:
            args.max_entity_tokens = int(
                max(64, np_ceil_p99(stats))) if stats["p99"] else 320

    t0_record = None
    if args.t0_status == tc.T0_ACCEPTED:
        missing = [k for k, v in (("commit", args.t0_commit),
                                  ("replay_set_hash",
                                   args.t0_replay_set_hash),
                                  ("metrics", args.t0_metrics),
                                  ("decision", args.t0_decision)) if not v]
        if missing:
            sys.exit("T0 accepted 需要: --t0-commit --t0-replay-set-hash "
                     "--t0-metrics --t0-decision (缺 %s)" % missing)
        t0_record = {"commit": args.t0_commit,
                     "replay_set_hash": args.t0_replay_set_hash,
                     "metrics_path": args.t0_metrics,
                     "decision": args.t0_decision}

    if os.path.exists(args.path) and not args.force:
        old = tc.load_contract(args.path)
        if tc.check_contract(old):
            sys.exit("%s 已存在且不兼容 (%s); 确认版本 bump 后用 --force"
                     % (args.path, "; ".join(tc.check_contract(old))[:200]))
        sys.exit("%s 已存在; 如需重建(影响 contract digest)用 --force"
                 % args.path)

    c = tc.build_contract(git_commit=commit, git_dirty=dirty,
                          t0_status=args.t0_status, t0_record=t0_record,
                          max_entity_tokens=args.max_entity_tokens,
                          xy_grid=xy_grid, bias_buckets=bias_buckets)
    os.makedirs(os.path.dirname(args.path), exist_ok=True)
    with open(args.path, "w", encoding="utf8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("contract written:", args.path)
    print(json.dumps({k: c[k] for k in (
        "contract_version", "schema_version", "engine_version",
        "battlefield_input", "sim_label_version", "tokenizer_version",
        "t0_backtest", "training_gpu_allowlist", "reserved_physical_gpus")},
        ensure_ascii=False, indent=1))
    if c["t0_backtest"]["status"] != tc.T0_ACCEPTED:
        print("\n[T0 GATE] 状态 pending: 仅允许工程产物; 正式 sim label/"
              "训练/test/arena 结论在冻结前被禁止 (任务书 §3.2)。")


def np_ceil_p99(stats):
    import math
    return int(math.ceil(stats["p99"]))


if __name__ == "__main__":
    main()
