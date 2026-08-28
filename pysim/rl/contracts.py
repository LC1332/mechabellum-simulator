# RL Phase 1: frozen contracts, action profile, digest helpers.
#
# 任务书 §2.2/§3/§11: everything the datasets/models/arena consume is pinned
# here — engine versions, the rl_phase1_core_v1 action profile, sample
# provenance schemas and the deterministic seed derivation. The generated
# contract JSON (tools/build_rl_phase1_contract.py -> data/rl_phase1_contract.json)
# binds the code commit to the feature profile so any later change must bump
# the contract version.
from __future__ import annotations

import hashlib
import json
import os

from ..transition.model import (SCHEMA_VERSION, RULESET_VERSION,
                                ENGINE_VERSION, ActionKind)

CONTRACT_VERSION = "rl_phase1_contract_v1"
OBSERVATION_VERSION = "obs_v1"
ACTION_VERSION = "act_v1"
DATA_VERSION = "data_v1"
FEATURE_PROFILE = "min_public_v1"
ACTION_PROFILE = "rl_phase1_core_v1"
SIM_LABEL_VERSION = "sim_label_v1"

# verbs the BC policy may emit (task §3.4). Exogenous verbs (gift/reinforce)
# are applied from the replay before the policy takes over and never appear
# here. SURRENDER is arena-only (never sampled by BC).
PROFILE_VERBS = (
    "END_DEPLOY", "BUY_UNIT", "UNLOCK_UNIT", "UPGRADE_UNIT", "BUY_TECH",
    "MOVE_UNIT", "SELL_UNIT", "USE_EQUIPMENT", "RELEASE_COMMANDER_SKILL",
    "ACTIVATE_ENERGY_TOWER_SKILL", "STRENGTHEN_TOWER",
)

VERB_TO_KIND = {
    "END_DEPLOY": ActionKind.END_DEPLOY,
    "BUY_UNIT": ActionKind.BUY_UNIT,
    "UNLOCK_UNIT": ActionKind.UNLOCK_UNIT,
    "UPGRADE_UNIT": ActionKind.UPGRADE_UNIT,
    "BUY_TECH": ActionKind.BUY_TECH,
    "MOVE_UNIT": ActionKind.MOVE_UNIT,
    "SELL_UNIT": ActionKind.SELL_UNIT,
    "USE_EQUIPMENT": ActionKind.USE_EQUIPMENT,
    "RELEASE_COMMANDER_SKILL": ActionKind.RELEASE_COMMANDER_SKILL,
    "ACTIVATE_ENERGY_TOWER_SKILL": ActionKind.ACTIVATE_ENERGY_TOWER_SKILL,
    "STRENGTHEN_TOWER": ActionKind.RAW_UNSUPPORTED,   # passthrough, fully modeled
}

KIND_TO_VERB = {v: k for k, v in VERB_TO_KIND.items()}

# receipt reason codes the RL prefix env converts into "accepted no-op with a
# fidelity flag" (用户裁决 2026-08-28: 未实现技能 = 执行了但没有效果). Everything
# else that rejects stays a hard error for teacher forcing.
NOOP_REASON_CODES = frozenset({
    "UNSUPPORTED_ACTION", "UNSUPPORTED_ACTION_FIELD", "MISSING_RULE_DATA",
    # equipment restriction tables lag the corpus versions — an equip the
    # game accepted but our restriction data refuses is treated as no-effect
    "EQUIPMENT_RESTRICTION_MISMATCH", "EQUIPMENT_TARGET_NOT_ALLOWED",
})

MAX_PLAN_ACTIONS = 64            # task §4.5 safety valve
MAX_UNITS_PAD = 64               # observation padding bound

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "data")
CONTRACT_PATH = os.path.join(DATA_DIR, "rl_phase1_contract.json")


# ---------------------------------------------------------------- digests
def stable_digest(obj) -> str:
    """sha256[:16] over canonical JSON — observation/action/sample digests."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def derive_seed(sample_id: str, seed_index: int,
                label_version: str = SIM_LABEL_VERSION) -> int:
    """Task §5.4: battle seeds derive from sha256(sample_id|seed_index|version).

    Kept inside [1, 2^30) to match the engine's own seed range."""
    h = hashlib.sha256(
        f"{sample_id}|{seed_index}|{label_version}".encode()).hexdigest()
    return 1 + int(h[:12], 16) % ((1 << 30) - 1)


def sample_id(*parts) -> str:
    return "|".join(str(p) for p in parts)


# ---------------------------------------------------------------- contract
RL_CODE_VERSION = "rl_phase1_v1"


def build_contract(git_commit: str | None = None, git_dirty: bool = False,
                   extra: dict | None = None) -> dict:
    c = {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "engine_version": ENGINE_VERSION,
        "observation_version": OBSERVATION_VERSION,
        "action_version": ACTION_VERSION,
        "action_profile": ACTION_PROFILE,
        "feature_profile": FEATURE_PROFILE,
        "data_version": DATA_VERSION,
        "sim_label_version": SIM_LABEL_VERSION,
        "rl_code_version": RL_CODE_VERSION,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "profile_verbs": list(PROFILE_VERBS),
        "noop_reason_codes": sorted(NOOP_REASON_CODES),
        "max_plan_actions": MAX_PLAN_ACTIONS,
        "max_units_pad": MAX_UNITS_PAD,
        "exogenous_verbs": ["gift", "reinforce"],
        "label_target_version": "wdl_damage_diff_v1",
        "notes": [
            "未实现指挥官技能按'执行但无效果'处理(NOOP_REASON_CODES + fidelity flag)",
            "V_sim/V_real 标签严格路由到各自的 domain head",
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
    """Return incompatibilities between the frozen contract and live code."""
    bad = []
    if c.get("schema_version") != SCHEMA_VERSION:
        bad.append("schema_version %s != %s" % (c.get("schema_version"),
                                                SCHEMA_VERSION))
    if c.get("ruleset_version") != RULESET_VERSION:
        bad.append("ruleset_version mismatch")
    if c.get("engine_version") != ENGINE_VERSION:
        bad.append("engine_version mismatch")
    for key, cur in (("contract_version", CONTRACT_VERSION),
                     ("observation_version", OBSERVATION_VERSION),
                     ("action_version", ACTION_VERSION),
                     ("feature_profile", FEATURE_PROFILE),
                     ("sim_label_version", SIM_LABEL_VERSION)):
        if c.get(key) != cur:
            bad.append("%s %s != %s" % (key, c.get(key), cur))
    return bad
