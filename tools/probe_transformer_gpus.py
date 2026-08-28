#!/usr/bin/env python
"""§9.1 environment Gate probe: physical GPU 1-7 visible, BF16/SDPA/NCCL
working, per-GPU and multi-GPU DDP throughput benchmark, GPU 0 untouched.

Modes:
  --gate            one-process visibility + BF16/SDPA smoke (fast)
  --bench           single-card fixed-step benchmark per visible GPU
  --ddp-smoke N     spawn N ranks (gloo on CPU / nccl on GPU) all-reduce +
                    DDP-vs-single update consistency (tiny TValue)
Run under the frozen env:
  CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7 tools/probe_transformer_gpus.py --gate
"""
import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer import distributed as D                # noqa: E402


def gate() -> dict:
    import torch
    physical = D.enforce_env()
    rows = D.audit_devices()
    out = {
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "physical": physical,
        "audit": rows,
        "device_count": torch.cuda.device_count(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version()
        if torch.backends.cudnn.is_available() else None,
        "reserved_gpus": tc.RESERVED_PHYSICAL_GPUS,
        "allowlist": tc.TRAINING_GPU_ALLOWLIST,
    }
    if torch.cuda.is_available():
        x = torch.randn(512, 512, device="cuda", dtype=torch.bfloat16)
        y = (x @ x).float().sum()
        out["bf16_matmul"] = bool(torch.isfinite(y))
        q = torch.randn(8, 4, 64, device="cuda")
        z = torch.nn.functional.scaled_dot_product_attention(q, q, q)
        out["sdpa"] = bool(torch.isfinite(z.sum()))
    return out


def bench(steps: int = 200, batch: int = 32) -> list:
    """Fixed-step single-card benchmark of the Tiny TValue forward+backward
    on every visible GPU (samples/s, step time p50/p95, peak memory)."""
    import torch
    from pysim.gamedata import GameData
    from pysim.rl.transformer.tokenizer import SemanticVocab, TokenizerConfig
    from pysim.rl.transformer.battle_value import TValue, TValueConfig
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    vocab = SemanticVocab.from_gamedata(gd)
    tok_cfg = TokenizerConfig(max_entity_tokens=192)
    mcfg = TValueConfig(d_model=192, n_layers=4, n_heads=6, d_ff=768)
    results = []
    for logical in range(torch.cuda.device_count()):
        dev = torch.device("cuda:%d" % logical)
        model = TValue(vocab, mcfg, tok_cfg).to(dev)
        opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
        t = tok_cfg.max_entity_tokens
        comps = torch.zeros(batch, 7, t, t, dtype=torch.int64, device=dev)
        batch_d = {
            "type": torch.zeros(batch, t, dtype=torch.int64, device=dev),
            "sem": torch.zeros(batch, t, dtype=torch.int64, device=dev),
            "feat": torch.randn(batch, t, 16, device=dev),
            "x": torch.zeros(batch, t, device=dev),
            "y": torch.zeros(batch, t, device=dev),
            "side": torch.full((batch, t), -1, dtype=torch.int64, device=dev),
            "group": torch.full((batch, t), 10, dtype=torch.int64, device=dev),
            "air": torch.full((batch, t), -1, dtype=torch.int64, device=dev),
            "area": torch.full((batch, t), -1, dtype=torch.int64, device=dev),
            "pad_mask": torch.ones(batch, t, device=dev),
        }
        times = []
        torch.cuda.reset_peak_memory_stats(logical)
        for i in range(steps):
            if i == 10:
                torch.cuda.synchronize(logical)
            t0 = time.time()
            wdl, dmg, _ = model(batch_d, comps, "sim")
            loss = wdl.sum() + dmg.sum()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            torch.cuda.synchronize(logical)
            times.append(time.time() - t0)
        results.append({
            "logical": logical,
            "physical": D.parse_visible_devices(
                os.environ.get("CUDA_VISIBLE_DEVICES", ""))[logical]
            if D.parse_visible_devices(
                os.environ.get("CUDA_VISIBLE_DEVICES", "")) else None,
            "steps": steps, "batch": batch,
            "step_ms_p50": round(float(np.percentile(times[10:], 50)) * 1e3,
                                 2),
            "step_ms_p95": round(float(np.percentile(times[10:], 95)) * 1e3,
                                 2),
            "samples_per_s": round(batch / float(np.mean(times[10:])), 1),
            "tokens_per_s": round(batch * t / float(np.mean(times[10:])), 0),
            "peak_allocated_gb": round(torch.cuda.max_memory_allocated(
                logical) / 2 ** 30, 2),
            "peak_reserved_gb": round(torch.cuda.max_memory_reserved(
                logical) / 2 ** 30, 2),
        })
        del model, opt
        torch.cuda.empty_cache()
    return results


def ddp_smoke(world: int, backend: str | None = None) -> dict:
    """mp.spawn `world` ranks; every rank draws the SAME batch so the DDP
    mean-reduce must equal the single-process reference (§9.1)."""
    import torch.multiprocessing as mp
    import torch
    if backend is None:
        backend = "nccl" if torch.cuda.is_available() else "gloo"
    if backend == "nccl":
        n_vis = torch.cuda.device_count()
        if world > n_vis:
            return {"ok": False, "error": "world %d > visible %d" %
                    (world, n_vis)}
    from pysim.rl.transformer._gloo_probe import ddp_rank_main
    out_q = mp.get_context("spawn").Queue()
    mp.spawn(ddp_rank_main, args=(world, backend, out_q), nprocs=world,
             join=True)
    try:
        return out_q.get_nowait()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--bench-steps", type=int, default=200)
    ap.add_argument("--ddp-smoke", type=int, default=0, metavar="WORLD")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    report = {}
    if args.gate or not (args.bench or args.ddp_smoke):
        try:
            report["gate"] = gate()
            print(json.dumps(report["gate"], ensure_ascii=False,
                             indent=1, default=str))
        except D.GPUAllowlistError as e:
            sys.exit("[GATE FAIL] %s" % e)
    if args.bench:
        report["bench"] = bench(steps=args.bench_steps)
        for r in report["bench"]:
            print(r)
    if args.ddp_smoke:
        report["ddp_smoke"] = ddp_smoke(args.ddp_smoke, args.backend)
        print(json.dumps(report["ddp_smoke"], ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
