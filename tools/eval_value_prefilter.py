#!/usr/bin/env python
"""TValue prefilter 评测 (任务书 §10.2): candidate group 内 top-k recall
(oracle = 真实 damage diff 最优候选进入模型排序前 k 的比例) + direct-sim
regret (选中候选与 oracle 的真实分差)。

  CUDA_VISIBLE_DEVICES=1 tools/eval_value_prefilter.py \
    --run-dir local_data/rl_transformer/auto_v1 --seed 0 --k 8 32
"""
import argparse
import gzip
import json
import os
import sys

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer import distributed as D                # noqa: E402
from pysim.rl.transformer.tokenizer import (SemanticVocab,       # noqa: E402
                                            TokenizerConfig)
from pysim.rl.transformer.data import (load_rows,                # noqa: E402
                                       encode_value_row, collate_value)
from pysim.rl.transformer.battle_value import TValue, TValueConfig  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, nargs="+", default=[8, 32])
    ap.add_argument("--split", default="test")
    args = ap.parse_args()

    D.enforce_env() if os.environ.get("CUDA_VISIBLE_DEVICES") else None
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    contract = tc.load_contract(os.path.join(args.run_dir, "contract.json"))
    tc.require_compatible(contract)
    ck = torch.load(os.path.join(args.run_dir, "checkpoints",
                                 "tvalue_seed%d.pt" % args.seed),
                    map_location="cpu", weights_only=False)
    vocab = SemanticVocab.from_dict(ck["vocab"])
    cfg = TValueConfig.from_dict(ck["config"])
    tok_cfg = TokenizerConfig.from_dict(cfg.tokenizer)
    model = TValue(vocab, cfg, tok_cfg)
    model.load_state_dict(ck["model"])
    model.to(device).eval()

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    rows = load_rows(os.path.join(args.run_dir, "datasets",
                                  "battle_sim_v2.jsonl.gz"),
                     split=args.split)
    by_group = {}
    for r in rows:
        by_group.setdefault(r.get("candidate_group_id"), []).append(r)

    def score(r):
        a = r["agg"]
        return a["y_damage_to_opp"] - a["y_damage_to_self"] \
            + 0.25 * (a["p_win"] - a["p_loss"])

    rec = {k: [] for k in args.k}
    regret = {k: [] for k in args.k}
    n_groups = 0
    for gid, grp in sorted(by_group.items()):
        if not gid or len(grp) < 2:
            continue
        enc = []
        for r in grp:
            try:
                enc.append(encode_value_row(r, vocab, tok_cfg))
            except Exception:
                break
        if len(enc) != len(grp):
            continue
        n_groups += 1
        with torch.no_grad():
            p_all, d_all = [], []
            for i in range(0, len(enc), 64):
                b, c = collate_value(enc[i:i + 64], device=device,
                                     tok_cfg=tok_cfg)
                p, d = model.predict_symmetric(b, c["comp"], "sim")
                p_all.append(p.cpu().numpy())
                d_all.append(d.cpu().numpy())
        p = np.concatenate(p_all)
        d = np.concatenate(d_all)
        v = (d[:, 0] - d[:, 1]) + 0.25 * (p[:, 2] - p[:, 0])
        y = np.asarray([score(r) for r in grp])
        oracle = int(y.argmax())
        order = np.argsort(-v)
        rank_of_oracle = int(np.where(order == oracle)[0][0])
        for k in args.k:
            rec[k].append(float(rank_of_oracle < k))
            if len(y) > k:
                picked = int(order[0])          # top-1 direct pick
                regret[k].append(float(y.max() - y[picked]))
    out = {"seed": args.seed, "split": args.split, "n_groups": n_groups}
    for k in args.k:
        out["recall@%d" % k] = float(np.mean(rec[k])) if rec[k] else None
        out["regret@%d_top1" % k] = float(np.mean(regret[k])) \
            if regret[k] else None
    print(json.dumps(out, indent=1))
    out_path = os.path.join(args.run_dir,
                            "prefilter_seed%d_%s.json" % (args.seed,
                                                          args.split))
    with open(out_path, "w", encoding="utf8") as f:
        json.dump(out, f, indent=1)
    print("written:", out_path)


if __name__ == "__main__":
    main()
