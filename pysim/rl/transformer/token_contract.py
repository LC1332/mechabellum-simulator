# rl_transformer_contract_v1 (Transformer基线任务书 §3.1).
#
# A NEW contract — never in-place over Phase 1's rl_phase1_contract_v1.
# check_contract() explicitly rejects the Phase 1 contract (and any v1
# artifact claiming transformer compatibility), and the T0 helpers enforce
# the 任务书 §0/§3 gate: formal labels / training / test / arena verdicts are
# FORBIDDEN until the 1000-replay backtest is absorbed and the user accepts
# the provisional skill profile.
from __future__ import annotations

import hashlib
import json
import os

from ...transition.model import (SCHEMA_VERSION, RULESET_VERSION,
                                 ENGINE_VERSION)
from ...battlefield.model import BATTLEFIELD_INPUT_VERSION

CONTRACT_VERSION = "rl_transformer_contract_v1"
OBSERVATION_VERSION = "transformer_obs_v2"
ACTION_VERSION = "transformer_act_v2"
DATA_VERSION = "transformer_data_v2"
TOKENIZER_VERSION = "structured_token_v1"
VALUE_MODEL_VERSION = "tvalue_v1"
POLICY_MODEL_VERSION = "tpolicy_bc_v1"

# split inheritance: v2 MUST reuse the phase1 replay-group split (§7.1)
SPLIT_INHERIT_FROM = "phase1 replay-group split"
SPLIT_VERSION = "phase1_split_v1"

# physical GPU policy (§1.2/§9, user-frozen): train on 1..7, GPU 0 reserved
TRAINING_GPU_ALLOWLIST = [1, 2, 3, 4, 5, 6, 7]
RESERVED_PHYSICAL_GPUS = [0]

# token budget bounds (§4.5): max_entity_tokens is frozen per-run from v2
# corpus length stats; hard ceiling here rejects absurd runs
MAX_ENTITY_TOKENS_HARD = 1024
MAX_ACTION_HISTORY = 64          # §4.3 structured action history
MAX_PLAN_ACTIONS = 64            # same safety valve as phase 1

# leakage: keys that must NEVER appear in any observation fed to the model
# (§11). assert_observation_clean() raises on a hit.
FORBIDDEN_OBS_KEYS = frozenset({
    "winner", "wdl", "damage", "y_wdl", "y_damage", "fight_report",
    "report", "replay_path", "replay_hash", "match_id_hash", "label",
    "future", "post_battle", "next_hp", "after", "seed_outcome", "file",
    "player_name", "filename", "path", "hash",
})

# every battle-affecting engine/compiler/settlement change invalidates the
# sim cache; every observation/action/tokenizer change invalidates the token
# cache (§3.1). Encode the rules as version tuples the cache manifest checks.
SIM_CACHE_BINDS = ("schema_version", "ruleset_version", "engine_version",
                   "battlefield_input", "sim_label_version")
TOKEN_CACHE_BINDS = ("observation_version", "action_version",
                     "tokenizer_version", "tokenizer_digest",
                     "max_entity_tokens", "xy_grid", "bias_buckets")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "data")
CONTRACT_PATH = os.path.join(DATA_DIR, "rl_transformer_contract.json")

# T0 gate states (§3): "pending" -> engineering only; "accepted" -> the
# backtest was absorbed and the user confirmed the provisional skills may
# enter training (recorded with commit + replay-set hash + metrics file).
T0_PENDING = "pending"
T0_ACCEPTED = "accepted"


class ContractError(ValueError):
    pass


# ---------------------------------------------------------------- digests
def stable_digest(obj) -> str:
    """sha256[:16] over canonical JSON — same convention as rl.contracts."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def engine_digest() -> str:
    """Digest of everything that can change a battle outcome (sim cache
    invalidation key, §3.1)."""
    return stable_digest({
        "schema": SCHEMA_VERSION, "ruleset": RULESET_VERSION,
        "engine": ENGINE_VERSION, "battlefield": BATTLEFIELD_INPUT_VERSION,
    })


def sim_label_version() -> str:
    return "sim_label_v2_%s" % engine_digest()


# ---------------------------------------------------------------- contract
RL_CODE_VERSION = "rl_transformer_v1"


def build_contract(git_commit: str | None = None, git_dirty: bool = False,
                   t0_status: str = T0_PENDING, t0_record: dict | None = None,
                   max_entity_tokens: int | None = None,
                   xy_grid: dict | None = None,
                   bias_buckets: dict | None = None,
                   extra: dict | None = None) -> dict:
    c = {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "engine_version": ENGINE_VERSION,
        "battlefield_input": BATTLEFIELD_INPUT_VERSION,
        "observation_version": OBSERVATION_VERSION,
        "action_version": ACTION_VERSION,
        "data_version": DATA_VERSION,
        "sim_label_version": sim_label_version(),
        "tokenizer_version": TOKENIZER_VERSION,
        "split_version": SPLIT_VERSION,
        "split_inherit_from": SPLIT_INHERIT_FROM,
        "value_model_version": VALUE_MODEL_VERSION,
        "policy_model_version": POLICY_MODEL_VERSION,
        "rl_code_version": RL_CODE_VERSION,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "training_gpu_allowlist": list(TRAINING_GPU_ALLOWLIST),
        "reserved_physical_gpus": list(RESERVED_PHYSICAL_GPUS),
        "max_entity_tokens_hard": MAX_ENTITY_TOKENS_HARD,
        "max_action_history": MAX_ACTION_HISTORY,
        "max_plan_actions": MAX_PLAN_ACTIONS,
        "max_entity_tokens": max_entity_tokens,
        "xy_grid": xy_grid or {},
        "bias_buckets": bias_buckets or {},
        "t0_backtest": {"status": t0_status, "record": t0_record or {}},
        "notes": [
            "T0 未冻结前仅允许工程产物(toy data/smoke/单测/probe),禁止正式标签与训练",
            "provisional 技能进入 coverage 训练,strict verified 指标单列",
        ],
    }
    if extra:
        c.update(extra)
    return c


def write_contract(path: str = CONTRACT_PATH, **kw) -> dict:
    c = build_contract(**kw)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1, sort_keys=True)
    return c


def load_contract(path: str = CONTRACT_PATH) -> dict:
    with open(path, encoding="utf8") as f:
        return json.load(f)


def check_contract(c: dict) -> list[str]:
    """Incompatibilities between `c` and the live code (§3.1).

    Old Phase 1 contracts (and any contract with a different family name)
    are a HARD incompatibility — never silently tolerated."""
    bad = []
    fam = str(c.get("contract_version", ""))
    if fam != CONTRACT_VERSION:
        if fam.startswith("rl_phase1"):
            bad.append("contract_version %s 是 Phase 1 合同, 与 %s 不兼容 "
                       "(v1 数据/checkpoint 只能作历史对照)" % (fam, CONTRACT_VERSION))
        else:
            bad.append("contract_version %s != %s" % (fam, CONTRACT_VERSION))
    if c.get("schema_version") != SCHEMA_VERSION:
        bad.append("schema_version %s != %s" % (c.get("schema_version"),
                                                SCHEMA_VERSION))
    if c.get("ruleset_version") != RULESET_VERSION:
        bad.append("ruleset_version mismatch")
    if c.get("engine_version") != ENGINE_VERSION:
        bad.append("engine_version %s != %s" % (c.get("engine_version"),
                                                ENGINE_VERSION))
    if c.get("battlefield_input") != BATTLEFIELD_INPUT_VERSION:
        bad.append("battlefield_input mismatch")
    for key, cur in (("observation_version", OBSERVATION_VERSION),
                     ("action_version", ACTION_VERSION),
                     ("data_version", DATA_VERSION),
                     ("tokenizer_version", TOKENIZER_VERSION),
                     ("sim_label_version", sim_label_version())):
        if c.get(key) != cur:
            bad.append("%s %s != %s" % (key, c.get(key), cur))
    return bad


def require_compatible(c: dict) -> None:
    bad = check_contract(c)
    if bad:
        raise ContractError("; ".join(bad))


# ---------------------------------------------------------------- T0 gate
def t0_gate_allows(c: dict, formal: bool = True) -> bool:
    """True when `formal` artifacts (labels/training/test/arena verdicts)
    may be produced under contract `c` (§3/§3.2)."""
    if not formal:
        return True
    t0 = c.get("t0_backtest") or {}
    return t0.get("status") == T0_ACCEPTED and bool(t0.get("record"))


def require_t0(c: dict, what: str) -> None:
    if not t0_gate_allows(c, formal=True):
        raise ContractError(
            "T0 未冻结: %s 被禁止 (任务书 §3.2 — 等待 1000 局回测吸收). "
            " toy/smoke/单测请用 formal=False 入口." % what)


def accept_t0(commit: str, replay_set_hash: str, metrics_path: str,
              decision: str, path: str = CONTRACT_PATH) -> dict:
    """Record the user's T0 acceptance into the contract (user action, §T0)."""
    c = load_contract(path)
    if c.get("contract_version") != CONTRACT_VERSION:
        raise ContractError("refusing to attach T0 to a non-transformer contract")
    c["t0_backtest"] = {
        "status": T0_ACCEPTED,
        "record": {
            "commit": commit, "replay_set_hash": replay_set_hash,
            "metrics_path": metrics_path, "decision": decision,
        },
    }
    with open(path, "w", encoding="utf8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1, sort_keys=True)
    return c


# ------------------------------------------------------- cache manifests
def cache_manifest(source_digest: str, contract: dict, lengths: dict,
                   shard_checksums: list[str], kind: str) -> dict:
    """Token/sim cache manifest entry (§3.1/§7.4): binds the cache to the
    exact code+config digests; any bind mismatch = stale cache."""
    binds = {k: contract.get(k) for k in
             (SIM_CACHE_BINDS if kind == "sim" else TOKEN_CACHE_BINDS)}
    return {
        "cache_kind": kind,
        "contract_version": CONTRACT_VERSION,
        "source_digest": source_digest,
        "binds": binds,
        "lengths": lengths,
        "shard_checksums": shard_checksums,
        "manifest_digest": stable_digest({
            "source": source_digest, "binds": binds, "lengths": lengths,
            "shards": shard_checksums}),
    }


def check_cache_manifest(manifest: dict, contract: dict) -> list[str]:
    bad = []
    for k, v in manifest.get("binds", {}).items():
        if contract.get(k) != v:
            bad.append("cache bind %s %s != %s (cache 已失效)" %
                       (k, v, contract.get(k)))
    if manifest.get("contract_version") != CONTRACT_VERSION:
        bad.append("cache contract_version mismatch")
    return bad


# ------------------------------------------------------- leakage guard
def assert_observation_clean(obs: dict) -> None:
    """§11: observations must never carry labels / future info / replay
    identity. Recursively checks keys (dicts + list members)."""
    def walk(obj, where):
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                for bad in FORBIDDEN_OBS_KEYS:
                    if bad in kl:
                        raise ContractError(
                            "observation 泄漏字段 %r at %s" % (k, where))
                walk(v, where + "." + str(k))
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                walk(v, "%s[%d]" % (where, i))
    walk(obs, "$")
