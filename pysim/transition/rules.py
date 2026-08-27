# Round-scoped deploy rules (step4 任务书 §2): the SINGLE rule source for
# the per-round buy limit and the cross-round movement permission.
#
# deploy_transition, the env legal mask, the GameView and the tests all read
# these functions — nobody keeps a second constant table. The two axes are
# pure functions over PlayerState (or the deploy working view), so a
# mid-round activation (blueprint 2, 部署模块 binding, 高速引擎 tech buy,
# redeploy release) is visible to the very next action of the same round.
from dataclasses import dataclass

from .model import PlayerState, UnitCard

# ------------------------------------------------------------- buy limit
# step4 任务书 §1.1 + FINAL user ruling (2026-08-27): the default per-round
# buy limit is 2 — 批量征召 (energy-tower skill 3, cost 50, the SAME round,
# 前置 action) adds +1 per purchase this round; every held 额外部署位
# expert/buff (10004) adds +1 per copy, permanently. Reinforcement GRANTS
# never consume the quota (only paid BUY_UNIT does).
#
# Corpus wall evidence (local_data, 16,512 buy-rounds): ZERO rounds exceed
# 2 + tower3-clicks-before-the-buy + 10004 copies — tower skill 3 appears
# between the 2nd and 3rd buy in 6,953 rounds (the 前置 pattern). The replays
# are v2119-v2207; earlier probes mis-attributed the +1 to blueprint 2.
BASE_BUY_LIMIT = 2
BLUEPRINT_MASS_RECRUIT = 2         # legacy name (blueprint channel research)
EXTRA_DEPLOY_OFFICER = 10004       # 额外部署位: +1 buy limit per held copy
# round-scoped sentinels stored in PlayerState.blueprints_round (persist
# across per-action deploy calls within the round; reset by advance_round):
# blueprint research keeps ids 1/2/3, the energy-tower channel uses these
TOWER_LOAN_SENTINEL = 101          # tower 1 快速补给 (+200/-300 loan)
TOWER_MASS_SENTINEL = 102          # tower 3 批量征召 (buy limit +1)
TOWER_ELITE_SENTINEL = 103         # tower 4 精英征召 (buy level +1)


@dataclass(frozen=True)
class BuyLimitQuote:
    base: int                         # 2
    blueprint_bonus: int              # 本回合批量征召(能量塔技能3)次数
    officer_bonus: int                # 10004 持有份数
    used: int                         # bought_this_round
    limit: int
    remaining: int

    @property
    def breakdown(self) -> str:
        return "%d+%d+%d used %d" % (self.base, self.blueprint_bonus,
                                     self.officer_bonus, self.used)


def _mass_recruit_bonus(blueprints_round) -> int:
    return sum(1 for b in (blueprints_round or ())
               if int(b) == TOWER_MASS_SENTINEL)


def buy_limit_quote(player: PlayerState) -> BuyLimitQuote:
    """Per-round purchase quota of one player (the only truth source)."""
    bp = _mass_recruit_bonus(getattr(player, "blueprints_round", ()))
    off = sum(1 for o in (player.officers or ())
              if int(o) == EXTRA_DEPLOY_OFFICER)
    used = int(player.bought_this_round or 0)
    limit = BASE_BUY_LIMIT + bp + off
    return BuyLimitQuote(base=BASE_BUY_LIMIT, blueprint_bonus=bp,
                         officer_bonus=off, used=used, limit=limit,
                         remaining=max(0, limit - used))


# ------------------------------------------------------ movement permission
# step4 任务书 §1.2 + QA rulings: units that fought last round are locked by
# default. A unit may move this round when ANY of:
#   NEW_THIS_ROUND      bought / granted / gifted this round (round-1 opening
#                       units count: they have not fought a battle yet)
#   DEPLOYMENT_MODULE   部署模块 13040001 is bound to the unit
#   MOBILITY_TECH       the unit's mech owns its 高速引擎 tech
#                       (兵蜂 1606 / 霸主 1611 / 凤凰 1616 / 深渊 1629 —
#                       QA#2: every 高速引擎 has the redeploy-right effect)
#   REDEPLOY_SKILL      selected by a 再部署 1000001 release this round
DEPLOYMENT_MODULE_EQUIPMENT = 13040001
REDEPLOY_SKILL_ID = 1000001
MOBILITY_TECHS = {1606: 6, 1611: 11, 1616: 16, 1629: 29}   # tech -> mech

MOVE_REASON_NEW = "NEW_THIS_ROUND"
MOVE_REASON_MODULE = "DEPLOYMENT_MODULE"
MOVE_REASON_TECH = "MOBILITY_TECH"
MOVE_REASON_REDEPLOY = "REDEPLOY_SKILL"


@dataclass(frozen=True)
class MovePermission:
    allowed: bool
    reasons: tuple[str, ...]


def movement_reasons(unit: UnitCard, spawned, redeployed, techs_of_mech):
    """Core predicate shared by the PlayerState view and the deploy working
    view (both pass their own spawned/redeployed id sets and mech->techs)."""
    reasons = []
    if int(unit.entity_id) in spawned:
        reasons.append(MOVE_REASON_NEW)
    if int(unit.equipment_id or 0) == DEPLOYMENT_MODULE_EQUIPMENT:
        reasons.append(MOVE_REASON_MODULE)
    techs = techs_of_mech.get(int(unit.mech_id), ())
    if any(int(t) in MOBILITY_TECHS for t in techs or ()):
        reasons.append(MOVE_REASON_TECH)
    if int(unit.entity_id) in redeployed:
        reasons.append(MOVE_REASON_REDEPLOY)
    return tuple(reasons)


def movement_permission(player: PlayerState, unit: UnitCard) -> MovePermission:
    """Public view: is `unit` movable in the current deploy phase, and why."""
    spawned = {int(e) for e in
               (getattr(player, "spawned_this_round", ()) or ())}
    redeployed = {int(e) for e in
                  (getattr(player, "redeployed_this_round", ()) or ())}
    techs = {int(m): tuple(t) for m, t in (player.tech_map or ())}
    reasons = movement_reasons(unit, spawned, redeployed, techs)
    return MovePermission(allowed=bool(reasons), reasons=reasons)
