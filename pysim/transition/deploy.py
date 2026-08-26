# Deployment transition: execute canonical action plans on an EnvironmentState.
#
# Hard rules (任务书 §1.3): rejected actions never mutate state; supply always
# equals before + ledger sum; deploy never touches hp/exp-settlement fields.
# Round-scoped working state (undo already folded by the canonicalizer):
#   - buy_level_bonus from ActiveBlueprint(3) 精英征召 (feature-flagged)
#   - grant/buy units become permanent units of the player
from dataclasses import dataclass, field

from . import errors
from .model import (ActionKind, CanonicalActionPlan, EnvironmentState,
                    PlayerState, UnitCard, Phase, ActionReceipt, EntityRef)
from .state_tools import state_digest, with_player, assert_state_invariants
from .economy import Economy, LedgerBuilder

MAP_X, MAP_Y = 350.0, 300.0

# v0 feature flags (unknown rules stay OFF and land in the unsupported bucket
# instead of being guessed in code):
FEATURES = {
    "elite_recruit_bp3": False,     # ActiveBlueprint 3 raises later buys' level
    "elite_officer_bonus": True,    # 精英专家 20032 / 3xxxx 精英卡 -> +1 buy level
    "upgrade_exp_gate": True,       # veterans need a full exp bar to upgrade
}


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
    commander_skills: tuple = ()
    spawned_ids: set = field(default_factory=set)   # units created this round
    buy_level_bonus: int = 0
    digests: list = field(default_factory=list)


def deploy_transition(state: EnvironmentState,
                      plans: tuple[CanonicalActionPlan, ...],
                      eco: Economy,
                      strict: bool = False) -> DeployResult:
    """Apply both players' plans; returns receipts + pre-battle state."""
    if state.phase is not Phase.DEPLOYMENT:
        raise errors.TransitionError(errors.WRONG_PHASE,
                                     "deploy needs DEPLOYMENT phase")
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
            commander_skills=p.commander_skills_raw,
            new_ref_entity={}, finished=state.finished_deploy[side],
            buy_count=0,
            ledger=LedgerBuilder(p.supply))
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
        officers=tuple(ctx.officers), blueprints=base.blueprints,
        commander_skills_raw=base.commander_skills_raw,
        tower_strengthen=base.tower_strengthen,
        constructions_raw=base.constructions_raw,
        bought_this_round=ctx.buy_count)


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
    if not FEATURES["elite_officer_bonus"]:
        return 0
    for o in ctx.officers:
        if o == 20032:
            return 1
        if 30000 + mech_id * 100 <= o < 30000 + (mech_id + 1) * 100:
            return 1
    return 0


def _fold_tech(active: list, new_tech: int, prev_of: dict) -> list:
    """previousTechID chains: the higher tier replaces the lower one."""
    out = [t for t in active if prev_of.get(new_tech) != t or t == new_tech]
    if new_tech not in out:
        out.append(new_tech)
    # drop any tech made obsolete by the new one
    out = [t for t in out if prev_of.get(t) != new_tech or t == new_tech]
    return out


def _apply(ctx, side, i, act, eco, state, unsupported, notes) -> ActionReceipt:
    kind = act.kind
    args = act.args

    if kind is ActionKind.END_DEPLOY:
        if ctx.finished:
            return _receipt(i, kind.value, False, errors.PLAYER_ALREADY_FINISHED)
        ctx.finished = True
        return _receipt(i, kind.value, True, errors.OK)

    if ctx.finished:
        return _receipt(i, kind.value, False, errors.ACTION_AFTER_END_DEPLOY)

    if kind is ActionKind.RAW_UNSUPPORTED:
        unsupported.append(args.raw_type)
        # modeled peeks (kept explicit; each changes only the flagged field):
        if args.raw_type == "ActiveBlueprint" and _raw_get(args, "ID") == 3 \
                and FEATURES["elite_recruit_bp3"]:
            ctx.buy_level_bonus += 1
            return _receipt(i, kind.value, True, errors.OK,
                            detail="elite_recruit_bp3 flag on",
                            changed_paths=("players[%d].buy_level_bonus" % side,))
        if args.raw_type == "ActiveBlueprint" and _raw_get(args, "ID") == 1:
            # 快速补给: +200 now; the -300 next round is an income-side effect
            # absorbed by the (injected) income policy in replay mode.
            ctx.supply += 200
            ctx.ledger.add("blueprint_loan:+200", 200, action_index=i)
            return _receipt(i, kind.value, True, errors.OK,
                            resource_delta=200,
                            changed_paths=("players[%d].supply" % side,))
        if args.raw_type == "ReleaseCommanderSkill" \
                and _resolves_to(args, ctx, 1100001):
            # 强化训练: target unit's exp jumps to its next upgrade threshold
            uidx = _raw_get(args, "UnitIndex")
            j, u = _find_unit(ctx, EntityRef(handle=uidx))
            if u is not None:
                need = eco.upgrade_exp_need(u.mech_id, u.level)
                if need and need > 0 and u.exp < need:
                    ctx.units[j] = UnitCard(**{**u.__dict__, "exp": need})
                    return _receipt(i, kind.value, True, errors.OK,
                                    detail="强化训练 sets exp to %d" % need,
                                    changed_paths=(
                                        "players[%d].units[entity=%d].exp" % (
                                            side, u.entity_id),))
            return _receipt(i, kind.value, True, errors.OK,
                            detail="强化训练 (no-op: exp already full or cap)")
        return _receipt(i, kind.value, False, errors.UNSUPPORTED_ACTION,
                        detail="raw type %s not executed in v0" % args.raw_type)

    if kind is ActionKind.CHOOSE_REINFORCE:
        item_id = args.item_id
        cost = eco.item_cost(item_id)
        if cost is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_ITEM,
                            detail="item %s" % item_id)
        if ctx.supply < cost:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d" % (cost, ctx.supply))
        ctx.supply -= cost
        if cost:
            ctx.ledger.add("reinforce:%s" % item_id, -cost, action_index=i)
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
                ctx.units.append(UnitCard(
                    entity_id=eid, mech_id=mech, level=max(1, level),
                    exp=0, x=0.0, y=0.0, sell_supply=price,
                    replay_index=game_idx))
            spawned = count
        # routing: unit-strengthen / expert cards persist into officers
        info = eco.items.get(item_id) if item_id else None
        if info and info.get("kind") in ("单位强化卡", "专家/补给卡"):
            ctx.officers.append(item_id)
        return _receipt(i, kind.value, True, errors.OK, resource_delta=-cost,
                        created_entity_id=None,
                        changed_paths=(("players[%d].supply" % side),) if cost else (),
                        detail="grant=%s" % (spawned or 0))

    if kind is ActionKind.UNLOCK_UNIT:
        price = eco.unlock_price(args.mech_id)
        if price is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_MECH)
        if args.mech_id in ctx.unlocked:
            return _receipt(i, kind.value, True, errors.OK,
                            detail="already unlocked (no charge)")
        if ctx.supply < price:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d" % (price, ctx.supply))
        ctx.supply -= price
        ctx.ledger.add("unlock:%s" % args.mech_id, -price, action_index=i)
        ctx.unlocked.add(args.mech_id)
        return _receipt(i, kind.value, True, errors.OK, resource_delta=-price,
                        changed_paths=("players[%d].unlocked_mechs" % side,
                                       "players[%d].supply" % side))

    if kind is ActionKind.BUY_UNIT:
        price = eco.buy_price(args.mech_id)
        if price is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_MECH)
        if args.mech_id not in ctx.unlocked:
            return _receipt(i, kind.value, False, errors.MECH_NOT_UNLOCKED)
        if not (_in_bounds(args.x, args.y)):
            return _receipt(i, kind.value, False, errors.POSITION_OUT_OF_BOUNDS)
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
            sell_supply=price,
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
        if u.level >= 9:
            return _receipt(i, kind.value, False, errors.MAX_LEVEL)
        # exp gate: veterans (present in the pre-round snapshot) need a full
        # exp bar and consume it; units bought/granted THIS round upgrade
        # immediately at money cost only (corpus: fresh buys upgrade with 0 exp)
        fresh = u.entity_id in ctx.spawned_ids
        need = eco.upgrade_exp_need(u.mech_id, u.level)
        if need < 0:
            return _receipt(i, kind.value, False, errors.MAX_LEVEL)
        if need and not fresh and FEATURES["upgrade_exp_gate"] \
                and u.exp < need:
            return _receipt(i, kind.value, False, errors.EXP_NOT_ENOUGH,
                            detail="exp %d need %d" % (u.exp, need))
        consume = need if (u.exp >= need) else 0
        if ctx.supply < price:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d" % (price, ctx.supply))
        ctx.supply -= price
        ctx.ledger.add("upgrade:%s" % u.mech_id, -price, action_index=i,
                       entity_id=u.entity_id)
        ctx.units[j] = UnitCard(**{**u.__dict__, "level": u.level + 1,
                                   "exp": max(0, u.exp - consume)})
        return _receipt(i, kind.value, True, errors.OK, resource_delta=-price,
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
        owned = ctx.tech_bought.get(args.mech_id, [])
        if args.tech_id in owned:
            return _receipt(i, kind.value, True, errors.OK,
                            detail="tech already active (no charge)")
        price = eco.tech_price(args.mech_id, args.tech_id, len(owned))
        if price is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_TECH)
        if td.previous_tech_id and td.previous_tech_id not in owned \
                and td.previous_tech_id != 0:
            # chain techs require their predecessor
            return _receipt(i, kind.value, False, errors.TECH_PREREQUISITE_MISSING,
                            detail="needs %d" % td.previous_tech_id)
        if ctx.supply < price:
            return _receipt(i, kind.value, False, errors.INSUFFICIENT_SUPPLY,
                            detail="need %d have %d" % (price, ctx.supply))
        ctx.supply -= price
        ctx.ledger.add("tech:%s" % args.tech_id, -price, action_index=i)
        prev_of = {t.id: t.previous_tech_id for t in eco.gd.techs.values()}
        ctx.tech_bought[args.mech_id] = _fold_tech(
            list(owned), args.tech_id, prev_of)
        return _receipt(i, kind.value, True, errors.OK, resource_delta=-price,
                        changed_paths=("players[%d].tech_map" % side,
                                       "players[%d].supply" % side))

    if kind is ActionKind.MOVE_UNIT:
        j, u = _find_unit(ctx, args.ref)
        if u is None:
            return _receipt(i, kind.value, False, errors.UNKNOWN_ENTITY)
        if not _in_bounds(args.x, args.y):
            return _receipt(i, kind.value, False, errors.POSITION_OUT_OF_BOUNDS)
        rot = u.is_rotate if args.is_rotate is None else args.is_rotate
        ctx.units[j] = UnitCard(**{**u.__dict__, "x": args.x, "y": args.y,
                                   "is_rotate": rot})
        return _receipt(i, kind.value, True, errors.OK,
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
        return _receipt(i, kind.value, True, errors.OK, resource_delta=refund,
                        removed_entity_id=u.entity_id,
                        changed_paths=("players[%d].units" % side,
                                       "players[%d].supply" % side))

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


def _resolves_to(args, ctx, skill_id: int) -> bool:
    """ReleaseCommanderSkill raw record -> resolved commander skill id.

    ID=0 releases resolve through the player's commanderSkills state
    (SkillIndex -> id, same-round snapshot entries)."""
    raw_id = _raw_get(args, "ID")
    if raw_id == skill_id:
        return True
    if raw_id not in (0, None):
        return False
    sidx = _raw_get(args, "SkillIndex")
    if sidx is None:
        return False
    for entry in ctx_officers(ctx):
        if entry and str(entry[0]) == str(sidx):
            try:
                return int(entry[1]) == skill_id
            except (TypeError, ValueError):
                return False
    return False


def ctx_officers(ctx):
    return getattr(ctx, "commander_skills", ()) or ()


def _in_bounds(x, y) -> bool:
    return abs(x) <= MAP_X and abs(y) <= MAP_Y


def _next_replay_index(ctx) -> int:
    """Game unit Index counter: snapshot max + 1, monotonic, never reused."""
    used = [u.replay_index for u in ctx.units if u.replay_index is not None]
    return (max(used) + 1) if used else 0
