#!/usr/bin/env python
"""Direct-pysim arena for TPolicy-BC (Transformer基线任务书 §10.4/§10.5).

Free-running here is REAL: every plan step rebuilds
PolicyTokenObservationV2 from the live PrefixEnv, decodes one atomic action
through the structured masked decoder, converts it back to an RLAction and
executes the transition. Rejections / forced END / stop reasons / exploit
flags are counted — the §10.4 Gate inputs. Judgement is direct pysim with
derived common seeds (§10.5: V_sim never judges).

Roots come from phase1 corpus chunks (same ReplayAdapter path as
run_rl_phase1_arena.py), so dev/small runs work pre-T0 and the formal run
points at the frozen test roots.

  CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7 tools/run_transformer_arena.py \
    --checkpoint <run>/checkpoints/tpolicy_seed0.pt \
    --corpus-chunks local_data/rl_phase1/dev_small/corpus_chunks \
    --n-roots 4
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.transition.economy import Economy                     # noqa: E402
from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer import distributed as D                # noqa: E402
from pysim.rl.transformer.tokenizer import (TokenizerConfig,     # noqa: E402
                                            SemanticVocab,
                                            ActionFields,
                                            fields_to_action,
                                            policy_token_obs_from_live,
                                            encode_policy_tokens,
                                            bias_components,
                                            collate_tokens)
from pysim.rl.transformer.data import _pad2d, _pad1d, torch_as_tensor
from pysim.rl.arena import play_joint, ego_reward, detect_exploits  # noqa: E402
from pysim.rl.prefix_env import PrefixEnv, apply_incomes         # noqa: E402
from pysim.rl.masks import RLAction                              # noqa: E402
from pysim.rl.contracts import derive_seed                       # noqa: E402


def load_model(checkpoint: str):
    ck = torch.load(checkpoint, map_location="cpu", weights_only=False)
    if ck.get("contract_version") != tc.CONTRACT_VERSION:
        raise SystemExit("checkpoint contract %r != %s (拒绝旧 checkpoint)"
                         % (ck.get("contract_version"),
                            tc.CONTRACT_VERSION))
    if ck.get("engineering_only"):
        print("[warn] engineering-only checkpoint (toy/smoke) — 结果不得作为正式结论")
    from pysim.rl.transformer.policy_bc import TPolicyBC, TPolicyConfig
    vocab = SemanticVocab.from_dict(ck["vocab"])
    cfg = TPolicyConfig.from_dict(ck["config"])
    tok_cfg = TokenizerConfig.from_dict(cfg.tokenizer)
    model = TPolicyBC(vocab, cfg, tok_cfg)
    model.load_state_dict(ck["model"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()
    return model, vocab, cfg, tok_cfg, ck, device


class TPolicyActuator:
    """Live PrefixEnv policy: obs -> tokens -> structured decode -> action.

    Masked decode is in-mask by construction (rejection 0 at the DECODE
    level, §13.2); a verb whose required stage has no legal candidate gives
    an explicit stop reason and the actuator masks that verb out and
    retries, ending at END_DEPLOY — every fallback is counted (§5.1)."""

    def __init__(self, model, vocab, cfg, tok_cfg, device, mode="greedy",
                 temperature=1.0, top_p=1.0, seed=0):
        self.model = model
        self._vocab = vocab
        self.cfg = cfg
        self.tok_cfg = tok_cfg
        self.device = device
        self.mode = mode
        self.temperature = temperature
        self.top_p = top_p
        self.seed = int(seed)
        self.history = []
        self.fallbacks = 0
        self._calls = 0

    def reset(self):
        self.history = []
        self.fallbacks = 0

    def act(self, obs, space):
        from pysim.rl import masks as M
        space_dict = M.space_to_dict(space)
        obs2 = policy_token_obs_from_live(obs, space_dict,
                                          history=self.history)
        ta, tables = encode_policy_tokens(obs2, self._vocab, self.tok_cfg)
        batch = {k: torch_as_tensor(v).to(self.device)
                 for k, v in collate_tokens([ta]).items()}
        comp = torch_as_tensor(
            bias_components(ta, self.tok_cfg))[None].to(self.device)
        max_obj = self.cfg.max_obj_cands
        max_ptr = self.cfg.max_ptr_cands
        tables_b = {
            "verb_mask": torch_as_tensor(np.asarray(
                [space_dict["verb_mask"]], dtype=np.float32)),
            "obj_mask": torch_as_tensor(_pad2d(
                tables["obj_mask"], max_obj).astype(np.float32))[None],
            "ptr_mask": torch_as_tensor(_pad2d(
                tables["ptr_mask"], max_ptr).astype(np.float32))[None],
            "xy_legal": torch_as_tensor(
                tables["xy_legal"].astype(np.float32))[None],
            "arities": torch_as_tensor(_pad1d(
                tables["arities"], max_obj)[None]),
        }
        vm = tables_b["verb_mask"][0]
        for attempt in range(5):
            self._calls += 1
            fields, stop = self.model.decode(
                batch, comp, tables_b, mode=self.mode,
                temperature=self.temperature, top_p=self.top_p,
                seed=self.seed * 100003 + self._calls)
            if not stop[0]:
                f = ActionFields.from_list(fields[0].tolist())
                a = fields_to_action(f, tables, self.tok_cfg)
                return self._to_rl_action(a)
            self.fallbacks += 1
            blocked = int(fields[0, 0])
            if 0 <= blocked < len(vm):
                vm[blocked] = 0.0
            if float(vm.sum()) <= 0.0:
                break
        return RLAction("END_DEPLOY")

    def _to_rl_action(self, a: dict) -> RLAction:
        kw = {}
        for k in ("mech", "handle", "equip", "skill_slot", "skill_id",
                  "tower", "tower_index", "blueprint", "contraption",
                  "x", "y", "rot"):
            if a.get(k) is not None:
                kw[k] = a[k]
        if a.get("tech") is not None:
            kw["tech"] = tuple(a["tech"])
        act = RLAction(a["verb"], **kw)
        if a.get("points"):
            act.points = tuple((float(x), float(y))
                               for (x, y) in a["points"])
        return act


def plan_with_actuator(root, ego, eco, gd, actuator, budget=64):
    """generate_plan variant that feeds receipt history to the actuator
    (§4.3: history = what THIS prefix already executed)."""
    from pysim.transition.state_tools import state_digest
    env = PrefixEnv(root, ego, eco, gd, budget=budget)
    actuator.reset()
    actions = []
    stop = "budget"
    t0 = time.time()
    last_digest = state_digest(env.state)
    noop_run, sig_run, last_sig = 0, 0, None
    while env.steps < budget:
        obs, space = env.observation()
        a = actuator.act(obs, space)
        actions.append(a)                   # RLAction objects (exploit audit)
        out = env.apply(a)
        actuator.history.append({
            "verb": a.verb,
            "x": float(a.x) if a.x is not None else 0.0,
            "y": float(a.y) if a.y is not None else 0.0,
            "points": [tuple(p) for p in (getattr(a, "points", ()) or ())],
            "receipt_ok": bool(out.accepted)})
        if a.verb == "END_DEPLOY":
            stop = "end"
            break
        if not out.accepted:
            stop = "rejected:%s" % out.reason_code
            break
        digest = state_digest(env.state)
        noop_run = noop_run + 1 if digest == last_digest else 0
        last_digest = digest
        sig = json.dumps(a.to_dict(), sort_keys=True, default=str)
        sig_run = sig_run + 1 if sig == last_sig else 1
        last_sig = sig
        if sig_run >= 3:
            stop = "cycle_stop"
            break
        if noop_run >= 3:
            stop = "no_op_stop"
            break
    return {"actions": actions,                 # RLAction objects
            "actions_dict": [a.to_dict() for a in actions],
            "engine_actions": tuple(env.engine_log),
            "noops": list(env.noop_flags), "forced_end": stop == "budget",
            "stop_reason": stop, "steps": env.steps,
            "latency_s": time.time() - t0, "final_state": env.state,
            "rejections": [stop[9:]] if stop.startswith("rejected:") else [],
            "fallbacks": actuator.fallbacks}


class EndOnlyPolicy:
    def act(self, obs, space):
        return RLAction("END_DEPLOY")


class RandomLegalPolicy:
    def __init__(self, seed):
        self.rng = np.random.RandomState(seed)

    def act(self, obs, space):
        legal = [v for i, v in enumerate(space.verbs)
                 if space.verb_mask[i]]
        return RLAction(str(self.rng.choice(
            legal if legal else ["END_DEPLOY"])))


def build_root_factory(chunk_paths, eco):
    from pysim.transition.replay_adapter import ReplayAdapter
    from pysim.rl.prefix_env import derive_round_incomes
    adapters = {}
    games = {}
    for p in chunk_paths:
        with open(p, encoding="utf8") as f:
            gs = json.load(f)
        adapters[p] = ReplayAdapter(gs)
        adapters[p]._games = gs
        adapters[p]._warned_raw = True
        games[p] = gs
    cache = {}

    def make(chunk_path, gi, rnd):
        key = (chunk_path, gi, rnd)
        if key in cache:
            return cache[key]
        adapter = adapters[chunk_path]
        g = games[chunk_path][gi]
        income_tab, _ = derive_round_incomes(g, eco)
        try:
            base = adapter.environment_state(gi, rnd, economy=eco)
        except Exception:
            return None
        inc = (int(income_tab.get((0, rnd), 0)),
               int(income_tab.get((1, rnd), 0)))
        root = apply_incomes(base, inc)
        cache[key] = root
        return root
    return make


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--corpus-chunks", required=True,
                    help="dir of chunk JSONs (dev fixtures pre-T0)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--n-roots", type=int, default=4)
    ap.add_argument("--rounds-per-root", type=int, default=2)
    ap.add_argument("--mode", default="greedy",
                    choices=["greedy", "sample"])
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--baselines", default="end_only,random")
    ap.add_argument("--max-games", type=int, default=4)
    args = ap.parse_args()

    D.enforce_env()
    model, vocab, cfg, tok_cfg, ck, device = load_model(args.checkpoint)
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    eco = Economy(gd)

    chunk_dir = args.corpus_chunks
    chunks = sorted(os.path.join(chunk_dir, f) for f in os.listdir(chunk_dir)
                    if f.endswith(".json") and f != "chunks.json")
    make_root = build_root_factory(chunks, eco)

    actuator = TPolicyActuator(model, vocab, cfg, tok_cfg, device,
                               mode=args.mode,
                               temperature=args.temperature,
                               top_p=args.top_p, seed=args.seed)

    os.makedirs(args.out_dir, exist_ok=True)
    matchups = []
    n_played = 0
    seeds_seen = set()
    for cp in chunks:
        if n_played >= args.n_roots * args.rounds_per_root:
            break
        gs = json.load(open(cp))
        for gi, g in enumerate(gs):
            if gi >= args.max_games:
                break
            max_round = max((int(r["round"]) for p in g["players"]
                             for r in p.get("rounds", [])), default=0)
            for rnd in range(1, max_round):
                if n_played >= args.n_roots * args.rounds_per_root:
                    break
                root = make_root(cp, gi, rnd)
                if root is None:
                    continue
                seed = derive_seed("tarena|%s|%d|%d" %
                                   (os.path.basename(cp), gi, rnd), 0)
                if seed in seeds_seen:
                    continue
                seeds_seen.add(seed)
                plan0 = plan_with_actuator(root, 0, eco, gd, actuator)
                # baselines: mirrored actuator seats
                end_act = EndOnlyPolicy()

                def gen(policy, seat):
                    from pysim.rl.arena import generate_plan
                    return generate_plan(root, seat, policy, eco, gd)

                row = {"root": "%s#%d#r%d" % (os.path.basename(cp), gi, rnd),
                       "tpolicy": {
                           "stop": plan0["stop_reason"],
                           "steps": plan0["steps"],
                           "forced_end": plan0["forced_end"],
                           "rejections": len(plan0["rejections"]),
                           "noops": len(plan0["noops"]),
                           "fallbacks": plan0["fallbacks"],
                           "latency_s": round(plan0["latency_s"], 3),
                           "exploits": detect_exploits(plan0, root)},
                       }
                if "end_only" in args.baselines:
                    p_end = gen(end_act, 1)
                    res = play_joint(root, plan0, p_end, eco, gd, seed)
                    row["vs_end_only"] = {
                        "winner": res["winner"],
                        "rejections": len(res["rejections"]),
                        "forced_ends": res["forced_ends"],
                        "damage_diff": int(
                            res["damage_to_player"][1] -
                            res["damage_to_player"][0])}
                if "random" in args.baselines:
                    rnd_pol = RandomLegalPolicy(seed=args.seed)
                    p_rand = gen(rnd_pol, 1)
                    res = play_joint(root, plan0, p_rand, eco, gd,
                                     derive_seed("tarena|r", 0))
                    row["vs_random"] = {
                        "winner": res["winner"],
                        "rejections": len(res["rejections"]),
                        "forced_ends": res["forced_ends"],
                        "damage_diff": int(
                            res["damage_to_player"][1] -
                            res["damage_to_player"][0])}
                matchups.append(row)
                n_played += 1
                print(json.dumps(row, ensure_ascii=False))

    # ------------------------------------------------- §10.4 gate summary
    def agg(key_fn):
        vals = [key_fn(m) for m in matchups]
        vals = [v for v in vals if v is not None]
        return float(np.mean(vals)) if vals else None

    summary = {
        "n_matchups": len(matchups),
        "rejection_rate": agg(
            lambda m: m["tpolicy"]["rejections"] / max(1, m["tpolicy"]["steps"])),
        "forced_end_rate": agg(
            lambda m: 1.0 if m["tpolicy"]["forced_end"] else 0.0),
        "normal_end_rate": agg(
            lambda m: 1.0 if m["tpolicy"]["stop"] == "end" else 0.0),
        "noop_rate": agg(
            lambda m: m["tpolicy"]["noops"] / max(1, m["tpolicy"]["steps"])),
        "exploit_roots": sum(len(m["tpolicy"]["exploits"]) for m in matchups),
        "checkpoint_engineering_only": bool(ck.get("engineering_only")),
    }
    out = {"summary": summary, "matchups": matchups,
           "gate_10_4": {
               "action_rejection_eq_0": summary["rejection_rate"] == 0.0,
               "normal_end_ge_0.99": (summary["normal_end_rate"] or 0) >= 0.99,
               "forced_end_lt_0.01": (summary["forced_end_rate"] or 1) < 0.01,
           }}
    path = os.path.join(args.out_dir, "arena_%s.json" %
                        time.strftime("%Y%m%d_%H%M%S"))
    with open(path, "w", encoding="utf8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print("summary:", json.dumps(summary, ensure_ascii=False))
    print("arena written:", path)


if __name__ == "__main__":
    main()
