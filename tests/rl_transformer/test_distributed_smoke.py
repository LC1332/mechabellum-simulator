# §13.3/§9.1 distributed smoke: allowlist logic (pure env-string), gloo
# 2-rank allreduce + DDP-vs-single update consistency, cache determinism.
import json
import os

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from pysim.rl.transformer import distributed as D                 # noqa: E402
from pysim.rl.transformer.token_contract import (                 # noqa: E402
    build_contract, stable_digest)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def test_allowlist_parsing():
    assert D.parse_visible_devices("1,2,3") == [1, 2, 3]
    assert D.parse_visible_devices("") is None
    assert D.parse_visible_devices(None) is None
    with pytest.raises(D.GPUAllowlistError):
        D.parse_visible_devices("GPU-abc")     # UUID syntax refused


def test_reserved_gpu0_rejected():
    with pytest.raises(D.GPUAllowlistError):
        D.assert_visible_against_allowlist("0")
    with pytest.raises(D.GPUAllowlistError):
        D.assert_visible_against_allowlist("0,1,2")
    with pytest.raises(D.GPUAllowlistError):
        D.assert_visible_against_allowlist(None)      # unset exposes ALL
    assert D.assert_visible_against_allowlist("1,2,3,4,5,6,7") == \
        [1, 2, 3, 4, 5, 6, 7]
    with pytest.raises(D.GPUAllowlistError):
        D.assert_visible_against_allowlist("8")       # outside allowlist


def test_suggested_env_and_worldsize_mismatch(monkeypatch):
    assert D.suggested_env(7) == "1,2,3,4,5,6,7"
    assert D.suggested_env(1) == "1"
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("TRANSFORMER_ALLOW_CPU", raising=False)
    with pytest.raises(D.GPUAllowlistError):
        D.enforce_env()                    # unset env must raise


def test_enforce_env_worldsize(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,2")
    assert D.enforce_env(world_size=2) == [1, 2]
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,2")
    with pytest.raises(D.GPUAllowlistError):
        D.enforce_env(world_size=3)


def test_reduce_metrics_single_process():
    info = {"distributed": False}
    out = D.reduce_metrics({"loss": 2.0}, info, weight=4.0)
    assert abs(out["loss"] - 2.0) < 1e-9 and out["_weight"] == 4.0


def test_gloo_two_rank_ddp_matches_single():
    """§13.3: DDP update == single-process update within tolerance.
    Spawned CPU (gloo) 2-rank job with identical per-rank batches — the
    DDP mean-reduce must reproduce the reference gradients exactly."""
    from pysim.rl.transformer._gloo_probe import gloo_rank_main
    import torch.multiprocessing as mp
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    mp.spawn(gloo_rank_main, args=(2, out_q), nprocs=2, join=True)
    try:
        res = out_q.get_nowait()
    except Exception as e:
        pytest.fail("no result: %s" % e)
    assert res["ok"], res
    assert res["max_diff"] <= 1e-5, res


def test_cache_rebuild_deterministic(tmp_path):
    """§7.4: two cache builds from the same inputs → identical manifest."""
    from pysim.rl.transformer import toydata
    from pysim.rl.transformer.data import TokenCacheWriter
    from pysim.gamedata import GameData
    from pysim.rl.transformer.tokenizer import (SemanticVocab,
                                                TokenizerConfig)
    from pysim.rl.transformer.data import (load_rows, fit_vocab,
                                           encode_value_row)
    ds = tmp_path / "ds"
    ds.mkdir()
    toydata.write_toy_datasets(str(ds), seed=2, n_games=3)
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    contract = build_contract(git_commit="test", git_dirty=False)
    manifests = []
    for run in range(2):
        out = tmp_path / ("cache%d" % run)
        rows = load_rows(str(ds / "battle_real_v2.jsonl.gz"))
        vocab = fit_vocab([r for r in rows if r["split"] == "train"], gd)
        w = TokenCacheWriter(str(out), [str(ds / "battle_real_v2.jsonl.gz")],
                             contract, TokenizerConfig(), shard_size=4)
        for r in rows:
            arrs = encode_value_row(r, vocab, TokenizerConfig())
            w.lengths.append(int(arrs["n_tokens"]))
            w.add(r["sample_id"], r["split"], arrs)
        manifests.append(w.finalize())
    assert manifests[0]["manifest_digest"] == \
        manifests[1]["manifest_digest"]
    assert manifests[0]["shard_checksums"] == manifests[1]["shard_checksums"]
