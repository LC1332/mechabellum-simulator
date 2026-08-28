#!/usr/bin/env python
"""Train TValue (V_battle_sim / V_battle_real) — Transformer基线任务书 §6.1/§8.1/§9.

Works single-process or under torchrun (§9.3):
  CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7 \
  torchrun --standalone --nproc_per_node=7 tools/train_transformer_value.py \
    --run-dir local_data/rl_transformer/<run_id> \
    --config configs/rl/transformer_value_medium_v1.json

The launcher enforces the physical GPU allowlist 1..7 (GPU 0 reserved) and
refuses formal training while the contract's T0 gate is pending (§3.2) —
pass --allow-engineering for toy/smoke runs. Checkpoints carry
model/optimizer/scheduler/RNG/epoch+cursor for exact resume (§8.3).
"""
import argparse
import json
import os
import random
import sys
import time
import zlib

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.rl import metrics as M                                # noqa: E402
from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer import distributed as D                # noqa: E402
from pysim.rl.transformer.tokenizer import (TokenizerConfig,     # noqa: E402
                                            SemanticVocab)
from pysim.rl.transformer.data import (load_rows, fit_vocab,     # noqa: E402
                                       encode_value_row, collate_value,
                                       TokenCacheReader)
from pysim.rl.transformer.battle_value import (TValue,           # noqa: E402
                                               TValueConfig,
                                               swapped_inputs)
from pysim.rl.transformer.losses import value_loss               # noqa: E402


def lr_lambda(step, warmup, total):
    if step < warmup:
        return step / max(1, warmup)
    p = (step - warmup) / max(1, total - warmup)
    return max(0.05, 0.5 * (1 + np.cos(np.pi * min(1.0, p))))


def bootstrap_ci(values, groups, n=1000, seed=0, alpha=0.05):
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
    return float(np.quantile(means, alpha / 2)), \
        float(np.quantile(means, 1 - alpha / 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--overrides", default="{}",
                    help='JSON applied onto cfg.model (ablation switches)')
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=0,
                    help="global optimizer-step cap (soak/benchmark 用)")
    ap.add_argument("--predictions-out", default=None,
                    help="test split 逐样本预测 jsonl.gz (§13.4 回溯)")
    ap.add_argument("--shuffle-labels", action="store_true",
                    help="§11 label-shuffle sanity probe")
    ap.add_argument("--resume", default=None)
    ap.add_argument("--allow-engineering", action="store_true",
                    help="permit training under a pending T0 gate "
                         "(toy/smoke only — report carries the flag)")
    args = ap.parse_args()

    # ---- environment gate (§9.1): physical GPU 1..7 only; CPU smoke runs
    # (no CUDA requested) are exempt from the GPU allowlist
    if args.device is None and not os.environ.get("CUDA_VISIBLE_DEVICES"):
        args.device = "cpu"
        os.environ["TRANSFORMER_ALLOW_CPU"] = "1"
    physical = D.enforce_env()
    info = D.setup_distributed()
    device = torch.device(args.device or info["device"])
    torch.manual_seed(args.seed if args.seed is not None else 0)

    with open(args.config, encoding="utf8") as f:
        cfg_all = json.load(f)
    model_cfg = TValueConfig.from_dict(cfg_all["model"])
    overrides = json.loads(args.overrides)
    for k, v in overrides.items():
        if not hasattr(model_cfg, k):
            sys.exit("unknown override key %s" % k)
        setattr(model_cfg, k, v)
    tok_cfg = TokenizerConfig.from_dict(model_cfg.tokenizer)
    tr = cfg_all["train"]
    epochs = args.epochs or tr["epochs"]
    bs = args.batch_size or tr["batch_size"]
    seed = args.seed if args.seed is not None else tr["seeds"][0]

    # ---- contract + T0 gate
    contract = tc.load_contract(os.path.join(args.run_dir, "contract.json"))
    bad = tc.check_contract(contract)
    if bad:
        sys.exit("contract incompatible: %s" % "; ".join(bad))
    formal = bool(cfg_all.get("formal")) and not args.allow_engineering
    if formal and not tc.t0_gate_allows(contract, formal=True):
        sys.exit("[T0 GATE] 正式训练被禁止 (§3.2). toy/smoke 请用 "
                 "--allow-engineering 或非 formal config.")
    torch.backends.cuda.matmul.allow_tf32 = bool(tr.get("tf32", True))
    use_bf16 = bool(tr.get("amp_bf16", False)) and device.type == "cuda"

    # ---- data (token cache if present, else direct encode; labels always
    # come from the v2 datasets keyed by sample_id)
    ds_dir = os.path.join(args.run_dir, "datasets")
    cache_dir = os.path.join(args.run_dir, "token_cache")
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    sim_rows = load_rows(os.path.join(ds_dir, "battle_sim_v2.jsonl.gz"),
                         limit=args.limit)
    real_rows = load_rows(os.path.join(ds_dir, "battle_real_v2.jsonl.gz"),
                          limit=args.limit)
    vpath = os.path.join(args.run_dir, "vocab.json")
    if os.path.exists(vpath):
        vocab = SemanticVocab.from_dict(json.load(open(vpath)))
    else:
        vocab = fit_vocab([r for r in real_rows
                           if r["split"] == "train"], gd)
    if args.shuffle_labels:
        rng = random.Random(seed)
        groups = {}
        for r in real_rows:
            groups.setdefault(r["match_id_hash"], []).append(r)
        for g in groups.values():
            ys = [r["y_wdl"] for r in g]
            rng.shuffle(ys)
            for r, y in zip(g, ys):
                r["y_wdl"] = y

    tokens_by_split = {}
    if os.path.exists(os.path.join(cache_dir, "manifest.json")):
        from pysim.rl.transformer.data import TokenCacheReader
        reader = TokenCacheReader(cache_dir, contract)
        for split in ("train", "validation", "test"):
            tokens_by_split[split] = {
                sid: row for sid, row in reader.iter_split(split)}
        if info["rank"] == 0:
            print("token cache:", reader.manifest["manifest_digest"],
                  "| rows:", len(reader))

    def enc(rows):
        out = []
        for r in rows:
            tok = tokens_by_split.get(r["split"], {}).get(r["sample_id"])
            if tok is None:
                try:
                    tok = encode_value_row(r, vocab, tok_cfg)
                except Exception:
                    continue        # excluded: counted in the data card
            out.append((r, tok))
        return out

    tr_sim = enc([r for r in sim_rows if r["split"] == "train"])
    va_sim = enc([r for r in sim_rows if r["split"] == "validation"])
    tr_real = enc([r for r in real_rows if r["split"] == "train"])
    va_real = enc([r for r in real_rows if r["split"] == "validation"])
    te_real = enc([r for r in real_rows if r["split"] == "test"])
    te_sim = enc([r for r in sim_rows if r["split"] == "test"])
    if info["rank"] == 0:
        print("sim tr/va/te %d/%d/%d | real tr/va/te %d/%d/%d" % (
            len(tr_sim), len(va_sim), len(te_sim),
            len(tr_real), len(va_real), len(te_real)))

    model = TValue(vocab, model_cfg, tok_cfg).to(device)
    n_params = model.n_params()
    raw_model = model
    if info["distributed"]:
        model = D.wrap_ddp(model, info)
    opt = torch.optim.AdamW(model.parameters(), lr=tr["lr"],
                            weight_decay=tr.get("weight_decay", 0.01),
                            fused=device.type == "cuda")
    steps_per_epoch = max(1, (max(len(tr_sim), len(tr_real)) + bs - 1)
                          // bs)
    total_steps = steps_per_epoch * epochs
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: lr_lambda(s, tr.get("warmup_steps", 200),
                                 total_steps))
    rng = np.random.RandomState(seed)

    start_epoch, global_step = 0, 0
    if args.resume and os.path.exists(args.resume):
        ck = torch.load(args.resume, map_location="cpu", weights_only=False)
        D.unwrap(model).load_state_dict(ck["model"])
        opt.load_state_dict(ck["optimizer"])
        sched.load_state_dict(ck["scheduler"])
        start_epoch = ck["epoch"]
        global_step = ck["global_step"]
        torch.set_rng_state(ck["rng_torch"])
        np.random.set_state(ck["rng_np"])
        random.setstate(ck["rng_py"])
        if info["rank"] == 0:
            print("resumed from %s at epoch %d" % (args.resume, start_epoch))

    def batches(rows):
        order = rng.permutation(len(rows))
        for i in range(0, len(order), bs):
            chunk = [rows[j] for j in order[i:i + bs]]
            if len(chunk) >= 2:
                yield chunk

    def run_epoch(rows, domain, epoch):
        model.train()
        agg, n = {}, 0
        for chunk in batches(rows):
            if args.max_steps and \
                    len(global_step_track) >= args.max_steps:
                break
            rws = [r for r, _ in chunk]
            batch, comps = collate_value([e for _, e in chunk],
                                         device=device, tok_cfg=tok_cfg)
            comp = comps["comp"]
            targets = {"group_id": torch.as_tensor(
                [-1 if r.get("candidate_group_id") is None
                 else zlib.crc32(str(r["candidate_group_id"]).encode())
                 % (1 << 30) for r in rws], device=device)}
            if "agg" in rws[0]:
                targets["wdl_soft"] = torch.as_tensor(np.asarray(
                    [[r["agg"]["p_loss"], r["agg"]["p_draw"],
                      r["agg"]["p_win"]] for r in rws]),
                    dtype=torch.float32, device=device)
            else:
                targets["wdl"] = torch.as_tensor(
                    [int(r["y_wdl"]) for r in rws], device=device)
            targets["dmg"] = torch.as_tensor(np.asarray(
                [[r["agg"]["y_damage_to_opp"] if "agg" in r
                  else r["y_damage_to_opp"],
                  r["agg"]["y_damage_to_self"] if "agg" in r
                  else r["y_damage_to_self"]] for r in rws]),
                dtype=torch.float32, device=device)
            with torch.autocast("cuda", dtype=torch.bfloat16,
                                enabled=use_bf16):
                loss, parts = value_loss(D.unwrap(model), batch, comp,
                                         targets, model_cfg, tok_cfg,
                                         domain)
            if not torch.isfinite(loss):
                print("NON-FINITE LOSS — step skipped (§8.3)")
                continue
            opt.zero_grad(set_to_none=True)
            loss.backward()
            gnorm = torch.nn.utils.clip_grad_norm_(model.parameters(),
                                                   tr.get("grad_clip", 5.0))
            opt.step()
            sched.step()
            global_step_track.append(1)
            for k, v in parts.items():
                agg[k] = agg.get(k, 0.0) + float(v) * len(chunk)
            agg["gnorm"] = agg.get("gnorm", 0.0) + float(gnorm) * len(chunk)
            n += len(chunk)
        return {k: v / max(n, 1) for k, v in agg.items()}, n

    global_step_track = []

    @torch.no_grad()
    def predict(enc_rows, domain):
        D.unwrap(model).eval()
        probas, dmgs = [], []
        for i in range(0, len(enc_rows), 256):
            chunk = enc_rows[i:i + 256]
            batch, comps = collate_value([e for _, e in chunk],
                                         device=device, tok_cfg=tok_cfg)
            p, d = D.unwrap(model).predict_symmetric(
                batch, comps["comp"], domain)
            probas.append(p.float().cpu().numpy())
            dmgs.append(d.float().cpu().numpy())
        if not probas:
            return np.zeros((0, 3)), np.zeros((0, 2))
        return np.concatenate(probas), np.concatenate(dmgs)

    def evaluate(split_rows_by_domain):
        rep = {}
        for domain, rows in split_rows_by_domain.items():
            if not rows:
                continue
            rws = [r for r, _ in rows]
            proba, dmg = predict(rows, domain)
            if "agg" in rws[0]:
                soft = np.asarray([[r["agg"]["p_loss"], r["agg"]["p_draw"],
                                    r["agg"]["p_win"]] for r in rws])
                y = soft.argmax(1)
                dmg_t = np.asarray([[r["agg"]["y_damage_to_opp"],
                                     r["agg"]["y_damage_to_self"]]
                                    for r in rws])
            else:
                y = np.asarray([int(r["y_wdl"]) for r in rws])
                dmg_t = np.asarray([[r["y_damage_to_opp"],
                                     r["y_damage_to_self"]] for r in rws])
            if len(y) == 0:
                continue
            m = M.wdl_metrics(y, proba)
            m["damage"] = M.damage_metrics(dmg_t, dmg)
            m["ece"], m["reliability"] = M.ece(proba, y)
            lo, hi = bootstrap_ci(
                -np.log(np.clip(proba[np.arange(len(y)), y], 1e-9, 1)),
                [r["match_id_hash"] for r in rws])
            m["nll_ci95"] = [lo, hi]
            # raw side-swap asymmetry on a subset (§6.1: reported honestly)
            sub = rows[:min(64, len(rows))]
            b0, c0 = collate_value([e for _, e in sub], device=device,
                                   tok_cfg=tok_cfg)
            with torch.no_grad():
                w0, d0, _ = D.unwrap(model)(b0, c0["comp"], domain)
                swb, swc = swapped_inputs(b0, c0["comp"], tok_cfg)
                w1, d1, _ = D.unwrap(model)(swb, swc, domain)
            w1i = torch.stack([w1[:, 2], w1[:, 1], w1[:, 0]], -1)
            m["side_swap_wdl_max_diff_raw"] = float(
                (torch.softmax(w0, -1) - torch.softmax(w1i, -1))
                .abs().max())
            m["side_swap_dmg_max_diff_raw"] = float(
                (d0 - torch.stack([d1[:, 1], d1[:, 0]], -1)).abs().max())
            rep[domain] = rep.get(domain, {})
            rep[domain].update(m)
        # ranking on sim candidate groups (validation)
        return rep

    def ranking_eval(rows, domain):
        by_group = {}
        for r, _ in rows:
            by_group.setdefault(r.get("candidate_group_id"), []).append(r)
        pair_acc, top1_hits, topk_hits = [], [], []
        for gid, grp in by_group.items():
            if not gid or len(grp) < 2:
                continue
            enc_rows = [(r, encode_value_row(r, vocab, tok_cfg))
                        for r in grp]
            proba, dmg = predict(enc_rows, domain)
            v = (dmg[:, 0] - dmg[:, 1]) + 0.25 * (proba[:, 2] - proba[:, 0])
            y = np.asarray([
                (r["agg"]["y_damage_to_opp"] - r["agg"]["y_damage_to_self"])
                for r, _ in enc_rows])
            best_y = int(y.argmax())
            pred_best = int(v.argmax())
            top1_hits.append(float(best_y == pred_best))
            order = np.argsort(-y)
            top_n = max(1, int(np.ceil(0.25 * len(y))))
            topk_hits.append(float(pred_best in set(order[:top_n])))
            for i in range(len(y)):
                for j in range(i + 1, len(y)):
                    if abs(y[i] - y[j]) < 1e-6:
                        continue
                    pair_acc.append(float((v[i] > v[j]) == (y[i] > y[j])))
        return {"pairwise_acc": float(np.mean(pair_acc)) if pair_acc else
                None, "pairs": len(pair_acc),
                "top_quartile_recall": float(np.mean(topk_hits))
                if topk_hits else None}

    # ------------------------------------------------------------- train
    t0 = time.time()
    ck_dir = os.path.join(args.run_dir, "checkpoints")
    os.makedirs(ck_dir, exist_ok=True)
    hist = []
    steps_at_start = len(global_step_track)
    for epoch in range(start_epoch, epochs):
        if args.max_steps and                 len(global_step_track) >= args.max_steps:
            break
        stats = {"epoch": epoch}
        for domain, rows in (("real", tr_real), ("sim", tr_sim)):
            if rows:
                s, n = run_epoch(rows, domain, epoch)
                stats[domain] = s
                stats[domain + "_n"] = n
        if info["rank"] == 0:
            hist.append(stats)
            print("epoch %d %s (%.0fs)" % (
                epoch, {k: round(v, 4) for k, v in stats.items()
                        if isinstance(v, float)}, time.time() - t0))
        if info["rank"] == 0 and (epoch % 4 == 3 or epoch == epochs - 1):
            ck_path = os.path.join(ck_dir, "tvalue_seed%d.pt" % seed)
            torch.save({
                "model": D.unwrap(model).state_dict(),
                "optimizer": opt.state_dict(), "scheduler": sched.state_dict(),
                "epoch": epoch + 1, "global_step": global_step + len(
                    global_step_track),
                "rng_torch": torch.get_rng_state(),
                "rng_np": np.random.get_state(),
                "rng_py": random.getstate(),
                "vocab": vocab.to_dict(), "config": model_cfg.to_dict(),
                "contract_version": tc.CONTRACT_VERSION,
                "contract_digest": tc.stable_digest(contract),
                "seed": seed, "git_commit": contract.get("git_commit"),
                "engineering_only": not formal,
            }, ck_path)

    if info["rank"] == 0 and args.max_steps:
        # benchmark parse anchor (§9.1)
        n_steps = len(global_step_track) - steps_at_start
        print("BENCH steps=%d seconds=%.1f steps_per_s=%.2f ranks=%d" % (
            n_steps, time.time() - t0,
            n_steps / max(time.time() - t0, 1e-9), info["world_size"]))
        for lg in range(torch.cuda.device_count()):
            print("BENCH gpu=%d peak_allocated_gb=%.2f peak_reserved_gb=%.2f"
                  % (lg, torch.cuda.max_memory_allocated(lg) / 2 ** 30,
                     torch.cuda.max_memory_reserved(lg) / 2 ** 30))

    # ---------------------------------------------- predictions (§13.4)
    if args.predictions_out and info["rank"] == 0:
        import gzip
        out_rows = []
        for domain, rows in (("real", te_real), ("sim", te_sim)):
            if not rows:
                continue
            proba, dmg = predict(rows, domain)
            for i, (r, _e) in enumerate(rows):
                if "agg" in r:
                    y = int(np.argmax([r["agg"]["p_loss"],
                                       r["agg"]["p_draw"],
                                       r["agg"]["p_win"]]))
                    d = (r["agg"]["y_damage_to_opp"],
                         r["agg"]["y_damage_to_self"])
                else:
                    y = int(r["y_wdl"])
                    d = (r["y_damage_to_opp"], r["y_damage_to_self"])
                out_rows.append({
                    "sample_id": r["sample_id"], "split": "test",
                    "domain": domain, "y": y,
                    "p": [float(x) for x in proba[i]],
                    "dmg_pred": [float(x) for x in dmg[i]],
                    "dmg_true": [float(x) for x in d],
                    "seed": seed,
                    "checkpoint": os.path.basename(args.resume or
                                                   "fresh")})
        with gzip.open(args.predictions_out, "wt", encoding="utf8") as f:
            for r in out_rows:
                f.write(json.dumps(r) + "\n")
        print("predictions:", args.predictions_out, len(out_rows))

    # ------------------------------------------------------------- report
    report = {
        "args": {**vars(args), "seed": seed},
        "contract": {k: contract.get(k) for k in (
            "contract_version", "engine_version", "sim_label_version",
            "t0_backtest")},
        "gpu": {"physical_allowlist": physical, "audit": D.audit_devices(),
                "reserved": tc.RESERVED_PHYSICAL_GPUS},
        "params": n_params, "tokenizer": tok_cfg.to_dict(),
        "history": hist,
        "engineering_only": not formal,
    }
    if info["rank"] == 0:
        rep = evaluate({"real": va_real, "sim": va_sim})
        report["validation"] = rep
        report["validation"]["sim_ranking"] = ranking_eval(va_sim, "sim")
        # symmetrized side-swap residual on validation subsets (§10.2)
        for domain, rows in (("real", va_real), ("sim", va_sim)):
            if not rows:
                continue
            sub = rows[:min(128, len(rows))]
            b0, c0 = collate_value([e for _, e in sub], device=device,
                                   tok_cfg=tok_cfg)
            p0, d0 = D.unwrap(model).predict_symmetric(b0, c0["comp"],
                                                       domain)
            swb, swc = swapped_inputs(b0, c0["comp"], tok_cfg)
            p1, d1 = D.unwrap(model).predict_symmetric(swb, swc, domain)
            p1i = torch.stack([p1[:, 2], p1[:, 1], p1[:, 0]], -1)
            report["symmetrized"] = report.get("symmetrized", {})
            report["symmetrized"][domain] = {
                "wdl_max_diff": float((p0 - p1i).abs().max()),
                "dmg_max_diff": float(
                    (d0 - torch.stack([d1[:, 1], d1[:, 0]], -1))
                    .abs().max())}
        # test (single frozen run; engineering runs still evaluate toy test)
        rep_t = evaluate({"real": te_real, "sim": te_sim})
        report["test"] = rep_t
        report["test"]["sim_ranking"] = ranking_eval(te_sim, "sim")
        out = os.path.join(args.run_dir, "value_report_seed%d.json" % seed)
        with open(out, "w", encoding="utf8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1, default=str)
        print("report:", out)
        sym = report.get("symmetrized", {})
        for dom, s in sym.items():
            print("symmetrized %s wdl_max_diff=%.2e dmg_max_diff=%.2e" % (
                dom, s["wdl_max_diff"], s["dmg_max_diff"]))
    D.barrier(info)


if __name__ == "__main__":
    main()
