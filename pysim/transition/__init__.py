# transition v0 public surface.
from .model import (SCHEMA_VERSION, RULESET_VERSION, ENGINE_VERSION,
                    Phase, UnitCard, PlayerState, EnvironmentState,
                    ActionKind, CanonicalAction, CanonicalActionPlan,
                    EntityRef, ActionReceipt, SupplyLedger, SupplyEntry,
                    BattleOutcome, CardBattleResult, StepResult,
                    BuyArgs, MoveArgs, UpgradeArgs, UnlockArgs, TechArgs,
                    SellArgs, GiftArgs, ChooseReinforceArgs, UnsupportedArgs)
from .errors import TransitionError
from .state_tools import (canonical_dict, state_digest, diff_state,
                          assert_state_invariants, state_to_dict,
                          state_from_dict, copy_state)
from .economy import (Economy, InjectedIncome, FixedIncome, Income200r,
                      REINFORCE_SKIP_BONUS)
from .normalize import Normalizer
from .replay_adapter import ReplayAdapter
from .canonicalize import canonicalize_plan
from .deploy import deploy_transition
from .battle_adapter import battle_from_state, run_battle
from .settlement import settle_transition, advance_round
from .env import TransitionEnv
from . import capability
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
    "diff_state", "assert_state_invariants", "state_to_dict",
    "state_from_dict", "copy_state", "Economy", "InjectedIncome",
    "FixedIncome", "Income200r", "REINFORCE_SKIP_BONUS", "Normalizer",
    "ReplayAdapter", "canonicalize_plan", "deploy_transition",
    "battle_from_state", "run_battle", "settle_transition", "advance_round",
    "TransitionEnv", "capability", "opening", "load_catalog", "package_of",
    "generate_offers", "generator_seed", "build_initial_state",
    "player_state_from_package", "recorded_team_of", "OpeningError",
]
