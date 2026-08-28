# §13.3 checkpoint exact resume: optimizer/scheduler/RNG 状态恢复后, 继续
# 训练的参数轨迹必须与不中断的 run 在容差内一致 (任务书 §8.3: 从中断恢复后,
# 固定 seed run 的样本顺序和最终指标应在声明容差内一致).
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from pysim.rl.transformer.tokenizer import TokenizerConfig       # noqa: E402
from pysim.rl.transformer.battle_value import TValue, TValueConfig  # noqa: E402
from pysim.rl.transformer.data import (encode_value_row,         # noqa: E402
                                       collate_value)
from pysim.rl.transformer.losses import value_loss               # noqa: E402


def _setup(vocab, tok_cfg, toy_rows, tiny_value_cfg):
    torch.manual_seed(0)
    rows = toy_rows["real"][:16]
    enc = [encode_value_row(r, vocab, tok_cfg) for r in rows]
    model = TValue(vocab, tiny_value_cfg, tok_cfg)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: 1.0 / (1 + s))
    targets = {
        "wdl": torch.as_tensor([int(r["y_wdl"]) for r in rows]),
        "dmg": torch.as_tensor(np.asarray(
            [[r["y_damage_to_opp"], r["y_damage_to_self"]] for r in rows],
            dtype=np.float32)),
        "group_id": torch.as_tensor([-1] * len(rows)),
    }
    return rows, enc, model, opt, sched, targets


def _steps(model, opt, sched, enc, targets, tok_cfg, cfg, n, rng):
    for _ in range(n):
        i0 = int(rng.randint(0, 8))
        batch, comps = collate_value(enc[i0:i0 + 8], tok_cfg=tok_cfg)
        tgt = {k: v[i0:i0 + 8] for k, v in targets.items()}
        loss, _ = value_loss(model, batch, comps["comp"], tgt, cfg,
                             tok_cfg, "real")
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()


def _snapshot(model):
    return {k: v.detach().clone() for k, v in model.state_dict().items()}


def test_checkpoint_exact_resume(vocab, tok_cfg, tiny_value_cfg,
                                 big_toy_rows):
    """中断恢复: 保存 (model, opt, sched, torch RNG, numpy RNG) 后, 恢复并
    继续训练 5 步的参数轨迹 == 不中断连跑 5 步 (allclose 1e-6)."""
    rows, enc, model, opt, sched, targets = _setup(
        vocab, tok_cfg, big_toy_rows, tiny_value_cfg)
    rng = np.random.RandomState(7)

    # ---- run A (参考轨迹): 3 步 → checkpoint(序列化!) → 5 步
    # 注意: opt.state_dict() 返回活引用, 内存快照会随原 run 继续漂移;
    # 真实 trainer 走 torch.save 序列化 — 测试必须复刻该语义
    import io
    _steps(model, opt, sched, enc, targets, tok_cfg, tiny_value_cfg, 3, rng)
    buf = io.BytesIO()
    torch.save({"model": model.state_dict(),
                "optimizer": opt.state_dict(),
                "scheduler": sched.state_dict(),
                "rng_torch": torch.get_rng_state(),
                "rng_np": np.random.get_state()}, buf)
    _steps(model, opt, sched, enc, targets, tok_cfg, tiny_value_cfg, 5, rng)
    final_a = _snapshot(model)
    buf.seek(0)
    ck = torch.load(buf, weights_only=False)

    # ---- run B: 前 3 步同轨迹 → 从 checkpoint 精确恢复 → 5 步
    rows2, enc2, model2, opt2, sched2, targets2 = _setup(
        vocab, tok_cfg, big_toy_rows, tiny_value_cfg)
    rng2 = np.random.RandomState(7)
    _steps(model2, opt2, sched2, enc2, targets2, tok_cfg,
           tiny_value_cfg, 3, rng2)
    torch.manual_seed(999)                       # 恢复前扰动
    model2.load_state_dict(ck["model"])
    opt2.load_state_dict(ck["optimizer"])
    sched2.load_state_dict(ck["scheduler"])
    torch.set_rng_state(ck["rng_torch"])
    np.random.set_state(ck["rng_np"])
    _steps(model2, opt2, sched2, enc2, targets2, tok_cfg,
           tiny_value_cfg, 5, rng2)
    final_b = _snapshot(model2)

    assert set(final_a) == set(final_b)
    worst = max(float((final_a[k] - final_b[k]).abs().max())
                for k in final_a)
    assert worst <= 1e-6, worst
