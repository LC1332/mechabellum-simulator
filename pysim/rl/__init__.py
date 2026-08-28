# RL Phase 1 package (task 第一阶段强化学习任务书-2026-08-27).
RL_VERSION = "rl_phase1_v1"

from .contracts import (CONTRACT_VERSION, OBSERVATION_VERSION, ACTION_VERSION,
                        DATA_VERSION, FEATURE_PROFILE, ACTION_PROFILE,
                        SIM_LABEL_VERSION, PROFILE_VERBS, VERB_TO_KIND,
                        KIND_TO_VERB, NOOP_REASON_CODES, MAX_PLAN_ACTIONS,
                        MAX_UNITS_PAD, build_contract, write_contract,
                        load_contract, check_contract, stable_digest,
                        derive_seed, sample_id)
