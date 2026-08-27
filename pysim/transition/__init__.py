# transition v0 public surface.
from .model import (SCHEMA_VERSION, RULESET_VERSION, ENGINE_VERSION,
                    Phase, UnitCard, PlayerState, EnvironmentState,
                    ActionKind, CanonicalAction, CanonicalActionPlan,
                    EntityRef, ActionReceipt, SupplyLedger, SupplyEntry,
                    BattleOutcome, CardBattleResult, StepResult,
                    BuyArgs, MoveArgs, UpgradeArgs, UnlockArgs, TechArgs,
                    SellArgs, GiftArgs, ChooseReinforceArgs, UnsupportedArgs,
                    ReleaseCommanderSkillArgs, UseEquipmentArgs,
                    ActivateEnergyTowerSkillArgs, SurrenderArgs)
from .errors import TransitionError
from .rules import (BASE_BUY_LIMIT, BuyLimitQuote, buy_limit_quote,
                    MovePermission, movement_permission, MOBILITY_TECHS,
                    DEPLOYMENT_MODULE_EQUIPMENT, REDEPLOY_SKILL_ID)
from .state_tools import (canonical_dict, state_digest, diff_state,
                          assert_state_invariants, state_to_dict,
                          state_from_dict, copy_state)
from .economy import (Economy, InjectedIncome, FixedIncome, Income200r,
                      REINFORCE_SKIP_BONUS, PriceQuote, PriceModifier)
from .normalize import Normalizer
from .replay_adapter import ReplayAdapter
from .canonicalize import canonicalize_plan
from .deploy import deploy_transition
from .battle_adapter import battle_from_state, run_battle
from .settlement import settle_transition, advance_round, tick_skill_cooldowns
from .env import TransitionEnv
from . import capability
from . import equipment
from .equipment import (EQUIPMENT_DEFS, EquipmentDef, equipment_target_ok,
                        giant_mechs, round_officer_skills,
                        round_officer_equipment, top_up_skill_slots)
from . import opening
from .opening import (load_catalog, package_of, generate_offers,
                      generator_seed, build_initial_state, player_state_from_package,
                      recorded_team_of, OpeningError)

__all__ = [
    "SCHEMA_VERSION", "RULESET_VERSION", "ENGINE_VERSION", "Phase",
    "UnitCard", "PlayerState", "EnvironmentState", "ActionKind",
    "CanonicalAction", "CanonicalActionPlan", "EntityRef", "ActionReceipt",
    "SupplyLedger", "SupplyEntry", "BattleOutcome", "CardBattleResult",
    "StepResult", "TransitionError", "canonical_dict", "state_digest",
    "BuyArgs", "MoveArgs", "UpgradeArgs", "UnlockArgs", "TechArgs",
    "SellArgs", "GiftArgs", "ChooseReinforceArgs", "UnsupportedArgs",
    "ReleaseCommanderSkillArgs", "UseEquipmentArgs",
    "ActivateEnergyTowerSkillArgs", "SurrenderArgs",
    "BASE_BUY_LIMIT", "BuyLimitQuote", "buy_limit_quote", "MovePermission",
    "movement_permission", "MOBILITY_TECHS", "DEPLOYMENT_MODULE_EQUIPMENT",
    "REDEPLOY_SKILL_ID",
    "diff_state", "assert_state_invariants", "state_to_dict",
    "state_from_dict", "copy_state", "Economy", "InjectedIncome",
    "FixedIncome", "Income200r", "REINFORCE_SKIP_BONUS", "Normalizer",
    "PriceQuote", "PriceModifier",
    "ReplayAdapter", "canonicalize_plan", "deploy_transition",
    "battle_from_state", "run_battle", "settle_transition", "advance_round",
    "tick_skill_cooldowns",
    "TransitionEnv", "capability", "equipment", "EQUIPMENT_DEFS",
    "EquipmentDef", "equipment_target_ok", "giant_mechs",
    "round_officer_skills", "round_officer_equipment",
    "top_up_skill_slots", "opening", "load_catalog", "package_of",
    "generate_offers", "generator_seed", "build_initial_state",
    "player_state_from_package", "recorded_team_of", "OpeningError",
]
