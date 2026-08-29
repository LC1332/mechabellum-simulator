# Round-scoped deploy rules (step4 任务书 §2): the SINGLE rule source for
# the per-round buy limit, the per-round manual unlock quota and the
# cross-round movement permission.
#
# deploy_transition, the env legal mask, the GameView and the tests all read
# these functions — nobody keeps a second constant table. The three axes are
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


# ---------------------------------------------------- manual unlock quota
# 爬虫动力学与伤害标定修正任务书 (2026-08-29) T11.1, user-frozen rule:
#   每个玩家每个回合最多成功执行 1 次主动 UnlockUnit。
# Only a SUCCESS that turns a locked mech into unlocked consumes the quota:
# already-unlocked queries, failed actions, insufficient supply and
# undone actions (the normalizer folds Undo BEFORE the executor ever sees
# the stream) never permanently occupy it. A 0-cost manual unlock still
# consumes the quota — the limit is on COUNT, not on spend. Quota is per
# player, per round, independent for both sides; advance_round resets it.
# Auto unlocks (unit experts / unit-grant reinforcements) never consume it.
BASE_MANUAL_UNLOCK_LIMIT = 1


@dataclass(frozen=True)
class UnlockLimitQuote:
    base: int                         # 1
    used: int                         # manual_unlocks_this_round
    remaining: int

    @property
    def breakdown(self) -> str:
        return "manual unlock %d/%d used" % (self.used, self.base)


def unlock_limit_quote(player: PlayerState) -> UnlockLimitQuote:
    """Per-round manual-unlock quota of one player (the only truth source)."""
    used = int(getattr(player, "manual_unlocks_this_round", 0) or 0)
    return UnlockLimitQuote(base=BASE_MANUAL_UNLOCK_LIMIT, used=used,
                            remaining=max(0, BASE_MANUAL_UNLOCK_LIMIT - used))


# ------------------------------------------------- auto unlock (no quota)
# T11.3: the EXPLICIT unit-expert registry. Category experts (巨型专家 20005
# with 10 unitIds, 空军专家 20021 with 7 unitIds, target "Air") must NEVER be
# generalized into batch unlocks — only these six single-mech officers
# (gamedata target "Custom", exactly one unitId) auto-unlock their mech.
# The mech id and the activeRound come from gamedata, not from this table;
# activeRound matches the corpus-verified delayed-gift timing
# (normalize.GIFT_OFFICERS). Q11 (unlock immediately on selection vs at
# activeRound) stays oracle-pending: until then the engine unlocks at the
# activeRound round start, the same timing as the gift spawn evidence.
# 先知专家 20037 has NO modeled gift spawn yet — it still auto-unlocks mech
# 26 at its activeRound (the user-frozen rule covers the unlock, not the
# spawn; the spawn stays unmodeled and never fabricated).
UNIT_EXPERT_OFFICERS = frozenset({20029, 20033, 20036, 20037, 20038, 20039})

AUTO_UNLOCK_EXPERT_TAG = "AUTO_UNLOCK_EXPERT:%d"      # provenance/receipt tag
AUTO_UNLOCK_REINFORCEMENT_TAG = "AUTO_UNLOCK_REINFORCEMENT:%d"


def expert_auto_unlock_mechs(officers, gd) -> tuple:
    """(mech_id, officer_id, active_round) triples owed by HELD unit experts.

    Pure over (officers, gamedata); used by advance_round (round-start
    application at the expert's activeRound), the GIFT_UNIT handler and the
    reinforcement handler (immediate application on acquisition). Idempotent
    by construction at the call sites (already-unlocked mechs are skipped)."""
    out = []
    for o in officers or ():
        od = gd.officers.get(int(o)) if gd is not None else None
        if od is None or int(od.id) not in UNIT_EXPERT_OFFICERS:
            continue
        if len(od.unit_ids) != 1:
            continue          # defense: registry requires a single mech
        mech = next(iter(od.unit_ids))
        out.append((int(mech), int(od.id), int(od.active_round or 0)))
    return tuple(sorted(out))


def pending_expert_auto_unlocks(officers, gd, round_no) -> tuple:
    """Subset of expert_auto_unlock_mechs whose activeRound has arrived
    (activeRound <= round_no). Round-start view used by advance_round."""
    return tuple(t for t in expert_auto_unlock_mechs(officers, gd)
                 if t[2] > 0 and t[2] <= int(round_no))


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
