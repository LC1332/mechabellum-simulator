# Toy v2 corpus generator (任务书 §3.2: 允许在 T0 前使用 toy data).
#
# Deterministic, self-consistent ObservationV2/ActionV2 rows that exercise
# every token type, all target arities (0/1/2/3-point), END/budget paths and
# candidate groups — used by the unit tests, the smoke chain and throughput
# probes. Labels come from a simple deterministic score of the observation
# so tiny models CAN overfit (§13.3). NOT training data for any formal
# claim; every row carries corpus="toy".
from __future__ import annotations

import numpy as np

from .tokenizer import VERBS_13
from .policy_arity import release_arity

MECHS = (15, 21, 24, 33, 45)
TECHS = (10215, 10216, 20215, 30215)
EQUIPS = (13030004, 13100002, 0)
SKILLS_POS1 = (300001, 300003, 300007)
SKILL_CAPSULE = 400002          # 2 ordered points
SKILL_BEACON = 1500001          # 3 ordered points
SKILL_UNIT = 1100001
TOWERS = (1, 3, 4, 5, 6)
BPS = (1, 3)
CONTRAPTIONS = (10001,)

SPACE_VERBS = VERBS_13


def toy_space(rng: np.random.RandomState, n_units: int) -> dict:
    """A legal, self-consistent action space for a toy observation."""
    buy = [int(rng.choice(MECHS)), int(rng.choice(MECHS))]
    buy = sorted(set(buy))
    unlock = [int(m) for m in MECHS if m not in buy][:2]
    up = [int(rng.rand() < 0.7) for _ in range(n_units)]
    sell = [1] * n_units
    mv = [1] + [int(rng.rand() < 0.6) for _ in range(max(0, n_units - 1))]
    skills = [(0, SKILL_CAPSULE), (1, SKILL_BEACON), (2, SKILL_UNIT)]
    if rng.rand() < 0.5:
        skills.append((3, int(rng.choice(SKILLS_POS1))))
    tower_mask = [int(rng.rand() < 0.8) for _ in TOWERS]
    return {
        "verbs": list(SPACE_VERBS),
        "verb_mask": [1,                                    # END_DEPLOY
                      int(len(buy) > 0),
                      int(len(unlock) > 0),
                      int(any(up)),
                      1,                                    # BUY_TECH
                      int(any(mv)),
                      int(n_units > 0),
                      1,                                    # USE_EQUIPMENT
                      1,                                    # RELEASE
                      int(any(tower_mask)),
                      1,                                    # STRENGTHEN
                      1,                                    # ACTIVE_BLUEPRINT
                      1],                                   # CONTRAPTION
        "mech_cands": sorted(set(buy) | set(unlock)),
        "mech_mask": {"BUY_UNIT": [1] * len(buy) + [0] * len(unlock),
                      "UNLOCK_UNIT": [0] * len(buy) + [1] * len(unlock)},
        "unit_mask": {"UPGRADE_UNIT": up, "SELL_UNIT": sell,
                      "MOVE_UNIT": mv},
        "tech_cands": [[int(rng.choice(MECHS[:1])), int(t)]
                       for t in TECHS[:2]],
        "tech_mask": [1, 1],
        "equip_cands": [e for e in EQUIPS if e],
        "equip_mask": [1, 1],
        "skill_cands": [[s, sid] for (s, sid) in skills],
        "skill_mask": [1] * len(skills),
        "skill_target": ["position" if release_arity(sid) >= 1 else "unit"
                         for (_, sid) in skills],
        "tower_cands": list(TOWERS),
        "tower_mask": tower_mask,
        "blueprint_cands": list(BPS),
        "blueprint_mask": [1, 1],
        "contraption_cands": list(CONTRAPTIONS),
        "contraption_mask": [1],
        "strengthen_mask": [1, 0],
    }


def toy_unit(rng: np.random.RandomState, side: int) -> dict:
    return {
        "kind": "self_unit" if side == 0 else "opp_unit",
        "mech": int(rng.choice(MECHS)), "level": int(rng.randint(1, 9)),
        "exp": int(rng.randint(0, 2000)),
        "x": round(float(rng.uniform(-320, 320)), 1),
        "y": round(float(rng.uniform(-280, -20) if side == 0
                         else rng.uniform(20, 280)), 1),
        "rot": bool(rng.rand() < 0.5),
        "equip": int(rng.choice(EQUIPS)),
        "air": 0, "value": float(rng.randint(50, 400)),
        "status": "known", "fidelity": 1.0,
    }


def toy_battle_obs(rng: np.random.RandomState, ego: int = 0) -> dict:
    from .tokenizer import battle_token_obs_from_v1
    n_self, n_opp = int(rng.randint(1, 7)), int(rng.randint(1, 7))

    def units(side, n):
        lo, hi = (-280, -20) if side == 0 else (20, 280)
        return [{"mech": int(rng.choice(MECHS)),
                 "level": int(rng.randint(1, 9)),
                 "exp": int(rng.randint(0, 2000)),
                 "x": round(float(rng.uniform(-320, 320)), 1),
                 "y": round(float(rng.uniform(lo, hi)), 1),
                 "rot": bool(rng.rand() < 0.5),
                 "equip": int(rng.choice(EQUIPS))} for _ in range(n)]

    v1 = {
        "version": "obs_v1", "round": int(rng.randint(1, 30)), "ego": ego,
        "self": {"hp": int(hs := int(rng.randint(2000, 6000))),
                 "max_hp": 6000,
                 "units": units(0, n_self),
                 "techs": {str(int(rng.choice(MECHS[:2]))):
                           [int(t) for t in rng.choice(
                               TECHS, int(rng.randint(1, 3)),
                               replace=False)]},
                 "officers": [20005] if rng.rand() < 0.5 else [],
                 "blueprints": [1] if rng.rand() < 0.5 else [],
                 "tower_strengthen": [int(rng.randint(0, 4)),
                                      int(rng.randint(0, 4))],
                 "tower_mods": [], "devices": [],
                 "skill_events": [{"id": int(rng.choice(SKILLS_POS1)),
                                   "x": float(rng.uniform(-300, 300)),
                                   "y": float(rng.uniform(-280, -20))}
                                  ] if rng.rand() < 0.5 else []},
        "opp": {"hp": int(rng.randint(2000, 6000)), "max_hp": 6000,
                "units": units(1, n_opp),
                "techs": {}, "officers": [], "blueprints": [],
                "tower_strengthen": [0, int(rng.randint(0, 4))],
                "tower_mods": [], "devices": [], "skill_events": []},
    }
    obs = battle_token_obs_from_v1(v1)
    if rng.rand() < 0.4:
        # a persistent ground area (capsule 2 ordered points, §4.2)
        ax, ay = float(rng.uniform(-300, 300)), float(rng.uniform(-200, 200))
        obs["ground_areas"] = [{
            "skill": SKILL_CAPSULE,
            "points": [(ax, ay), (ax + float(rng.uniform(-80, 80)),
                                  ay + float(rng.uniform(-80, 80)))],
            "radius": 30.0, "ttl": 1, "confidence": 0.5,
        }]
    return obs


def battle_score(obs: dict) -> float:
    """Deterministic label source: sum of unit value + hp advantage."""

    def side_score(side_key: str) -> float:
        g = obs["global"]
        hp = g[side_key + "_hp"] / max(1, g[side_key + "_max_hp"])
        s = 3.0 * hp
        for e in obs["entities"]:
            if e["kind"] == ("self_unit" if side_key == "self"
                             else "opp_unit"):
                s += float(e.get("value", 100.0)) / 100.0 + 0.3 * \
                    float(e.get("level", 1))
        return s

    return side_score("self") - side_score("opp")


# ---------------------------------------------------------------- rows
def make_toy_rows(seed: int = 0, n_games: int = 24, group_k: int = 4):
    """Returns (sim_rows, real_rows, policy_rows) with split assignment
    (replay-group style: one game -> one split, §7.1)."""
    rng = np.random.RandomState(seed)
    sim_rows, real_rows, policy_rows = [], [], []
    for g in range(n_games):
        split = ("train", "validation", "test")[g % 3]
        mid = "toy|%05d" % g
        obs = toy_battle_obs(rng)
        # --- sim: candidate group = K perturbations of the same root
        gid = "%s|cf" % mid
        for k in range(group_k):
            o = dict(obs)
            o["entities"] = [dict(e) for e in obs["entities"]]
            if k:                       # perturb: drop/add self units
                us = [e for e in o["entities"]
                      if e["kind"] == "self_unit"]
                others = [e for e in o["entities"]
                          if e["kind"] != "self_unit"]
                if k == 1 and len(us) > 1:
                    us = us[:-1]
                elif k == 2:
                    u = dict(us[0]); u["level"] = min(
                        9, int(u["level"]) + 2); us[0] = u
                elif k == 3:
                    u = dict(toy_unit(rng, 0)); us.append(u)
                o["entities"] = others + us
            s = battle_score(o)
            logits = np.asarray([max(0.0, -s), 0.0, max(0.0, s)]) / 2.0
            logits = logits - logits.max()
            soft = np.exp(logits) / np.exp(logits).sum()
            sim_rows.append({
                "sample_id": "%s|k%d" % (gid, k), "split": split,
                "corpus": "toy", "candidate_group_id": gid,
                "match_id_hash": mid,
                "observation": o,
                "agg": {"p_loss": float(soft[0]),
                        "p_draw": float(soft[1]),
                        "p_win": float(soft[2]),
                        "y_damage_to_opp": float(
                            np.clip(0.25 + 0.02 * s, 0.02, 0.9)),
                        "y_damage_to_self": float(
                            np.clip(0.25 - 0.02 * s, 0.02, 0.9))},
            })
        # --- real: one labelled battle
        s = battle_score(obs)
        real_rows.append({
            "sample_id": "%s|real" % mid, "split": split, "corpus": "toy",
            "match_id_hash": mid, "observation": obs,
            "y_wdl": int(2 if s > 0.5 else (0 if s < -0.5 else 1)),
            "y_damage_to_opp": float(np.clip(0.25 + 0.02 * s, 0.02, 0.9)),
            "y_damage_to_self": float(np.clip(0.25 - 0.02 * s, 0.02, 0.9)),
        })
        # --- policy: one deploy prefix walk (multi-step, END-terminated)
        rng2 = np.random.RandomState(seed * 100003 + g)
        n_units = int(rng2.randint(1, 5))
        space = toy_space(rng2, n_units)
        own_units = [toy_unit(rng2, 0) for _ in range(n_units)]
        opp_units = [toy_unit(rng2, 1) for _ in range(int(rng2.randint(1, 4)))]
        base_obs = {
            "version": "transformer_obs_v2", "round": int(rng2.randint(1, 12)),
            "ego": 0,
            "global": {"self_hp": 5000, "self_max_hp": 6000, "opp_hp": 4800,
                       "opp_max_hp": 6000,
                       "self_tower_strengthen": [1, 0],
                       "opp_tower_strengthen": [0, 0]},
            "entities": own_units + opp_units +
            [{"kind": "self_tower", "x": 0.0, "y": -295.0},
             {"kind": "opp_tower", "x": 0.0, "y": 295.0}],
            "ground_areas": [],
        }
        plan = craft_plan(rng2, space, n_units)
        history = []
        for step_i, target in enumerate(plan):
            obs_p = dict(base_obs)
            obs_p["global"] = dict(base_obs["global"])
            obs_p["global"]["prefix_len"] = step_i
            obs_p["global"]["budget_left"] = 64 - step_i
            obs_p["policy"] = {
                "unlocked_mechs": list(MECHS[:3]),
                "skills": [{"slot": s, "skill": sid, "active": True,
                            "cd": 0} for (s, sid) in space["skill_cands"]],
                "equipment_inventory": [e for e in EQUIPS if e],
                "history": [dict(h) for h in history],
            }
            obs_p["space"] = space
            policy_rows.append({
                "sample_id": "%s|p%d" % (mid, step_i), "split": split,
                "corpus": "toy", "match_id_hash": mid,
                "observation": obs_p, "target": dict(target),
                "end": 1 if target["verb"] == "END_DEPLOY" else 0,
                "rem_bucket": min(8, (64 - step_i) // 8),
            })
            history.append(_history_entry(target))
    return sim_rows, real_rows, policy_rows


def _history_entry(target: dict) -> dict:
    return {
        "verb": target["verb"],
        "x": float(target.get("x", 0.0) or 0.0),
        "y": float(target.get("y", 0.0) or 0.0),
        "points": [tuple(p) for p in target.get("points") or []],
        "receipt_ok": True,
    }


def craft_plan(rng: np.random.RandomState, space: dict,
               n_units: int) -> list[dict]:
    """A toy deploy plan: a few legal actions then END_DEPLOY. Every target
    is guaranteed in-mask (the cache builder re-validates, §13.2)."""
    plan = []
    plan.append({"verb": "BUY_UNIT",
                 "mech": int(space["mech_cands"][0]),
                 "x": float(rng.uniform(-250, 250)),
                 "y": float(rng.uniform(-280, -60)),
                 "rot": int(rng.randint(0, 2))})
    if n_units > 1:
        plan.append({"verb": "MOVE_UNIT", "handle": 0,
                     "x": float(rng.uniform(-250, 250)),
                     "y": float(rng.uniform(-250, -30)),
                     "rot": int(rng.randint(0, 3))})
    plan.append({"verb": "BUY_TECH",
                 "tech": [int(space["tech_cands"][0][0]),
                          int(space["tech_cands"][0][1])]})
    # multi-point releases: capsule (2 ordered) and beacon (3 ordered)
    plan.append({"verb": "RELEASE_COMMANDER_SKILL",
                 "skill_slot": 0, "skill_id": SKILL_CAPSULE,
                 "points": [(float(rng.uniform(-200, 200)),
                             float(rng.uniform(-200, 0))),
                            (float(rng.uniform(-200, 200)),
                             float(rng.uniform(-200, 0)))]})
    plan.append({"verb": "RELEASE_COMMANDER_SKILL",
                 "skill_slot": 1, "skill_id": SKILL_BEACON,
                 "points": [(float(rng.uniform(-250, 250)),
                             float(rng.uniform(-250, -20)))
                            for _ in range(3)]})
    plan.append({"verb": "RELEASE_COMMANDER_SKILL",
                 "skill_slot": 2, "skill_id": SKILL_UNIT, "handle": 0})
    plan.append({"verb": "STRENGTHEN_TOWER", "tower_index": 0})
    plan.append({"verb": "END_DEPLOY"})
    return plan


def write_toy_datasets(out_dir: str, seed: int = 0, n_games: int = 24,
                       group_k: int = 4) -> dict:
    import gzip
    import json
    import os
    sim, real, pol = make_toy_rows(seed, n_games, group_k)
    os.makedirs(out_dir, exist_ok=True)
    counts = {}
    for name, rows in (("battle_sim_v2", sim), ("battle_real_v2", real),
                       ("policy_prefix_real_v2", pol)):
        path = os.path.join(out_dir, name + ".jsonl.gz")
        with gzip.open(path, "wt", encoding="utf8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str)
                        + "\n")
        counts[name] = len(rows)
    counts["dir"] = out_dir
    return counts
