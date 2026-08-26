# Endpoint A: counterfactual replay of a real player match under pysim rules.
#
#   python examples/replay_player_match.py --rounds data/samples/rounds.json \
#       --game 0 --start-round 1 --seed 7 --strict
#
# The mutable state is built ONCE from the start round's snapshot; every later
# round comes from the transition chain (deploy -> pysim battle -> settle ->
# advance). Per-round income and the reinforcement offer are injected from the
# replay as declared exogenous inputs (logged); hp / fight result / exp come
# ONLY from the pysim outcome — never from the next round's XML.
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                  errors="replace")
except Exception:
    pass

from pysim.gamedata import GameData
from pysim.transition import (ReplayAdapter, Economy, TransitionEnv,
                              Income200r, canonicalize_plan, state_digest,
                              EnvironmentState, PlayerState, Phase)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=os.path.join(
        ROOT, "data", "samples", "rounds.json"))
    ap.add_argument("--game", type=int, default=0)
    ap.add_argument("--start-round", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--strict", action="store_true",
                    help="stop at the first rejected core action")
    ap.add_argument("--tolerant", action="store_true",
                    help="record rejections and continue (default)")
    ap.add_argument("--trajectory", default=None,
                    help="write the full trajectory JSON here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    eco = Economy(gd)
    adapter = ReplayAdapter(args.rounds)
    g = adapter.game(args.game)
    income_policy = Income200r()

    state = adapter.environment_state(args.game, args.start_round, economy=eco)
    env = TransitionEnv(gd, eco)
    obs = env.reset(state)
    n_rounds = min(len(g["players"][0]["rounds"]),
                   len(g["players"][1]["rounds"]))
    last_round = n_rounds - 1

    # oracle labels, observation only (never fed back into the state)
    label_by_round = {}
    for pair in g.get("pairs", []):
        label_by_round[int(pair["round"])] = pair.get("label")

    trajectory = {"game": g.get("file"), "start_round": args.start_round,
                  "seed": args.seed, "mode": "pysim_human_replay",
                  "schema_version": state.schema_version,
                  "ruleset_version": state.ruleset_version,
                  "engine_version": state.engine_version,
                  "initial_digest": state_digest(state), "rounds": []}
    rng = __import__("random").Random(args.seed)
    stop_reason = "replay_actions_exhausted"
    round_no = args.start_round

    while round_no <= last_round:
        s = env.state
        plans = []
        reports = []
        any_rejected = False
        for side in (0, 1):
            acts, nrep = adapter.norm_actions(g, side, round_no)
            for e in acts:
                if e.get("t") == "passthrough" and \
                        e.get("raw_type") == "ActiveBlueprint" and \
                        str((e.get("raw_rec") or {}).get("ID")) == "1":
                    income_policy.record_fast_supply(side, round_no + 1)
            try:
                plan, rep = canonicalize_plan(
                    side, acts, s.players[side], economy=eco,
                    norm_report=nrep)
                reports.append(rep)
            except Exception as ex:                    # noqa: BLE001
                print("round %d side %d canonicalize failed: %r" % (
                    round_no, side, ex))
                stop_reason = "canonicalize_error"
                any_rejected = True
                plan = None
            if plan is not None:
                plans.append(plan)
        if stop_reason == "canonicalize_error":
            break
        inc = tuple(income_policy.income(
            side, s.players[side], round_no,
            s.players[side].pre_round_fight_result) for side in (0, 1))
        step = env.step_joint(*plans, battle_seed=rng.randrange(1 << 30),
                              incomes=inc)
        rej = [[(r.kind, r.reason_code) for r in rs
                if not r.accepted and r.reason_code not in
                ("UNSUPPORTED_ACTION", "UNSUPPORTED_RULE_DATA")]
               for rs in step.deploy_receipts]
        if any(rej):
            any_rejected = True
        if not args.quiet:
            o = outcome = step.battle_outcome
            print("round %2d | hp %d/%d supply %d/%d | pysim winner %s "
                  "dmg %s reward %s done=%s" % (
                      round_no, step.state.players[0].hp,
                      step.state.players[1].hp,
                      step.state.players[0].supply,
                      step.state.players[1].supply,
                      outcome.winner if outcome else "-",
                      tuple(outcome.damage_to_player) if outcome else "-",
                      tuple(step.reward), step.done))
            if label_by_round.get(round_no):
                actual = 0 if label_by_round[round_no] == "Win" else 1
                if outcome:
                    hit = (outcome.winner == actual)
                    print("           replay label=%s pysim=%s %s" % (
                        label_by_round[round_no], outcome.winner,
                        "AGREE" if hit else "disagree"))
            if any_rejected:
                print("           rejected core actions:", rej)
        trajectory["rounds"].append({
            "round": round_no,
            "state_digest": step.state_digest,
            "deploy_digest_before": None,
            "receipts": [[[r.kind, r.accepted, r.reason_code,
                           r.resource_delta] for r in rs]
                         for rs in step.deploy_receipts],
            "ledgers": [[e.reason, e.amount] for led in step.ledgers
                        for e in led.entries],
            "injected_income": list(inc),
            "winner": step.battle_outcome.winner if step.battle_outcome else None,
            "damage": list(step.battle_outcome.damage_to_player)
            if step.battle_outcome else None,
            "reward": list(step.reward),
            "done": step.done,
            "exp_deltas": {c.entity_id: c.exp_delta
                           for c in step.battle_outcome.cards}
            if step.battle_outcome else {},
        })
        if step.done:
            stop_reason = step.state.terminal_reason or "terminal"
            break
        if args.strict and any_rejected:
            stop_reason = "strict_rejection"
            break
        round_no = env.state.round

    print("stopped: %s after round %d (started at %d)" % (
        stop_reason, env.state.round, args.start_round))
    trajectory["stop_reason"] = stop_reason
    trajectory["final_digest"] = state_digest(env.state)
    if args.trajectory:
        os.makedirs(os.path.dirname(os.path.abspath(args.trajectory)),
                    exist_ok=True)
        json.dump(trajectory, open(args.trajectory, "w", encoding="utf8"),
                  ensure_ascii=False, indent=1)
        print("trajectory ->", args.trajectory)


if __name__ == "__main__":
    main()
