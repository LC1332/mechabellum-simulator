#!/usr/bin/env python
"""T1: run non-neural baselines + health checks over the RL Phase 1
datasets and write baseline_report.json (task §6)."""
import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.rl.features import Vocab                                  # noqa: E402
from pysim.rl.baselines import (load_rows, value_matrix, value_targets,  # noqa: E402
                                constant_prior, fit_value_baselines,
                                verb_stats, policy_baseline_predict,
                                duplicate_detector, observation_leakage_scan,
                                VERBS)
from pysim.rl.metrics import wdl_metrics, damage_metrics  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--limit-train", type=int, default=60000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    ds = os.path.join(args.run_dir, "datasets")
    t0 = time.time()

    bat = load_rows(os.path.join(ds, "battle_real_v1.jsonl.gz"))
    pol = load_rows(os.path.join(ds, "policy_prefix_real_v1.jsonl.gz"),
                    limit=120000)
    vocab = Vocab()
    # vocab from gamedata via any trained matrix build
    tr = [r for r in bat if r["split"] == "train"]
    va = [r for r in bat if r["split"] == "validation"]
    te = [r for r in bat if r["split"] == "test"]
    print("battle rows train/val/test = %d/%d/%d" % (len(tr), len(va), len(te)))

    from pysim.gamedata import GameData
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    vocab = Vocab(gd)

    X_tr = value_matrix(tr, vocab)[:args.limit_train]
    yw_tr, yd_tr = value_targets(tr)[:args.limit_train]
    X_va = value_matrix(va, vocab)
    yw_va, yd_va = value_targets(va)
    X_te = value_matrix(te, vocab)
    yw_te, yd_te = value_targets(te)

    report = {"elapsed_s": 0, "value": {}, "policy": {}, "health": {}}

    # ---- constant prior
    cp = constant_prior(yw_tr, yd_tr)
    prior_proba = lambda X: np.tile(cp["probs"], (len(X), 1))
    prior_dmg = lambda X: np.tile(cp["damage_mean"], (len(X), 1))

    # ---- fitted baselines
    t1 = time.time()
    preds = fit_value_baselines(X_tr, yw_tr, yd_tr, seed=args.seed)
    preds["constant_prior"] = (prior_proba, prior_dmg)
    print("baselines fit in %.1fs" % (time.time() - t1))

    for name, (pf, df) in preds.items():
        entry = {}
        for split, X, yw, yd in (("validation", X_va, yw_va, yd_va),
                                 ("test", X_te, yw_te, yd_te)):
            proba = pf(X)
            proba = proba / proba.sum(axis=1, keepdims=True)
            entry[split] = {
                "wdl": wdl_metrics(yw, proba),
                "damage": damage_metrics(yd, df(X)),
            }
        report["value"][name] = entry

    # ---- label shuffle sanity (test must drop to prior level)
    rng = np.random.RandomState(args.seed)
    yw_shuf = yw_te.copy()
    perm_groups = {}
    for i, r in enumerate(te):
        perm_groups.setdefault(r["match_id_hash"], []).append(i)
    for gidx in perm_groups.values():
        gidx = np.asarray(gidx)
        yw_shuf[gidx] = yw_shuf[rng.permutation(gidx)]
    proba = preds["hgb"][0](X_te)
    proba = proba / proba.sum(axis=1, keepdims=True)
    report["health"]["label_shuffle_test_nll"] = wdl_metrics(
        yw_shuf, proba)["nll"]
    report["health"]["test_nll_clean"] = \
        report["value"]["hgb"]["test"]["wdl"]["nll"]

    # ---- policy baselines (teacher-forced verb accuracy)
    stats = verb_stats([r for r in pol if r["split"] == "train"])
    for name in ("end_only", "freq_global", "freq_cond", "random_legal"):
        accs = {}
        for split in ("validation", "test"):
            rows = [r for r in pol if r["split"] == split]
            correct = 0
            verb_mask_recalls = []
            for r in rows:
                _, choice = policy_baseline_predict(name, r, stats)
                correct += int(choice == r["target"]["verb"])
                v = r["target"]["verb"]
                verb_mask_recalls.append(bool(
                    r["space"]["verb_mask"][v]) if v < len(
                        r["space"]["verb_mask"]) else False)
            accs[split] = {"verb_top1": correct / max(len(rows), 1),
                           "n": len(rows),
                           "target_in_verbmask_recall": float(np.mean(
                               verb_mask_recalls))}
        report["policy"][name] = accs
    # teacher-forced target-in-mask recall on gold (T1 gate: 100%)
    recalls = []
    for r in pol:
        if r["tier"] != "gold":
            continue
        v = r["target"]["verb"]
        recalls.append(bool(r["space"]["verb_mask"][v])
                       if v < len(r["space"]["verb_mask"]) else False)
    report["health"]["gold_target_in_verbmask_recall"] = float(
        np.mean(recalls)) if recalls else None
    report["health"]["gold_target_in_verbmask_n"] = len(recalls)

    # ---- data health
    report["health"]["split_leaks_battle"] = duplicate_detector(bat)
    report["health"]["split_leaks_policy"] = duplicate_detector(pol)
    report["health"]["observation_leakage"] = observation_leakage_scan(bat)
    counts = {"battle": {}, "policy": {}}
    for r in bat:
        counts["battle"].setdefault(r["split"], {}).setdefault(
            r["tier"], 0)
        counts["battle"][r["split"]][r["tier"]] += 1
    for r in pol:
        counts["policy"].setdefault(r["split"], {}).setdefault(
            r["tier"], 0)
        counts["policy"][r["split"]][r["tier"]] += 1
    report["health"]["conservation"] = counts

    report["elapsed_s"] = round(time.time() - t0, 1)
    with open(os.path.join(args.run_dir, "baseline_report.json"), "w") as f:
        json.dump(report, f, indent=1, default=str)
    print(json.dumps(report["value"], indent=1, default=str)[:900])
    print("policy:", json.dumps(
        {k: {s: v[s]["verb_top1"] for s in v} for k, v in
         report["policy"].items()}, indent=1))
    print("health:", json.dumps(
        {k: v for k, v in report["health"].items()
         if not isinstance(v, list) or len(v) < 5}, default=str)[:400])
    print("done in %.1fs -> baseline_report.json" % report["elapsed_s"])


if __name__ == "__main__":
    main()
