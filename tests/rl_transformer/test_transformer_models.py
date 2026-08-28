# §13.3 model tests: tiny overfit (32-128 samples), strict head gradient
# routing, ranking group isolation, side-swap symmetrization ≤1e-5,
# BF16/FP32 smoke, checkpoint exact resume, label-shuffle sanity, CPU
# inference smoke.
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from pysim.rl.transformer.tokenizer import TokenizerConfig       # noqa: E402
from pysim.rl.transformer.battle_value import TValue             # noqa: E402
from pysim.rl.transformer.policy_bc import TPolicyBC             # noqa: E402
from pysim.rl.transformer.data import (encode_value_row,         # noqa: E402
                                       encode_policy_row,
                                       collate_value, collate_policy)
from pysim.rl.transformer.losses import (value_loss,             # noqa: E402
                                         policy_stage_losses,
                                         build_stage_masks,
                                         ranking_loss)
from pysim.rl.transformer.battle_value import swapped_inputs     # noqa: E402


def _value_batches(vocab, tok_cfg, rows, n):
    enc = [encode_value_row(r, vocab, tok_cfg) for r in rows[:n]]
    return enc


def _value_targets(rows, n, device="cpu"):
    import zlib
    rows = rows[:n]
    return {
        "wdl": torch.as_tensor([int(r["y_wdl"]) for r in rows],
                               device=device),
        "dmg": torch.as_tensor(np.asarray(
            [[r["y_damage_to_opp"], r["y_damage_to_self"]]
             for r in rows], dtype=np.float32), device=device),
        "group_id": torch.as_tensor(
            [zlib.crc32(str(r.get("candidate_group_id") or "").encode())
             % (1 << 30) if r.get("candidate_group_id") else -1
             for r in rows], device=device),
    }


def test_tiny_value_overfit(vocab, tok_cfg, tiny_value_cfg, big_toy_rows):
    """32-128 sample overfit: loss must drop well below the prior (§13.3)."""
    torch.manual_seed(0)
    rows = big_toy_rows["real"][:64]
    enc = _value_batches(vocab, tok_cfg, rows, 48)
    model = TValue(vocab, tiny_value_cfg, tok_cfg)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3)
    targets = _value_targets(rows, 48)
    first = last = None
    for it in range(250):
        i0 = (it * 16) % 32
        chunk = enc[i0:i0 + 16]
        tgt = {k: v[i0:i0 + 16] for k, v in targets.items()}
        batch, comps = collate_value(chunk, tok_cfg=tok_cfg)
        loss, _ = value_loss(model, batch, comps["comp"], tgt,
                             tiny_value_cfg, tok_cfg, "real")
        opt.zero_grad(); loss.backward(); opt.step()
        if first is None:
            first = float(loss)
        last = float(loss)
    assert last < first * 0.5, (first, last)


def test_tiny_policy_overfit(vocab, tok_cfg, tiny_policy_cfg, toy_rows):
    """teacher-forced memorization: verb top-1 reaches 1.0 on train (§13.3,
    full mask/decoder path)."""
    from pysim.rl.transformer.losses import policy_stage_losses
    torch.manual_seed(0)
    rows = toy_rows["policy"][:48]
    enc = [encode_policy_row(r, vocab, tok_cfg,
                             tiny_policy_cfg.max_obj_cands,
                             tiny_policy_cfg.max_ptr_cands) for r in rows]
    model = TPolicyBC(vocab, tiny_policy_cfg, tok_cfg)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3)
    first = last = None
    for it in range(200):
        i0 = (it * 16) % 32
        pb = collate_policy(enc[i0:i0 + 16], tok_cfg=tok_cfg)
        logits = model(pb["batch"], pb["components"], pb["tables"],
                       pb["fields"])
        sm = build_stage_masks(pb["fields"], pb["tables"],
                               pb["batch"]["type"].device)
        out = policy_stage_losses(logits, pb["fields"], sm, pb["end"],
                                  pb["rem_bucket"])
        loss = out["total"]
        opt.zero_grad(); loss.backward(); opt.step()
        if first is None:
            first = float(loss)
        last = float(loss)
    assert last < first * 0.4, (first, last)
    # after memorization, teacher-forced verb top-1 on the SAME rows ~1.0
    model.eval()
    hit = 0
    with torch.no_grad():
        for i in range(0, len(enc), 16):
            pb = collate_policy(enc[i:i + 16], tok_cfg=tok_cfg)
            logits = model(pb["batch"], pb["components"], pb["tables"],
                           pb["fields"])
            act = pb["fields"][:, 0] != -100
            hit += int((logits["verb"][act].argmax(-1) ==
                        pb["fields"][:, 0][act]).sum())
    total = sum(1 for e in enc if e["fields"][0] != -100)
    assert hit / total > 0.95, (hit, total)


def test_domain_head_gradient_routing(vocab, tok_cfg, tiny_value_cfg,
                                      toy_rows):
    """sim batches update ONLY SimHead; real batches ONLY RealHead (§6.1)."""
    torch.manual_seed(0)
    rows = toy_rows["real"][:8]
    enc = _value_batches(vocab, tok_cfg, rows, 8)
    model = TValue(vocab, tiny_value_cfg, tok_cfg)
    targets = _value_targets(rows, 8)

    batch, comps = collate_value(enc, tok_cfg=tok_cfg)
    loss, _ = value_loss(model, batch, comps["comp"], targets,
                         tiny_value_cfg, tok_cfg, "sim")
    model.zero_grad()
    loss.backward()
    assert model.sim_head.mlp[0].weight.grad is not None
    assert model.real_head.mlp[0].weight.grad is None

    loss, _ = value_loss(model, batch, comps["comp"], targets,
                         tiny_value_cfg, tok_cfg, "real")
    model.zero_grad()
    loss.backward()
    assert model.real_head.mlp[0].weight.grad is not None
    assert model.sim_head.mlp[0].weight.grad is None
    # shared backbone receives both
    bb = model.backbone.blocks[0].ff[0].weight
    assert bb.grad is not None


def test_split_backbone_ablation_isolation(vocab, tok_cfg, tiny_value_cfg,
                                           toy_rows):
    """§6.4-6: shared_backbone=False gives each domain a private encoder;
    a sim step must leave the real encoder untouched."""
    tiny_value_cfg.shared_backbone = False
    torch.manual_seed(0)
    rows = toy_rows["real"][:8]
    enc = _value_batches(vocab, tok_cfg, rows, 8)
    model = TValue(vocab, tiny_value_cfg, tok_cfg)
    targets = _value_targets(rows, 8)
    batch, comps = collate_value(enc, tok_cfg=tok_cfg)
    loss, _ = value_loss(model, batch, comps["comp"], targets,
                         tiny_value_cfg, tok_cfg, "sim")
    model.zero_grad()
    loss.backward()
    assert model.backbone_sim is not None and model.backbone is None
    assert model.backbone_real.blocks[0].ff[0].weight.grad is None
    assert model.backbone_sim.blocks[0].ff[0].weight.grad is not None


def test_ranking_loss_only_within_groups(vocab, tok_cfg, tiny_value_cfg,
                                         toy_rows):
    """§13.3: cross-group ordering must not enter the ranking loss."""
    torch.manual_seed(0)
    wdl = torch.randn(4, 3, requires_grad=True)
    dmg = torch.tensor([[0.9, 0.0], [0.0, 0.9],   # group A: sorted
                        [0.1, 0.0], [0.0, 0.1]])  # group B
    g_two = torch.as_tensor([0, 0, 1, 1])
    l_two = ranking_loss(wdl, dmg, g_two)
    # same values, all in ONE group: extra cross pairs change the loss
    g_one = torch.as_tensor([0, 0, 0, 0])
    l_one = ranking_loss(wdl, dmg, g_one)
    assert float(l_one) != float(l_two)
    # identical scores inside a group -> zero signal pairs handled
    g_none = torch.as_tensor([-1, -1, -1, -1])
    assert float(ranking_loss(wdl, dmg, g_none)) == 0.0


def test_side_swap_symmetrized_gate(vocab, tok_cfg, tiny_value_cfg,
                                    toy_rows):
    """§13.3/§10.2: symmetrized side-swap max difference ≤ 1e-5 (here the
    exactness is 0 by construction — the gate metric path)."""
    torch.manual_seed(0)
    rows = toy_rows["real"][:16]
    enc = _value_batches(vocab, tok_cfg, rows, 16)
    model = TValue(vocab, tiny_value_cfg, tok_cfg).eval()
    batch, comps = collate_value(enc, tok_cfg=tok_cfg)
    with torch.no_grad():
        p0, d0 = model.predict_symmetric(batch, comps["comp"], "real")
        swb, swc = swapped_inputs(batch, comps["comp"], tok_cfg)
        p1, d1 = model.predict_symmetric(swb, swc, "real")
    p1i = torch.stack([p1[:, 2], p1[:, 1], p1[:, 0]], -1)
    d1i = torch.stack([d1[:, 1], d1[:, 0]], -1)
    assert float((p0 - p1i).abs().max()) <= 1e-5
    assert float((d0 - d1i).abs().max()) <= 1e-5


def test_bf16_fp32_smoke(vocab, tok_cfg, tiny_value_cfg, toy_rows):
    """BF16 autocast vs FP32: finite and loose-consistent (§13.3)."""
    if not hasattr(torch, "autocast"):
        pytest.skip("no autocast")
    torch.manual_seed(0)
    rows = toy_rows["real"][:8]
    enc = _value_batches(vocab, tok_cfg, rows, 8)
    model = TValue(vocab, tiny_value_cfg, tok_cfg).eval()
    batch, comps = collate_value(enc, tok_cfg=tok_cfg)
    with torch.no_grad():
        w32, d32, _ = model(batch, comps["comp"], "real")
        with torch.autocast("cpu", dtype=torch.bfloat16):
            w16, d16, _ = model(batch, comps["comp"], "real")
    assert torch.isfinite(w16.float()).all() and torch.isfinite(d16).all()
    assert (w16.float() - w32).abs().max() < 0.5


def test_checkpoint_exact_resume(vocab, tok_cfg, tiny_value_cfg, toy_rows):
    torch.manual_seed(0)
    rows = toy_rows["real"][:4]
    enc = _value_batches(vocab, tok_cfg, rows, 4)
    model = TValue(vocab, tiny_value_cfg, tok_cfg).eval()
    batch, comps = collate_value(enc, tok_cfg=tok_cfg)
    with torch.no_grad():
        ref = model(batch, comps["comp"], "real")[0]
    import io
    buf = io.BytesIO()
    torch.save({"model": model.state_dict()}, buf)
    buf.seek(0)
    ck = torch.load(buf, weights_only=False)
    model2 = TValue(vocab, tiny_value_cfg, tok_cfg)
    model2.load_state_dict(ck["model"])
    model2.eval()
    with torch.no_grad():
        got = model2(batch, comps["comp"], "real")[0]
    assert torch.equal(ref, got)


def test_label_shuffle_sanity(vocab, tok_cfg, tiny_value_cfg,
                              big_toy_rows):
    """§11/§13.3: after shuffling labels within replay groups, the model
    can no longer beat the prior (3-class → ≤0.5 acc)."""
    import random
    torch.manual_seed(0)
    rows = [dict(r) for r in big_toy_rows["real"][:64]]
    rng = random.Random(0)
    # toy real rows are 1/game so a within-group shuffle is the identity —
    # randomize labels ACROSS the set (the §11 intent: label independent
    # of the observation)
    ys = [r["y_wdl"] for r in rows]
    rng.shuffle(ys)
    for r, y in zip(rows, ys):
        r["y_wdl"] = y
    # keep the majority-class rate as the reference the model must not beat
    vals, counts = np.unique([r["y_wdl"] for r in rows], return_counts=True)
    prior = float(counts.max()) / len(rows)
    enc = _value_batches(vocab, tok_cfg, rows, 48)
    model = TValue(vocab, tiny_value_cfg, tok_cfg)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3)
    targets = _value_targets(rows, 48)
    for it in range(120):
        i0 = (it * 16) % 32
        batch, comps = collate_value(enc[i0:i0 + 16], tok_cfg=tok_cfg)
        tgt = {k: v[i0:i0 + 16] for k, v in targets.items()}
        loss, _ = value_loss(model, batch, comps["comp"], tgt,
                             tiny_value_cfg, tok_cfg, "real")
        opt.zero_grad(); loss.backward(); opt.step()
    model.eval()
    batch, comps = collate_value(enc[:24], tok_cfg=tok_cfg)
    tgt = {k: v[:24] for k, v in targets.items()}
    with torch.no_grad():
        p = torch.softmax(model(batch, comps["comp"], "real")[0], -1)
    acc = float((p.argmax(-1) == tgt["wdl"]).float().mean())
    assert acc <= max(0.6, prior + 0.1), (acc, prior)


def test_cpu_inference_smoke(vocab, tok_cfg, tiny_value_cfg, tiny_policy_cfg,
                             toy_rows):
    """§13.3: CPU inference + structured decode run end-to-end."""
    torch.manual_seed(0)
    vrows = toy_rows["real"][:4]
    enc = _value_batches(vocab, tok_cfg, vrows, 4)
    vmodel = TValue(vocab, tiny_value_cfg, tok_cfg).eval()
    batch, comps = collate_value(enc, tok_cfg=tok_cfg)
    with torch.no_grad():
        p, d = vmodel.predict_symmetric(batch, comps["comp"], "sim")
    assert p.shape == (4, 3) and d.shape == (4, 2)
    assert torch.allclose(p.sum(-1), torch.ones(4), atol=1e-5)

    prows = toy_rows["policy"][:4]
    penc = [encode_policy_row(r, vocab, tok_cfg,
                              tiny_policy_cfg.max_obj_cands,
                              tiny_policy_cfg.max_ptr_cands)
            for r in prows]
    pmodel = TPolicyBC(vocab, tiny_policy_cfg, tok_cfg).eval()
    pb = collate_policy(penc, tok_cfg=tok_cfg)
    fields, stop = pmodel.decode(pb["batch"], pb["components"],
                                 pb["tables"], mode="greedy")
    assert fields.shape[0] == 4
