# Shadow prefix environment (task §3.3/§8.1).
#
# One PrefixEnv owns ONE side's deploy phase over a frozen root state:
#   observation -> (PolicyObservationV1, LegalActionSpace)
#   apply(RLAction) -> receipt (with the 用户裁决 noop conversion: unmapped
#   commander skills are "执行了但没有效果" — receipt accepted + fidelity flag)
#
# Both sides of an arena game each run their own PrefixEnv from the SAME root
# state, so neither generation path can observe the other's plan (task §3.3).
# Teacher forcing walks the human norm stream step by step: every successful
# prefix yields one (observation, mask, target action) training sample; the
# first failure is kept as a diagnostic and the walk stops (no labels may be
# taken after an untrusted state, task §8.1).
from __future__ import annotations

from dataclasses import dataclass, field

from ..transition.model import (EnvironmentState, Phase, CanonicalAction,
                                CanonicalActionPlan, ActionKind)
from ..transition.env import TransitionEnv
from ..transition.economy import Economy
from ..gamedata import GameData
from .contracts import MAX_PLAN_ACTIONS, NOOP_REASON_CODES
from .observation import policy_observation
from .masks import (LegalActionSpace, build_action_space, RLAction,
                    to_engine_action)

EXOGENOUS_KINDS = frozenset({ActionKind.GIFT_UNIT, ActionKind.CHOOSE_REINFORCE})


def apply_incomes(state: EnvironmentState, incomes: tuple) -> EnvironmentState:
    """Round income lands at deploy start (mirrors TransitionEnv timing)."""
    import dataclasses
    from ..transition.model import PlayerState
    players = tuple(
        PlayerState(**{**p.__dict__, "supply": p.supply + int(incomes[i])})
        for i, p in enumerate(state.players))
    return dataclasses.replace(state, players=players)


def derive_round_incomes(game: dict, eco: Economy):
    """Snapshot-anchored per-round income: income(r) = supply(r+1) -
    supply(r) + cost(raw actions of r), EXCLUDING tech purchases.

    Rationale (corpus-verified): round-rec techMap already contains the
    round's own tech buys, so the prefix walk skips them (masks.SKIP) and
    their cost must not double-charge here. Snapshot anchoring also absorbs
    every unmodeled refund (construction recycle) and income-model residual,
    keeping the walk's supply exact whenever all executed prices are known.

    Returns ({(side, round): income}, {(side, round): reason}) where the
    second map marks rounds whose price derivation was approximate."""
    out, approx = {}, {}
    for side, pr in enumerate(game["players"]):
        rs = pr["rounds"]
        for i in range(len(rs) - 1):
            r = rs[i]
            rnd = int(r["round"])
            cost = 0
            ok = True
            unknown = None
            for a in r.get("actions") or []:
                t = a.get("type")
                if t == "UpgradeTechnology":
                    continue            # skipped by the walk (techMap quirk)
                try:
                    if t == "BuyUnit":
                        p = eco.buy_price(int(a["UID"]))
                        if p is None:
                            unknown = unknown or "buy:%s" % a.get("UID")
                            continue
                        cost += p
                    elif t == "UnlockUnit":
                        p = eco.unlock_price(int(a["UID"]))
                        if p is None:
                            unknown = unknown or "unlock:%s" % a.get("UID")
                            continue
                        cost += p
                    elif t == "UpgradeUnit":
                        uid = int(a.get("UID", 0) or 0)
                        p = eco.upgrade_price(uid)
                        if p is None:
                            unknown = unknown or "upgrade:%s" % uid
                            continue
                        cost += p
                    elif t == "ChooseReinforceItem":
                        p = eco.item_cost(int(a.get("ID", 0) or 0))
                        if p is None:
                            unknown = unknown or "reinforce:%s" % a.get("ID")
                            continue
                        cost += p
                    elif t == "ActiveBlueprint":
                        from ..transition.deploy import BLUEPRINT_COSTS
                        cost += BLUEPRINT_COSTS.get(int(a.get("ID", 0) or 0), 0)
                    elif t == "StrengthenTower":
                        cost += 100          # TOWER_STRENGTHEN_COST
                    elif t == "ReleaseContraption":
                        from ..transition.deploy import CONTRAPTION_COSTS
                        cost += CONTRAPTION_COSTS.get(
                            str(a.get("ContraptionID")), 0)
                    # MoveUnit / ReleaseCommanderSkill / UseEquipment cost 0
                except (KeyError, TypeError, ValueError):
                    unknown = unknown or "malformed:%s" % t
            out[(side, rnd)] = int(rs[i + 1]["supply"]) \
                - int(r["supply"]) + cost
            if unknown is not None:
                approx[(side, rnd)] = unknown
    return out, approx


@dataclass
class StepOutcome:
    action: RLAction
    receipt_kind: str
    accepted: bool
    reason_code: str
    noop: bool = False                 # True = 执行了但没有效果 (fidelity flag)
    noop_detail: str = ""
    state_digest: str = ""


@dataclass
class PrefixFailure:
    step: int
    kind: str                          # NOT_IN_MASK / REJECTED / NOT_RESOLVED / ...
    detail: str = ""


class PrefixEnv:
    def __init__(self, root: EnvironmentState, ego: int, eco: Economy,
                 gd: GameData, budget: int = MAX_PLAN_ACTIONS):
        self.eco = eco
        self.gd = gd
        self.ego = int(ego)
        self.budget = int(budget)
        self._env = TransitionEnv(gd, eco=eco)
        self._env.reset(root)
        self.steps = 0
        self.outcomes: list[StepOutcome] = []
        self.noop_flags: list[str] = []
        self.engine_log: list = []        # executed CanonicalActions (audit
        #   + joint pre-battle board reconstruction)
        self.failure: PrefixFailure | None = None

    # ---------------------------------------------------------------- view
    @property
    def state(self) -> EnvironmentState:
        return self._env.state

    def observation(self) -> tuple[PolicyObservationV1, LegalActionSpace]:
        from ..transition.rules import buy_limit_quote
        s = self.state
        if s.phase is not Phase.DEPLOYMENT:
            raise ValueError("prefix env finished (phase %s)" % s.phase.value)
        quote = buy_limit_quote(s.players[self.ego])
        obs = policy_observation(
            s, self.ego, buy_remaining=quote.remaining,
            prefix_len=self.steps,
            budget_left=self.budget - self.steps)
        space = build_action_space(s, self.ego, obs, self.eco)
        return obs, space

    # ---------------------------------------------------------------- apply
    def apply(self, a: RLAction) -> StepOutcome:
        obs, _ = self.observation()
        engine = to_engine_action(a, self.ego, obs.handle_map)
        receipt = self._env.apply_player_action(self.ego, engine)
        self.engine_log.append(engine)
        self.steps += 1
        noop = False
        detail = receipt.detail
        if not receipt.accepted and receipt.reason_code in NOOP_REASON_CODES:
            # 用户裁决: 未实现机制按"执行了但没有效果"记账 — plan continues
            noop = True
            detail = "noop:%s %s" % (receipt.reason_code, receipt.detail)
            self.noop_flags.append("verb:%s reason:%s" % (
                a.verb, receipt.reason_code))
        out = StepOutcome(action=a, receipt_kind=str(receipt.kind),
                          accepted=bool(receipt.accepted or noop),
                          reason_code=receipt.reason_code, noop=noop,
                          noop_detail=detail)
        from ..transition.state_tools import state_digest
        out.state_digest = state_digest(self.state)
        self.outcomes.append(out)
        return out

    def apply_engine(self, action: CanonicalAction) -> StepOutcome:
        """Exogenous entries (gift/reinforce) applied straight from the
        canonical stream — no mask, no policy sample."""
        receipt = self._env.apply_player_action(self.ego, action)
        self.engine_log.append(action)
        self.steps += 1
        return StepOutcome(action=RLAction(verb="EXOGENOUS"),
                           receipt_kind=str(receipt.kind),
                           accepted=bool(receipt.accepted),
                           reason_code=receipt.reason_code)


# ---------------------------------------------------------------- teacher
@dataclass
class PrefixWalkResult:
    samples: list[tuple] = field(default_factory=list)   # (obs, space, target)
    final_state: EnvironmentState | None = None
    end_reason: str = ""           # human_end / failure / budget / exog_only
    failure: PrefixFailure | None = None
    noops: list[str] = field(default_factory=list)
    n_exogenous: int = 0
    n_skipped: int = 0
    receipts: list[StepOutcome] = field(default_factory=list)
    engine_actions: tuple = ()     # executed CanonicalActions in order


def teacher_force_walk(root: EnvironmentState, ego: int, norm_entries: list,
                       eco: Economy, gd: GameData,
                       budget: int = MAX_PLAN_ACTIONS) -> PrefixWalkResult:
    """Walk one human deploy stream, yielding per-prefix training samples.

    norm_entries are the (already undo-folded) entries for (ego, round).
    gift/reinforce entries are exogenous (applied, no sample). The walk stops
    at the first failure with a diagnostic (task §8.1)."""
    from .masks import action_from_norm_entry, target_in_mask
    from ..transition.canonicalize import canonicalize_plan

    res = PrefixWalkResult()
    env = PrefixEnv(root, ego, eco, gd, budget=budget)
    for i, e in enumerate(norm_entries):
        t = e.get("t")
        if t in ("gift", "reinforce"):
            plan, rep = canonicalize_plan(ego, [e], norm_report=None)
            if len(plan.actions) != 1:
                res.failure = PrefixFailure(step=i, kind="EXOGENOUS_CANON",
                                            detail=str(rep.notes))
                res.end_reason = "failure"
                return res
            out = env.apply_engine(plan.actions[0])
            res.n_exogenous += 1
            res.receipts.append(out)
            if not out.accepted:
                res.failure = PrefixFailure(
                    step=i, kind="EXOG_REJECTED", detail=out.reason_code)
                res.end_reason = "failure"
                return res
            continue
        if t == "surrender":
            res.end_reason = "surrender"
            break
        if t == "passthrough":
            rt = e.get("raw_type")
            if rt == "ChooseAdvanceTeam":
                continue            # round-0 special entry: nothing to execute
            if rt == "GiveUp":
                res.end_reason = "give_up"
                break
        obs, space = env.observation()
        target = action_from_norm_entry(e, obs)
        if target is None:
            res.failure = PrefixFailure(step=i, kind="UNMAPPED_ENTRY",
                                        detail=t or "?")
            res.end_reason = "failure"
            return res
        from .masks import SKIP
        if target is SKIP:
            res.n_skipped += 1
            continue
        if not target_in_mask(target, space):
            res.failure = PrefixFailure(
                step=i, kind="NOT_IN_MASK",
                detail=str(target.to_dict()))
            res.end_reason = "failure"
            return res
        res.samples.append((obs, space, target))
        out = env.apply(target)
        res.receipts.append(out)
        if not out.accepted:
            res.failure = PrefixFailure(step=i, kind="REJECTED",
                                        detail=out.reason_code)
            res.end_reason = "failure"
            return res
        if target.verb == "END_DEPLOY":
            res.end_reason = "human_end"
            break
        if env.steps >= budget:
            res.end_reason = "budget"
            break
    else:
        if res.end_reason == "":
            # stream ended without an explicit finish entry
            res.end_reason = "stream_end"
    res.final_state = env.state
    res.noops = list(env.noop_flags)
    res.engine_actions = tuple(env.engine_log)
    return res
