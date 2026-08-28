# §13.1 symmetry tests: entity permutation invariance / pointer
# equivariance, side mirror (tokens + ordered positions), padding
# invariance, over-limit handling.
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from pysim.rl.transformer.tokenizer import (                     # noqa: E402
    TokenizerError, encode_battle_tokens, encode_policy_tokens,
    swap_token_arrays, bias_components, mirror_points,
    battle_token_obs_from_v1)
from pysim.rl.transformer.battle_value import (                  # noqa: E402
    TValue, swapped_inputs, batch_components)
from pysim.rl.transformer.backbone import TokenBackbone          # noqa: E402
from pysim.rl.transformer import toydata                         # noqa: E402


def _permute_units(obs, seed=0):
    """Shuffle SELF_UNIT entities (the pointer candidates) in place."""
    rng = np.random.RandomState(seed)
    obs = dict(obs)
    ents = list(obs["entities"])
    unit_pos = [i for i, e in enumerate(ents)
                if e["kind"] == "self_unit"]
    perm = list(unit_pos)
    rng.shuffle(perm)
    shuffled = list(ents)
    for src, dst in zip(unit_pos, perm):
        shuffled[src] = ents[dst]
    obs["entities"] = shuffled
    return obs, perm


def test_value_permutation_invariance(vocab, tok_cfg, tiny_value_cfg,
                                      toy_rows):
    obs = toy_rows["real"][0]["observation"]
    ta1 = encode_battle_tokens(obs, vocab, tok_cfg)
    perm_obs, _ = _permute_units(obs, seed=1)
    ta2 = encode_battle_tokens(perm_obs, vocab, tok_cfg)

    torch.manual_seed(0)
    model = TValue(vocab, tiny_value_cfg, tok_cfg).eval()
    from pysim.rl.transformer.data import collate_value
    b1, c1 = collate_value([_pack(ta1)])
    b2, c2 = collate_value([_pack(ta2)])
    with torch.no_grad():
        w1, d1, _ = model(b1, c1["comp"], "real")
        w2, d2, _ = model(b2, c2["comp"], "real")
    assert torch.allclose(w1, w2, atol=1e-5)
    assert torch.allclose(d1, d2, atol=1e-5)


def _pack(ta):
    """TokenArrays -> encode_value_row-style dict (no swap arrays)."""
    return {"type": ta.type, "sem": ta.sem, "feat": ta.feat,
            "x": ta.x, "y": ta.y, "side": ta.side, "group": ta.group,
            "air": ta.air, "area": ta.area, "pad_mask": ta.mask,
            "comp": bias_components(ta, None or _cfg()),
            "comp_sw": bias_components(swap_token_arrays(ta), _cfg()),
            "n_tokens": ta.n_tokens}


def _cfg():
    from pysim.rl.transformer.tokenizer import TokenizerConfig
    return TokenizerConfig()


def test_pointer_equivariance_under_unit_permutation(vocab, tok_cfg,
                                                     tiny_policy_cfg,
                                                     toy_rows):
    """Pointer scores must follow the tokens under permutation (§4.1)."""
    from pysim.rl.transformer.policy_bc import TPolicyBC
    from pysim.rl.transformer.data import collate_policy, encode_policy_row
    row = toy_rows["policy"][0]
    perm_obs, _ = _permute_units(row["observation"], seed=2)
    perm_obs["space"] = row["observation"]["space"]
    # pointer candidate order follows the (permuted) own units; keep the
    # same target — the unit MASK rows stay index-aligned with the pool
    row2 = dict(row)
    row2["observation"] = perm_obs

    e1 = encode_policy_row(row, vocab, tok_cfg, 256, 64)
    e2 = encode_policy_row(row2, vocab, tok_cfg, 256, 64)
    torch.manual_seed(0)
    model = TPolicyBC(vocab, tiny_policy_cfg, tok_cfg).eval()
    p1 = collate_policy([e1])
    p2 = collate_policy([e2])
    with torch.no_grad():
        h1 = model.encode(p1["batch"], p1["components"])
        h2 = model.encode(p2["batch"], p2["components"])
    # own-unit token hidden states must match pairwise: the permuted
    # observation emits its units in a different ORDER, so hidden row for
    # handle k in e1 equals hidden of that unit's NEW position in e2
    pos1 = e1["ptr_token_pos"]
    pos2 = e2["ptr_token_pos"]
    n = min(len(pos1), len(pos2))
    if n == 0:
        pytest.skip("no own units in toy row")
    # the permutation maps positions; pair each unit by its mech+coords
    key1 = {(int(u["mech"]), u["x"], u["y"]): i
            for i, u in enumerate(row["observation"]["entities"])
            if u["kind"] == "self_unit"}
    inv = {}
    for new_i, u in enumerate(perm_obs["entities"]):
        if u["kind"] == "self_unit":
            old_i = key1[(int(u["mech"]), u["x"], u["y"])]
            inv[old_i] = new_i
    for old_i, new_i in inv.items():
        h_a = h1[0, e1["ptr_token_pos"][old_i]]
        h_b = h2[0, e2["ptr_token_pos"][inv[old_i]]]
        assert torch.allclose(h_a, h_b, atol=1e-5)


def test_side_mirror_tokens_and_ordered_positions(vocab, tok_cfg,
                                                  toy_rows):
    """mirror(mirror(s)) == s and mirror preserves ordered points (§4.4)."""
    obs = toy_rows["real"][1]["observation"]
    from pysim.rl.transformer.tokenizer import mirror_battle_obs
    m1 = mirror_battle_obs(obs)
    m2 = mirror_battle_obs(m1)
    tc_digest = None
    from pysim.rl.transformer.token_contract import stable_digest
    assert stable_digest(m1) != stable_digest(obs)      # actually mirrored
    assert stable_digest(m2) == stable_digest(obs)      # involution

    pts = [(10.0, -20.0), (30.0, 40.5), (-5.0, 0.0)]
    assert mirror_points(mirror_points(pts)) == pts
    assert mirror_points(pts)[0][1] == 20.0             # order preserved


def test_padding_does_not_change_outputs(vocab, tok_cfg, tiny_value_cfg,
                                         toy_rows):
    """§13.1: padding tokens never attend/pool/score."""
    from pysim.rl.transformer.tokenizer import TokenizerConfig
    from pysim.rl.transformer.battle_value import TValue
    obs = toy_rows["real"][0]["observation"]
    torch.manual_seed(1)
    model = TValue(vocab, tiny_value_cfg, TokenizerConfig(
        max_entity_tokens=tok_cfg.max_entity_tokens)).eval()
    from pysim.rl.transformer.data import collate_value
    ta1 = encode_battle_tokens(obs, vocab, tok_cfg)
    row1 = _pack(ta1)
    # a batch padded to a longer row must give the same output
    obs2 = toy_rows["real"][1]["observation"]
    ta2 = encode_battle_tokens(obs2, vocab, tok_cfg)
    b_single, c_single = collate_value([row1, _pack(ta2)])
    with torch.no_grad():
        w_pad, d_pad, _ = model(b_single, c_single["comp"], "real")
    b_alone, c_alone = collate_value([row1])
    with torch.no_grad():
        w_alone, d_alone, _ = model(b_alone, c_alone["comp"], "real")
    assert torch.allclose(w_pad[:1], w_alone[0][None], atol=1e-5)
    assert torch.allclose(d_pad[:1], d_alone[0][None], atol=1e-5)


def test_swap_is_involutive_and_components_exact(vocab, tok_cfg,
                                                 toy_rows):
    from pysim.rl.transformer.data import collate_value, encode_value_row
    from pysim.rl.transformer.battle_value import swapped_inputs
    rows = [encode_value_row(r, vocab, tok_cfg)
            for r in toy_rows["real"][:3]]
    batch, comps = collate_value(rows)
    swb1, swc1 = swapped_inputs(batch, comps["comp"], tok_cfg)
    swb2, swc2 = swapped_inputs(swb1, swc1, tok_cfg)
    real = batch["pad_mask"] > 0
    fd = ((batch["feat"] - swb2["feat"]).abs() * real.unsqueeze(-1)).max()
    assert float(fd) == 0.0
    pair = (real.unsqueeze(2) & real.unsqueeze(1)) \
        .unsqueeze(1).expand(-1, 7, -1, -1)
    assert float((comps["comp"] - swc2).abs()[pair].max()) == 0.0


def test_bias_mirror_bucket_exactness():
    """dy bucket of the mirrored offset = mirrored bucket (§4.4 numeric)."""
    from pysim.rl.transformer import relative_bias as rb
    edges = rb.DEFAULT_DY_EDGES
    n = len(edges) + 1
    rng = np.random.RandomState(0)
    for _ in range(500):
        dy = float(rng.uniform(-299, 299))
        b = rb.dy_bucket(dy, edges)
        bm = rb.dy_bucket(-dy, edges)
        if dy == 0:
            continue                  # boundary: shared edge bucket
        assert bm == (n - 1) - b, (dy, b, bm)
        assert 0 <= b < n and 0 <= bm < n
    # boundary values (dy on an edge, incl. 0) sit on shared edges; the
    # swap path recomputes components from mirrored geometry instead of
    # reflecting bucket indices (exactness test elsewhere in this file)
    # distance buckets are non-negative and monotone in |dy|
    assert rb.dist_bucket(0, 0) <= rb.dist_bucket(0, 50) \
        <= rb.dist_bucket(0, 500)


def test_torch_and_numpy_component_paths_agree(vocab, tok_cfg, toy_rows):
    """batch_components (torch) == bias_components (numpy) per sample."""
    from pysim.rl.transformer.data import encode_value_row, collate_value
    from pysim.rl.transformer.battle_value import batch_components
    rows = [encode_value_row(r, vocab, tok_cfg)
            for r in toy_rows["real"][:2]]
    batch, comps = collate_value(rows)
    for i in range(len(rows)):
        t = int(rows[i]["n_tokens"])
        comp_t = batch_components(
            {k: batch[k][i:i + 1] for k in batch}, tok_cfg)[0]
        assert torch.equal(comp_t[:, :t, :t],
                           torch.as_tensor(comps["comp"][i][:, :t, :t]))


def test_no_ordinal_position_embedding(vocab, tok_cfg):
    """§4.1: the backbone must not embed token ORDER — verified by
    swapping two non-adjacent entity tokens and checking attention-pool
    invariance of a randomly initialized backbone."""
    torch.manual_seed(3)
    from pysim.rl.transformer.backbone import TokenBackbone
    n_sem = max(vocab.sizes().values())
    bb = TokenBackbone(n_sem, 32, 2, 4, 64, 0.0, True, {})
    sim, real, pol = toydata.make_toy_rows(seed=5, n_games=1)
    ta = encode_battle_tokens(sim[0]["observation"], vocab, tok_cfg)
    order = np.arange(ta.n_tokens)
    rng = np.random.RandomState(0)
    perm = order.copy()
    rng.shuffle(perm[2:])                 # keep CLS+GLOBAL slots
    ta_p = type(ta)(
        type=ta.type[perm], sem=ta.sem[perm], feat=ta.feat[perm],
        x=ta.x[perm], y=ta.y[perm], side=ta.side[perm],
        group=ta.group[perm], air=ta.air[perm], area=ta.area[perm],
        mask=ta.mask[perm], index={}, n_tokens=ta.n_tokens)
    from pysim.rl.transformer.data import _pad_stack  # noqa: F401
    from pysim.rl.transformer.tokenizer import collate_tokens
    from pysim.rl.transformer.data import torch_as_tensor
    b1 = {k: torch_as_tensor(v) for k, v in collate_tokens([ta]).items()}
    b2 = {k: torch_as_tensor(v) for k, v in collate_tokens([ta_p]).items()}
    c1 = torch_as_tensor(bias_components(ta, tok_cfg))[None]
    c2 = torch_as_tensor(bias_components(ta_p, tok_cfg))[None]
    with torch.no_grad():
        h1 = bb(b1, c1)
        h2 = bb(b2, c2)
    # real tokens emit the same multiset of hidden vectors
    s1 = h1[0][b1["pad_mask"][0] > 0]
    s2 = h2[0][b2["pad_mask"][0] > 0]
    assert torch.allclose(s1.sum(0), s2.sum(0), atol=1e-5)
