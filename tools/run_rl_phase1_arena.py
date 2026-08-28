#!/usr/bin/env python
"""T4: single-round arena + best-of-N on frozen test roots (task §9).

Matchups (all with side swap + common battle seeds, direct pysim judge):
  pi_BC(greedy)  vs {END-only, random-legal, heuristic, human replay plan}
  pi_BC(sampled) vs pi_BC(sampled, mirrored seat)   # side-bias probe
  best-of-8 direct pysim vs raw sampled pi_BC       # paired improvement
  V_sim prefilter 32 -> 8 + direct-sim top-k        # recall + regret
"""
import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                                  # noqa: E402
from pysim.transition.economy import Economy                         # noqa: E402
from pysim.transition.replay_adapter import ReplayAdapter            # noqa: E402
from pysim.rl.prefix_env import (PrefixEnv, teacher_force_walk,       # noqa: E402
                                 apply_incomes, derive_round_incomes)
from pysim.rl.policies import (RandomLegalPolicy, HeuristicPolicy,    # noqa: E402
                               EndOnlyPolicy)
from pysim.rl.arena import (BCPolicy, generate_plan, play_joint,       # noqa: E402
                            ego_reward, detect_exploits)
from pysim.rl.contracts import derive_seed, MAX_PLAN_ACTIONS          # noqa: E402
from pysim.rl.features import Vocab, battle_features                  # noqa: E402


def pick_arena_roots(corpus_chunks, fingerprints, n_roots, seed=0):
    """Test-split game roots (task §9.1: arena-only replay groups)."""
    rng = np.random.RandomState(seed)
    test_fps = set(fingerprints)
    roots = []
    for chunk_path in corpus_chunks:
        games = json.load(open(chunk_path))
        for gi, g in enumerate(games):
            if g.get("info", {}).get("matchMode") != "VS_1_1":
                continue
            fp = game_fp(g)
            if fp not in test_fps:
                continue
            roots.append((chunk_path, gi, g))
            if len(roots) >= n_roots * 3:
                break
        if len(roots) >= n_roots * 3:
            break
    rng.shuffle(roots)
    return roots[:n_roots]


def game_fp(g):
    import hashlib
    h = hashlib.sha256()
    for side in (0, 1):
        for r in sorted(g["players"][side].get("rounds", []),
                        key=lambda r: int(r["round"])):
            h.update(json.dumps(
                {k: r.get(k) for k in ("round", "units", "techMap", "supply",
                                       "reactorCore", "officers")},
                sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--bc-mode", default="greedy")
    ap.add_argument("--n-roots", type=int, default=40)
    ap.add_argument("--rounds-per-root", type=int, default=3,
                    help="how many deploy rounds per game to use")
    ap.add_argument("--n-common-seeds", type=int, default=3)
    ap.add_argument("--best-of-n", type=int, default=8)
    ap.add_argument("--prefilter-pool", type=int, default=32)
    ap.add_argument("--value-checkpoint", default=None)
    ap.add_argument("--device", default="cuda" if __import__("torch")
                    .cuda.is_available() else "cpu")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--skip-prefilter", action="store_true")
    args = ap.parse_args()
    torch = __import__("torch")

    ds = os.path.join(args.run_dir, "datasets")
    out_rows = []
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    eco = Economy(gd)
    ck = args.checkpoint or os.path.join(args.run_dir, "checkpoints",
                                         "policy_bc_seed0.pt")

    # arena fingerprints = test-split groups (read from the split recorded
    # in the battle dataset rows)
    fingerprints = set()
    with gzip.open(os.path.join(ds, "battle_real_v1.jsonl.gz"), "rt") as f:
        for line in f:
            r = json.loads(line)
            if r["split"] == "test" and r["tier"] == "gold":
                fingerprints.add(r["match_id_hash"])
    print("test groups:", len(fingerprints))

    chunk_dir = os.path.join(args.run_dir, "corpus_chunks")
    chunks = [os.path.join(chunk_dir, f) for f in sorted(os.listdir(
        chunk_dir)) if f.endswith(".json") and f != "chunks.json"]
    roots = pick_arena_roots(chunks, fingerprints, args.n_roots,
                             seed=args.seed)
    print("arena roots:", len(roots))

    bc = BCPolicy(ck, mode=args.bc_mode, temperature=1.0, seed=args.seed,
                  device=args.device)
    value_model = None
    if not args.skip_prefilter and args.value_checkpoint:
        from pysim.rl.models.battle_value import BattleValueNet
        vck = torch.load(args.value_checkpoint, map_location="cpu",
                         weights_only=False)
        vv = Vocab.from_dict(vck["vocab"])
        value_model = BattleValueNet(vv.n_mech, vv.n_equip,
                                     n_tech=vv.n_tech).to(args.device)
        value_model.load_state_dict(vck["model"])
        value_model.eval()

    adapter_cache = {}
    t0 = time.time()
    match_id = 0

    def build_root(chunk_path, gi, g, rnd):
        adapter = adapter_cache.get(chunk_path)
        if adapter is None:
            games = json.load(open(chunk_path))
            adapter = ReplayAdapter([g])
            adapter._games = [g]
            adapter._warned_raw = True     # the dict path spams the warning
            adapter_cache[chunk_path] = adapter
        income_tab, _ = derive_round_incomes(g, eco)
        base = adapter.environment_state(0, rnd, economy=eco)
        inc = (int(income_tab.get((0, rnd), 0)),
               int(income_tab.get((1, rnd), 0)))
        return apply_incomes(base, inc)

    def seeds_for(tag, n):
        return [derive_seed("arena|%s" % tag, k) for k in range(n)]

    def score_plan_vs_opponent(root, plan, opp_plan, seed_tag, k_seeds):
        scores = []
        for k in range(k_seeds):
            seed = derive_seed(seed_tag, k)
            res = play_joint(root, plan, opp_plan, eco, gd, seed)
            res["_max_hp"] = (root.players[0].max_hp,
                              root.players[1].max_hp)
            scores.append(ego_reward(res, 0))
        return float(np.mean(scores)), res

    # ------------------------------------------------ match loop
    for chunk_path, gi, g in roots:
        adapter = adapter_cache.get(chunk_path)
        if adapter is None:
            adapter = ReplayAdapter([g])
            adapter._games = [g]
            adapter._warned_raw = True     # the dict path spams the warning
            adapter_cache[chunk_path] = adapter
        income_tab, _ = derive_round_incomes(g, eco)
        max_round = max((int(r["round"]) for p in g["players"]
                         for r in p.get("rounds", [])), default=0)
        done_rounds = 0
        for rnd in range(1, max_round):
            if done_rounds >= args.rounds_per_root:
                break
            try:
                base = adapter.environment_state(0, rnd, economy=eco)
            except KeyError:
                continue
            inc = (int(income_tab.get((0, rnd), 0)),
                   int(income_tab.get((1, rnd), 0)))
            root = apply_incomes(base, inc)
            # human plans (opponent reference)
            try:
                w0 = teacher_force_walk(root, 0,
                                        adapter.norm_actions(g, 0, rnd)[0],
                                        eco, gd)
                w1 = teacher_force_walk(root, 1,
                                        adapter.norm_actions(g, 1, rnd)[0],
                                        eco, gd)
            except Exception:
                continue
            if w0.end_reason != "human_end" or w1.end_reason != "human_end":
                continue
            human = {0: {"actions": [], "engine_actions": w0.engine_actions,
                         "noops": w0.noops, "forced_end": False,
                         "steps": w0.n_exogenous + len(w0.samples),
                         "latency_s": 0.0, "final_state": w0.final_state},
                     1: {"actions": [], "engine_actions": w1.engine_actions,
                         "noops": w1.noops, "forced_end": False,
                         "steps": w1.n_exogenous + len(w1.samples),
                         "latency_s": 0.0, "final_state": w1.final_state}}
            done_rounds += 1

            def mk_policy(name, seat_seed=0):
                if name == "bc_greedy":
                    return bc
                if name == "bc_sampled":
                    return BCPolicy(ck, mode="sample", temperature=1.0,
                                    seed=args.seed * 977 + seat_seed,
                                    device=args.device)
                if name == "end_only":
                    return EndOnlyPolicy()
                if name == "random":
                    return RandomLegalPolicy(seed=args.seed * 7 + seat_seed)
                if name == "heuristic":
                    return HeuristicPolicy(eco=eco, seed=args.seed + seat_seed)
                if name == "human":
                    return ("human",)      # marker: replay that seat's plan
                raise ValueError(name)

            def plan_for(name, seat, seat_seed=0):
                if isinstance(mk_policy(name), tuple) and \
                        mk_policy(name)[0] == "human":
                    return human[seat]
                return generate_plan(root, seat, mk_policy(name, seat_seed),
                                     eco, gd)

            matchups = [
                ("bc_greedy", "end_only"),
                ("bc_greedy", "random"),
                ("bc_greedy", "heuristic"),
                ("bc_greedy", "human"),
                ("bc_sampled", "bc_sampled"),
            ]
            for name_a, name_b in matchups:
                for swap in (False, True):
                    # ego side swap (task §9.1): A takes the opposite seat,
                    # plans are generated FOR their seat (engine coords),
                    # and A's reward reads from A's own seat perspective
                    seat_a, seat_b = (1, 0) if swap else (0, 1)
                    pa = plan_for(name_a, seat_a, args.seed * 13 + rnd)
                    pb = plan_for(name_b, seat_b, args.seed * 17 + rnd)
                    plans_by_seat = {seat_a: pa, seat_b: pb}
                    seed_tag = "m%d" % match_id
                    scores = []
                    for k in range(args.n_common_seeds):
                        seed = derive_seed(seed_tag, k)
                        res = play_joint(root, plans_by_seat[0],
                                         plans_by_seat[1], eco, gd, seed)
                        res["_max_hp"] = (root.players[0].max_hp,
                                          root.players[1].max_hp)
                        scores.append(ego_reward(res, seat_a))
                    match_id += 1
                    out_rows.append({
                        "match_id": match_id - 1,
                        "root": {"file": g.get("file", ""), "round": rnd},
                        "matchup": "%s_vs_%s%s" % (name_a, name_b,
                                                   "_swap" if swap else ""),
                        "a": name_a, "b": name_b, "side_swap": swap,
                        "ego_reward": float(np.mean(scores)),
                        "scores": scores,
                        "winner": int(res["winner"]),
                        "damage": [int(res["damage_to_player"][0]),
                                   int(res["damage_to_player"][1])],
                        "rejections": len(res["rejections"]),
                        "reject_detail": res["rejections"][:3],
                        "plan_noops": len(res.get("noops", [])),
                        "forced_ends": res.get("forced_ends", []),
                        "fidelity": res["fidelity_warnings"][:2],
                        "plan_a_steps": pa["steps"],
                        "plan_b_steps": pb["steps"],
                        "forced_a": pa["forced_end"],
                        "forced_b": pb["forced_end"],
                        "latency_a": pa["latency_s"],
                        "exploits_a": detect_exploits(pa, root),
                        "exploits_b": detect_exploits(pb, root),
                        "noops_a": len(pa["noops"]),
                    })

            # ---- best-of-N + V_sim prefilter (paired, frozen opponent)
            opp = human[1]
            rng = np.random.RandomState(args.seed * 31 + rnd)
            pool = []
            bc_s = BCPolicy(ck, mode="sample", temperature=1.0,
                            seed=args.seed * 101 + rnd, device=args.device)
            for i in range(args.prefilter_pool):
                pool.append(generate_plan(root, 0, bc_s, eco, gd))
            tag = "bon|%s|%d" % (g.get("file", ""), rnd)
            sims, rewards = [], []
            k_seeds = 2
            for i, plan in enumerate(pool):
                s, res = score_plan_vs_opponent(root, plan, opp,
                                                "%s|%d" % (tag, i), k_seeds)
                sims.append(s)
                rewards.append(s)
            raw_score = sims[0]
            best_direct = int(np.argmax(sims))
            bon_score = sims[best_direct]
            entry = {
                "root": {"file": g.get("file", ""), "round": rnd},
                "raw_sampled_score": raw_score,
                "best_of_n_score": bon_score,
                "best_of_n_gain": bon_score - raw_score,
                "best_idx": best_direct,
                "pool_scores": sims,
            }
            if value_model is not None:
                from tools.train_battle_value import make_batch
                vocab = Vocab(gd)
                obs_rows = [{"observation": p["final_state"] and None}
                            for p in []]
                # value scores the PRE-BATTLE observation of each candidate
                from pysim.rl.observation import battle_observation
                from pysim.transition.model import (CanonicalActionPlan,
                                                    CanonicalAction,
                                                    ActionKind)
                from pysim.transition.deploy import deploy_transition
                vf = []
                for plan in pool:
                    st = plan["final_state"]
                    if st.phase.value != "pre_battle":
                        dep = deploy_transition(
                            st, (CanonicalActionPlan(player=0, actions=(
                                CanonicalAction(ActionKind.END_DEPLOY,
                                                None),)),), eco)
                        st = dep.state
                        dep1 = deploy_transition(
                            st, (CanonicalActionPlan(
                                player=1,
                                actions=opp["engine_actions"]),), eco)
                        st = dep1.state
                    bo = battle_observation(st, 0)
                    vf.append({"observation": bo.to_dict()})
                with torch.no_grad():
                    vals = []
                    for i in range(0, len(vf), 32):
                        batch = make_batch(vf[i:i + 32], vocab, args.device)
                        vals.append(value_model.v_rank(batch, "sim")
                                    .cpu().numpy())
                v_rank = np.concatenate(vals)
                sims_arr = np.asarray(sims)
                topk = np.argsort(-v_rank)[:args.best_of_n]
                entry["value_topk"] = topk.tolist()
                entry["value_topk_recall"] = float(best_direct in topk)
                kept_best = int(topk[int(np.argmax(sims_arr[topk]))]) \
                    if len(topk) else best_direct
                entry["value_topk_regret"] = float(
                    sims[best_direct] - sims[kept_best])
                entry["v_rank"] = v_rank.tolist()
            out_rows.append({
                "match_id": match_id, "matchup": "best_of_n", **entry})
            match_id += 1
            if done_rounds and match_id % 10 == 0:
                print("matches %d (%.0fs)" % (match_id, time.time() - t0),
                      flush=True)

    os.makedirs(args.run_dir, exist_ok=True)
    with gzip.open(os.path.join(args.run_dir, "arena_matches.jsonl.gz"),
                   "wt") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ------------------------------------------------ summary
    by_matchup = {}
    for r in out_rows:
        if r.get("matchup") == "best_of_n":
            continue
        key = r["matchup"]
        by_matchup.setdefault(key, []).append(r["ego_reward"])
    summary = {}
    for key, vals in sorted(by_matchup.items()):
        vals = np.asarray(vals)
        summary[key] = {"n": len(vals), "mean": float(vals.mean()),
                        "win_share": float((vals > 0).mean()),
                        "loss_share": float((vals < 0).mean())}
    bon = [r for r in out_rows if r.get("matchup") == "best_of_n"]
    if bon:
        gains = np.asarray([r["best_of_n_gain"] for r in bon])
        groups = {}
        for r in bon:
            groups.setdefault(r["root"]["file"], []).append(
                r["best_of_n_gain"])
        rng = np.random.RandomState(0)
        gvals = [np.asarray(v) for v in groups.values()]
        boots = []
        for _ in range(1000):
            pick = rng.choice(len(gvals), len(gvals))
            boots.append(np.concatenate([gvals[i] for i in pick]).mean())
        lo, hi = np.quantile(boots, [0.025, 0.975])
        summary["best_of_n"] = {
            "n": len(gains),
            "mean_gain": float(gains.mean()),
            "ci95": [float(lo), float(hi)],
            "win_rate": float((gains > 0).mean()),
            "value_topk_recall": float(np.mean([r.get("value_topk_recall",
                                                      0) for r in bon]))
            if value_model is not None else None,
        }
    rej = sum(r.get("rejections", 0) for r in out_rows
              if r.get("matchup") != "best_of_n")
    summary["total_rejections"] = rej
    report = {"args": vars(args), "matchups": summary,
              "elapsed_s": round(time.time() - t0, 1)}
    with open(os.path.join(args.run_dir, "arena_report.json"), "w") as f:
        json.dump(report, f, indent=1, default=str)
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
