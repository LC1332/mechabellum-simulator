#!/usr/bin/env python
"""Tokenize v2 datasets into the disposable sharded token cache (§7.4).

Reads battle_{real,sim}_v2.jsonl.gz (+ policy_prefix_real_v2.jsonl.gz for
--policy), encodes every row through the versioned tokenizer, writes
sharded .npz shards + a manifest binding: source digests, contract binds,
tokenizer digest, length stats, per-shard checksums. Two runs on the same
inputs produce byte-identical manifests (deterministic order). Rows whose
token count exceeds max_entity_tokens are EXCLUDED and counted (§4.5) —
never silently truncated.

--stats-only: print P50/P95/P99/max + over-limit count and exit (the §4.5
freeze evidence for max_entity_tokens).
"""
import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer.tokenizer import (TokenizerConfig,     # noqa: E402
                                            SemanticVocab,
                                            token_length_stats,
                                            TokenizerError)
from pysim.rl.transformer.data import (load_rows, fit_vocab,     # noqa: E402
                                       TokenCacheWriter,
                                       encode_value_row,
                                       encode_policy_row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--datasets-dir", default=None,
                    help="default <run-dir>/datasets")
    ap.add_argument("--out-dir", default=None,
                    help="default <run-dir>/token_cache")
    ap.add_argument("--policy", action="store_true",
                    help="also tokenize the policy prefix dataset")
    ap.add_argument("--max-obj", type=int, default=512)
    ap.add_argument("--max-ptr", type=int, default=64)
    ap.add_argument("--shard-size", type=int, default=4096)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stats-only", action="store_true")
    args = ap.parse_args()

    ds_dir = args.datasets_dir or os.path.join(args.run_dir, "datasets")
    out_dir = args.out_dir or os.path.join(args.run_dir, "token_cache")
    contract_path = os.path.join(args.run_dir, "contract.json")
    if not os.path.exists(contract_path):
        sys.exit("missing %s — run build_rl_transformer_contract.py / "
                 "the run scaffold first" % contract_path)
    contract = tc.load_contract(contract_path)
    bad = tc.check_contract(contract)
    if bad:
        sys.exit("contract incompatible: %s" % "; ".join(bad))
    if not tc.t0_gate_allows(contract, formal=True):
        # engineering mode is fine for cache building on toy data
        if (contract.get("t0_backtest") or {}).get("status") \
                != tc.T0_PENDING:
            sys.exit("contract t0 status unknown")
        print("[T0 GATE] pending: 只允许 toy/engineering cache (§3.2)")

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    tok_cfg = TokenizerConfig.from_dict({
        "max_entity_tokens": contract.get("max_entity_tokens") or 320,
        "xy_grid": contract.get("xy_grid") or {},
        "bias_buckets": contract.get("bias_buckets") or {},
    })

    paths = {
        "sim": os.path.join(ds_dir, "battle_sim_v2.jsonl.gz"),
        "real": os.path.join(ds_dir, "battle_real_v2.jsonl.gz"),
    }
    if args.policy:
        paths["policy"] = os.path.join(ds_dir,
                                       "policy_prefix_real_v2.jsonl.gz")
    for name, p in paths.items():
        if not os.path.exists(p):
            sys.exit("missing dataset %s" % p)

    train_rows = load_rows(paths["real"], split="train", limit=20000)
    vocab = fit_vocab(train_rows, gd)
    print("vocab sizes:", json.dumps(vocab.sizes()))

    all_rows = []
    for name, p in paths.items():
        rows = load_rows(p, limit=args.limit)
        print("%s: %d rows" % (name, len(rows)))
        all_rows.extend((name, r) for r in rows)

    if args.stats_only:
        stats = token_length_stats([r["observation"] for _, r in all_rows],
                                   vocab, tok_cfg)
        print(json.dumps(stats, indent=1))
        return

    writer = TokenCacheWriter(out_dir, list(paths.values()), contract,
                              tok_cfg, shard_size=args.shard_size)
    n_excluded = 0
    for name, r in all_rows:            # deterministic insertion order
        try:
            if name == "policy":
                arrs = encode_policy_row(r, vocab, tok_cfg, args.max_obj,
                                         args.max_ptr)
            else:
                arrs = encode_value_row(r, vocab, tok_cfg)
        except TokenizerError as e:
            n_excluded += 1
            continue                    # counted, never truncated (§4.5)
        writer.lengths.append(int(arrs["n_tokens"]))
        writer.add(r["sample_id"], r["split"], arrs)
    manifest = writer.finalize()
    print("cache written:", out_dir)
    print("rows: %d | excluded(over-limit/mask): %d | shards: %d" %
          (manifest["lengths"]["n"], n_excluded, len(manifest[
              "shard_checksums"])))
    print("lengths:", json.dumps(manifest["lengths"]))
    print("manifest_digest:", manifest["manifest_digest"])
    if n_excluded:
        print("§4.5: excluded 样本已计数; 数据卡必须报告该数字与原因分布")


if __name__ == "__main__":
    main()
