# §13.1 contract/token tests: contract build/check + old-contract
# rejection + cache manifest binding + vocab collision-freedom + leakage
# guard + observation round-trip digest stability.
import json
import os

import pytest

torch = pytest.importorskip("torch")

from pysim.rl.transformer import token_contract as tc                 # noqa: E402
from pysim.rl.transformer.tokenizer import (SemanticVocab,            # noqa: E402
                                            TokenizerConfig,
                                            battle_token_obs_from_v1)
from pysim.rl.transformer import toydata                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


def test_contract_versions_match_task_book():
    c = tc.build_contract()
    assert c["contract_version"] == "rl_transformer_contract_v1"
    assert c["observation_version"] == "transformer_obs_v2"
    assert c["action_version"] == "transformer_act_v2"
    assert c["data_version"] == "transformer_data_v2"
    assert c["tokenizer_version"] == "structured_token_v1"
    assert c["sim_label_version"].startswith("sim_label_v2_")
    assert c["split_inherit_from"].startswith("phase1")
    assert c["training_gpu_allowlist"] == [1, 2, 3, 4, 5, 6, 7]
    assert c["reserved_physical_gpus"] == [0]


def test_check_contract_rejects_phase1_contract():
    """§13.1: old rl_phase1 contract must be a HARD incompatibility."""
    phase1 = {
        "contract_version": "rl_phase1_contract_v1",
        "schema_version": "transition-v0.6",
        "ruleset_version": "normal_1v1_replay_v0",
        "engine_version": "pysim-step30",
        "observation_version": "obs_v1",
        "action_version": "act_v1",
        "data_version": "data_v1",
        "feature_profile": "min_public_v1",
        "sim_label_version": "sim_label_v1",
    }
    on_disk = os.path.join(ROOT, "data", "rl_phase1_contract.json")
    if os.path.exists(on_disk):
        with open(on_disk, encoding="utf8") as f:
            phase1 = json.load(f)
    bad = tc.check_contract(phase1)
    assert any("rl_phase1" in b for b in bad), bad
    with pytest.raises(tc.ContractError):
        tc.require_compatible(phase1)


def test_check_contract_detects_engine_change():
    c = tc.build_contract()
    c["engine_version"] = "pysim-step999"
    assert any("engine" in b for b in tc.check_contract(c))


def test_sim_label_version_binds_engine_digest():
    v1 = tc.sim_label_version()
    assert v1 == "sim_label_v2_%s" % tc.engine_digest()
    # a different engine digest must produce a different sim label
    # (cache invalidation, §3.1) — verified structurally here
    d1 = tc.engine_digest()
    assert d1 == tc.engine_digest()          # deterministic
    assert len(d1) == 16


def test_t0_gate_blocks_formal_artifacts():
    c = tc.build_contract()                  # default: pending
    assert not tc.t0_gate_allows(c, formal=True)
    assert tc.t0_gate_allows(c, formal=False)   # engineering allowed
    c2 = tc.build_contract(t0_status=tc.T0_ACCEPTED,
                           t0_record={"commit": "x"})
    assert tc.t0_gate_allows(c2, formal=True)
    with pytest.raises(tc.ContractError):
        tc.require_t0(c, "正式 sim label 生成")


def test_cache_manifest_binds_and_rejects():
    contract = tc.build_contract()
    m = tc.cache_manifest("src_digest", contract, {"n": 3},
                          ["c0", "c1"], kind="token")
    assert tc.check_cache_manifest(m, contract) == []
    m2 = dict(m)
    m2["binds"] = dict(m["binds"])
    m2["binds"]["tokenizer_version"] = "structured_token_v0"
    assert tc.check_cache_manifest(m2, contract)
    m3 = dict(m)
    m3["contract_version"] = "rl_phase1_contract_v1"
    assert tc.check_cache_manifest(m3, contract)


def test_vocab_no_collisions(vocab):
    """§13.1: typed vocabs — no `%64`-style collisions possible."""
    for kind in SemanticVocab.KINDS:
        ids = list(vocab.tables[kind].values())
        assert len(ids) == len(set(ids)), kind       # injective
        assert 0 not in ids                          # 0 = OOV bucket
    # OOV lookups land in bucket 0 for every kind
    for kind in ("mech", "tech", "skill"):
        assert vocab.id(kind, 987654321) == 0


def test_leakage_guard_rejects_labels_and_identity():
    from pysim.rl.transformer.token_contract import (assert_observation_clean,
                                                     ContractError)
    assert_observation_clean({"global": {"self_hp": 1}, "entities": []})
    for bad in ({"winner": 1}, {"fight_report": {}}, {"replay_path": "x"},
                {"y_wdl": 1}, {"next_hp": 5}, {"match_id_hash": "h"}):
        with pytest.raises(ContractError):
            assert_observation_clean(bad)
    # nested
    with pytest.raises(ContractError):
        assert_observation_clean({"entities": [{"damage": 3}]})


def test_observation_roundtrip_digest_stable(vocab, tok_cfg):
    sim, real, pol = toydata.make_toy_rows(seed=3, n_games=2)
    from pysim.rl.transformer.tokenizer import encode_battle_tokens
    ta1 = encode_battle_tokens(sim[0]["observation"], vocab, tok_cfg)
    ta2 = encode_battle_tokens(sim[0]["observation"], vocab, tok_cfg)
    assert ta1.digest() == ta2.digest()
    # re-run through the v1 adapter must be deterministic too
    v1 = {
        "version": "obs_v1", "round": 2, "ego": 0,
        "self": {"hp": 100, "max_hp": 100, "units": [], "techs": {},
                 "officers": [], "blueprints": [], "tower_strengthen": [0, 0],
                 "tower_mods": [], "devices": [], "skill_events": []},
        "opp": {"hp": 90, "max_hp": 100, "units": [], "techs": {},
                "officers": [], "blueprints": [], "tower_strengthen": [0, 0],
                "tower_mods": [], "devices": [], "skill_events": []}}
    o1 = battle_token_obs_from_v1(v1)
    o2 = battle_token_obs_from_v1(json.loads(json.dumps(v1)))
    assert tc.stable_digest(o1) == tc.stable_digest(o2)


def test_over_limit_raises_not_truncates(vocab, tok_cfg):
    from pysim.rl.transformer.tokenizer import (encode_battle_tokens,
                                                TokenizerError)
    obs = toydata.toy_battle_obs.__wrapped__ if False else None
    sim, real, pol = toydata.make_toy_rows(seed=1, n_games=1)
    big = dict(sim[0]["observation"])
    big["entities"] = big["entities"] * 100
    with pytest.raises(TokenizerError):
        encode_battle_tokens(big, vocab, tok_cfg)


def test_token_length_stats_counts_over_limit(vocab, tok_cfg):
    sim, real, pol = toydata.make_toy_rows(seed=1, n_games=1)
    big = dict(sim[0]["observation"])
    big["entities"] = big["entities"] * 100
    stats = tok_stats = None
    from pysim.rl.transformer.tokenizer import token_length_stats
    stats = token_length_stats([sim[0]["observation"], big], vocab, tok_cfg)
    assert stats["n"] == 2 and stats["n_over_limit"] == 1
