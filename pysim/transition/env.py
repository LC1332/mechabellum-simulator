# TransitionEnv: the joint environment wrapping deploy/battle/settle/advance.
#
# Semantics (任务书 §2): step_joint applies both plans (each may only touch its
# own player), runs ONE battle when both finished, settles, advances the round
# and returns a zero-sum StepResult. save/load round-trips the full state.
from dataclasses import dataclass, field

from ..gamedata import GameData
from . import errors
from .model import (EnvironmentState, CanonicalActionPlan, CanonicalAction,
                    StepResult, Phase, ActionKind, ActionReceipt)
from .state_tools import (state_digest, state_to_dict, state_from_dict,
                          assert_state_invariants)
from .economy import Economy, IncomePolicy
from .deploy import deploy_transition
from .battle_adapter import run_battle
from .settlement import settle_transition, advance_round

MAX_ROUND = 40
MAX_ACTIONS_PER_ROUND = 256


@dataclass
class Observation:
    """Public per-player view (no entity ids, no RNG, no opponent internals
    beyond what the game shows: opponent units are public in Mechabellum)."""
    round: int
    phase: str
    hp: int
    supply: int
    pre_round_fight_result: str | None
    finished_deploy: bool
    units: tuple                 # (handle, mech_id, level, exp, x, y, is_rotate)
    unlocked_mechs: tuple
    tech_map: tuple
    officers: tuple
    opponent_units: tuple        # public board of the other player
    opponent_hp: int


class TransitionEnv:
    def __init__(self, gd: GameData, eco: Economy | None = None,
                 income_policy: IncomePolicy | None = None,
                 battle_opts: dict | None = None, max_round: int = MAX_ROUND):
        self.gd = gd
        self.eco = eco or Economy(gd)
        self.income_policy = income_policy
        self.battle_opts = battle_opts or {}
        self.max_round = max_round
        self._state: EnvironmentState | None = None
        self._incomes_used = (0, 0)

    # ---------------------------------------------------------------- lifecycle
    def reset(self, state: EnvironmentState) -> tuple[Observation, Observation]:
        assert_state_invariants(state)
        if state.phase is Phase.TERMINAL:
            raise errors.TransitionError("TERMINAL_STATE", "reset on terminal")
        self._state = state
        return self.observe(0), self.observe(1)

    @property
    def state(self) -> EnvironmentState:
        if self._state is None:
            raise errors.TransitionError("NO_STATE", "reset() first")
        return self._state

    # ---------------------------------------------------------------- observe
    def observe(self, player: int) -> Observation:
        s = self.state
        p, o = s.players[player], s.players[1 - player]
        units = tuple((j, u.mech_id, u.level, u.exp, u.x, u.y, u.is_rotate)
                      for j, u in enumerate(p.units))
        opp = tuple((u.mech_id, u.level, u.exp, u.x, u.y, u.is_rotate)
                    for u in o.units)
        return Observation(
            round=s.round, phase=s.phase.value, hp=p.hp, supply=p.supply,
            pre_round_fight_result=p.pre_round_fight_result,
            finished_deploy=s.finished_deploy[player], units=units,
            unlocked_mechs=tuple(sorted(p.unlocked_mechs)),
            tech_map=tuple(p.tech_map), officers=tuple(p.officers),
            opponent_units=opp, opponent_hp=o.hp)

    def handles(self, player: int) -> tuple:
        """Observation-local unit handles for action refs."""
        return tuple(range(len(self.state.players[player].units)))

    # ---------------------------------------------------------------- step
    def apply_player_action(self, player: int, action: CanonicalAction) -> ActionReceipt:
        """Single-action API for interactive policies (audit-game frontend)."""
        plan = CanonicalActionPlan(player=player, actions=(action,))
        res = deploy_transition(self.state, (plan,), self.eco)
        self._state = res.state
        return res.receipts[0][0] if res.receipts else \
            ActionReceipt(action_index=0, kind=str(action.kind), accepted=False,
                          reason_code=errors.WRONG_PHASE)

    def step_joint(self, plan0: CanonicalActionPlan, plan1: CanonicalActionPlan,
                   battle_seed: int | None = None,
                   incomes: tuple[int, int] | None = None) -> StepResult:
        s = self.state
        if s.phase is not Phase.DEPLOYMENT:
            raise errors.TransitionError(errors.WRONG_PHASE,
                                         "step_joint needs DEPLOYMENT phase")
        if len(plan0.actions) + len(plan1.actions) > MAX_ACTIONS_PER_ROUND:
            raise errors.TransitionError("ACTION_BUDGET",
                                         "too many actions in one round")
        if incomes is not None:
            # income lands at deploy start, before any action
            s = self._inject_income(s, incomes)
        dep = deploy_transition(s, (plan0, plan1), self.eco)
        self._state = dep.state
        # fast-supply debts: blueprint 1 activations survive the round ->
        # next round's income owes -300 each (Income200r.fast_debts)
        self._record_fast_supply(dep.ledgers, s.round)
        if dep.state.phase is Phase.TERMINAL:
            # battlefield M1: typed SURRENDER ends the match atomically —
            # no battle, no settlement; the surrendering side loses
            return self._surrender_result(dep)
        if dep.state.phase is not Phase.PRE_BATTLE:
            return StepResult(state=dep.state, reward=(0.0, 0.0), done=False,
                              deploy_receipts=dep.receipts, ledgers=dep.ledgers,
                              battle_outcome=None,
                              state_digest=state_digest(dep.state),
                              info={"phase": dep.state.phase.value,
                                    "unsupported": dep.unsupported_types})
        return self._battle_settle_advance(dep, battle_seed)

    def _surrender_result(self, dep) -> StepResult:
        """Terminal view of a surrender: zero-sum ±1 reward, no outcome."""
        reason = dep.state.terminal_reason or "surrender"
        # "surrender:player0" means player 0 surrendered -> player 0 loses
        loser = 0 if reason.endswith("player0") else \
            (1 if reason.endswith("player1") else -1)
        if loser == 0:
            reward = (-1.0, 1.0)
        elif loser == 1:
            reward = (1.0, -1.0)
        else:
            reward = (0.0, 0.0)
        return StepResult(
            state=dep.state, reward=reward, done=True,
            deploy_receipts=dep.receipts, ledgers=dep.ledgers,
            battle_outcome=None, state_digest=state_digest(dep.state),
            info={"phase": "terminal", "terminal_reason": reason,
                  "unsupported": dep.unsupported_types})

    def finish_round(self, battle_seed: int | None = None,
                     with_trace: bool = False) -> StepResult:
        """Interactive path: battle+settle+advance once both players finished
        deploying (state already PRE_BATTLE after the last END_DEPLOY).
        Same single simulate as step_joint; with_trace additionally returns
        the engine trace in info (settlement never parses it)."""
        s = self.state
        if s.phase is not Phase.PRE_BATTLE:
            raise errors.TransitionError(errors.WRONG_PHASE,
                                         "finish_round needs PRE_BATTLE state")
        from .deploy import DeployResult
        dep = DeployResult(
            state=s, receipts=((), ()), ledgers=self._round_ledgers(),
            unsupported_types=())
        return self._battle_settle_advance(dep, battle_seed,
                                           with_trace=with_trace)

    def add_incomes(self, incomes: tuple[int, int]):
        """Public income injection at deploy start (round 1 after the
        opening; mirrors step_joint's income timing)."""
        s = self.state
        if s.phase is not Phase.DEPLOYMENT:
            raise errors.TransitionError(errors.WRONG_PHASE,
                                         "add_incomes needs DEPLOYMENT")
        self._state = self._inject_income(s, incomes)

    def _round_ledgers(self):
        """Zero-entry ledgers for the finish_round path (per-action deploy
        calls already recorded their entries; the caller aggregates them)."""
        from .economy import SupplyLedger
        return tuple(SupplyLedger(supply_before=p.supply)
                     for p in self.state.players)

    def _record_fast_supply(self, ledgers, round_no: int):
        if hasattr(self.income_policy, "record_fast_supply"):
            for side, led in enumerate(ledgers):
                n_fast = sum(1 for e in led.entries
                             if e.reason == "blueprint_loan:+200")
                if n_fast:
                    self.income_policy.record_fast_supply(
                        side, round_no + 1, n_fast)

    def _battle_settle_advance(self, dep, battle_seed, with_trace: bool = False,
                               incomes=None) -> StepResult:
        if battle_seed is None:
            import random
            battle_seed = random.randrange(1 << 30)
        outcome = run_battle(dep.state, self.gd, battle_seed,
                             opts=self.battle_opts, with_trace=with_trace)
        trace_extra = None
        if with_trace:
            outcome, trace_extra = outcome
        st = settle_transition(dep.state, outcome, max_round=self.max_round,
                               eco=self.eco)
        if st.done:
            self._state = st.state
        else:
            # incomes==None lets advance_round ask the policy for the NEW
            # round (settled.round + 1); precomputing here would be off by
            # one (income lands at the deploy start of the incoming round)
            self._state = advance_round(st.state, self.income_policy, incomes,
                                        max_round=self.max_round, gd=self.gd)
        info = {"settled_digest": st.state_digest,
                "winner": outcome.winner,
                "damage": tuple(outcome.damage_to_player),
                "incomes": self._incomes_used,
                "unsupported": dep.unsupported_types}
        if trace_extra is not None:
            info["trace"] = trace_extra["trace"]
            info["battle_extra"] = trace_extra
        return StepResult(
            state=self._state, reward=st.reward, done=st.done,
            deploy_receipts=dep.receipts, ledgers=dep.ledgers,
            battle_outcome=outcome, state_digest=state_digest(self._state),
            info=info)

    def _next_incomes(self, state: EnvironmentState):
        if self.income_policy is None:
            return (0, 0)
        return tuple(int(self.income_policy.income(
            side, state.players[side], state.round,
            state.players[side].pre_round_fight_result)) for side in (0, 1))

    def _inject_income(self, s: EnvironmentState, incomes):
        from .model import PlayerState
        players = tuple(
            PlayerState(**{**s.players[i].__dict__,
                           "supply": s.players[i].supply + int(incomes[i])})
            for i in (0, 1))
        self._incomes_used = tuple(int(v) for v in incomes)
        return EnvironmentState(
            schema_version=s.schema_version, ruleset_version=s.ruleset_version,
            engine_version=s.engine_version, round=s.round, phase=s.phase,
            players=players, finished_deploy=s.finished_deploy,
            next_entity_id=s.next_entity_id, terminal_reason=s.terminal_reason,
            provenance=s.provenance)

    # ---------------------------------------------------------------- legal
    def legal_action_candidates(self, player: int, move_grid: int = 10) -> tuple:
        """Candidate actions for random/policy sampling (same rule source as
        apply: candidates are checked again at apply time; the accept/reject
        agreement is asserted by tests)."""
        s = self.state
        if s.phase is not Phase.DEPLOYMENT:
            return ()
        p = s.players[player]
        if s.finished_deploy[player]:
            return ()
        out = [CanonicalAction(ActionKind.END_DEPLOY, None)]
        step_x, step_y = 700.0 / move_grid, 600.0 / move_grid
        xs = [-350.0 + (k + 0.5) * step_x for k in range(move_grid)]
        # moves sample the acting player's OWN half only (side 0 y<0, side 1
        # y>0), so every candidate stays inside the deploy zone
        half0 = [-300.0 + (k + 0.5) * step_y for k in range(move_grid // 2)]
        ys = half0 if player == 0 else [-y for y in half0]
        # moves: a sample of positions (the full grid per unit is huge; the
        # random policy samples unit x target from this product). Only
        # units with movement permission are candidates (step4 任务书 §1.2 —
        # same rule source as MOVE_UNIT's deploy check).
        from .model import MoveArgs, EntityRef
        from .rules import movement_permission
        for j, u in enumerate(p.units):
            if not movement_permission(p, u).allowed:
                continue
            h = u.replay_index if u.replay_index is not None else j
            out.append(CanonicalAction(
                ActionKind.MOVE_UNIT,
                MoveArgs(ref=EntityRef(handle=h), x=u.x, y=u.y, is_rotate=None)))
            out.append(CanonicalAction(
                ActionKind.MOVE_UNIT,
                MoveArgs(ref=EntityRef(handle=h), x=u.x, y=u.y,
                         is_rotate=(not u.is_rotate))))
            for gi in range(move_grid):
                out.append(CanonicalAction(
                    ActionKind.MOVE_UNIT,
                    MoveArgs(ref=EntityRef(handle=h), x=xs[gi],
                             y=ys[(j + gi) % len(ys)], is_rotate=False)))
        from .model import BuyArgs, UnlockArgs, UpgradeArgs, TechArgs
        from .rules import buy_limit_quote, unlock_limit_quote
        buy_quote = buy_limit_quote(p)
        for mech in sorted(p.unlocked_mechs):
            price = self.eco.buy_price(mech)
            if price is not None and p.supply >= price \
                    and buy_quote.remaining > 0:
                # buy candidates sit inside the acting player's own half
                # (deploy.in_own_half: side 0 y<0, side 1 y>0)
                by = -150.0 if player == 0 else 150.0
                for (x, y) in ((-100.0, by), (0.0, by), (100.0, by)):
                    out.append(CanonicalAction(
                        ActionKind.BUY_UNIT,
                        BuyArgs(mech_id=mech, x=x, y=y,
                                new_ref=len(p.units))))
        for j, u in enumerate(p.units):
            if u.level < 9:
                # same rule source as deploy's UPGRADE_UNIT: NO exp gate
                # (corpus 455/455), officers mod + 强化模块 discount applied
                from .deploy import (UPGRADE_DISCOUNT_EQUIPMENT,
                                     UPGRADE_DISCOUNT_AMOUNT,
                                     FEATURES as DEPLOY_FEATURES)
                price = self.eco.upgrade_price(u.mech_id) or 0
                price = max(0, price + self.eco.upgrade_price_mod(
                    u.mech_id, p.officers))
                if DEPLOY_FEATURES["equipment_upgrade_discount"] \
                        and int(u.equipment_id or 0) == \
                        UPGRADE_DISCOUNT_EQUIPMENT:
                    price = max(0, price - UPGRADE_DISCOUNT_AMOUNT)
                if p.supply >= price:
                    out.append(CanonicalAction(
                        ActionKind.UPGRADE_UNIT,
                        UpgradeArgs(ref=EntityRef(
                            handle=u.replay_index if u.replay_index is not None
                            else j))))
        # tech candidates follow the FIELD mechs (step3 任务书 §4.1): the
        # snapshot tech_map alone hides buyable first techs of newly bought
        # mechs and leaks mechs whose last unit left the field
        tech_owned_map = {int(m): tuple(t) for m, t in p.tech_map}
        for mech in sorted({u.mech_id for u in p.units}):
            card = self.gd.cards.get(mech)
            techs = tech_owned_map.get(mech, ())
            for tid in (card.technologies if card else ()):
                if tid in techs:
                    continue
                td = self.gd.techs.get(tid)
                if td is None or (td.previous_tech_id
                                  and td.previous_tech_id not in techs):
                    continue
                price = self.eco.tech_price(mech, tid, len(techs), p.officers)
                if price is not None and p.supply >= price:
                    out.append(CanonicalAction(
                        ActionKind.BUY_TECH, TechArgs(mech_id=mech, tech_id=tid)))
        # T11.2 (same rule source as the executor): once this round's manual
        # unlock quota is spent, no new UNLOCK_UNIT candidates are enumerated
        if unlock_limit_quote(p).remaining > 0:
            for mech in sorted(set(self.gd.cards) - set(p.unlocked_mechs)):
                price = self.eco.unlock_price(mech, p.officers)
                if price is not None and p.supply >= price:
                    out.append(CanonicalAction(
                        ActionKind.UNLOCK_UNIT, UnlockArgs(mech_id=mech)))
        return tuple(out)

    # ---------------------------------------------------------------- save/load
    def save(self) -> dict:
        return {"state": state_to_dict(self.state),
                "income_policy": getattr(self.income_policy, "name", None),
                "battle_opts": dict(self.battle_opts),
                "max_round": self.max_round}

    def load(self, snapshot: dict) -> None:
        self._state = state_from_dict(snapshot["state"])
        assert_state_invariants(self._state)
