# v2 dataset <-> token cache plumbing (任务书 §7.1/§7.4).
#
# The source of truth is the v2 JSONL datasets (battle_real_v2 /
# battle_sim_v2 / policy_prefix_real_v2). The token cache is a DISPOSABLE
# derived artifact: sharded .npz with a manifest binding source digest +
# tokenizer/config digests + per-shard checksums; rebuilding it twice from
# the same inputs yields identical checksums (deterministic order, §7.4).
from __future__ import annotations

import gzip
import hashlib
import json
import os

import numpy as np

from .tokenizer import (TokenizerConfig, SemanticVocab, TokenizerError,
                        encode_battle_tokens, encode_policy_tokens,
                        action_to_fields, collate_tokens, bias_components,
                        swap_token_arrays, N_FEAT)
from .token_contract import (cache_manifest, check_cache_manifest,
                             stable_digest)


class ContractError(ValueError):
    pass


# ---------------------------------------------------------------- rows
def load_rows(path: str, split: str | None = None, limit: int = 0) -> list:
    op = gzip.open if str(path).endswith(".gz") else open
    rows = []
    with op(path, "rt", encoding="utf8") as f:
        for line in f:
            r = json.loads(line)
            if split is not None and r.get("split") != split:
                continue
            rows.append(r)
            if limit and len(rows) >= limit:
                break
    return rows


def file_digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def fit_vocab(train_rows: list, gd) -> SemanticVocab:
    """Vocabulary fitted on TRAIN ONLY (§11: normalization statistics,
    vocab and coordinate buckets are train-fitted)."""
    vocab = SemanticVocab.from_gamedata(gd)
    extras = {k: set() for k in SemanticVocab.KINDS}
    for r in train_rows:
        obs = r["observation"]
        for e in obs.get("entities") or []:
            k = e.get("kind")
            if k in ("self_unit", "opp_unit"):
                extras["mech"].add(int(e["mech"]))
                if e.get("equip"):
                    extras["equip"].add(int(e["equip"]))
            elif k in ("self_tech", "opp_tech"):
                extras["tech"].add(int(e["tech"]))
            elif k == "skill_release":
                extras["skill"].add(int(e["id"]))
            elif k == "construction":
                extras["construction"].add(int(e["id"]))
            elif k == "device":
                extras["contraption"].add(int(e["id"]))
        pol = obs.get("policy") or {}
        extras["mech"].update(int(m) for m in pol.get("unlocked_mechs") or ())
        extras["skill"].update(int(s["skill"])
                               for s in pol.get("skills") or [])
        extras["equip"].update(int(x) for x in
                               pol.get("equipment_inventory") or [])
        sp = obs.get("space") or {}
        extras["mech"].update(int(m) for m in sp.get("mech_cands") or ())
        extras["tower"].update(int(t) for t in sp.get("tower_cands") or ())
        extras["blueprint"].update(int(b) for b in
                                   sp.get("blueprint_cands") or [])
        extras["contraption"].update(int(c) for c in
                                     sp.get("contraption_cands") or [])
        for m, t in sp.get("tech_cands") or []:
            extras["tech"].update((int(m), int(t)))
        for s, sid in sp.get("skill_cands") or []:
            extras["skill"].update((int(s), int(sid)))
    for kind, ids in extras.items():
        vocab.register(kind, ids)
    return vocab


# ---------------------------------------------------------------- cache
def _pad_stack(vals: list) -> np.ndarray:
    """Stack variable-length per-row arrays by padding to the shard max.

    Padding value: 0 for numeric arrays — pad rows carry pad_mask=0 so they
    never attend/score (§4.5). Object arrays (candidate values) pad with
    None."""
    a0 = np.asarray(vals[0])
    if a0.dtype == object:
        width = max(len(v) for v in vals)
        out = np.full((len(vals), width), None, dtype=object)
        for i, v in enumerate(vals):
            out[i, :len(v)] = list(v)
        return out
    if a0.ndim == 0:
        return np.asarray(vals, dtype=a0.dtype)
    shape = [max(np.asarray(v).shape[d] for v in vals)
             for d in range(a0.ndim)]
    fill = 0.0 if a0.dtype.kind == "f" else 0
    out = np.full((len(vals),) + tuple(shape), fill, dtype=a0.dtype)
    for i, v in enumerate(vals):
        v = np.asarray(v)
        idx = tuple(slice(0, s) for s in v.shape)
        out[i][idx] = v
    return out


class TokenCacheWriter:
    """Sharded .npz writer with a deterministic manifest (§7.4)."""

    def __init__(self, out_dir: str, source_paths: list[str],
                 contract: dict, tok_cfg: TokenizerConfig,
                 shard_size: int = 4096):
        self.out_dir = out_dir
        self.source_paths = list(source_paths)
        self.contract = contract
        self.tok_cfg = tok_cfg
        self.shard_size = int(shard_size)
        os.makedirs(out_dir, exist_ok=True)
        self._buf: list[tuple] = []
        self._shard_ids: list[int] = []
        self.checksums: list[str] = []
        self.rows_written = 0
        self.lengths: list[int] = []

    def add(self, sample_id: str, split: str, arrays: dict) -> None:
        self._buf.append((sample_id, split, arrays))
        if len(self._buf) >= self.shard_size:
            self._flush()

    def _flush(self):
        if not self._buf:
            return
        # rows of different kinds (value vs policy encodings) have
        # different key sets — group by signature, one npz per group
        groups: dict[tuple, list] = {}
        for b in self._buf:
            groups.setdefault(tuple(sorted(b[2].keys())), []).append(b)
        for rows in groups.values():
            idx = len(self.checksums)
            ids = [b[0] for b in rows]
            path = os.path.join(self.out_dir, "shard_%05d.npz" % idx)
            payload = {}
            for key in rows[0][2]:
                vals = [b[2][key] for b in rows]
                payload[key] = _pad_stack(vals)
            payload["sample_id"] = np.asarray(ids, dtype=object)
            payload["split"] = np.asarray([b[1] for b in rows],
                                          dtype=object)
            np.savez_compressed(path, **payload)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            self.checksums.append(h.hexdigest()[:16])
            self.rows_written += len(rows)
        self._buf = []

    def finalize(self) -> dict:
        self._flush()
        source = stable_digest({os.path.basename(p): file_digest(p)
                                for p in self.source_paths})
        lengths = {
            "n": self.rows_written,
            "p50": float(np.percentile(self.lengths, 50)) if
            self.lengths else 0.0,
            "p95": float(np.percentile(self.lengths, 95)) if
            self.lengths else 0.0,
            "p99": float(np.percentile(self.lengths, 99)) if
            self.lengths else 0.0,
            "max": float(max(self.lengths)) if self.lengths else 0.0,
        }
        manifest = cache_manifest(source, self.contract, lengths,
                                  self.checksums, kind="token")
        manifest["tokenizer_digest"] = self.tok_cfg.digest()
        manifest["source_paths"] = [os.path.basename(p)
                                    for p in self.source_paths]
        with open(os.path.join(self.out_dir, "manifest.json"), "w",
                  encoding="utf8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
        return manifest


class TokenCacheReader:
    def __init__(self, cache_dir: str, contract: dict | None = None):
        self.cache_dir = cache_dir
        with open(os.path.join(cache_dir, "manifest.json"),
                  encoding="utf8") as f:
            self.manifest = json.load(f)
        if contract is not None:
            bad = check_cache_manifest(self.manifest, contract)
            if bad:
                raise ContractError("token cache 不兼容: " + "; ".join(bad))
        self._by_split: dict[str, list] = {}
        for path in sorted(p for p in os.listdir(cache_dir)
                           if p.endswith(".npz")):
            z = np.load(os.path.join(cache_dir, path), allow_pickle=True)
            for i, (sid, split) in enumerate(zip(z["sample_id"],
                                                 z["split"])):
                self._by_split.setdefault(str(split), []).append(
                    (str(sid), i, path))
        for rows in self._by_split.values():
            rows.sort(key=lambda r: r[0])       # deterministic order

    def sample_ids(self, split: str) -> list[str]:
        return [r[0] for r in self._by_split.get(split, [])]

    def __len__(self):
        return sum(len(v) for v in self._by_split.values())

    def iter_split(self, split: str):
        cache: dict[str, dict] = {}
        for sid, ri, path in self._by_split.get(split, []):
            if path not in cache:
                cache = {path: np.load(os.path.join(self.cache_dir, path),
                                       allow_pickle=True)}
            z = cache[path]
            row = {}
            for k in z.files:
                if k in ("sample_id", "split"):
                    continue
                row[k] = z[k][ri]
            row["sample_id"] = sid
            yield sid, row


# ------------------------------------------------------- row -> tensors
def encode_value_row(row: dict, vocab: SemanticVocab,
                     tok_cfg: TokenizerConfig) -> dict:
    """battle row -> token arrays + components + mirrored copy arrays
    (the whole side-swap path is precomputed, no JSON in the loop)."""
    if "policy" in row["observation"]:
        raise ValueError("value rows must carry battle observations")
    ta = encode_battle_tokens(row["observation"], vocab, tok_cfg)
    comp = bias_components(ta, tok_cfg)
    sw = swap_token_arrays(ta)
    comp_sw = bias_components(sw, tok_cfg)
    return {
        "type": ta.type, "sem": ta.sem, "feat": ta.feat,
        "x": ta.x, "y": ta.y, "side": ta.side, "group": ta.group,
        "air": ta.air, "area": ta.area, "pad_mask": ta.mask,
        "comp": comp, "n_tokens": np.int64(ta.n_tokens),
        "type_sw": sw.type, "sem_sw": sw.sem, "feat_sw": sw.feat,
        "x_sw": sw.x, "y_sw": sw.y, "side_sw": sw.side,
        "group_sw": sw.group, "air_sw": sw.air, "area_sw": sw.area,
        "pad_mask_sw": sw.mask, "comp_sw": comp_sw,
    }


def encode_policy_row(row: dict, vocab: SemanticVocab,
                      tok_cfg: TokenizerConfig, max_obj: int,
                      max_ptr: int) -> dict:
    """policy prefix row -> token arrays + candidate tables + target fields.
    Raises TokenizerError when the target is out of mask (teacher-forced
    target-in-mask must be 100%, §13.2)."""
    obs = row["observation"]
    ta, tables = encode_policy_tokens(obs, vocab, tok_cfg)
    target = row["target"]
    fields = action_to_fields(target, tables, tok_cfg)
    n_obj = len(tables["obj_entries"])
    n_ptr = tables["ptr_mask"].shape[1]
    from .tokenizer import POOL_INDEX
    pools = [POOL_INDEX[e["pool"]] for e in tables["obj_entries"]]
    out = {
        "type": ta.type, "sem": ta.sem, "feat": ta.feat,
        "x": ta.x, "y": ta.y, "side": ta.side, "group": ta.group,
        "air": ta.air, "area": ta.area, "pad_mask": ta.mask,
        "comp": bias_components(ta, tok_cfg),
        "n_tokens": np.int64(ta.n_tokens),
        "obj_mask": _pad2d(tables["obj_mask"], max_obj),
        "ptr_mask": _pad2d(tables["ptr_mask"], max_ptr),
        "xy_legal": tables["xy_legal"],
        "verb_mask": np.asarray(obs["space"]["verb_mask"], dtype=np.float32),
        "arities": _pad1d(tables["arities"], max_obj),
        "ptr_token_pos": _pad1d(tables["ptr_token_pos"], max_ptr, pad=-1),
        "obj_pool": _pad1d(np.asarray(pools, dtype=np.int64), max_obj,
                           pad=-1),
        "obj_value": np.asarray([e["value"] for e in tables["obj_entries"]]
                                + [None] * (max_obj - len(pools)),
                                dtype=object)[:max_obj],
        "n_obj": np.int64(n_obj), "n_ptr": np.int64(n_ptr),
        "fields": np.asarray(fields.to_list(), dtype=np.int64),
        "end": np.int64(1 if target["verb"] == "END_DEPLOY" else 0),
        "rem_bucket": np.int64(int(row.get("rem_bucket", 0))),
    }
    return out


def _pad2d(m: np.ndarray, width: int) -> np.ndarray:
    m = np.asarray(m)
    if m.shape[-1] >= width:
        return m[..., :width]
    pad = np.zeros(m.shape[:-1] + (width - m.shape[-1],), dtype=m.dtype)
    return np.concatenate([m, pad], axis=-1)


def _pad1d(v: np.ndarray, width: int, pad: int = 0) -> np.ndarray:
    v = np.asarray(v)
    if len(v) >= width:
        return v[:width]
    return np.concatenate([v, np.full(width - len(v), pad, dtype=v.dtype)])


def collate_value(rows: list[dict], device=None) -> tuple[dict, dict]:
    base = [{k: r[k] for k in ("type", "sem", "feat", "x", "y", "side",
                               "group", "air", "area", "pad_mask",
                               "n_tokens")}
            for r in rows]
    batch = collate_tokens_base(base)
    batch = {k: torch_as_tensor(v) for k, v in batch.items()}
    comps = [r["comp"] for r in rows]
    comps_sw = [r["comp_sw"] for r in rows]
    t = max(c.shape[-1] for c in comps)
    b = len(rows)
    comp = np.zeros((b, 7, t, t), dtype=np.int64)
    comp_sw = np.zeros((b, 7, t, t), dtype=np.int64)
    for i, (c, cs) in enumerate(zip(comps, comps_sw)):
        n = c.shape[-1]
        comp[i, :, :n, :n] = c
        comp_sw[i, :, :n, :n] = cs
    comps_t = {"comp": torch_as_tensor(comp),
               "comp_sw": torch_as_tensor(comp_sw)}
    if device is not None:
        batch = {k: v.to(device) for k, v in batch.items()}
        comps_t = {k: v.to(device) for k, v in comps_t.items()}
    return batch, comps_t


def collate_tokens_base(rows: list[dict]) -> dict:
    """Collate already-encoded token dicts (cache rows) with padding."""
    arrays = [TokenArraysView(r) for r in rows]
    out = collate_tokens(arrays)
    return out


class TokenArraysView:
    """Minimal TokenArrays-like view over a cache row dict."""

    def __init__(self, r: dict):
        self.type = r["type"]; self.sem = r["sem"]; self.feat = r["feat"]
        self.x = r["x"]; self.y = r["y"]; self.side = r["side"]
        self.group = r["group"]; self.air = r["air"]; self.area = r["area"]
        self.mask = r["pad_mask"]
        self.n_tokens = int(r["n_tokens"])
        self.index = {}


def collate_policy(rows: list[dict], device=None) -> dict:
    base = [{k: r[k] for k in ("type", "sem", "feat", "x", "y", "side",
                               "group", "air", "area", "pad_mask",
                               "n_tokens")}
            for r in rows]
    batch = collate_tokens_base(base)
    batch = {k: torch_as_tensor(v) for k, v in batch.items()}
    comps = [r["comp"] for r in rows]
    t = max(c.shape[-1] for c in comps)
    comp = np.zeros((len(rows), 7, t, t), dtype=np.int64)
    for i, c in enumerate(comps):
        n = c.shape[-1]
        comp[i, :, :n, :n] = c
    tables = {
        "verb_mask": torch_as_tensor(np.stack([r["verb_mask"] for r in rows])),
        "obj_mask": torch_as_tensor(np.stack([r["obj_mask"] for r in rows])),
        "ptr_mask": torch_as_tensor(np.stack([r["ptr_mask"] for r in rows])),
        "xy_legal": torch_as_tensor(np.stack([r["xy_legal"] for r in rows])),
        "arities": torch_as_tensor(np.stack([r["arities"] for r in rows])),
    }
    out = {
        "batch": batch, "components": torch_as_tensor(comp),
        "tables": tables,
        "fields": torch_as_tensor(np.stack([r["fields"] for r in rows])),
        "end": torch_as_tensor(np.asarray([r["end"] for r in rows],
                                          dtype=np.int64)),
        "rem_bucket": torch_as_tensor(np.asarray([r["rem_bucket"]
                                                  for r in rows],
                                                 dtype=np.int64)),
        "sample_id": [r.get("sample_id", "") for r in rows],
    }
    if device is not None:
        import torch
        out["batch"] = {k: v.to(device) for k, v in out["batch"].items()}
        out["components"] = out["components"].to(device)
        out["tables"] = {k: v.to(device) for k, v in out["tables"].items()}
        for k in ("fields", "end", "rem_bucket"):
            out[k] = out[k].to(device)
    return out


def torch_as_tensor(arr):
    import torch
    return torch.as_tensor(np.ascontiguousarray(arr))
