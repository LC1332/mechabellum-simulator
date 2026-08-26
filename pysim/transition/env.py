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
        if dep.state.phase is not Phase.PRE_BATTLE:
            return StepResult(state=dep.state, reward=(0.0, 0.0), done=False,
                              deploy_receipts=dep.receipts, ledgers=dep.ledgers,
                              battle_outcome=None,
                              state_digest=state_digest(dep.state),
                              info={"phase": dep.state.phase.value,
                                    "unsupported": dep.unsupported_types})
        if battle_seed is None:
            import random
            battle_seed = random.randrange(1 << 30)
        outcome = run_battle(dep.state, self.gd, battle_seed,
                             opts=self.battle_opts)
        st = settle_transition(dep.state, outcome, max_round=self.max_round,
                               eco=self.eco)
        if st.done:
            self._state = st.state
        else:
            inc = incomes if incomes is not None else self._next_incomes(st.state)
            self._state = advance_round(st.state, self.income_policy, inc,
                                        max_round=self.max_round)
        return StepResult(
            state=self._state, reward=st.reward, done=st.done,
            deploy_receipts=dep.receipts, ledgers=dep.ledgers,
            battle_outcome=outcome, state_digest=state_digest(self._state),
            info={"settled_digest": st.state_digest,
                  "winner": outcome.winner,
                  "damage": tuple(outcome.damage_to_player),
                  "incomes": self._incomes_used,
                  "unsupported": dep.unsupported_types})

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
        ys = [-300.0 + (k + 0.5) * step_y for k in range(move_grid)]
        # moves: a sample of positions (the full grid per unit is huge; the
        # random policy samples unit x target from this product)
        from .model import MoveArgs, EntityRef
        for j, u in enumerate(p.units):
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
                             y=ys[(j + gi) % move_grid], is_rotate=False)))
        from .model import BuyArgs, UnlockArgs, UpgradeArgs, TechArgs
        for mech in sorted(p.unlocked_mechs):
            price = self.eco.buy_price(mech)
            if price is not None and p.supply >= price:
                for (x, y) in ((-100.0, -150.0), (0.0, -150.0), (100.0, -150.0)):
                    out.append(CanonicalAction(
                        ActionKind.BUY_UNIT,
                        BuyArgs(mech_id=mech, x=x, y=y,
                                new_ref=len(p.units))))
        for j, u in enumerate(p.units):
            if u.level < 9:
                need = self.eco.upgrade_exp_need(u.mech_id, u.level)
                price = self.eco.upgrade_price(u.mech_id) or 0
                if need >= 0 and u.exp >= need and p.supply >= price:
                    out.append(CanonicalAction(
                        ActionKind.UPGRADE_UNIT,
                        UpgradeArgs(ref=EntityRef(
                            handle=u.replay_index if u.replay_index is not None
                            else j))))
        for mech, techs in p.tech_map:
            card = self.gd.cards.get(mech)
            for tid in (card.technologies if card else ()):
                if tid in techs:
                    continue
                td = self.gd.techs.get(tid)
                if td is None or (td.previous_tech_id
                                  and td.previous_tech_id not in techs):
                    continue
                price = self.eco.tech_price(mech, tid, len(techs))
                if price is not None and p.supply >= price:
                    out.append(CanonicalAction(
                        ActionKind.BUY_TECH, TechArgs(mech_id=mech, tech_id=tid)))
        for mech in sorted(set(self.gd.cards) - set(p.unlocked_mechs)):
            price = self.eco.unlock_price(mech)
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
