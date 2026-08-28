# Arena: single-round direct-pysim matchups + best-of-N (task §9).
#
# Fairness invariants: both plans generate from the SAME root via
# independent PrefixEnvs; the direct pysim (common seeds) is the judge —
# V_sim only pre-filters; every matchup plays ego side swap; bootstrap CIs
# are computed per replay group.
from __future__ import annotations

import time

import numpy as np
import torch
import torch.nn.functional as F

from .masks import RLAction, VERB_INDEX, ALL_VERBS
from .prefix_env import PrefixEnv
from .contracts import MAX_PLAN_ACTIONS

HEAD_FOR_VERB = {
    "BUY_UNIT": ("mech", "xy", "rot_buy"),
    "UNLOCK_UNIT": ("mech",),
    "UPGRADE_UNIT": ("unit",),
    "SELL_UNIT": ("unit",),
    "MOVE_UNIT": ("unit", "xy", "rot_move"),
    "BUY_TECH": ("tech",),
    "USE_EQUIPMENT": ("equip", "unit"),
    "RELEASE_COMMANDER_SKILL": ("skill",),
    "ACTIVATE_ENERGY_TOWER_SKILL": ("tower",),
    "STRENGTHEN_TOWER": ("strengthen",),
    "ACTIVE_BLUEPRINT": ("bp",),
    "RELEASE_CONTRAPTION": ("contr", "xy"),
    "END_DEPLOY": (),
}

# which candidate pool a pointer head reads from a LegalActionSpace
POOL = {
    "mech": lambda s: (s.mech_cands, s.mech_mask.get("BUY_UNIT", []) or
                       s.mech_mask.get("UNLOCK_UNIT", [])),
    "tech": lambda s: (s.tech_cands, s.tech_mask),
    "equip": lambda s: (s.equip_cands, s.equip_mask),
    "skill": lambda s: (s.skill_cands, s.skill_mask),
    "tower": lambda s: (s.tower_cands, s.tower_mask),
    "bp": lambda s: (s.blueprint_cands, s.blueprint_mask),
    "contr": lambda s: (s.contraption_cands, s.contraption_mask),
}


class BCPolicy:
    """Masked decoding of pi_BC inside a live PrefixEnv (task §8.2).

    mode: greedy | sample. Zero rejection by construction: after the verb is
    chosen, pointer scores are masked by THAT verb's candidate mask; a verb
    whose required head has no legal candidate is skipped (next-best verb)
    or falls back to END_DEPLOY."""

    def __init__(self, checkpoint: str, mode: str = "greedy",
                 temperature: float = 1.0, device: str | None = None,
                 seed: int = 0):
        ck = torch.load(checkpoint, map_location="cpu", weights_only=False)
        from .models.policy_bc import PolicyBC
        from .features import Vocab
        self.vocab = Vocab.from_dict(ck["vocab"])
        self.model = PolicyBC(self.vocab.n_mech, self.vocab.n_equip,
                              self.vocab.n_tech)
        self.model.load_state_dict(ck["model"])
        self.device = device or ("cuda" if torch.cuda.is_available()
                                 else "cpu")
        self.model.to(self.device).eval()
        self.mode = mode
        self.temperature = temperature
        self.rng = np.random.RandomState(seed)

    def act(self, obs, space):
        from .features import policy_features
        from tools.train_policy_bc import collate  # repo-root import
        f = policy_features(obs.to_dict(), space_to_dict(space), self.vocab)
        batch = collate([f], self.vocab, self.device)
        with torch.no_grad():
            out = self.model(batch, batch["space"])
        verb_order = self._rank_verbs(out, space)
        for v in verb_order:
            a = self._instantiate(v, out, space, obs)
            if a is not None:
                return a
        return RLAction("END_DEPLOY")

    def _rank_verbs(self, out, space):
        logits = out["verb_logits"][0]
        vmask = torch.as_tensor(np.asarray([space.verb_mask]),
                                dtype=torch.float32,
                                device=logits.device)
        logits = logits + (1.0 - vmask[0]) * -1e9
        if self.mode == "greedy":
            order = torch.argsort(logits, descending=True).tolist()
        else:
            probs = F.softmax(logits / self.temperature, dim=-1)
            # sample verbs without replacement until exhausted
            order = torch.multinomial(probs, len(probs),
                                      replacement=False).tolist()
        return [ALL_VERBS[i] for i in order]

    def _instantiate(self, v, out, space, obs):
        heads = HEAD_FOR_VERB.get(v)
        if heads is None or not space.verb_allowed(v):
            return None
        a = RLAction(v)

        def pick(head):
            """Masked pointer argmax/sample; None when no legal candidate."""
            cands, mask = POOL[head](space)
            cands = list(cands)
            legal = [i for i, ok in enumerate(mask) if ok]
            if not legal:
                return None
            key = {"mech": "mech_scores", "tech": "tech_scores",
                   "equip": "equip_scores", "skill": "skill_scores",
                   "tower": "tower_scores", "bp": "bp_scores",
                   "contr": "contr_scores"}[head]
            scores = out[key][0]
            legal_t = torch.as_tensor(legal, dtype=torch.long,
                                      device=scores.device)
            sc = scores[legal_t]
            if self.mode == "greedy":
                j = legal[int(sc.argmax().item())]
            else:
                probs = F.softmax(sc / self.temperature, dim=-1)
                j = legal[int(torch.multinomial(probs, 1).item())]
            return cands[j]

        for head in heads:
            if head == "xy":
                mu = out["xy_mu"][0]
                if self.mode == "greedy":
                    a.x = float(mu[0]) * 350.0
                    a.y = float(mu[1]) * 300.0
                else:
                    ls = out["xy_logscale"][0]
                    a.x = float(torch.normal(mu[0], ls.exp()[0])) * 350.0
                    a.y = float(torch.normal(mu[1], ls.exp()[1])) * 300.0
                lo_x, hi_x, lo_y, hi_y = space.xy_bounds(v)
                a.x = float(np.clip(a.x, lo_x, hi_x))
                a.y = float(np.clip(a.y, lo_y, hi_y))
            elif head == "rot_move":
                r = int(out["rot_move_logits"][0].argmax().item())
                a.rot = r
            elif head == "rot_buy":
                r = int(out["rot_buy_logits"][0].argmax().item())
                a.rot = 1 if r == 1 else 2
            elif head == "mech":
                which = "BUY_UNIT" if space.verb_mask[
                    VERB_INDEX["BUY_UNIT"]] else "UNLOCK_UNIT"
                cands, mask = POOL["mech"](space)
                legal = [i for i, ok in enumerate(mask) if ok]
                # use the verb-specific mask
                vmask = space.mech_mask.get(v) or mask
                legal = [i for i, ok in enumerate(vmask) if ok]
                if not legal:
                    return None
                scores = out["mech_scores"][0]
                if self.mode == "greedy":
                    j = legal[int(scores[legal].argmax().item())]
                else:
                    probs = F.softmax(scores[legal] / self.temperature, -1)
                    j = legal[int(torch.multinomial(probs, 1).item())]
                a.mech = cands[j]
            elif head == "strengthen":
                legal = [i for i, ok in enumerate(space.strengthen_mask)
                         if ok]
                if not legal:
                    return None
                a.tower_index = legal[0] if self.mode == "greedy" \
                    else self.rng.choice(legal)
            elif head == "unit":
                legal = [h for h, ok in zip(space.unit_cands,
                                            space.unit_mask.get(
                                                v, [True] * len(
                                                    space.unit_cands)))
                         if ok]
                if not legal:
                    return None
                scores = out["unit_scores"][0]
                if self.mode == "greedy":
                    h = legal[int(scores[legal].argmax().item())]
                else:
                    probs = F.softmax(scores[legal] / self.temperature, -1)
                    h = legal[int(torch.multinomial(probs, 1).item())]
                a.handle = h
            else:
                val = pick(head)
                if val is None:
                    return None
                if head == "tech":
                    a.tech = tuple(val)
                elif head == "skill":
                    a.skill_slot, a.skill_id = val
                elif head == "tower":
                    a.tower = val
                elif head == "bp":
                    a.blueprint = val
                elif head == "contr":
                    a.contraption = val
                elif head == "equip":
                    a.equip = val
        # skill releases with a position target need coordinates
        if v == "RELEASE_COMMANDER_SKILL":
            kinds = space.skill_target
            for i, (slot, sid) in enumerate(space.skill_cands):
                if a.skill_slot == slot:
                    if kinds[i] == "position":
                        if a.x is None:
                            a.x = 0.0
                            a.y = -150.0
                    break
        return a


def space_to_dict(space):
    from .masks import space_to_dict as _s
    return _s(space)


# ---------------------------------------------------------------- match
def generate_plan(root, ego, policy, eco, gd, budget=MAX_PLAN_ACTIONS):
    """Free-run one side's deploy plan from the root (independent shadow)."""
    env = PrefixEnv(root, ego, eco, gd, budget=budget)
    t0 = time.time()
    actions = []
    forced = False
    while env.steps < budget:
        obs, space = env.observation()
        a = policy.act(obs, space)
        actions.append(a)
        env.apply(a)
        if a.verb == "END_DEPLOY":
            break
    else:
        forced = True
    return {"actions": actions, "engine_actions": tuple(env.engine_log),
            "noops": list(env.noop_flags), "forced_end": forced,
            "steps": env.steps, "latency_s": time.time() - t0,
            "final_state": env.state}


def play_joint(root, plan0, plan1, eco, gd, battle_seed):
    """Execute both plans jointly on one env, then ONE direct pysim.

    A plan that never emitted END_DEPLOY (BC stopping failure) is force-
    ended here and counted in `forced_ends` — the match stays playable and
    the T3 stopping gate is measured from the plan stats, not hidden.
    Rejections follow the SAME semantics as training: an engine refusal in
    NOOP_REASON_CODES is the accepted no-op (执行了但没有效果) and is
    counted in `noops`, never as a rejection."""
    from pysim.transition.model import (CanonicalActionPlan, CanonicalAction,
                                        ActionKind)
    from pysim.transition.deploy import deploy_transition
    from pysim.transition.env import TransitionEnv
    from pysim.transition.battle_adapter import run_battle
    from .contracts import NOOP_REASON_CODES
    env = TransitionEnv(gd, eco=eco)
    env.reset(root)
    rejects = []
    noops = []
    forced_ends = []
    for side, plan in ((0, plan0), (1, plan1)):
        actions = list(plan["engine_actions"])
        if not (actions and actions[-1].kind is ActionKind.END_DEPLOY):
            actions.append(CanonicalAction(ActionKind.END_DEPLOY, None))
            forced_ends.append(side)
        dep = deploy_transition(env.state,
                                (CanonicalActionPlan(player=side,
                                                     actions=actions),),
                                eco)
        env._state = dep.state
        for k, rec in enumerate(dep.receipts[0]):
            if rec.accepted:
                continue
            if rec.reason_code in NOOP_REASON_CODES:
                noops.append((side, str(rec.kind), rec.reason_code))
            else:
                rejects.append((side, str(rec.kind), rec.reason_code,
                                str(actions[k].args)[:80]))
    pre_battle = env.state
    if pre_battle.phase.value != "pre_battle":
        raise RuntimeError("joint execution did not reach PRE_BATTLE: %s"
                           % pre_battle.phase.value)
    outcome = run_battle(pre_battle, gd, battle_seed)
    return {
        "winner": int(outcome.winner),
        "damage_to_player": tuple(int(d) for d in outcome.damage_to_player),
        "end_time": float(outcome.end_time),
        "rejections": rejects,
        "noops": noops,
        "forced_ends": forced_ends,
        "fidelity_warnings": list(outcome.fidelity_warnings),
    }


def ego_reward(res, ego):
    """Zero-sum-ish reward in ego terms (damage diff normalized)."""
    hp = res.get("_max_hp", (4500, 4500))
    d_opp = res["damage_to_player"][1 - ego] / max(1, hp[1 - ego])
    d_self = res["damage_to_player"][ego] / max(1, hp[ego])
    r = d_opp - d_self
    if res["winner"] == ego:
        r += 0.05
    elif res["winner"] == 1 - ego:
        r -= 0.05
    return r


# ------------------------------------------------------------- exploit
def detect_exploits(plan, root_state):
    """Return exploit flags for a generated plan (task §9.4)."""
    flags = []
    acts = plan["actions"]
    if plan["forced_end"]:
        flags.append("forced_end")
    if plan["steps"] >= MAX_PLAN_ACTIONS:
        flags.append("overlong")
    # overlapping units / extreme coordinates on the final board
    units = list(plan["final_state"].players[0].units) \
        if plan.get("final_state") is not None else []
    pts = [(u.x, u.y) for u in units]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if abs(pts[i][0] - pts[j][0]) < 5 and \
                    abs(pts[i][1] - pts[j][1]) < 5:
                flags.append("overlap_units")
                break
        else:
            continue
        break
    for x, y in pts:
        if abs(x) > 351 or abs(y) > 301:
            flags.append("extreme_coord")
            break
    # repeated identical actions (no-op loop)
    sig = [a.to_dict() for a in acts]
    if len(sig) > 4:
        for i in range(len(sig) - 3):
            if sig[i] == sig[i + 1] == sig[i + 2] == sig[i + 3]:
                flags.append("repeated_action")
                break
    return sorted(set(flags))
