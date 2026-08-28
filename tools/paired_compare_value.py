#!/usr/bin/env python
"""TValue vs DeepSets-v2 公平 paired 对照 (任务书 §10.2/§15).

同一批 test 行、同一 replay-group bootstrap: real 域逐样本 NLL 差,
sim 域逐 candidate-group pairwise-acc 差。输出均值差 + 95% CI +
"Transformer 更优" 判定(至少一个核心指标 CI 排除 0 且其余无明显退化)。

  CUDA_VISIBLE_DEVICES=1 tools/paired_compare_value.py \
    --tvalue-run local_data/rl_transformer/auto_v1 \
    --deepsets-run local_data/rl_transformer/deepsets_v2 --seed 0
"""
import argparse
import json
import os
import sys

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer.tokenizer import (SemanticVocab,       # noqa: E402
                                            TokenizerConfig,
                                            battle_token_obs_from_v1)
from pysim.rl.transformer.data import (encode_value_row,         # noqa: E402
                                       collate_value)
from pysim.rl.transformer.battle_value import TValue, TValueConfig  # noqa: E402


def group_bootstrap_ci(diff_by_group, n=2000, seed=0, alpha=0.05):
    rng = np.random.RandomState(seed)
    groups = [np.asarray(v) for v in diff_by_group.values() if len(v)]
    means = []
    for _ in range(n):
        pick = rng.choice(len(groups), len(groups))
        means.append(np.concatenate([groups[i] for i in pick]).mean())
    return float(np.mean([m for m in means])), \
        float(np.quantile(means, alpha / 2)), \
        float(np.quantile(means, 1 - alpha / 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tvalue-run", required=True)
    ap.add_argument("--deepsets-run", required=True)
    ap.add_argument("--v1-datasets",
                    default="local_data/rl_phase1/v1_full/datasets")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))

    # ---- TValue
    contract = tc.load_contract(os.path.join(args.tvalue_run,
                                             "contract.json"))
    tc.require_compatible(contract)
    ck = torch.load(os.path.join(args.tvalue_run, "checkpoints",
                                 "tvalue_seed%d.pt" % args.seed),
                    map_location="cpu", weights_only=False)
    tv_vocab = SemanticVocab.from_dict(ck["vocab"])
    tv_cfg = TValueConfig.from_dict(ck["config"])
    tok_cfg = TokenizerConfig.from_dict(tv_cfg.tokenizer)
    tv = TValue(tv_vocab, tv_cfg, tok_cfg)
    tv.load_state_dict(ck["model"])
    tv.to(device).eval()

    # ---- DeepSets-v2 (phase1 model, same rows/split)
    from pysim.rl.features import Vocab, battle_features
    from pysim.rl.models.battle_value import BattleValueNet
    ds_ck = torch.load(os.path.join(args.deepsets_run, "checkpoints",
                                    "value_seed%d.pt" % args.seed),
                       map_location="cpu", weights_only=False)
    ds_vocab = Vocab.from_dict(ds_ck["vocab"])
    ds = BattleValueNet(ds_vocab.n_mech, ds_vocab.n_equip,
                        n_tech=ds_vocab.n_tech)
    ds.load_state_dict(ds_ck["model"])
    ds.to(device).eval()

    from pysim.rl.transformer.data import load_rows
    real_rows = load_rows(os.path.join(args.v1_datasets,
                                       "battle_real_v1.jsonl.gz"),
                          split="test")
    sim_rows = load_rows(os.path.join(args.v1_datasets,
                                      "battle_sim_v1.jsonl.gz"),
                         split="test")

    # ---- real 域: 逐样本 NLL 差 (tvalue - deepsets; 负 = transformer 更优)
    nll_a, nll_b, groups = [], [], []
    with torch.no_grad():
        for i in range(0, len(real_rows), 64):
            chunk = real_rows[i:i + 64]
            enc = []
            for r in chunk:
                obs2 = battle_token_obs_from_v1(r["observation"])
                enc.append(encode_value_row({"observation": obs2},
                                            tv_vocab, tok_cfg))
            b2, c2 = collate_value(enc, device=device, tok_cfg=tok_cfg)
            wa, da, _ = tv(b2, c2["comp"], "real")
            pa = F.softmax(wa.float(), -1)
            from tools.train_battle_value import make_batch
            b1 = make_batch(chunk, ds_vocab, device)
            wb, db = ds(b1, "real")
            pb = F.softmax(wb.float(), -1)
            y = torch.as_tensor([int(r["y_wdl"]) for r in chunk],
                                device=device)
            nll_a += (-torch.log(pa.gather(1, y[:, None]).squeeze(1)
                                 .clamp(min=1e-9))).tolist()
            nll_b += (-torch.log(pb.gather(1, y[:, None]).squeeze(1)
                                 .clamp(min=1e-9))).tolist()
            groups += [r["match_id_hash"] for r in chunk]
    real_diff = {}
    for a, b, g in zip(nll_a, nll_b, groups):
        real_diff.setdefault(g, []).append(a - b)
    real_mean, real_lo, real_hi = group_bootstrap_ci(real_diff)

    # ---- sim 域: 逐 group pairwise acc 差
    def sim_pairwise(rows, predict_fn):
        by_group = {}
        for r in rows:
            by_group.setdefault(r.get("candidate_group_id"), []).append(r)
        acc = {}
        for gid, grp in by_group.items():
            if not gid or len(grp) < 2:
                continue
            v, y = predict_fn(grp)
            ok = []
            for i in range(len(y)):
                for j in range(i + 1, len(y)):
                    if abs(y[i] - y[j]) < 1e-6:
                        continue
                    ok.append(float((v[i] > v[j]) == (y[i] > y[j])))
            if ok:
                acc[gid] = float(np.mean(ok))
        return acc

    def tv_scores(grp):
        enc = []
        for r in grp:
            obs2 = battle_token_obs_from_v1(r["observation"])
            enc.append(encode_value_row({"observation": obs2}, tv_vocab,
                                        tok_cfg))
        with torch.no_grad():
            p_all, d_all = [], []
            for i in range(0, len(enc), 64):
                b, c = collate_value(enc[i:i + 64], device=device,
                                     tok_cfg=tok_cfg)
                p, d = tv.predict_symmetric(b, c["comp"], "sim")
                p_all.append(p.cpu().numpy())
                d_all.append(d.cpu().numpy())
        p = np.concatenate(p_all)
        d = np.concatenate(d_all)
        v = (d[:, 0] - d[:, 1]) + 0.25 * (p[:, 2] - p[:, 0])
        y = np.asarray([r["agg"]["y_damage_to_opp"]
                        - r["agg"]["y_damage_to_self"] for r in grp])
        return v, y

    def ds_scores(grp):
        with torch.no_grad():
            proba, dmg = [], []
            for i in range(0, len(grp), 64):
                from tools.train_battle_value import make_batch
                b = make_batch(grp[i:i + 64], ds_vocab, device)
                w, d = ds(b, "sim")
                proba.append(F.softmax(w.float(), -1).cpu().numpy())
                dmg.append(d.float().cpu().numpy())
        p = np.concatenate(proba)
        d = np.concatenate(dmg)
        v = (d[:, 0] - d[:, 1]) + 0.25 * (p[:, 2] - p[:, 0])
        y = np.asarray([r["agg"]["y_damage_to_opp"]
                        - r["agg"]["y_damage_to_self"] for r in grp])
        return v, y

    acc_tv = sim_pairwise(sim_rows, tv_scores)
    acc_ds = sim_pairwise(sim_rows, ds_scores)
    common = sorted(set(acc_tv) & set(acc_ds))
    sim_diff = {g: [acc_tv[g] - acc_ds[g]] for g in common}
    sim_mean, sim_lo, sim_hi = group_bootstrap_ci(sim_diff)

    out = {
        "seed": args.seed,
        "real_nll_diff_mean(tvalue-deepsets)": real_mean,
        "real_nll_diff_ci95": [real_lo, real_hi],
        "real_n": len(real_rows),
        "sim_pairwise_diff_mean(tvalue-deepsets)": sim_mean,
        "sim_pairwise_diff_ci95": [sim_lo, sim_hi],
        "sim_groups": len(common),
        "verdict": {
            "real_ci_excludes_0_favors_tvalue": bool(
                real_hi < 0),
            "sim_ci_excludes_0_favors_tvalue": bool(sim_lo > 0),
        },
    }
    print(json.dumps(out, indent=1, ensure_ascii=False))
    out_path = args.out or os.path.join(
        args.tvalue_run, "paired_vs_deepsets_seed%d.json" % args.seed)
    with open(out_path, "w", encoding="utf8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("written:", out_path)


if __name__ == "__main__":
    main()
