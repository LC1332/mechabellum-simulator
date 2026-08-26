# Typed, immutable transition state model (transition v0).
#
# Semantics frozen by corpus probes (information/transition实现任务书.md + the
# probes behind it), 2026-08-26:
#   - level is 1-based here; replay XML Level is 0-based (adapter does +1).
#   - exp is the cumulative per-unit experience (replay `Exp`), int.
#   - UpgradeUnit consumes upgrade_at[next_level] exp and costs baseMoney//2.
#   - money is the replay `supply` field (income is added during the deploy
#     phase, AFTER the pre-deploy snapshot; see economy.IncomePolicy).
#   - hp is the replay `reactorCore`; settlement subtracts the opponent's
#     FightReport.Score (oracle mode) or pysim survivor value (pysim mode).
#   - dead units stay in `units` across rounds (they leave only via the sell
#     skill 900001); survival per round is battle output, not state.
#   - replay_index is the game's per-player unit Index (provenance only).
import os
from dataclasses import dataclass, field
from enum import Enum

SCHEMA_VERSION = "transition-v0.3"
RULESET_VERSION = "normal_1v1_replay_v0"
ENGINE_VERSION = "pysim-step29"


class Phase(str, Enum):
    DEPLOYMENT = "deployment"
    PRE_BATTLE = "pre_battle"
    SETTLEMENT = "settlement"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class UnitCard:
    entity_id: int
    mech_id: int
    level: int                 # canonical 1-based (replay XML Level + 1)
    exp: int                   # cumulative exp; upgrade consumes thresholds
    x: float
    y: float
    is_rotate: bool = False
    equipment_id: int = 0
    sell_supply: int = 0
    round_count: int = 0
    replay_index: int | None = None   # game unit Index, provenance only


@dataclass(frozen=True)
class PlayerState:
    hp: int                          # reactorCore
    max_hp: int
    supply: int                      # money at pre-deploy snapshot
    pre_round_fight_result: str | None   # "Win"/"Lose"/"Deuce" of the PREVIOUS fight
    units: tuple[UnitCard, ...]
    unlocked_mechs: frozenset[int]
    tech_map: tuple[tuple[int, tuple[int, ...]], ...]   # mech -> active tech ids
    officers: tuple[int, ...] = ()           # pass-through (battle side consumes)
    blueprints: tuple[int, ...] = ()         # pass-through research state
    commander_skills_raw: tuple[tuple[str, ...], ...] = ()   # (index,id,isActive,cd) as str
    tower_strengthen: tuple[int, int] = (0, 0)
    constructions_raw: tuple[tuple[str, ...], ...] = ()      # (index,id,x,y) as str
    bought_this_round: int = 0       # buy counter (BuyCount shop field)
    # audit-game v1 round-scoped fields (reset by advance_round):
    tower_mods_raw: tuple = ()       # ActiveEnergyTowerSkill ids this round (5/6)
    devices_raw: tuple = ()          # ReleaseContraption (cid,x,y) this round
    skill_events_raw: tuple = ()     # ReleaseCommanderSkill (sid,x,y) this round


@dataclass(frozen=True)
class EnvironmentState:
    schema_version: str
    ruleset_version: str
    engine_version: str
    round: int                                # replay round number (0-based like the XML)
    phase: Phase
    players: tuple[PlayerState, PlayerState]
    finished_deploy: tuple[bool, bool] = (False, False)
    next_entity_id: int = 1
    terminal_reason: str | None = None
    provenance: tuple[tuple[str, str], ...] = ()   # (key, value) provenance pairs

    # ----- helpers -----
    def player(self, idx: int) -> PlayerState:
        return self.players[idx]


# ---------------------------------------------------------------- actions
class ActionKind(str, Enum):
    GIFT_UNIT = "gift_unit"           # opening-team delayed gift (free spawn)
    CHOOSE_REINFORCE = "choose_reinforce"   # item pick at round start (cost + grant)
    UNLOCK_UNIT = "unlock_unit"
    BUY_UNIT = "buy_unit"
    UPGRADE_UNIT = "upgrade_unit"
    BUY_TECH = "buy_tech"
    MOVE_UNIT = "move_unit"
    SELL_UNIT = "sell_unit"                 # skill 900001 recycle
    END_DEPLOY = "end_deploy"
    RAW_UNSUPPORTED = "raw_unsupported"     # faithful marker for v0-unsupported raw types


@dataclass(frozen=True)
class EntityRef:
    """Action-local unit reference.

    handle   - index into the current plan-external observation order; for
               replay-driven plans this is the game unit Index when known.
    new_ref  - plan-local reference to a unit bought earlier in this plan.
    Exactly one of them is set for unit-targeting actions.
    """
    handle: int | None = None
    new_ref: int | None = None


@dataclass(frozen=True)
class GiftArgs:
    mech_id: int
    game_index: int | None = None   # game unit Index burned by this gift


@dataclass(frozen=True)
class BuyArgs:
    mech_id: int
    x: float
    y: float
    new_ref: int = 0            # plan-local id of the bought unit
    is_rotate: bool = False
    game_index: int | None = None   # game unit Index burned by this buy


@dataclass(frozen=True)
class MoveArgs:
    ref: EntityRef
    x: float
    y: float
    is_rotate: bool | None = None   # None = keep current orientation


@dataclass(frozen=True)
class UpgradeArgs:
    ref: EntityRef


@dataclass(frozen=True)
class UnlockArgs:
    mech_id: int


@dataclass(frozen=True)
class TechArgs:
    mech_id: int
    tech_id: int


@dataclass(frozen=True)
class ChooseReinforceArgs:
    item_id: int                # 0 = skip the offer
    grant_specs: tuple = ()     # ((new_ref, game_index), ...) granted units


@dataclass(frozen=True)
class SellArgs:
    ref: EntityRef


@dataclass(frozen=True)
class UnsupportedArgs:
    raw_type: str
    raw: tuple[tuple[str, object], ...]   # stable serialization of the raw record


ActionArgs = (BuyArgs | MoveArgs | UpgradeArgs | UnlockArgs | TechArgs
              | ChooseReinforceArgs | SellArgs | GiftArgs | UnsupportedArgs)


@dataclass(frozen=True)
class CanonicalAction:
    kind: ActionKind
    args: ActionArgs
    raw_index: int = -1         # position in the raw action log (audit)


@dataclass(frozen=True)
class CanonicalActionPlan:
    player: int
    actions: tuple[CanonicalAction, ...] = ()


# ---------------------------------------------------------------- receipts
@dataclass(frozen=True)
class ActionReceipt:
    action_index: int
    kind: str
    accepted: bool
    reason_code: str
    resource_delta: int = 0            # supply change (negative = spent)
    created_entity_id: int | None = None
    removed_entity_id: int | None = None
    changed_paths: tuple[str, ...] = ()
    state_digest_after: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SupplyEntry:
    reason: str                        # ledger reason tag, e.g. buy:10 / income / sell:12
    amount: int
    action_index: int | None = None
    entity_id: int | None = None


@dataclass(frozen=True)
class SupplyLedger:
    supply_before: int
    entries: tuple[SupplyEntry, ...] = ()

    @property
    def supply_after(self) -> int:
        return self.supply_before + sum(e.amount for e in self.entries)


# ---------------------------------------------------------------- battle
@dataclass(frozen=True)
class CardBattleResult:
    entity_id: int
    exp_before: int
    exp_delta: int
    exp_after: int
    damage: float
    kills: int
    survived: bool
    level_after: int


@dataclass(frozen=True)
class BattleOutcome:
    battle_seed: int
    winner: int                        # 0 / 1 / -1
    score_by_team: tuple[int, int]     # survivor value per team (pysim_survivor_value_v1)
    damage_to_player: tuple[int, int]  # hp deducted from each player this round
    cards: tuple[CardBattleResult, ...]
    end_time: float
    engine_version: str

    @classmethod
    def from_fight_report(cls, reports, seed=-1,
                          engine_version="fight_report_oracle"):
        """Oracle outcome from the replay's own FightReports.

        damage_to_player[p] = Score of the opponent's report (verified exact on
        13,222 aligned player rounds, information/回放格式确认.md)."""
        # reports may carry more than 2 entries (team modes); 1v1 uses [0],[1]
        if len(reports) < 2:
            raise ValueError("fight report oracle needs 2 reports")
        d0 = int(reports[1].get("score", 0))
        d1 = int(reports[0].get("score", 0))
        return cls(battle_seed=seed, winner=-1, score_by_team=(d1, d0),
                   damage_to_player=(d0, d1), cards=(), end_time=0.0,
                   engine_version=engine_version)


@dataclass(frozen=True)
class StepResult:
    state: EnvironmentState
    reward: tuple[float, float]
    done: bool
    deploy_receipts: tuple[tuple[ActionReceipt, ...], ...]
    ledgers: tuple[SupplyLedger, SupplyLedger]
    battle_outcome: BattleOutcome | None
    state_digest: str
    info: dict = field(default_factory=dict)
