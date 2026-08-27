# Deployment transition: execute canonical action plans on an EnvironmentState.
#
# Hard rules (任务书 §1.3): rejected actions never mutate state; supply always
# equals before + ledger sum; deploy never touches hp/exp-settlement fields.
# Round-scoped working state (undo already folded by the canonicalizer):
#   - buy_level_bonus from ActiveBlueprint(3) 精英征召 (feature-flagged)
#   - grant/buy units become permanent units of the player
from dataclasses import dataclass, field

from . import errors
from .canonicalize import FORBIDDEN_RAW_TYPES
from .model import (ActionKind, CanonicalActionPlan, EnvironmentState,
                    PlayerState, UnitCard, Phase, ActionReceipt, EntityRef,
                    GiftArgs, ActivateEnergyTowerSkillArgs)
from .rules import (BASE_BUY_LIMIT, EXTRA_DEPLOY_OFFICER,
                    DEPLOYMENT_MODULE_EQUIPMENT, REDEPLOY_SKILL_ID,
                    BuyLimitQuote, buy_limit_quote, movement_reasons,
                    MOVE_REASON_NEW, MOVE_REASON_MODULE, MOVE_REASON_TECH,
                    MOVE_REASON_REDEPLOY, TOWER_LOAN_SENTINEL,
                    TOWER_MASS_SENTINEL, TOWER_ELITE_SENTINEL)
from . import rules
from .state_tools import state_digest, with_player, assert_state_invariants
from .economy import Economy, LedgerBuilder

MAP_X, MAP_Y = 350.0, 300.0

# v0 feature flags (unknown rules stay OFF and land in the unsupported bucket
# instead of being guessed in code):
FEATURES = {
    "elite_officer_bonus": True,    # 精英专家 20032 / 精英卡 -> +1 buy level
                                    # (corpus: blueprint 2/3 do NOT boost)
    "elite_officer_charge": True,   # ...and charges one upgrade price (Q11)
    "upgrade_exp_gate": True,       # veterans need a full exp bar to upgrade
    "manufacturing_discount": True, # 20022/20023 高效制造 -50 per matching buy
    "skill_slot_lifecycle": True,   # battlefield M1: releases consume slots
                                    # + set the corpus-verified cooldown
    "extra_deploy_slots": True,     # officer 10004 额外部署位: +1 buy limit
                                    # per held copy (可重复, survey desc)
    "equipment_upgrade_discount": True,  # 强化模块 13030004: upgrade -100
}

# passive deploy-action supply costs (corpus-attributed 2026-08-26: r1
# window algebra bp2-only -> 0 (1251 rounds), bp4/bp5 -> +100 each;
# tower -> +100; con10001 -> +100, con20001 -> +50; prices_v1_passive).
# step4 FINAL ruling (user, 2026-08-27): the five commend-center items
# (快速补给/批量征召/精英征召/强化瞄准/高速移动) are ONE-SHOT PER ROUND
# purchases via ActiveEnergyTowerSkill — NOT blueprints. Blueprint 1/2/3 are
# the commander-skill RESEARCH unlocks (one-time per game, slot granted at
# the NEXT round start; corpus unlock correlation 98.8%/100%/0-unresearched).
# The step3 r1-window algebra that read bp1=loan/bp2=free was confounded by
# the tower channel and is retired here.
BLUEPRINT_COSTS = {1: 150, 2: 100, 3: 100, 4: 100, 5: 100, 401: 300, 501: 300}
# energy-tower round skills (user ruling 2026-08-27): id table
# (corpus-anchored): 1 快速补给 0 (+200 now / -300 next round income),
# 3 批量征召 50 (this round's buy limit +1, 前置 action), 4 精英征召 100
# (buys AFTER it spawn at level+1, order matters), 5 强化瞄准 100
# (range +15), 6 高速移动 50 (speed +3). id 2 never appears in the corpus.
# Buy-limit wall evidence: base 2 + tower3 + 10004 => 0 violations across
# 16,512 corpus buy-rounds.
TOWER_SKILL_COSTS = {1: 0, 3: 50, 4: 100, 5: 100, 6: 50}
# round-scoped sentinels live in rules.py (single source: the quote reads
# them); re-exported here for the deploy handlers
TOWER_LOAN_SENTINEL = TOWER_LOAN_SENTINEL
TOWER_MASS_SENTINEL = TOWER_MASS_SENTINEL
TOWER_ELITE_SENTINEL = TOWER_ELITE_SENTINEL
CONTRAPTION_COSTS = {"10001": 100, "20001": 50, "30001": 100}
TOWER_STRENGTHEN_COST = 100
MANUFACTURING_OFFICERS = {20022: "giant", 20023: "small"}
# blueprint semantics (user ruling 2026-08-27 + corpus unlock probes):
#   1 黏油弹   cost 150 -> research: commander skill 400002 next round
#   2 战地回收 cost 100 -> research: commander skill 900001 next round
#   3 移动信标 cost 100 -> research: commander skill 1500001 next round
#   4/5 进攻/防御强化I  cost 100 -> permanent officer 20310/20300
#   401/501 II tiers    cost 300 -> officer 20311/20301, replaces the I tier
# (unlock correlation: bp1->400002 470/470 researched-seen, never without;
# bp2->900001 1837/1860; bp3->1500001 548; slot lag = +1 round ~100%)
BLUEPRINT_SKILLS = {1: 400002, 2: 900001, 3: 1500001}
BLUEPRINT_OFFICERS = {4: 20310, 5: 20300, 401: 20311, 501: 20301}
# step4 任务书 §1.1 (user ruling 2026-08-27): base buy limit single-sourced
# in rules.py — re-exported here for older importers
BASE_BUY_LIMIT = BASE_BUY_LIMIT
EXTRA_DEPLOY_OFFICER = EXTRA_DEPLOY_OFFICER     # 额外部署位: +1 per copy
UPGRADE_DISCOUNT_EQUIPMENT = 13030004   # 强化模块: upgrade cost -100
UPGRADE_DISCOUNT_AMOUNT = 100


@dataclass
class DeployResult:
    state: EnvironmentState                 # post-deploy (PRE_BATTLE when both finished)
    receipts: tuple                         # tuple[tuple[ActionReceipt, ...], ...] per player
    ledgers: tuple                          # tuple[SupplyLedger, SupplyLedger]
    unsupported_types: tuple = ()           # raw types recorded, not executed
    notes: tuple = ()


@dataclass
class _RoundCtx:
    """Mutable per-player working view for one deploy phase."""
    units: list
    supply: int
    unlocked: set
    tech_bought: dict          # mech -> active (folded) tech list
    officers: list
    entity_seq: object
    new_ref_entity: dict       # plan-local new_ref -> entity_id
    finished: bool
    buy_count: int
    ledger: LedgerBuilder
    commander_skills: list = field(default_factory=list)  # (index,id,active,cd) skill inventory
    spawned_ids: set = field(default_factory=set)   # units created this round
    redeployed_ids: set = field(default_factory=set)  # 再部署 unlocks this round
    buy_level_bonus: int = 0
    buy_limit_bonus: int = 0        # 批量征召 (blueprint 2): +1 this round
    digests: list = field(default_factory=list)
    tower_strengthen: tuple = (0, 0)   # (left, right) core tower levels
    blueprints: list = field(default_factory=list)   # activated blueprint ids
    blueprints_round: list = field(default_factory=list)  # this round only
    tower_mods: list = field(default_factory=list)   # ActiveEnergyTowerSkill ids this round
    devices: list = field(default_factory=list)      # (cid,x,y) contraption releases
    skill_events: list = field(default_factory=list)  # (sid,x,y) commander releases
    equipment: list = field(default_factory=list)    # unequipped stock (multiset)
    surrendered: bool = False        # battlefield M1: typed GiveUp terminal


def deploy_transition(state: EnvironmentState,
                      plans: tuple[CanonicalActionPlan, ...],
                      eco: Economy,
                      strict: bool = False) -> DeployResult:
    """Apply both players' plans; returns receipts + pre-battle state."""
    if state.phase is not Phase.DEPLOYMENT:
        raise errors.TransitionError(errors.WRONG_PHASE,
                                     "deploy needs DEPLOYMENT phase")
    # v0.1 defense: the norm stream must never carry un-folded undo records
    for plan in plans:
        for act in plan.actions:
            if act.kind is ActionKind.RAW_UNSUPPORTED and \
                    act.args.raw_type in FORBIDDEN_RAW_TYPES:
                raise errors.TransitionError(
                    "UNDO_IN_NORM_STREAM",
                    "player %d action %d carries raw %s: normalize first"
                    % (plan.player, act.raw_index, act.args.raw_type))
    all_receipts = []
    ledgers = []
    states = list(state.players)
    next_entity_id = state.next_entity_id
    unsupported = []
    notes = []

    def entity_seq_fn():
        nonlocal next_entity_id
        def next_entity():
            nonlocal next_entity_id
            v = next_entity_id
            next_entity_id += 1
            return v
        return next_entity

    ctxs = []
    for side in (0, 1):
        p = states[side]
        ctx = _RoundCtx(
            units=list(p.units), supply=p.supply, unlocked=set(p.unlocked_mechs),
            tech_bought={m: list(t) for m, t in p.tech_map},
            officers=list(p.officers), entity_seq=entity_seq_fn(),
            commander_skills=[tuple(str(x) for x in e)
                              for e in p.commander_skills_raw],
            new_ref_entity={}, finished=state.finished_deploy[side],
            buy_count=int(p.bought_this_round or 0),
            ledger=LedgerBuilder(p.supply),
            tower_strengthen=tuple(int(x) for x in (p.tower_strengthen
                                                    or (0, 0))[:2]),
            blueprints=list(p.blueprints),
            blueprints_round=list(getattr(p, "blueprints_round", ())
                                 or ()),
            tower_mods=list(p.tower_mods_raw or ()),
            devices=list(p.devices_raw or ()),
            skill_events=list(p.skill_events_raw or ()),
            equipment=list(p.equipment_inventory or ()),
            # battlefield M1: buys/grants of EARLIER deploy calls this round
            # stay flank-eligible across per-action invocations
            spawned_ids=set(int(e) for e in
                            (getattr(p, "spawned_this_round", ()) or ())),
            # step4 §2.2: 再部署 unlocks of earlier deploy calls this round
            redeployed_ids=set(int(e) for e in
                               (getattr(p, "redeployed_this_round", ())
                                or ())),
            buy_limit_bonus=sum(1 for b in
                                (getattr(p, "blueprints_round", ()) or ())
                                if int(b) == rules.TOWER_MASS_SENTINEL),
            buy_level_bonus=sum(1 for b in
                                (getattr(p, "blueprints_round", ()) or ())
                                if int(b) in (3, rules.TOWER_ELITE_SENTINEL)))
        if FEATURES["extra_deploy_slots"]:
            # 额外部署位 10004: +1 buy limit per held copy (可重复)
            ctx.buy_limit_bonus += sum(1 for o in ctx.officers
                                       if int(o) == EXTRA_DEPLOY_OFFICER)
        ctxs.append(ctx)

    for plan in plans:
        side = plan.player
        ctx = ctxs[side]
        receipts = []
        for i, act in enumerate(plan.actions):
            r = _apply(ctx, side, i, act, eco, state, unsupported, notes)
            receipts.append(r)
            if strict and not r.accepted and r.reason_code != errors.UNSUPPORTED_ACTION:
                raise errors.TransitionError(
                    r.reason_code,
                    "player %d action %d (%s): %s" % (side, i, r.kind, r.detail))
            if strict and r.reason_code == errors.UNSUPPORTED_ACTION \
                    and act.kind is not ActionKind.RAW_UNSUPPORTED:
                raise errors.TransitionError(r.reason_code, r.detail)
        # supply invariant per player
        spent = sum(e.amount for e in ctx.ledger.entries)
        assert ctx.supply == ctx.ledger.supply_before + spent, "ledger drift"
        all_receipts.append(tuple(receipts))
        states[side] = _freeze(ctx, states[side])

    surrendered = [side for side in (0, 1) if ctxs[side].surrendered]
    if surrendered:
        # battlefield M1: typed SURRENDER (GiveUp) is atomic-terminal — no
        # battle runs, the opponent wins; reward is decided by the env
        new_state = EnvironmentState(
            schema_version=state.schema_version,
            ruleset_version=state.ruleset_version,
            engine_version=state.engine_version, round=state.round,
            phase=Phase.TERMINAL, players=tuple(states),
            finished_deploy=(True, True),
            next_entity_id=next_entity_id,
            terminal_reason="surrender:player%d" % surrendered[0],
            provenance=state.provenance)
        assert_state_invariants(new_state)
        return DeployResult(state=new_state, receipts=tuple(all_receipts),
                            ledgers=tuple(c.ledger.build() for c in ctxs),
                            unsupported_types=tuple(sorted(set(unsupported))),
                            notes=tuple(notes))
    both_done = all(c.finished for c in ctxs)
    new_state = EnvironmentState(
        schema_version=state.schema_version, ruleset_version=state.ruleset_version,
        engine_version=state.engine_version, round=state.round,
        phase=Phase.PRE_BATTLE if both_done else Phase.DEPLOYMENT,
        players=tuple(states),
        finished_deploy=(ctxs[0].finished, ctxs[1].finished),
        next_entity_id=next_entity_id,
        terminal_reason=state.terminal_reason,
        provenance=state.provenance)
    assert_state_invariants(new_state)
    return DeployResult(state=new_state, receipts=tuple(all_receipts),
                        ledgers=tuple(c.ledger.build() for c in ctxs),
                        unsupported_types=tuple(sorted(set(unsupported))),
                        notes=tuple(notes))


# ---------------------------------------------------------------- internals
def _freeze(ctx: _RoundCtx, base: PlayerState) -> PlayerState:
    return PlayerState(
        hp=base.hp, max_hp=base.max_hp, supply=ctx.supply,
        pre_round_fight_result=base.pre_round_fight_result,
        units=tuple(sorted(ctx.units, key=lambda u: u.entity_id)),
        unlocked_mechs=frozenset(ctx.unlocked),
        tech_map=tuple(sorted((m, tuple(t)) for m, t in ctx.tech_bought.items())),
        officers=tuple(ctx.officers),
        blueprints=tuple(ctx.blueprints),
        blueprints_round=tuple(ctx.blueprints_round),
        commander_skills_raw=tuple(tuple(e) for e in ctx.commander_skills),
        tower_strengthen=tuple(ctx.tower_strengthen),
        constructions_raw=base.constructions_raw,
        bought_this_round=ctx.buy_count,
        equipment_inventory=tuple(sorted(ctx.equipment)),
        tower_mods_raw=tuple(ctx.tower_mods),
        devices_raw=tuple(ctx.devices),
        skill_events_raw=tuple(ctx.skill_events),
        spawned_this_round=tuple(sorted(ctx.spawned_ids)),
        redeployed_this_round=tuple(sorted(ctx.redeployed_ids)))


def _receipt(i, kind, ok, reason, detail="", **kw) -> ActionReceipt:
    return ActionReceipt(action_index=i, kind=kind, accepted=ok,
                         reason_code=reason, detail=detail, **kw)


def _find_unit(ctx, ref):
    """Resolve EntityRef -> (position_in_list, UnitCard) or (None, None).

    handle = the game unit Index (replay plans and env plans share the same
    space: every unit, bought or granted, gets a monotonic replay_index)."""
    if ref is None:
        return None, None
    if ref.new_ref is not None:
        eid = ctx.new_ref_entity.get(ref.new_ref)
        if eid is None:
            return None, None
        for j, u in enumerate(ctx.units):
            if u.entity_id == eid:
                return j, u
        return None, None
    if ref.handle is None:
        return None, None
    for j, u in enumerate(ctx.units):
        if u.replay_index == ref.handle:
            return j, u
    return None, None


def _elite_bonus(ctx, mech_id: int) -> int:
    """+1 buy level: officer 20032 (all mechs) and the unit-specific 精英
    strengthen cards (e.g. 30804 精英钢球 boosts ONLY mech 8; corpus:
    its 鬼鳐/爬虫 buys stayed level 1). Range checks on 3xxxx wrongly
    promoted 增程/改进型 cards in v0."""
    if not FEATURES["elite_officer_bonus"]:
        return 0
    officers = set(ctx.officers)
    if 20032 in officers:
        return 1
    return 1 if mech_id in officers_map_mechs(officers) else 0


_ELITE_CACHE = None


def _elite_mechs() -> dict:
    """officer_id -> boosted mech for 精英-named strengthen cards."""
    global _ELITE_CACHE
    if _ELITE_CACHE is None:
        import json as _json
        import os as _os
        from .economy import _ROOT
        raw = _json.load(open(_os.path.join(
            _ROOT, "information", "增援卡牌-回放全量信息.json"), encoding="utf8"))
        _gd = _json.load(open(_os.path.join(_ROOT, "data", "gamedata.json"),
                              encoding="utf8"))
        gd_names = {c.get("name"): int(c.get("mechID", 0))
                    for c in _gd["cards"].values()}
        table = {}
        for c in raw.get("cards", []):
            if c.get("类别") != "单位强化卡":
                continue
            name = str(c.get("名称") or "")
            if "精英" not in name:
                continue
            for cname, mid in sorted(gd_names.items(),
                                     key=lambda kv: -len(kv[0])):
                if cname and cname in name:
                    table[int(c["id"])] = mid
                    break
        _ELITE_CACHE = table
    return _ELITE_CACHE


def officers_map_mechs(officers) -> set:
    """Mechs boosted by the held unit-specific elite cards."""
    table = _elite_mechs()
    return {table[o] for o in officers if o in table}


def _is_giant(gd, mech_id: int) -> bool:
    """Giant units: big single entities (slot_size >= 30); squads and small
    vehicles sit at slot 6-20."""
    c = gd.cards.get(int(mech_id))
    return bool(c and c.slot_size >= 30)


def _buy_cost(ctx, eco: Economy, mech_id: int) -> int | None:
    """base + strengthen-card mods + elite charge (Q11) + manufacturing
    discounts (Q10); None when the base price is unknown."""
    base = eco.buy_price(mech_id)
    if base is None:
        return None
    cost = base + eco.buy_price_mod(mech_id, ctx.officers)
    if FEATURES["elite_officer_charge"] and _elite_bonus(ctx, mech_id):
        cost += eco.upgrade_price(mech_id) or 0
    if FEATURES["manufacturing_discount"]:
        for o, cls in MANUFACTURING_OFFICERS.items():
            if o in ctx.officers:
                if (cls == "giant") == _is_giant(eco.gd, mech_id):
                    cost -= 50
    return max(0, cost)


def _fold_tech(active: list, new_tech: int, prev_of: dict) -> list:
    """previousTechID chains: the higher tier replaces the lower one."""
    out = [t for t in active if prev_of.get(new_tech) != t or t == new_tech]
    if new_tech not in out:
        out.append(new_tech)
    # drop any tech made obsolete by the new one
    out = [t for t in out if prev_of.get(t) != new_tech or t == new_tech]
    return out


def _resolve_release_id(ctx, skill_id, skill_index):
    """Explicit non-zero id wins; else SkillIndex -> id via the current
    commander-skill inventory (step3 任务书 §5.2 resolution rule)."""
    if skill_id:
        try:
            return int(skill_id)
        except (TypeError, ValueError):
            return None
    if skill_index is None:
        return None
    for entry in ctx_officers(ctx):
        if entry and str(entry[0]) == str(skill_index):
            try:
                sid = int(entry[1])
                return sid if sid else None
            except (TypeError, ValueError):
                return None
    return None


def _release_target_kind(positions, unit_ref, construction_index) -> str:
    if construction_index is not None:
        return "construction"
    if unit_ref is not None:
        return "unit"
    if positions:
        return "position"
    return "unknown"


def _find_release_slot(ctx, sid, skill_index):
    """battlefield M1: locate the ACTIVE slot backing this release WITHOUT
    mutating anything. Returns (slot_position | None, error | None).

    Resolution: an explicit skill_index targets exactly that slot (it must
    be active and carry the id); a skill_id alone targets the FIRST active
    slot with that id. When the feature flag is off or no slot backs the
    release (granted-out-of-band ids in legacy fixtures), (None, None) -
    the release proceeds without consumption."""
    if not FEATURES["skill_slot_lifecycle"]:
        return None, None
    slots = ctx.commander_skills

    def _active(pos):
        try:
            return str(slots[pos][2]).lower() == "true"
        except (TypeError, ValueError, IndexError):
            return False

    def _id(pos):
        try:
            return int(slots[pos][1])
        except (TypeError, ValueError, IndexError):
            return None

    if skill_index is not None:
        pos = None
        for k in range(len(slots)):
            try:
                if int(slots[k][0]) == int(skill_index):
                    pos = k
                    break
            except (TypeError, ValueError, IndexError):
                continue
        if pos is None:
            return None, "unknown skill_index %s" % skill_index
        if not _active(pos):
            return None, "skill slot %s not active" % skill_index
        if _id(pos) != int(sid):
            return None, ("skill slot %s carries id %s, released %s"
                          % (skill_index, _id(pos), sid))
        return pos, None
    for k in range(len(slots)):
        if _active(k) and _id(k) == int(sid):
            return k, None
    return None, None


def _consume_slot(ctx, pos, sid):
    """Apply the consumption AFTER the release succeeded: isActive=false +
    the corpus-verified cooldown (advance_round ticks it back to active)."""
    from ..battlefield.registry import skill_cooldown_rounds
    entry = list(ctx.commander_skills[pos])
    entry[2] = "false"
    entry[3] = str(skill_cooldown_rounds(sid))
    ctx.commander_skills[pos] = tuple(entry)


def _activate_tower_skill(ctx, side, i, kind_value, sid):
    """Typed 能量塔技能 activation (step4 任务书 §1.4 + user ruling
    2026-08-27): the five commend-center items are ONE-SHOT PER ROUND:
      1 快速补给 (0)   +200 supply now / -300 next round (income debt)
      3 批量征召 (50)  this round's buy limit +1 (前置 action)
      4 精英征召 (100) buys AFTER this spawn at level+1 (order matters)
      5 强化瞄准 (100) 全体远程射程 +15 (battle SideMods)
      6 高速移动 (50)  全体移速 +3 (battle SideMods)
    Single purchase per id per round; unknown ids (e.g. the never-observed
    id 2) stay precise blockers."""
    if sid is None:
        return _receipt(i, kind_value, False, errors.UNSUPPORTED_ACTION,
                        detail="tower skill id missing")
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return _receipt(i, kind_value, False, errors.UNSUPPORTED_ACTION,
                        detail="tower skill %s not executed in v0" % sid)
    if sid not in TOWER_SKILL_COSTS:
        return _receipt(i, kind_value, False, errors.UNSUPPORTED_ACTION,
                        detail="tower skill %s not executed in v0" % sid)
    # per-item round sentinels (single purchase per id per round)
    sentinel = {1: TOWER_LOAN_SENTINEL, 3: TOWER_MASS_SENTINEL,
                4: TOWER_ELITE_SENTINEL}.get(sid)
    if sentinel is not None and sentinel in ctx.blueprints_round:
        return _receipt(i, kind_value, False, errors.TOWER_SKILL_ALREADY_ACTIVE,
                        detail="tower skill %d already purchased this round "
                               "(single purchase)" % sid)
    if sid in (5, 6) and int(sid) in (int(s) for s in ctx.tower_mods):
        return _receipt(i, kind_value, False, errors.TOWER_SKILL_ALREADY_ACTIVE,
                        detail="tower skill %d already active this round "
                               "(single purchase)" % sid)
    cost = TOWER_SKILL_COSTS[int(sid)]
    if ctx.supply < cost:
        return _receipt(i, kind_value, False, errors.INSUFFICIENT_SUPPLY,
                        detail="tower skill %d needs %d" % (sid, cost))
    if sid == 1:
        # 快速补给: +200 now; the -300 lands on next round's income
        # (Income200r.fast_debts, picked up from the ledger reason)
        ctx.supply += 200
        ctx.ledger.add("blueprint_loan:+200", 200, action_index=i)
        ctx.blueprints_round.append(TOWER_LOAN_SENTINEL)
        return _receipt(i, kind_value, True, errors.OK,
                        resource_delta=200,
                        detail="快速补给 +200 (下回合收入 -300)",
                        changed_paths=("players[%d].supply" % side,))
    ctx.supply -= cost
    ctx.ledger.add("tower_skill:%d" % int(sid), -cost, action_index=i)
    if sid == 3:
        # 批量征召: this round's buy limit +1 (the very next buy may use it)
        ctx.blueprints_round.append(TOWER_MASS_SENTINEL)
        ctx.buy_limit_bonus += 1
        q = _ctx_buy_limit_quote(ctx)
        return _receipt(i, kind_value, True, errors.OK,
                        resource_delta=-cost,
                        detail="批量征召: buy limit %d (%s)"
                               % (q.limit, q.breakdown),
                        changed_paths=("players[%d].supply" % side,))
    if sid == 4:
        # 精英征召: buys AFTER this point spawn at level+1 (doc order rule)
        ctx.blueprints_round.append(TOWER_ELITE_SENTINEL)
        ctx.buy_level_bonus += 1
        return _receipt(i, kind_value, True, errors.OK,
                        resource_delta=-cost,
                        detail="精英征召: subsequent buys spawn at level+1 "
                               "this round",
                        changed_paths=("players[%d].supply" % side,))
    # 5/6: round battle buffs consumed by battlefield/compiler SideMods
    ctx.tower_mods.append(int(sid))
    return _receipt(i, kind_value, True, errors.OK,
                    resource_delta=-cost,
                    detail="tower skill %d (%s)" % (
                        sid, "强化瞄准+15射程" if sid == 5 else "高速移动+3移速"),
                    changed_paths=("players[%d].supply" % side,
                                   "players[%d].tower_mods_raw" % side))


def _release_commander_skill(ctx, side, i, kind_value, eco,
                             skill_index=None, skill_id=None, positions=(),
                             unit_ref=None, construction_index=None):
    """Typed battlefield-skill release (step3 任务书 §5).

    Mapped ids only: 1100001 强化训练 bumps the target unit's exp;
    skills.COMMANDER_SKILLS ids become round battle events at the recorded
    positions. 200001 (EMP) / 1000001 (再部署) / anything unmapped returns a
    precise blocker carrying skill_id/skill_index/target_kind — never a
    wrong approximation.

    battlefield M1: a successful release CONSUMES the backing inventory slot
    (isActive=false + corpus-verified cooldown). Rejected releases never
    mutate the slot (digest invariant)."""
    from ..skills import COMMANDER_SKILLS, TRANSITION_SKILLS
    sid = _resolve_release_id(ctx, skill_id, skill_index)
    target_kind = _release_target_kind(positions, unit_ref, construction_index)
    precise = "skill_id=%s skill_index=%s target_kind=%s" % (
        sid if sid is not None else "-",
        skill_index if skill_index is not None else "-", target_kind)
    if sid is None:
        return _receipt(i, kind_value, False, errors.UNSUPPORTED_ACTION,
                        detail="unresolvable release (%s)" % precise)
    if construction_index is not None:
        # building recycles have no v0 effect: precise blocker carrying the
        # target kind, never a positional approximation (step3 任务书 §5.2)
        return _receipt(i, kind_value, False, errors.UNSUPPORTED_ACTION,
                        detail="construction-target release unsupported "
                               "(%s)" % precise)
    slot_pos, slot_err = _find_release_slot(ctx, sid, skill_index)
    if slot_err is not None:
        return _receipt(i, kind_value, False, "SKILL_SLOT_UNAVAILABLE",
                        detail="%s (%s)" % (slot_err, precise))
    consumed = slot_pos is not None
    if sid in TRANSITION_SKILLS:
        if sid == REDEPLOY_SKILL_ID:
            # step4 任务书 §1.3/QA#3: 再部署 is transition-only — the target
            # (an own unit locked this round) joins this round's move
            # permission; the backing slot cools down (next round usable
            # again; two redeploy slots = two releases per round). No pysim
            # battle event, no summon.
            if unit_ref is None:
                return _receipt(i, kind_value, False, errors.SKILL_TARGET_INVALID,
                                detail="再部署 needs a unit target (%s)" % precise)
            j, u = _find_unit(ctx, unit_ref)
            if u is None:
                return _receipt(i, kind_value, False, errors.UNKNOWN_ENTITY,
                                detail="再部署 target missing (%s)" % precise)
            reasons = _ctx_movement_reasons(ctx, u)
            if reasons:
                return _receipt(i, kind_value, False, errors.UNIT_ALREADY_MOVABLE,
                                detail="再部署 target movable via %s (%s)"
                                       % ("+".join(reasons), precise))
            if consumed:
                _consume_slot(ctx, slot_pos, sid)
            ctx.redeployed_ids.add(int(u.entity_id))
            return _receipt(i, kind_value, True, errors.OK,
                            detail="再部署 unlocks unit %s this round%s"
                                   % (u.replay_index,
                                      (" slot %s consumed" % slot_pos)
                                      if consumed else ""),
                            changed_paths=(
                                "players[%d].redeployed_this_round" % side,)
                            + (("players[%d].commander_skills_raw" % side,)
                               if consumed else ()))
        # 强化训练: target unit's exp jumps to its next upgrade threshold
        if unit_ref is None:
            return _receipt(i, kind_value, False, errors.SKILL_TARGET_INVALID,
                            detail="强化训练 needs a unit target (%s)" % precise)
        j, u = _find_unit(ctx, unit_ref)
        if u is None:
            return _receipt(i, kind_value, False, errors.UNKNOWN_ENTITY,
                            detail="强化训练 target missing (%s)" % precise)
        need = eco.upgrade_exp_need(u.mech_id, u.level)
        if consumed:
            _consume_slot(ctx, slot_pos, sid)
        if need and need > 0 and u.exp < need:
            ctx.units[j] = UnitCard(**{**u.__dict__, "exp": need})
            return _receipt(i, kind_value, True, errors.OK,
                            detail="强化训练 sets exp to %d" % need,
                            changed_paths=(
                                "players[%d].units[entity=%d].exp" % (
                                    side, u.entity_id),))
        return _receipt(i, kind_value, True, errors.OK,
                        detail="强化训练 (no-op: exp already full or cap)")
    if sid in COMMANDER_SKILLS:
        spots = [(float(x), float(y)) for (x, y) in (positions or ())] \
            or [(0.0, 0.0)]
        if consumed:
            _consume_slot(ctx, slot_pos, sid)
        for (sx2, sy2) in spots:
            ctx.skill_events.append((int(sid), float(sx2), float(sy2)))
        d = COMMANDER_SKILLS[sid]
        return _receipt(i, kind_value, True, errors.OK,
                        detail="release %s(%s) x%d%s" % (
                            sid, d.get("name", "?"), len(spots),
                            (" slot %s consumed" % slot_pos)
                            if consumed else ""),
                        changed_paths=(
                            "players[%d].skill_events_raw" % side,)
                        + (("players[%d].commander_skills_raw" % side,)
                           if consumed else ()))
    return _receipt(i, kind_value, False, errors.UNSUPPORTED_ACTION,
                    detail="commander skill not executed in v0 (%s)" % precise)


def _apply(ctx, side, i, act, eco, state, unsupported, notes) -> ActionReceipt:
    kind = act.kind
    args = act.args

    if kind is ActionKind.END_DEPLOY:
        if ctx.finished:
            return _receipt(i, kind.value, False, errors.PLAYER_ALREADY_FINISHED)
        ctx.finished = True
        return _receipt(i, kind.value, True, errors.OK)

    if kind is ActionKind.SURRENDER:
        # battlefield M1: typed GiveUp — atomic terminal, no deploy effect,
        # the opponent wins (env decides reward/terminal view)
        if ctx.surrendered:
            return _receipt(i, kind.value, False, "ALREADY_SURRENDERED")
        ctx.surrendered = True
        ctx.finished = True
        return _receipt(i, kind.value, True, errors.OK,
                        detail="surrender (terminal, opponent wins)")

    if ctx.surrendered:
        return _receipt(i, kind.value, False, errors.ACTION_AFTER_END_DEPLOY,
                        detail="player already surrendered")

    if ctx.finished:
        # GiveUp is a terminal marker recorded after the finish click; it
        # now routes through the SAME surrender rule as the typed action
        # (one rule source) instead of being a no-effect marker
        if kind is ActionKind.RAW_UNSUPPORTED and args.raw_type == "GiveUp":
            ctx.surrendered = True
            return _receipt(i, kind.value, True, errors.OK,
                            detail="giveup (terminal, opponent wins)")
        return _receipt(i, kind.value, False, errors.ACTION_AFTER_END_DEPLOY)

    if kind is ActionKind.GIFT_UNIT:
        # opening-team delayed gift: free spawn at the default deploy line
        price = eco.buy_price(args.mech_id)
        if price is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_MECH)
        eid = ctx.entity_seq()
        ctx.spawned_ids.add(eid)
        y = -160.0 if side == 0 else 160.0
        ctx.units.append(UnitCard(
            entity_id=eid, mech_id=args.mech_id, level=1, exp=0,
            x=0.0, y=y, sell_supply=price,
            replay_index=args.game_index if args.game_index is not None
            else _next_replay_index(ctx)))
        return _receipt(i, kind.value, True, errors.OK,
                        created_entity_id=eid,
                        changed_paths=("players[%d].units" % side,))

    if kind is ActionKind.RAW_UNSUPPORTED:
        unsupported.append(args.raw_type)
        rt = args.raw_type
        rid = _raw_get(args, "ID")
        if rt == "ActiveBlueprint":
            if int(rid) in BLUEPRINT_SKILLS:
                # 指挥官技能研究 (user ruling 2026-08-27): one-time research
                # unlocking the mapped commander skill; the SLOT is granted at
                # the NEXT round start (advance_round, corpus lag=+1 ~100%).
                # bp1=黏油弹400002/150, bp2=战地回收900001/100,
                # bp3=移动信标1500001/100
                bid = int(rid)
                if bid in [int(b) for b in ctx.blueprints]:
                    return _receipt(i, kind.value, True, errors.OK,
                                    detail="blueprint %d already researched "
                                           "(no charge)" % bid)
                cost = BLUEPRINT_COSTS[bid]
                if ctx.supply < cost:
                    return _receipt(i, kind.value, False,
                                    errors.INSUFFICIENT_SUPPLY,
                                    detail="blueprint %d needs %d" % (
                                        rid, cost))
                ctx.supply -= cost
                ctx.ledger.add("blueprint:%s" % rid, -cost, action_index=i)
                ctx.blueprints.append(bid)
                ctx.blueprints_round.append(bid)
                return _receipt(i, kind.value, True, errors.OK,
                                resource_delta=-cost,
                                detail="研究 %d: 解锁指挥官技能 %d (下回合入槽)"
                                       % (bid, BLUEPRINT_SKILLS[bid]),
                                changed_paths=("players[%d].supply" % side,
                                               "players[%d].blueprints"
                                               % side))
            officer = BLUEPRINT_OFFICERS.get(rid)
            cost = BLUEPRINT_COSTS.get(rid if rid is not None else -1, 0)
            if officer:
                if ctx.supply < cost:
                    return _receipt(i, kind.value, False,
                                    errors.INSUFFICIENT_SUPPLY,
                                    detail="blueprint %s needs %d" % (rid, cost))
                ctx.supply -= cost
                ctx.ledger.add("blueprint:%s" % rid, -cost, action_index=i)
                # II replaces I in the equipped list (corpus: never both)
                replace = {20311: 20310, 20301: 20300}.get(officer)
                if replace is not None and replace in ctx.officers:
                    ctx.officers.remove(replace)
                ctx.officers.append(officer)
                ctx.blueprints.append(rid)
                ctx.blueprints_round.append(rid)
                return _receipt(i, kind.value, True, errors.OK,
                                resource_delta=-cost,
                                changed_paths=("players[%d].supply" % side,
                                               "players[%d].officers" % side,
                                               "players[%d].blueprints" % side))
            return _receipt(i, kind.value, True, errors.OK,
                            detail="blueprint %s (no supply effect)" % rid)
        if rt == "ReleaseContraption":
            cid = _raw_get_str(args, "ContraptionID")
            pos = _raw_get(args, "Position")
            cost = CONTRAPTION_COSTS.get(cid, 0)
            if cost:
                if ctx.supply < cost:
                    return _receipt(i, kind.value, False,
                                    errors.INSUFFICIENT_SUPPLY,
                                    detail="contraption %s needs %d" % (cid, cost))
                ctx.supply -= cost
                ctx.ledger.add("contraption:%s" % cid, -cost, action_index=i)
            # record the device for the battle adapter (10001 飞弹 turret /
            # 20001 护盾装置 barrier; 30001 unmapped - cost only, the
            # capability scanner blocks it from strict play)
            try:
                cx = float(pos.get("x", 0.0)) if isinstance(pos, dict) else 0.0
                cy = float(pos.get("y", 0.0)) if isinstance(pos, dict) else 0.0
                ctx.devices.append((int(cid), cx, cy))
            except (TypeError, ValueError):
                cx = cy = 0.0
                ctx.devices.append((int(cid) if cid else 0, 0.0, 0.0))
            return _receipt(i, kind.value, True, errors.OK,
                            resource_delta=-cost,
                            changed_paths=(
                                ("players[%d].supply" % side,) if cost else ())
                            + ("players[%d].devices_raw" % side,))
        if rt == "StrengthenTower":
            # audit-game v1: full semantics — charge + persistent tower level
            # (battle adapter reads tower_strengthen[:2]); Index selects the
            # tower slot (0/1), max level mirrors units (9).
            tidx = _raw_get(args, "Index") or 0
            tidx = 0 if tidx not in (0, 1) else int(tidx)
            levels = list(ctx.tower_strengthen) + [0, 0]
            levels = levels[:2]
            if levels[tidx] >= 9:
                return _receipt(i, kind.value, False, errors.MAX_LEVEL,
                                detail="tower %d at max" % tidx)
            if ctx.supply < TOWER_STRENGTHEN_COST:
                return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                                detail="tower needs %d" % TOWER_STRENGTHEN_COST)
            ctx.supply -= TOWER_STRENGTHEN_COST
            ctx.ledger.add("tower_strengthen", -TOWER_STRENGTHEN_COST,
                           action_index=i)
            levels[tidx] += 1
            ctx.tower_strengthen = (levels[0], levels[1])
            return _receipt(i, kind.value, True, errors.OK,
                            resource_delta=-TOWER_STRENGTHEN_COST,
                            changed_paths=(
                                "players[%d].tower_strengthen" % side,
                                "players[%d].supply" % side))
        if rt == "ActiveEnergyTowerSkill":
            # legacy raw form (old fixtures/shards): forward to the SAME
            # typed handler (step4 任务书 §2.3 — one rule source)
            return _activate_tower_skill(
                ctx, side, i, kind.value, _raw_get(args, "SkillID"))
        if rt == "ReleaseCommanderSkill":
            # legacy raw form: resolve explicit ID vs SkillIndex, then share
            # the typed release path (one rule source with the normalizer's
            # typed entries; step3 任务书 §5.1/§5.2)
            uidx = _raw_get(args, "UnitIndex")
            cidx = _raw_get(args, "ConstructionIndex")
            return _release_commander_skill(
                ctx, side, i, kind.value, eco,
                skill_index=_raw_get(args, "SkillIndex"),
                skill_id=_raw_get(args, "ID") or 0,
                positions=_raw_positions(args),
                unit_ref=(None if uidx is None else EntityRef(handle=uidx)),
                construction_index=cidx)
        return _receipt(i, kind.value, False, errors.UNSUPPORTED_ACTION,
                        detail="raw type %s not executed in v0" % args.raw_type)

    if kind is ActionKind.CHOOSE_REINFORCE:
        item_id = args.item_id
        cost = eco.item_cost(item_id)
        if cost is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_ITEM,
                            detail="item %s" % item_id)
        info = eco.items.get(item_id) if item_id else None
        if info and info.get("kind") == "装备":
            # step3 任务书 §6.3: equipment cards charge normally and stock one
            # copy in the inventory (multiset). Battle fidelity stays
            # `approximate` (pysim does not consume the combat modifier —
            # battle results carry a fidelity warning); an UNREGISTERED
            # equipment id remains a hard blocker.
            from .equipment import EQUIPMENT_DEFS
            d = EQUIPMENT_DEFS.get(int(item_id))
            if d is None:
                return _receipt(i, kind.value, False,
                                errors.MISSING_RULE_DATA,
                                detail="equipment card %s not registered"
                                       % item_id)
            if ctx.supply < cost:
                return _receipt(i, kind.value, False,
                                errors.INSUFFICIENT_SUPPLY,
                                detail="need %d have %d" % (cost, ctx.supply))
            ctx.supply -= cost
            if cost:
                ctx.ledger.add("reinforce:%s" % item_id, -cost,
                               action_index=i)
            ctx.equipment.append(int(item_id))
            return _receipt(i, kind.value, True, errors.OK,
                            resource_delta=-cost,
                            detail="equipment %s(%s) stocked x%d "
                                   "(battle_approximate)"
                                   % (item_id, d.name,
                                      ctx.equipment.count(int(item_id))),
                            changed_paths=("players[%d].supply" % side,
                                           "players[%d].equipment_inventory"
                                           % side))
        if ctx.supply < cost:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d" % (cost, ctx.supply))
        ctx.supply -= cost
        if cost:
            ctx.ledger.add("reinforce:%s" % item_id, -cost, action_index=i)
        if item_id == 0:
            # skipping all four offers pays a small bonus (Q4, corpus-fit
            # +50 at rounds 2..5; rule reinforce_skip_bonus_v1)
            from .economy import REINFORCE_SKIP_BONUS
            ctx.supply += REINFORCE_SKIP_BONUS
            ctx.ledger.add("reinforce_skip:+50", REINFORCE_SKIP_BONUS,
                           action_index=i)
            return _receipt(i, kind.value, True, errors.OK,
                            resource_delta=REINFORCE_SKIP_BONUS - cost,
                            changed_paths=("players[%d].supply" % side,),
                            detail="skip bonus +%d" % REINFORCE_SKIP_BONUS)
        grant = eco.item_grant(item_id)
        spawned = None
        if grant:
            mech, count, level = grant
            price = eco.buy_price(mech)
            if price is None:
                return _receipt(i, kind.value, False, errors.UNKNOWN_MECH,
                                detail="grant mech %s" % mech)
            for c in range(count):
                eid = ctx.entity_seq()
                spec = args.grant_specs[c] if c < len(args.grant_specs) else None
                if spec is not None:
                    ctx.new_ref_entity[spec[0]] = eid
                game_idx = spec[1] if spec is not None else \
                    _next_replay_index(ctx)
                ctx.spawned_ids.add(eid)
                gx, gy = _grant_spawn_pos(ctx, side)
                ctx.units.append(UnitCard(
                    entity_id=eid, mech_id=mech, level=max(1, level),
                    exp=0, x=gx, y=gy, sell_supply=price,
                    replay_index=game_idx))
            spawned = count
        # routing: unit-strengthen / expert cards persist into officers;
        # 舰长技能/战术 cards enter the skill inventory (releasable later)
        info = eco.items.get(item_id) if item_id else None
        if info and info.get("kind") in ("单位强化卡", "专家/补给卡"):
            ctx.officers.append(item_id)
        if info and info.get("kind") == "舰长技能/战术":
            next_idx = 0
            for e in ctx.commander_skills:
                try:
                    next_idx = max(next_idx, int(e[0]) + 1)
                except (TypeError, ValueError):
                    continue
            ctx.commander_skills.append(
                (str(next_idx), str(item_id), "true", "0"))
            return _receipt(i, kind.value, True, errors.OK, resource_delta=-cost,
                            changed_paths=("players[%d].supply" % side,
                                           "players[%d].commander_skills_raw" % side),
                            detail="skill card %s -> inventory slot %d"
                                   % (item_id, next_idx))
        return _receipt(i, kind.value, True, errors.OK, resource_delta=-cost,
                        created_entity_id=None,
                        changed_paths=(("players[%d].supply" % side),) if cost else (),
                        detail="grant=%s" % (spawned or 0))

    if kind is ActionKind.UNLOCK_UNIT:
        quote = eco.unlock_quote(args.mech_id, ctx.officers)
        if quote is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_MECH)
        if args.mech_id in ctx.unlocked:
            return _receipt(i, kind.value, True, errors.OK,
                            detail="already unlocked (no charge)")
        if ctx.supply < quote.final_price:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d (%s)"
                                   % (quote.final_price, ctx.supply,
                                      quote.breakdown))
        ctx.supply -= quote.final_price
        ctx.ledger.add("unlock:%s" % args.mech_id, -quote.final_price,
                       action_index=i)
        ctx.unlocked.add(args.mech_id)
        return _receipt(i, kind.value, True, errors.OK,
                        resource_delta=-quote.final_price,
                        detail=quote.breakdown,
                        changed_paths=("players[%d].unlocked_mechs" % side,
                                       "players[%d].supply" % side))

    if kind is ActionKind.BUY_UNIT:
        price = _buy_cost(ctx, eco, args.mech_id)
        if price is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_MECH)
        if args.mech_id not in ctx.unlocked:
            return _receipt(i, kind.value, False, errors.MECH_NOT_UNLOCKED)
        if not (_in_bounds(args.x, args.y)):
            return _receipt(i, kind.value, False, errors.POSITION_OUT_OF_BOUNDS)
        if not in_own_half(side, args.y):
            return _receipt(i, kind.value, False,
                            errors.POSITION_OUT_OF_DEPLOY_ZONE,
                            detail=_zone_detail(side))
        # step4 任务书 §1.1: one buy-limit rule source (rules.buy_limit_quote
        # semantics over the live working view)
        quote = _ctx_buy_limit_quote(ctx)
        if quote.remaining <= 0:
            return _receipt(i, kind.value, False, errors.BUY_LIMIT_REACHED,
                            detail="buy %d/%d this round (base %d + 批量征召 "
                                   "%d + 额外部署位 %d)"
                                   % (quote.used, quote.limit, quote.base,
                                      quote.blueprint_bonus,
                                      quote.officer_bonus))
        if ctx.supply < price:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d" % (price, ctx.supply))
        ctx.supply -= price
        ctx.ledger.add("buy:%s" % args.mech_id, -price, action_index=i)
        eid = ctx.entity_seq()
        ctx.new_ref_entity[args.new_ref] = eid
        ctx.spawned_ids.add(eid)
        level = 1 + _elite_bonus(ctx, args.mech_id) + \
            getattr(ctx, "buy_level_bonus", 0)
        ctx.units.append(UnitCard(
            entity_id=eid, mech_id=args.mech_id, level=level, exp=0,
            x=args.x, y=args.y, is_rotate=args.is_rotate,
            sell_supply=eco.buy_price(args.mech_id) or 0,
            replay_index=args.game_index if args.game_index is not None
            else _next_replay_index(ctx)))
        ctx.buy_count += 1
        return _receipt(i, kind.value, True, errors.OK, resource_delta=-price,
                        created_entity_id=eid,
                        changed_paths=("players[%d].units" % side,
                                       "players[%d].supply" % side))

    if kind is ActionKind.UPGRADE_UNIT:
        j, u = _find_unit(ctx, args.ref)
        if u is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_ENTITY)
        price = eco.upgrade_price(u.mech_id)
        if price is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_MECH)
        price = max(0, price + eco.upgrade_price_mod(u.mech_id, ctx.officers))
        discount = 0
        if FEATURES["equipment_upgrade_discount"] \
                and int(u.equipment_id or 0) == UPGRADE_DISCOUNT_EQUIPMENT:
            # 强化模块 13030004: 升级消耗的补给减少100 (survey card text;
            # applies to THIS unit only — the bound item carries it)
            discount = min(UPGRADE_DISCOUNT_AMOUNT, price)
            price -= discount
        if u.level >= 9:
            return _receipt(i, kind.value, False, errors.MAX_LEVEL)
        # corpus truth (2026-08-26): every recorded UpgradeUnit of a live
        # unit levels it up (455/455 with exp below the v0 threshold) —
        # there is NO exp gate; exp consumption happens elsewhere
        consume = 0
        if FEATURES["upgrade_exp_gate"]:
            need = eco.upgrade_exp_need(u.mech_id, u.level)
            if need and need > 0 and u.exp >= need:
                consume = need
        if ctx.supply < price:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d" % (price, ctx.supply))
        ctx.supply -= price
        ctx.ledger.add("upgrade:%s" % u.mech_id, -price, action_index=i,
                       entity_id=u.entity_id)
        ctx.units[j] = UnitCard(**{**u.__dict__, "level": u.level + 1,
                                   "exp": max(0, u.exp - consume)})
        return _receipt(i, kind.value, True, errors.OK, resource_delta=-price,
                        detail=("强化模块 discount -%d; " % discount
                                if discount else ""),
                        changed_paths=("players[%d].units[entity=%d].level" % (
                            side, u.entity_id),
                            "players[%d].units[entity=%d].exp" % (side, u.entity_id),
                            "players[%d].supply" % side))

    if kind is ActionKind.BUY_TECH:
        td = eco.gd.techs.get(args.tech_id)
        if td is None:
            # special-unit techs (e.g. experimental 4001 family) have no
            # decoded price yet: record without charge/effect instead of
            # blocking the round (census bucket `tech_unknown`)
            return _receipt(i, kind.value, False, errors.UNSUPPORTED_RULE_DATA,
                            detail="tech %s not in gamedata (no charge)" %
                                    args.tech_id)
        card = eco.gd.cards.get(int(args.mech_id))
        if card is None or int(args.tech_id) not in \
                (card.technologies or ()):
            return _receipt(i, kind.value, False, errors.UNKNOWN_TECH,
                            detail="tech %s not of mech %s"
                                   % (args.tech_id, args.mech_id))
        # step3 任务书 §4.2: the mech must be on the field (>=1 unit)
        if not any(u.mech_id == int(args.mech_id) for u in ctx.units):
            return _receipt(i, kind.value, False, errors.TECH_MECH_NOT_ON_FIELD,
                            detail="mech %s has no unit on the field"
                                   % args.mech_id)
        owned = ctx.tech_bought.get(args.mech_id, [])
        if args.tech_id in owned:
            return _receipt(i, kind.value, True, errors.OK,
                            detail="tech already active (no charge)")
        quote = eco.tech_quote(args.mech_id, args.tech_id, len(owned),
                               ctx.officers)
        if quote is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_TECH)
        if td.previous_tech_id and td.previous_tech_id not in owned \
                and td.previous_tech_id != 0:
            # chain techs require their predecessor
            return _receipt(i, kind.value, False, errors.TECH_PREREQUISITE_MISSING,
                            detail="needs %d" % td.previous_tech_id)
        if ctx.supply < quote.final_price:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d (%s)"
                                   % (quote.final_price, ctx.supply,
                                      quote.breakdown))
        ctx.supply -= quote.final_price
        ctx.ledger.add("tech:%s" % args.tech_id, -quote.final_price,
                       action_index=i)
        prev_of = {t.id: t.previous_tech_id for t in eco.gd.techs.values()}
        ctx.tech_bought[args.mech_id] = _fold_tech(
            list(owned), args.tech_id, prev_of)
        return _receipt(i, kind.value, True, errors.OK,
                        resource_delta=-quote.final_price,
                        detail=quote.breakdown,
                        changed_paths=("players[%d].tech_map" % side,
                                       "players[%d].supply" % side))

    if kind is ActionKind.MOVE_UNIT:
        j, u = _find_unit(ctx, args.ref)
        if u is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_ENTITY)
        if not _in_bounds(args.x, args.y):
            return _receipt(i, kind.value, False, errors.POSITION_OUT_OF_BOUNDS)
        # step4 任务书 §1.2: units that fought last round are locked unless
        # new this round / 部署模块 / 高速引擎 / 再部署. Evaluated against the
        # live working view, so a module binding or tech buy earlier in this
        # round unlocks the very next move. Rejected moves change nothing
        # (position AND orientation stay).
        reasons = _ctx_movement_reasons(ctx, u)
        if not reasons:
            return _receipt(i, kind.value, False,
                            errors.UNIT_NOT_MOVABLE_THIS_ROUND,
                            detail="unit %s fought last round; movable only "
                                   "via new/module/tech/redeploy"
                                   % (u.replay_index,))
        # corpus truth (step2 G2 audit): moves may reposition anywhere on the
        # map (7/258 sample moves cross the midline — R3+ flank pushes), so
        # only NEW buys are restricted to the acting player's half
        rot = u.is_rotate if args.is_rotate is None else args.is_rotate
        ctx.units[j] = UnitCard(**{**u.__dict__, "x": args.x, "y": args.y,
                                   "is_rotate": rot})
        return _receipt(i, kind.value, True, errors.OK,
                        detail="move via %s" % "+".join(reasons),
                        changed_paths=("players[%d].units[entity=%d].pos" % (
                            side, u.entity_id),))

    if kind is ActionKind.SELL_UNIT:
        j, u = _find_unit(ctx, args.ref)
        if u is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_ENTITY)
        refund = u.sell_supply
        ctx.supply += refund
        ctx.ledger.add("sell:%s" % u.mech_id, refund, action_index=i,
                       entity_id=u.entity_id)
        del ctx.units[j]
        # battlefield M1: selling consumes the recycle skill slot (corpus:
        # 900001 releases show (inactive, cd=0) the following round)
        sell_slot = None
        if FEATURES["skill_slot_lifecycle"]:
            sell_slot, _err = _find_release_slot(ctx, 900001, None)
            if sell_slot is not None:
                _consume_slot(ctx, sell_slot, 900001)
        return _receipt(i, kind.value, True, errors.OK, resource_delta=refund,
                        removed_entity_id=u.entity_id,
                        changed_paths=("players[%d].units" % side,
                                       "players[%d].supply" % side)
                        + (("players[%d].commander_skills_raw" % side,)
                           if sell_slot is not None else ()))

    if kind is ActionKind.RELEASE_COMMANDER_SKILL:
        return _release_commander_skill(
            ctx, side, i, kind.value, eco,
            skill_index=args.skill_index, skill_id=args.skill_id,
            positions=args.positions, unit_ref=args.unit_ref,
            construction_index=args.construction_index)

    if kind is ActionKind.ACTIVATE_ENERGY_TOWER_SKILL:
        # step4 任务书 §2.3: typed 能量塔技能 (browser/normalizer/canonicalizer
        # all land here; the raw ActiveEnergyTowerSkill adapter forwards to
        # this same handler)
        return _activate_tower_skill(ctx, side, i, kind.value, args.skill_id)

    if kind is ActionKind.USE_EQUIPMENT:
        from .equipment import EQUIPMENT_DEFS, equipment_target_ok
        eid = int(args.equipment_id)
        d = EQUIPMENT_DEFS.get(eid)
        if d is None:
            return _receipt(i, kind.value, False, errors.MISSING_RULE_DATA,
                            detail="equipment %s unknown" % eid)
        j, u = _find_unit(ctx, args.unit_ref)
        if u is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_ENTITY,
                            detail="equipment %s target missing" % eid)
        if eid not in ctx.equipment:
            return _receipt(i, kind.value,
                            False, errors.EQUIPMENT_NOT_IN_INVENTORY,
                            detail="equipment %s (%s) not stocked"
                                   % (eid, d.name))
        card = eco.gd.cards.get(u.mech_id)
        if not (card and card.can_add_equipment):
            return _receipt(i, kind.value,
                            False, errors.EQUIPMENT_TARGET_NOT_ALLOWED,
                            detail="mech %s cannot carry equipment"
                                   % u.mech_id)
        ok, reason = equipment_target_ok(eco.gd, eid, u.mech_id)
        if not ok:
            return _receipt(i, kind.value,
                            False, errors.EQUIPMENT_RESTRICTION_MISMATCH,
                            detail="equipment %s (%s) restriction %s: %s"
                                   % (eid, d.name, d.target_restriction,
                                      reason))
        # consume one copy from the multiset inventory; a previous binding is
        # REPLACED and the old item does not return to stock
        ctx.equipment.remove(eid)
        replaced = u.equipment_id or 0
        ctx.units[j] = UnitCard(**{**u.__dict__, "equipment_id": eid})
        detail = "equip %s(%s) -> unit %s" % (eid, d.name, u.replay_index)
        if replaced:
            detail += " (replaced %s, not restocked)" % replaced
        return _receipt(i, kind.value, True, errors.OK, detail=detail,
                        changed_paths=(
                            "players[%d].equipment_inventory" % side,
                            "players[%d].units[entity=%d].equipment_id" % (
                                side, u.entity_id)))

    return _receipt(i, str(kind), False, errors.UNSUPPORTED_ACTION,
                    detail="unhandled canonical kind")


def _raw_get(args, key):
    for k, v in args.raw:
        if k == key:
            try:
                return int(v)
            except (TypeError, ValueError):
                return v
    return None


def _raw_get_str(args, key):
    for k, v in args.raw:
        if k == key:
            return str(v)
    return None


def _raw_positions(args):
    """Positions field of a raw release record -> [(x, y), ...]."""
    for k, v in args.raw:
        if k != "Positions":
            continue
        out = []
        if isinstance(v, dict):
            v = [v]
        for p in (v or []):
            try:
                out.append((float(p.get("x", 0.0) or 0.0),
                            float(p.get("y", 0.0) or 0.0)))
            except (AttributeError, TypeError, ValueError):
                continue
        return out
    return []


def ctx_officers(ctx):
    return getattr(ctx, "commander_skills", ()) or ()


def _ctx_buy_limit_quote(ctx) -> BuyLimitQuote:
    """Live buy-limit quote over the deploy working view (能量塔技能3
    批量征召 purchases and 10004 officers held mid-round count immediately)."""
    bp = sum(1 for b in (ctx.blueprints_round or [])
             if int(b) == TOWER_MASS_SENTINEL)
    off = (sum(1 for o in ctx.officers if int(o) == EXTRA_DEPLOY_OFFICER)
           if FEATURES["extra_deploy_slots"] else 0)
    used = int(ctx.buy_count)
    limit = BASE_BUY_LIMIT + bp + off
    return BuyLimitQuote(base=BASE_BUY_LIMIT, blueprint_bonus=bp,
                         officer_bonus=off, used=used, limit=limit,
                         remaining=max(0, limit - used))


def _ctx_movement_reasons(ctx, unit):
    """Movement permission inside the deploy working view: reads the live
    spawned/redeployed sets and tech map so mid-round activations (部署模块
    binding, 高速引擎 buy) apply to the very next move — same core predicate
    as rules.movement_permission (one rule source)."""
    return movement_reasons(unit, ctx.spawned_ids, ctx.redeployed_ids,
                            ctx.tech_bought)


def _in_bounds(x, y) -> bool:
    return abs(x) <= MAP_X and abs(y) <= MAP_Y


def in_own_half(side: int, y: float) -> bool:
    """Deployment-zone rule (step2 任务书 §4.1): player 0 owns y < 0,
    player 1 owns y > 0; the midline y == 0 belongs to neither side."""
    return y < 0 if side == 0 else y > 0


def _grant_spawn_pos(ctx, side):
    """Reinforcement-grant arrival point: first free slot on the owner's
    deploy lines (>= 32 from every existing unit, so grants never stack on
    another unit or on the opponent's mirrored grant at the midline). The
    unit stays movable — this is only where it appears, like the gift spawn
    at y = -/+160."""
    taken = [(u.x, u.y) for u in ctx.units]
    xs = [0.0]
    for s in range(1, 9):
        xs += [s * 40.0, -s * 40.0]
    for dist in (160.0, 220.0, 280.0):
        y = -dist if side == 0 else dist
        for x in xs:
            if all((x - tx) ** 2 + (y - ty) ** 2 >= 32.0 ** 2
                   for tx, ty in taken):
                return x, y
    return 0.0, (-160.0 if side == 0 else 160.0)


def _zone_detail(side: int) -> str:
    return "player %d deploys in %s (midline y=0 excluded)" % (
        side, "y<0" if side == 0 else "y>0")


def _next_replay_index(ctx) -> int:
    """Game unit Index counter: snapshot max + 1, monotonic, never reused."""
    used = [u.replay_index for u in ctx.units if u.replay_index is not None]
    return (max(used) + 1) if used else 0
