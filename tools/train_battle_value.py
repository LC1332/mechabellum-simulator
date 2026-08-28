#!/usr/bin/env python
"""T2: train V_battle_sim / V_battle_real (task §7).

Two-domain training on one shared encoder: sim batches (battle_sim_v1,
soft WDL + mean damage) update SimHead only; real batches (battle_real_v1)
update RealHead only. Reports WDL/damage/calibration/ranking/symmetry on
validation + test, error examples, and the T2 gate verdict.
"""
import argparse
import copy
import gzip
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.rl.features import Vocab, battle_features                 # noqa: E402
from pysim.rl.baselines import load_rows, wdl_metrics, damage_metrics  # noqa: E402
from pysim.rl.models.battle_value import BattleValueNet              # noqa: E402
from pysim.gamedata import GameData                                  # noqa: E402


def make_batch(rows, vocab, device):
    B = {
        k: [] for k in ("self_f", "self_mech", "self_equip", "self_mask",
                        "opp_f", "opp_mech", "opp_equip", "opp_mask",
                        "global", "self_off", "opp_off")
    }
    B["self_tech"] = {"tech_ids": [], "tech_owners": []}
    B["opp_tech"] = {"tech_ids": [], "tech_owners": []}
    for r in rows:
        f = battle_features(r["observation"], vocab)
        for k in B:
            if k.endswith("_tech"):
                continue
            B[k].append(f[k])
        for side in ("self", "opp"):
            ti = f[side + "_tech"]["tech_ids"]
            to = f[side + "_tech"]["tech_owners"]
            if len(ti) == 0:
                ti = np.zeros(1, dtype=np.int64)
                to = np.zeros(1, dtype=np.int64)
            B[side + "_tech"]["tech_ids"].append(ti)
            B[side + "_tech"]["tech_owners"].append(to)
    out = {}
    for k, v in B.items():
        if k.endswith("_tech"):
            T = max(len(x) for x in v["tech_ids"])
            ids = np.zeros((len(rows), T), dtype=np.int64)
            own = np.zeros((len(rows), T), dtype=np.int64)
            for i, (a, b) in enumerate(zip(v["tech_ids"],
                                           v["tech_owners"])):
                ids[i, :len(a)] = a
                own[i, :len(b)] = b
            out[k] = {"tech_ids": torch.as_tensor(ids).to(device),
                      "tech_owners": torch.as_tensor(own).to(device)}
        else:
            out[k] = torch.as_tensor(np.stack(v)).to(device)
    return out


def swap_batch(batch):
    """Side swap = the view from the other end of the table: sides exchange
    AND geometry mirrors (y negates, rotation flips), matching
    observation.ego_mirror_state. Unit float layout: [lvl, exp, x/350,
    y/300, rot, move_ok]."""
    out = dict(batch)
    for k in ("self_f", "self_mech", "self_equip", "self_mask", "self_tech",
              "self_off"):
        opp_k = k.replace("self", "opp")
        out[k], out[opp_k] = batch[opp_k], batch[k]
    for k in ("self_f", "opp_f"):
        f = out[k].clone()
        f[..., 3] = -f[..., 3]          # y
        f[..., 4] = 1.0 - f[..., 4]     # rot
        out[k] = f
    out["global"] = batch["global"].clone()
    out["global"][:, 1] = batch["global"][:, 2]
    out["global"][:, 2] = batch["global"][:, 1]
    out["global"][:, 3] = batch["global"][:, 4]
    out["global"][:, 4] = batch["global"][:, 3]
    out["global"][:, 5] = batch["global"][:, 7]
    out["global"][:, 7] = batch["global"][:, 5]
    out["global"][:, 6] = batch["global"][:, 8]
    out["global"][:, 8] = batch["global"][:, 6]
    out["global"][:, 9] = batch["global"][:, 10]
    out["global"][:, 10] = batch["global"][:, 9]
    out["global"][:, 11] = batch["global"][:, 12]
    out["global"][:, 12] = batch["global"][:, 11]
    out["global"][:, 13] = batch["global"][:, 14]
    out["global"][:, 14] = batch["global"][:, 13]
    return out


def rows_domain(rows, domain):
    if domain == "sim":
        # soft targets from the aggregated sim distribution
        out = []
        for r in rows:
            agg = r["agg"]
            out.append({
                "observation": r["observation"],
                "wdl_soft": (agg["p_loss"], agg["p_draw"], agg["p_win"]),
                "dmg": (agg["y_damage_to_opp"], agg["y_damage_to_self"]),
                "sample_id": r["sample_id"], "split": r["split"],
                "match_id_hash": r["match_id_hash"],
                "state_source": r.get("state_source"),
                "candidate_group_id": r.get("candidate_group_id"),
                "tier": r.get("tier"),
            })
        return out
    out = []
    for r in rows:
        out.append({
            "observation": r["observation"],
            "wdl": int(r["y_wdl"]),
            "dmg": (float(r["y_damage_to_opp"]), float(r["y_damage_to_self"])),
            "sample_id": r["sample_id"], "split": r["split"],
            "match_id_hash": r["match_id_hash"],
        })
    return out


def loss_domain(model, batch, targets, domain, lam_dmg=1.0, lam_sym=0.1,
                device="cpu"):
    wdl_logits, dmg = model(batch, domain)
    if "wdl_soft" in targets[0]:
        soft = torch.as_tensor(np.asarray([t["wdl_soft"] for t in targets]),
                               dtype=torch.float32, device=device)
        ce = -(soft * F.log_softmax(wdl_logits, dim=-1)).sum(-1).mean()
    else:
        y = torch.as_tensor([t["wdl"] for t in targets], device=device)
        ce = F.cross_entropy(wdl_logits, y)
    d = torch.as_tensor(np.asarray([t["dmg"] for t in targets]),
                        dtype=torch.float32, device=device)
    huber = F.huber_loss(dmg, d, delta=0.1)
    if lam_sym > 0:
        sw = swap_batch(batch)
        wdl2, dmg2 = model(sw, domain)
        sym = (F.softmax(wdl_logits, -1) -
               F.softmax(torch.stack([wdl2[:, 2], wdl2[:, 1], wdl2[:, 0]],
                                     dim=-1), -1)).abs().mean() + \
              (dmg - torch.stack([dmg2[:, 1], dmg2[:, 0]], dim=-1)).abs().mean()
    else:
        sym = torch.zeros((), device=device)
    return ce + lam_dmg * huber + lam_sym * sym, \
        {"ce": float(ce), "huber": float(huber), "sym": float(sym)}


def bootstrap_ci(values, groups, n=1000, seed=0, alpha=0.05):
    """Replay-group bootstrap 95% CI (task §7.3)."""
    rng = np.random.RandomState(seed)
    by_group = {}
    for v, g in zip(values, groups):
        by_group.setdefault(g, []).append(v)
    groups = [np.asarray(v) for v in by_group.values() if len(v)]
    if not groups:
        return (float("nan"),) * 2
    means = []
    for _ in range(n):
        pick = rng.choice(len(groups), len(groups))
        means.append(np.concatenate([groups[i] for i in pick]).mean())
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available()
                    else "cpu")
    ap.add_argument("--lambda-dmg", type=float, default=1.0)
    ap.add_argument("--lambda-sym", type=float, default=0.1)
    ap.add_argument("--tiny", action="store_true", help="tiny-overfit probe")
    ap.add_argument("--shuffle-labels", action="store_true")
    args = ap.parse_args()
    ds = os.path.join(args.run_dir, "datasets")
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = torch.device(args.device)

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    vocab = Vocab(gd)

    sim_raw = load_rows(os.path.join(ds, "battle_sim_v1.jsonl.gz"))
    real_rows = load_rows(os.path.join(ds, "battle_real_v1.jsonl.gz"))
    if args.shuffle_labels:
        import random
        rng = random.Random(args.seed)
        groups = {}
        for r in real_rows:
            groups.setdefault(r["match_id_hash"], []).append(r)
        for g in groups.values():
            ys = [r["y_wdl"] for r in g]
            rng.shuffle(ys)
            for r, y in zip(g, ys):
                r["y_wdl"] = y
    if args.tiny:
        sim_raw = sim_raw[:64]
        real_rows = real_rows[:64]
        args.epochs = 60
    sim_rows = rows_domain(sim_raw, "sim")
    real_domain = rows_domain(real_rows, "real")
    tr_sim = [r for r in sim_rows if r["split"] == "train"]
    tr_real = [r for r in real_domain if r["split"] == "train"]
    va_real = [r for r in real_domain if r["split"] == "validation"]
    te_real = [r for r in real_domain if r["split"] == "test"]
    va_sim = [r for r in sim_rows if r["split"] == "validation"]
    te_sim = [r for r in sim_rows if r["split"] == "test"]
    print("sim train %d | real train %d | val/test real %d/%d" % (
        len(tr_sim), len(tr_real), len(va_real), len(te_real)))

    model = BattleValueNet(vocab.n_mech, vocab.n_equip,
                           n_tech=vocab.n_tech).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print("params: %.2fM" % (n_params / 1e6))
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    rng = np.random.RandomState(args.seed)

    def iterate(rows, domain):
        order = rng.permutation(len(rows))
        for i in range(0, len(order), args.batch_size):
            idx = order[i:i + args.batch_size]
            yield [rows[j] for j in idx]

    t0 = time.time()
    for epoch in range(args.epochs):
        model.train()
        tot = {"ce": 0.0, "huber": 0.0, "sym": 0.0, "n": 0}
        for domain, rows in (("real", tr_real), ("sim", tr_sim)):
            if not rows:
                continue
            for chunk in iterate(rows, domain):
                if len(chunk) < 2:
                    continue
                batch = make_batch(chunk, vocab, device)
                loss, parts = loss_domain(model, batch, chunk, domain,
                                          args.lambda_dmg, args.lambda_sym,
                                          device)
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                opt.step()
                bs = len(chunk)
                for k in parts:
                    tot[k] += parts[k] * bs
                tot["n"] += bs
        print("epoch %d %s (%.0fs)" % (
            epoch, {k: round(v / max(tot["n"], 1), 4) for k, v in tot.items()
                    if k != "n"}, time.time() - t0))

    # ------------------------------------------------------------- eval
    model.eval()

    @torch.no_grad()
    def predict(rows, domain):
        probas, dmgs = [], []
        for i in range(0, len(rows), 512):
            chunk = rows[i:i + 512]
            batch = make_batch(chunk, vocab, device)
            wdl, dmg = model(batch, domain)
            probas.append(F.softmax(wdl, -1).cpu().numpy())
            dmgs.append(dmg.cpu().numpy())
        if not probas:
            return np.zeros((0, 3)), np.zeros((0, 2))
        return np.concatenate(probas), np.concatenate(dmgs)

    report = {"args": vars(args), "params": n_params, "domains": {}}
    for domain, rows_by_split in (
            ("real", {"validation": va_real, "test": te_real}),
            ("sim", {"validation": va_sim, "test": te_sim})):
        entry = {}
        for split, rows in rows_by_split.items():
            if not rows:
                continue
            proba, dmg = predict(rows, domain)
            if len(rows) and "wdl_soft" in rows[0]:
                y = np.argmax(np.asarray([r["wdl_soft"] for r in rows]),
                              axis=1)
            else:
                y = np.asarray([r["wdl"] for r in rows])
            yd = np.asarray([r["dmg"] for r in rows])
            m = wdl_metrics(y, proba)
            m["damage"] = damage_metrics(yd, dmg)
            groups = [r["match_id_hash"] for r in rows]
            lo, hi = bootstrap_ci(
                -np.log(np.clip(proba[np.arange(len(y)), y], 1e-9, 1)),
                groups)
            m["nll_ci95"] = [lo, hi]
            # side-swap consistency
            sw = swap_batch(make_batch(rows[:256], vocab, device))
            b0 = make_batch(rows[:256], vocab, device)
            w0, d0 = model(b0, domain)
            w1, d1 = model(sw, domain)
            m["side_swap_wdl_max_diff"] = float(
                (F.softmax(w0, -1) -
                 F.softmax(torch.stack([w1[:, 2], w1[:, 1], w1[:, 0]], -1),
                           -1)).abs().max())
            m["side_swap_dmg_max_diff"] = float(
                (d0 - torch.stack([d1[:, 1], d1[:, 0]], -1)).abs().max())
            # error examples: most confident mistakes
            conf = proba.max(axis=1)
            wrong = np.where(proba.argmax(axis=1) != y)[0]
            top_wrong = wrong[np.argsort(-conf[wrong])][:50]
            m["top_wrong_samples"] = [rows[i]["sample_id"] for i in top_wrong]
            entry[split] = m
        report["domains"][domain] = entry

    # ranking accuracy on candidate groups (sim validation)
    by_group = {}
    for r in va_sim:
        by_group.setdefault(r["candidate_group_id"], []).append(r)
    pair_acc, spear = [], []
    for gid, rows in by_group.items():
        if len(rows) < 2:
            continue
        proba, dmg = predict(rows, "sim")
        v = ((dmg[:, 0] - dmg[:, 1]) + 0.25 * (proba[:, 2] - proba[:, 0]))
        y = np.asarray([r["dmg"][0] - r["dmg"][1] for r in rows])
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if abs(y[i] - y[j]) < 1e-6:
                    continue
                pair_acc.append(float((v[i] > v[j]) == (y[i] > y[j])))
    report["sim_ranking_pairwise_acc"] = float(np.mean(pair_acc)) \
        if pair_acc else None
    report["sim_ranking_pairs"] = len(pair_acc)

    # save checkpoint
    ckpt_dir = os.path.join(args.run_dir, "checkpoints")
    os.makedirs(ckpt_dir, exist_ok=True)
    ck = os.path.join(ckpt_dir, "value_seed%d.pt" % args.seed)
    torch.save({"model": model.state_dict(),
                "vocab": vocab.to_dict(),
                "args": vars(args),
                "n_mech": vocab.n_mech, "n_equip": vocab.n_equip}, ck)
    report["checkpoint"] = ck
    with open(os.path.join(args.run_dir,
                           "value_report_seed%d.json" % args.seed),
              "w") as f:
        json.dump(report, f, indent=1, default=str)
    for domain, entry in report["domains"].items():
        for split, m in entry.items():
            print("%s %s: nll=%.4f acc=%.3f dmg_mae=%.4f swap=%.5f" % (
                domain, split, m["nll"], m["acc"], m["damage"]["mae"],
                m["side_swap_wdl_max_diff"]))
    print("sim ranking pairwise acc:", report["sim_ranking_pairwise_acc"],
          "(%d pairs)" % report["sim_ranking_pairs"])


if __name__ == "__main__":
    main()
