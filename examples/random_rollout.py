# Endpoint B: random legal-policy episodes (env soak / RL smoke test).
#
#   python examples/random_rollout.py --episodes 100 --seed 7
#   python examples/random_rollout.py --episodes 1000 --seed 7 \
#       --report /tmp/random_rollout_report.json
#
# The policy only uses observe()/legal_action_candidates()/apply; every
# episode must terminate (hp zero or max round) with zero state-invariant
# failures and a zero-sum reward.
import argparse
import io
import json
import os
import random
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                  errors="replace")
except Exception:
    pass

from pysim.gamedata import GameData
from pysim.transition import (TransitionEnv, Economy, FixedIncome,
                              EnvironmentState, PlayerState, UnitCard, Phase,
                              ActionKind, CanonicalAction, CanonicalActionPlan,
                              assert_state_invariants, state_digest)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sandbox_state(hp=4500, supply=400, max_round=40) -> EnvironmentState:
    """Fixed symmetric sandbox opening: a few starter units per side."""
    units = []
    eid = 1
    for side, y in ((0, -150.0), (1, 150.0)):
        for i, mech in enumerate((10, 7)):
            for k in range(2):
                units.append(UnitCard(entity_id=eid, mech_id=mech, level=1,
                                      exp=0, x=-60.0 + k * 60.0 + i * 0.0,
                                      y=y, replay_index=eid - 1,
                                      sell_supply=100))
                eid += 1
    players = []
    for side in (0, 1):
        players.append(PlayerState(
            hp=hp, max_hp=hp, supply=supply, pre_round_fight_result=None,
            units=tuple(u for u in units
                        if (u.y < 0) == (side == 0)),
            unlocked_mechs=frozenset({2, 7, 8, 9, 10, 13, 15, 20, 21, 28}),
            tech_map=((10, ()), (7, ()))))
    return EnvironmentState(
        schema_version="transition-v0.3", ruleset_version="sandbox_v0",
        engine_version="pysim-step29", round=1, phase=Phase.DEPLOYMENT,
        players=tuple(players), next_entity_id=eid,
        provenance=(("mode", "random_rollout_sandbox"),))


class RandomLegalPolicy:
    """Samples only from legal_action_candidates; finishes with probability
    `finish_p`, and ALWAYS finishes when nothing else is legal."""

    def __init__(self, rng: random.Random, finish_p=0.15, max_actions=30):
        self.rng = rng
        self.finish_p = finish_p
        self.max_actions = max_actions

    def plan(self, env: TransitionEnv, player: int) -> CanonicalActionPlan:
        acts = []
        for _ in range(self.max_actions):
            cands = [a for a in env.legal_action_candidates(player)
                     if a.kind is not ActionKind.END_DEPLOY]
            finish = CanonicalAction(ActionKind.END_DEPLOY, None)
            if not cands or self.rng.random() < self.finish_p:
                acts.append(finish)
                return CanonicalActionPlan(player=player,
                                           actions=tuple(acts))
            acts.append(self.rng.choice(cands))
        acts.append(finish)
        return CanonicalActionPlan(player=player, actions=tuple(acts))


def run_episode(env, policies, seed, max_round=40):
    import random as _r
    rngs = [_r.Random(seed), _r.Random(seed + 1)]
    env.reset(sandbox_state())
    rounds = 0
    rejected = Counter()
    total_reward = [0.0, 0.0]
    while not env.state.phase is Phase.TERMINAL and rounds < max_round:
        plans = tuple(policies[p].plan(env, p) for p in (0, 1))
        step = env.step_joint(plans[0], plans[1], battle_seed=seed * 31 + rounds)
        for rs in step.deploy_receipts:
            for r in rs:
                if not r.accepted:
                    rejected[r.reason_code] += 1
        total_reward[0] += step.reward[0]
        total_reward[1] += step.reward[1]
        rounds += 1
    return {"rounds": rounds, "rejected": dict(rejected),
            "reward": total_reward, "done": env.state.phase is Phase.TERMINAL,
            "final_hp": [env.state.players[0].hp, env.state.players[1].hp]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    eco = Economy(gd)
    env = TransitionEnv(gd, eco, income_policy=FixedIncome(200))
    results = []
    t0 = time.time()
    failures = Counter()
    for ep in range(args.episodes):
        seed = args.seed + ep * 1000
        policies = [RandomLegalPolicy(random.Random(seed)),
                    RandomLegalPolicy(random.Random(seed + 5000))]
        try:
            res = run_episode(env, policies, seed)
            results.append(res)
            if not res["done"]:
                failures["not_terminated"] += 1
            if abs(sum(res["reward"])) > 1e-9:
                failures["reward_nonzero_sum"] += 1
            for reason, n in res["rejected"].items():
                failures["rejected:%s" % reason] += n
        except Exception as ex:                    # noqa: BLE001
            failures["crash"] += 1
            results.append({"crash": repr(ex)[:200]})
    dt = time.time() - t0
    done = sum(1 for r in results if r.get("done"))
    rounds = [r["rounds"] for r in results if "rounds" in r]
    print("episodes %d | done %d | crashes %d | %.1fs (%.2fs/ep)" % (
        args.episodes, done, failures["crash"], dt, dt / max(1, args.episodes)))
    if rounds:
        print("rounds mean %.1f max %d" % (
            sum(rounds) / len(rounds), max(rounds)))
    print("failures:", dict(failures) or "none")
    if args.report:
        json.dump({"episodes": args.episodes, "seed": args.seed,
                   "failures": dict(failures), "results": results[:20]},
                  open(args.report, "w", encoding="utf8"), ensure_ascii=False,
                  indent=1)
        print("report ->", args.report)


if __name__ == "__main__":
    main()
