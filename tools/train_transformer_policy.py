#!/usr/bin/env python
"""Train TPolicy-BC (teacher-forced) — Transformer基线任务书 §6.2/§8.2/§10.3.

torchrun entry (§9.3):
  CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7 \
  torchrun --standalone --nproc_per_node=7 tools/train_transformer_policy.py \
    --run-dir local_data/rl_transformer/<run_id> \
    --config configs/rl/transformer_policy_medium_v1.json

Teacher-forced BC only (§17-7): no scheduled sampling / DAgger / online RL
in the baseline checkpoint. The policy trainer refuses formal runs while
the T0 gate is pending; --allow-engineering covers toy/smoke.
The formal free-running Gate (§10.4) runs through the live transition in
run_transformer_arena.py — never from stored teacher states.
"""
import argparse
import json
import os
import random
import sys
import time

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer import distributed as D                # noqa: E402
from pysim.rl.transformer.tokenizer import (TokenizerConfig,     # noqa: E402
                                            SemanticVocab,
                                            ActionFields,
                                            fields_to_action)
from pysim.rl.transformer.data import (load_rows, fit_vocab,     # noqa: E402
                                       encode_policy_row,
                                       collate_policy)
from pysim.rl.transformer.policy_bc import (TPolicyBC,           # noqa: E402
                                            TPolicyConfig)
from pysim.rl.transformer.losses import (policy_stage_losses,    # noqa: E402
                                         build_stage_masks)


def lr_lambda(step, warmup, total):
    if step < warmup:
        return step / max(1, warmup)
    p = (step - warmup) / max(1, total - warmup)
    return max(0.05, 0.5 * (1 + np.cos(np.pi * min(1.0, p))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--overrides", default="{}")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--predictions-out", default=None,
                    help="test split 逐样本预测 jsonl.gz (§13.4 回溯)")
    ap.add_argument("--allow-engineering", action="store_true")
    args = ap.parse_args()

    if args.device == "cpu" or (args.device is None and
                                 not os.environ.get("CUDA_VISIBLE_DEVICES")):
        args.device = args.device or "cpu"
        os.environ["TRANSFORMER_ALLOW_CPU"] = "1"
    physical = D.enforce_env()
    info = D.setup_distributed()
    device = torch.device(args.device or info["device"])

    with open(args.config, encoding="utf8") as f:
        cfg_all = json.load(f)
    model_cfg = TPolicyConfig.from_dict(cfg_all["model"])
    for k, v in json.loads(args.overrides).items():
        if not hasattr(model_cfg, k):
            sys.exit("unknown override key %s" % k)
        setattr(model_cfg, k, v)
    tok_cfg = TokenizerConfig.from_dict(model_cfg.tokenizer)
    tr = cfg_all["train"]
    epochs = args.epochs or tr["epochs"]
    bs = args.batch_size or tr["batch_size"]
    seed = args.seed if args.seed is not None else tr["seeds"][0]
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)

    contract = tc.load_contract(os.path.join(args.run_dir, "contract.json"))
    bad = tc.check_contract(contract)
    if bad:
        sys.exit("contract incompatible: %s" % "; ".join(bad))
    formal = bool(cfg_all.get("formal")) and not args.allow_engineering
    if formal and not tc.t0_gate_allows(contract, formal=True):
        sys.exit("[T0 GATE] 正式训练被禁止 (§3.2). toy/smoke 用 "
                 "--allow-engineering.")
    torch.backends.cuda.matmul.allow_tf32 = bool(tr.get("tf32", True))
    use_bf16 = bool(tr.get("amp_bf16", False)) and device.type == "cuda"

    ds_dir = os.path.join(args.run_dir, "datasets")
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    cache_dir = os.path.join(args.run_dir, "token_cache")
    use_cache = os.path.exists(os.path.join(cache_dir, "manifest.json"))
    if use_cache:
        from pysim.rl.transformer.data import TokenCacheReader
        reader = TokenCacheReader(cache_dir, contract)
        if info["rank"] == 0:
            print("token cache manifest:", reader.manifest[
                "manifest_digest"], "| rows:", len(reader))

        def load_split(split):
            rows = []
            nx_ny = tok_cfg.grid_nx * tok_cfg.grid_ny
            for sid, row in reader.iter_split(split):
                if "fields" not in row:
                    continue          # value-domain shard rows
                f = row["fields"]
                # §4.5-style counted exclusion: targets unrepresentable at
                # the frozen head widths (corpus quirks: >64-unit spaces)
                if (f[0] >= 13 or f[1] >= model_cfg.max_obj_cands
                        or (f[2] >= 0 and f[2] >= model_cfg.max_ptr_cands)
                        or any(f[i] >= nx_ny for i in (3, 6, 9) if f[i] >= 0)
                        or f[12] >= 3):
                    st_row = (sid, int(f[0]), int(f[1]), int(f[2]))
                    continue
                if args.limit and len(rows) >= args.limit:
                    break
                row["sample_id"] = sid
                rows.append(row)
            return rows

        vpath = os.path.join(args.run_dir, "vocab.json")
        vocab = SemanticVocab.from_dict(
            json.load(open(vpath)) if os.path.exists(vpath)
            else SemanticVocab().to_dict())
        tr_rows = load_split("train")
        va_rows = load_split("validation")
        te_rows = load_split("test")
    else:
        rows = load_rows(os.path.join(
            ds_dir, "policy_prefix_real_v2.jsonl.gz"), limit=args.limit)
        vocab = fit_vocab([r for r in rows if r["split"] == "train"], gd)

        def enc(rs):
            out = []
            for r in rs:
                try:
                    out.append(encode_policy_row(
                        r, vocab, tok_cfg, model_cfg.max_obj_cands,
                        model_cfg.max_ptr_cands))
                except Exception as e:
                    if info["rank"] == 0 and not out:
                        print("encode excluded:", e)
            return out

        tr_rows = enc([r for r in rows if r["split"] == "train"])
        va_rows = enc([r for r in rows if r["split"] == "validation"])
        te_rows = enc([r for r in rows if r["split"] == "test"])
    if info["rank"] == 0:
        print("policy tr/va/te %d/%d/%d" % (len(tr_rows), len(va_rows),
                                            len(te_rows)))

    model = TPolicyBC(vocab, model_cfg, tok_cfg).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    if info["rank"] == 0:
        print("params: %.2fM" % (n_params / 1e6))
    if info["distributed"]:
        model = D.wrap_ddp(model, info)
    opt = torch.optim.AdamW(model.parameters(), lr=tr["lr"],
                            weight_decay=tr.get("weight_decay", 0.01),
                            fused=device.type == "cuda")
    steps_per_epoch = max(1, (len(tr_rows) + bs - 1) // bs)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: lr_lambda(s, tr.get("warmup_steps", 200),
                                 steps_per_epoch * epochs))
    rng = np.random.RandomState(seed)

    start_epoch = 0
    if args.resume and os.path.exists(args.resume):
        ck = torch.load(args.resume, map_location="cpu", weights_only=False)
        D.unwrap(model).load_state_dict(ck["model"])
        opt.load_state_dict(ck["optimizer"])
        sched.load_state_dict(ck["scheduler"])
        start_epoch = ck["epoch"]
        torch.set_rng_state(ck["rng_torch"])
        np.random.set_state(ck["rng_np"])
        random.setstate(ck["rng_py"])
        if info["rank"] == 0:
            print("resumed from %s at epoch %d" % (args.resume, start_epoch))

    def run_epoch(rows):
        model.train()
        order = rng.permutation(len(rows))
        agg, n = {}, 0
        for i in range(0, len(order), bs):
            chunk = [rows[j] for j in order[i:i + bs]]
            if len(chunk) < 2:
                continue
            pb = collate_policy(chunk, device=device, tok_cfg=tok_cfg)
            with torch.autocast("cuda", dtype=torch.bfloat16,
                                enabled=use_bf16):
                logits = D.unwrap(model)(pb["batch"], pb["components"],
                                         pb["tables"], pb["fields"])
                sm = build_stage_masks(pb["fields"], pb["tables"], device)
                out = policy_stage_losses(logits, pb["fields"], sm,
                                          pb["end"], pb["rem_bucket"])
            loss = out["total"] * model_cfg_lambda_end_scale(model_cfg)
            if not torch.isfinite(loss):
                print("NON-FINITE LOSS — step skipped (§8.3)")
                continue
            opt.zero_grad(set_to_none=True)
            loss.backward()
            gnorm = torch.nn.utils.clip_grad_norm_(model.parameters(),
                                                   tr.get("grad_clip", 5.0))
            opt.step()
            sched.step()
            for k, v in out.items():
                if k.startswith("loss_") or k == "illegal":
                    agg[k] = agg.get(k, 0.0) + \
                        (float(v) if not isinstance(v, dict)
                         else 0.0) * len(chunk)
            agg["gnorm"] = agg.get("gnorm", 0.0) + float(gnorm) * len(chunk)
            n += len(chunk)
        return {k: v / max(n, 1) for k, v in agg.items()}, n

    @torch.no_grad()
    def eval_tf(rows):
        """Teacher-forced metrics (§10.3): per-verb top-1, stage top-1,
        target-in-mask, unmasked illegal mass."""
        D.unwrap(model).eval()
        verb_hit, verb_n = 0, 0
        stage_hit = {}
        stage_n = {}
        illegal = {}
        end_hit, end_n = 0, 0
        in_mask = 0
        for i in range(0, len(rows), 128):
            chunk = rows[i:i + 128]
            pb = collate_policy(chunk, device=device, tok_cfg=tok_cfg)
            logits = D.unwrap(model)(pb["batch"], pb["components"],
                                     pb["tables"], pb["fields"])
            sm = build_stage_masks(pb["fields"], pb["tables"], device)
            out = policy_stage_losses(logits, pb["fields"], sm, pb["end"],
                                      pb["rem_bucket"])
            f = pb["fields"]
            for key, col in (("verb", 0), ("obj", 1), ("ptr", 2),
                             ("P1C", 3), ("ori", 12)):
                if key not in logits:
                    continue
                act = f[:, col] != -100
                if int(act.sum()) == 0:
                    continue
                lg = logits[key][act].float()
                mask = sm[key][act] if key in sm else torch.ones_like(lg)
                lg_m = lg + (1.0 - mask.to(lg.dtype)) * -1e9
                pred = lg_m.argmax(-1)
                hit = (pred == f[:, col][act].clamp(min=0)).float()
                stage_hit[key] = stage_hit.get(key, 0.0) + float(hit.sum())
                stage_n[key] = stage_n.get(key, 0) + int(act.sum())
            for k, v in (out.get("illegal") or {}).items():
                illegal[k] = illegal.get(k, 0.0) + float(v) * \
                    len(chunk)
            verb_act = f[:, 0] != -100
            verb_hit += int((logits["verb"][verb_act].argmax(-1) ==
                             f[:, 0][verb_act]).sum())
            verb_n += int(verb_act.sum())
            end_hit += int((logits["end"].argmax(-1) == pb["end"]).sum())
            end_n += len(chunk)
        rep = {"verb_top1": verb_hit / max(verb_n, 1), "verb_n": verb_n,
               "end_acc": end_hit / max(end_n, 1),
               "illegal_mass": illegal}
        for k, v in stage_hit.items():
            rep["top1_" + k] = v / max(stage_n[k], 1)
        return rep

    # ------------------------------------------------------------- train
    t0 = time.time()
    ck_dir = os.path.join(args.run_dir, "checkpoints")
    os.makedirs(ck_dir, exist_ok=True)
    hist = []
    for epoch in range(start_epoch, epochs):
        stats, n = run_epoch(tr_rows)
        stats["epoch"] = epoch
        if info["rank"] == 0:
            hist.append(stats)
            mean_loss = sum(v for k, v in stats.items()
                            if k.startswith("loss_")) / \
                max(1, sum(1 for k in stats if k.startswith("loss_")))
            print("epoch %d mean_stage_loss=%.4f (%.0fs)" % (
                epoch, mean_loss, time.time() - t0))
        if info["rank"] == 0 and (epoch % 4 == 3 or epoch == epochs - 1):
            torch.save({
                "model": D.unwrap(model).state_dict(),
                "optimizer": opt.state_dict(), "scheduler": sched.state_dict(),
                "epoch": epoch + 1,
                "rng_torch": torch.get_rng_state(),
                "rng_np": np.random.get_state(),
                "rng_py": random.getstate(),
                "vocab": vocab.to_dict(), "config": model_cfg.to_dict(),
                "contract_version": tc.CONTRACT_VERSION,
                "contract_digest": tc.stable_digest(contract),
                "seed": seed, "git_commit": contract.get("git_commit"),
                "engineering_only": not formal,
            }, os.path.join(ck_dir, "tpolicy_seed%d.pt" % seed))

    # ---------------------------------------------- predictions (§13.4)
    if args.predictions_out and te_rows and info["rank"] == 0:
        import gzip
        from pysim.rl.transformer.losses import STAGES
        out_rows = []
        D.unwrap(model).eval()
        with torch.no_grad():
            for i in range(0, len(te_rows), 128):
                chunk = te_rows[i:i + 128]
                pb = collate_policy(chunk, device=device, tok_cfg=tok_cfg)
                logits = D.unwrap(model)(pb["batch"], pb["components"],
                                         pb["tables"], pb["fields"])
                f = pb["fields"]
                # per-sample record (verb stage; compact)
                act = f[:, 0] != -100
                preds = logits["verb"][act].argmax(-1).tolist()
                gts = f[:, 0][act].tolist()
                sids = [c["sample_id"] for c, a in zip(
                    chunk, (f[:, 0] != -100).tolist()) if a]
                for sid, pr, gt in zip(sids, preds, gts):
                    out_rows.append({"sample_id": sid, "split": "test",
                                     "verb_pred": int(pr),
                                     "verb_true": int(gt), "seed": seed})
        with gzip.open(args.predictions_out, "wt", encoding="utf8") as fo:
            for r in out_rows:
                fo.write(json.dumps(r) + "\n")
        print("predictions:", args.predictions_out, len(out_rows))

    # ------------------------------------------------------------- report
    if info["rank"] == 0:
        report = {
            "args": {**vars(args), "seed": seed},
            "contract": {k: contract.get(k) for k in (
                "contract_version", "engine_version", "t0_backtest")},
            "gpu": {"physical_allowlist": physical,
                    "audit": D.audit_devices(),
                    "reserved": tc.RESERVED_PHYSICAL_GPUS},
            "params": n_params, "tokenizer": tok_cfg.to_dict(),
            "history": hist, "engineering_only": not formal,
            "validation_teacher_forced": eval_tf(va_rows) if va_rows else {},
            "test_teacher_forced": eval_tf(te_rows) if te_rows else {},
        }
        out = os.path.join(args.run_dir, "policy_report_seed%d.json" % seed)
        with open(out, "w", encoding="utf8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1, default=str)
        print("report:", out)
        for split in ("validation_teacher_forced", "test_teacher_forced"):
            if report.get(split):
                print(split, "verb_top1=%.3f end_acc=%.3f" % (
                    report[split]["verb_top1"], report[split]["end_acc"]))
    D.barrier(info)


def model_cfg_lambda_end_scale(cfg):
    return 1.0     # end-aux weight already applied inside the loss sum


if __name__ == "__main__":
    main()
