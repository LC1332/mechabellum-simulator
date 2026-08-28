# Simple non-learned deploy policies over the PrefixEnv mask layer (task
# §5.4 counterfactuals + §9.2 arena baselines). Every action these policies
# emit is mask-legal by construction, so their plans execute with zero
# rejection — the property the T3/T4 gates demand from pi_BC as well.
from __future__ import annotations

import random

from .masks import RLAction, VERB_INDEX
from .prefix_env import PrefixEnv

BUY_Y_MID = -150.0


class RandomLegalPolicy:
    """Uniform over legal verbs (END weighted to keep plans short)."""

    def __init__(self, seed: int = 0, end_prob: float = 0.12):
        self.rng = random.Random(seed)
        self.end_prob = end_prob

    def act(self, obs, space) -> RLAction:
        rng = self.rng
        allowed = [v for v, ok in zip(space.verbs, space.verb_mask) if ok]
        if not allowed:
            return RLAction("END_DEPLOY")
        if rng.random() < self.end_prob or "END_DEPLOY" not in allowed:
            return RLAction("END_DEPLOY")
        v = rng.choice([a for a in allowed if a != "END_DEPLOY"])
        return self._instantiate(v, obs, space, rng)

    def _instantiate(self, v, obs, space, rng):
        a = RLAction(v)
        if v in ("BUY_UNIT", "UNLOCK_UNIT"):
            cands = [m for m, ok in zip(space.mech_cands,
                                        space.mech_mask[v]) if ok]
            if not cands:
                return RLAction("END_DEPLOY")
            a.mech = rng.choice(cands)
            lo_x, hi_x, lo_y, hi_y = space.xy_bounds(v)
            a.x = rng.uniform(lo_x, hi_x)
            a.y = rng.uniform(lo_y, hi_y)
            a.rot = rng.choice((0, 1))
        elif v in ("UPGRADE_UNIT", "SELL_UNIT", "MOVE_UNIT"):
            cands = [h for h, ok in zip(space.unit_cands,
                                        space.unit_mask[v]) if ok]
            if not cands:
                return RLAction("END_DEPLOY")
            a.handle = rng.choice(cands)
            if v == "MOVE_UNIT":
                lo_x, hi_x, lo_y, hi_y = space.xy_bounds(v)
                a.x = rng.uniform(lo_x, hi_x)
                a.y = rng.uniform(lo_y, hi_y)
                a.rot = rng.choice((0, 1, 2))
        elif v == "BUY_TECH":
            cands = [t for t, ok in zip(space.tech_cands,
                                        space.tech_mask) if ok]
            if not cands:
                return RLAction("END_DEPLOY")
            a.tech = tuple(rng.choice(cands))
        elif v == "USE_EQUIPMENT":
            ok_e = [e for e, ok in zip(space.equip_cands,
                                       space.equip_mask) if ok]
            if not ok_e:
                return RLAction("END_DEPLOY")
            a.equip = rng.choice(ok_e)
            a.handle = rng.choice(space.unit_cands)
        elif v == "RELEASE_COMMANDER_SKILL":
            cands = [i for i, ok in enumerate(space.skill_mask) if ok]
            if not cands:
                return RLAction("END_DEPLOY")
            i = rng.choice(cands)
            slot, sid = space.skill_cands[i]
            a.skill_slot = slot
            a.skill_id = sid
            kind = space.skill_target[i]
            if kind == "position":
                lo_x, hi_x, lo_y, hi_y = space.xy_bounds(v)
                a.x = rng.uniform(lo_x, hi_x)
                a.y = rng.uniform(lo_y, hi_y)
            elif kind == "unit" and space.unit_cands:
                a.handle = rng.choice(space.unit_cands)
        elif v == "ACTIVATE_ENERGY_TOWER_SKILL":
            cands = [t for t, ok in zip(space.tower_cands,
                                        space.tower_mask) if ok]
            if not cands:
                return RLAction("END_DEPLOY")
            a.tower = rng.choice(cands)
        elif v == "STRENGTHEN_TOWER":
            ti = [i for i, ok in enumerate(space.strengthen_mask) if ok]
            if not ti:
                return RLAction("END_DEPLOY")
            a.tower_index = rng.choice(ti)
        elif v == "ACTIVE_BLUEPRINT":
            cands = [b for b, ok in zip(space.blueprint_cands,
                                        space.blueprint_mask) if ok]
            if not cands:
                return RLAction("END_DEPLOY")
            a.blueprint = rng.choice(cands)
        elif v == "RELEASE_CONTRAPTION":
            cands = [c for c, ok in zip(space.contraption_cands,
                                        space.contraption_mask) if ok]
            if not cands:
                return RLAction("END_DEPLOY")
            a.contraption = rng.choice(cands)
            lo_x, hi_x, lo_y, hi_y = space.xy_bounds(v)
            a.x = rng.uniform(lo_x, hi_x)
            a.y = rng.uniform(lo_y, hi_y)
        return a

    def plan(self, env: PrefixEnv, max_steps: int = 64):
        """Free-run a full plan; returns (actions, walk-like env)."""
        actions = []
        while env.steps < max_steps:
            obs, space = env.observation()
            a = self.act(obs, space)
            actions.append(a)
            env.apply(a)
            if a.verb == "END_DEPLOY":
                break
        return actions


class HeuristicPolicy(RandomLegalPolicy):
    """Greedy economy policy: buy the most expensive affordable mech (up to
    quota), one reposition per movable unit, then END."""

    def __init__(self, eco=None, seed: int = 0):
        super().__init__(seed=seed)
        self.eco = eco
        self._moved = None

    def act(self, obs, space):
        rng = self.rng
        if obs.prefix_len == 0 or self._moved is None:
            self._moved = set()
        # 1) buy the priciest affordable mech while quota lasts
        if space.verb_allowed("BUY_UNIT"):
            cands = [m for m, ok in
                     zip(space.mech_cands, space.mech_mask["BUY_UNIT"]) if ok]
            if cands:
                if self.eco is not None:
                    mech = max(cands, key=lambda m: self.eco.buy_price(m) or 0)
                else:
                    mech = max(cands)
                return RLAction("BUY_UNIT", mech=mech,
                                x=rng.uniform(-250, 250), y=-150.0, rot=0)
        # 2) one move per movable unit toward the mid-line
        if space.verb_allowed("MOVE_UNIT"):
            cands = [h for h, ok in zip(space.unit_cands,
                                        space.unit_mask["MOVE_UNIT"])
                     if ok and h not in self._moved]
            if cands:
                h = cands[0]
                self._moved.add(h)
                u = obs.units[h]
                return RLAction("MOVE_UNIT", handle=h,
                                x=u["x"], y=min(u["y"] + 80.0, -10.0),
                                rot=2)
        self._moved = None
        return RLAction("END_DEPLOY")


class EndOnlyPolicy:
    def act(self, obs, space):
        return RLAction("END_DEPLOY")

    def plan(self, env: PrefixEnv, max_steps: int = 64):
        obs, space = env.observation()
        env.apply(RLAction("END_DEPLOY"))
        return [RLAction("END_DEPLOY")]
