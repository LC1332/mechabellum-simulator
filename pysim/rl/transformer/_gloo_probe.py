# DDP consistency probes (§9.1/§13.3).
#
# Lives in the package so torch.multiprocessing.spawn can pickle the
# target by module path (spawn refuses dynamically-defined functions).
# Every rank draws the SAME fixed batch; with identical per-rank
# gradients the DDP mean-reduce must reproduce the single-process
# reference update exactly — a real allreduce correctness check.
import os


def gloo_rank_main(rank: int, world: int, out_q) -> None:
    import torch
    import torch.distributed as dist
    os.environ.update({
        "RANK": str(rank), "WORLD_SIZE": str(world),
        "LOCAL_RANK": str(rank), "MASTER_ADDR": "127.0.0.1",
        "MASTER_PORT": "29733"})
    dist.init_process_group("gloo")
    try:
        from pysim.gamedata import GameData
        from pysim.rl.transformer.tokenizer import (SemanticVocab,
                                                    TokenizerConfig)
        from pysim.rl.transformer.battle_value import TValue, TValueConfig
        from pysim.rl.transformer.losses import value_loss
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        gd = GameData(os.path.join(root, "data", "gamedata.json"))
        vocab = SemanticVocab.from_gamedata(gd)
        tok_cfg = TokenizerConfig(max_entity_tokens=48)
        cfg = TValueConfig(d_model=32, n_layers=1, n_heads=2, d_ff=64,
                           dropout=0.0, use_swap_loss=False,
                           use_ranking_loss=False)
        torch.manual_seed(0)
        model = TValue(vocab, cfg, tok_cfg)
        ref = TValue(vocab, cfg, tok_cfg)
        ref.load_state_dict(model.state_dict())
        ddp = torch.nn.parallel.DistributedDataParallel(
            model, find_unused_parameters=True)
        # weight_decay=0: DDP materializes grad=0 (not None) for unused
        # params, so AdamW decay would diverge them from the reference's
        # untouched params — a decoupled weight-decay artifact, not an
        # allreduce error (§13.3 compares gradient math, not decay policy)
        opt = torch.optim.AdamW(ddp.parameters(), lr=1e-3, weight_decay=0.0)
        opt_r = torch.optim.AdamW(ref.parameters(), lr=1e-3,
                                  weight_decay=0.0)
        g = torch.Generator().manual_seed(5)
        t, b = 48, 4
        batch = {
            "type": torch.zeros(b, t, dtype=torch.int64),
            "sem": torch.zeros(b, t, dtype=torch.int64),
            "feat": torch.randn(b, t, 16, generator=g),
            "x": torch.zeros(b, t), "y": torch.zeros(b, t),
            "side": torch.full((b, t), -1, dtype=torch.int64),
            "group": torch.full((b, t), 10, dtype=torch.int64),
            "air": torch.full((b, t), -1, dtype=torch.int64),
            "area": torch.full((b, t), -1, dtype=torch.int64),
            "pad_mask": torch.ones(b, t),
        }
        comps = torch.zeros(b, 7, t, t, dtype=torch.int64)
        targets = {"wdl": torch.randint(0, 3, (b,), generator=g),
                   "dmg": torch.rand(b, 2, generator=g),
                   "group_id": None}
        max_step_diff = 0.0
        for _ in range(3):
            loss, _ = value_loss(ddp, batch, comps, targets, cfg, tok_cfg,
                                 "sim")
            opt.zero_grad(); loss.backward(); opt.step()
            loss_r, _ = value_loss(ref, batch, comps, targets, cfg,
                                   tok_cfg, "sim")
            opt_r.zero_grad(); loss_r.backward(); opt_r.step()
            with torch.no_grad():
                d1 = model(batch, comps, "sim")[0]
                d2 = ref(batch, comps, "sim")[0]
                max_step_diff = max(max_step_diff,
                                    float((d1 - d2).abs().max()))
        dist.barrier()
        if rank == 0:
            out_q.put({"ok": True, "max_diff": max_step_diff})
    finally:
        dist.destroy_process_group()


def ddp_rank_main(rank: int, world: int, backend: str, out_q) -> None:
    """Generic DDP-vs-single consistency worker (gloo on CPU / nccl on
    GPU): identical per-rank batches -> DDP mean-reduce must equal the
    single-process reference update."""
    import torch
    import torch.distributed as dist
    os.environ.update({
        "RANK": str(rank), "WORLD_SIZE": str(world),
        "LOCAL_RANK": str(rank), "MASTER_ADDR": "127.0.0.1",
        "MASTER_PORT": "29739"})
    dist.init_process_group(backend)
    try:
        if backend == "nccl":
            torch.cuda.set_device(rank)
        from pysim.gamedata import GameData
        from pysim.rl.transformer.tokenizer import (SemanticVocab,
                                                    TokenizerConfig)
        from pysim.rl.transformer.battle_value import TValue, TValueConfig
        from pysim.rl.transformer.losses import value_loss
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        gd = GameData(os.path.join(root, "data", "gamedata.json"))
        vocab = SemanticVocab.from_gamedata(gd)
        tok_cfg = TokenizerConfig(max_entity_tokens=48)
        cfg = TValueConfig(d_model=32, n_layers=1, n_heads=2, d_ff=64,
                           dropout=0.0, use_swap_loss=False,
                           use_ranking_loss=False)
        torch.manual_seed(0)
        model = TValue(vocab, cfg, tok_cfg)
        ref = TValue(vocab, cfg, tok_cfg)
        ref.load_state_dict(model.state_dict())
        if backend == "nccl":
            model, ref = model.cuda(), ref.cuda()
        ddp = torch.nn.parallel.DistributedDataParallel(
            model, find_unused_parameters=True)
        opt = torch.optim.AdamW(ddp.parameters(), lr=1e-3, weight_decay=0.0)
        opt_r = torch.optim.AdamW(ref.parameters(), lr=1e-3,
                                  weight_decay=0.0)
        g = torch.Generator().manual_seed(5)
        t, b = 48, 4
        batch = {
            "type": torch.zeros(b, t, dtype=torch.int64),
            "sem": torch.zeros(b, t, dtype=torch.int64),
            "feat": torch.randn(b, t, 16, generator=g),
            "x": torch.zeros(b, t), "y": torch.zeros(b, t),
            "side": torch.full((b, t), -1, dtype=torch.int64),
            "group": torch.full((b, t), 10, dtype=torch.int64),
            "air": torch.full((b, t), -1, dtype=torch.int64),
            "area": torch.full((b, t), -1, dtype=torch.int64),
            "pad_mask": torch.ones(b, t),
        }
        comps = torch.zeros(b, 7, t, t, dtype=torch.int64)
        targets = {"wdl": torch.randint(0, 3, (b,), generator=g),
                   "dmg": torch.rand(b, 2, generator=g),
                   "group_id": None}
        if backend == "nccl":
            batch = {k: v.cuda() for k, v in batch.items()}
            comps = comps.cuda()
            targets = {k: (v.cuda() if torch.is_tensor(v) else v)
                       for k, v in targets.items()}
        max_step_diff = 0.0
        for _ in range(3):
            loss, _ = value_loss(ddp, batch, comps, targets, cfg, tok_cfg,
                                 "sim")
            opt.zero_grad(); loss.backward(); opt.step()
            loss_r, _ = value_loss(ref, batch, comps, targets, cfg,
                                   tok_cfg, "sim")
            opt_r.zero_grad(); loss_r.backward(); opt_r.step()
            with torch.no_grad():
                d1 = model(batch, comps, "sim")[0]
                d2 = ref(batch, comps, "sim")[0]
                max_step_diff = max(max_step_diff,
                                    float((d1 - d2).abs().max()))
        dist.barrier()
        if rank == 0:
            out_q.put({"ok": True, "backend": backend, "world": world,
                       "max_diff": max_step_diff})
    finally:
        dist.destroy_process_group()
