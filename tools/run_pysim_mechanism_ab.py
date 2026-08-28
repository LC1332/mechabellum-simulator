# -*- coding: utf-8 -*-
"""T0 机制级 paired A/B runner (pysim动态装备与伤害管线修正任务书-2026-08-28).

Stable pair_id + structured per-pair results for mechanism-level paired
A/B over the 1106-game human replay corpus. 战斗语义零改动: 只是把语料
units 的 `equipment` 字段映射到引擎的 `equipmentId`, 并按臂 (arm) 决定
哪些装备 ID 生效。

Arms:
  baseline          所有装备清零 (复现 57.91%/56.91% 冻结基线)
  on                装备全部生效 (静态 + runtime)
  id:<equipment_id> 只让该 ID 生效, 其余清零 (单机制因果臂;
                    OFF 臂 = baseline, 配对读 flips)

用法:
  python tools/run_pysim_mechanism_ab.py --arms baseline,on \
      --out local_data/equipment_runtime_ab/step32_probe
  python tools/run_pysim_mechanism_ab.py --arms id:1305003 --limit 50
  并行: --shard i --num-shards N (按整局回放分片, 不拆断跨回合状态)

输出: <out>/meta.json + pairs_<arm>.jsonl + summary.json
"""
import argparse
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                                    # noqa: E402
from pysim.engine import battle_from_units                             # noqa: E402
from pysim.replay_check import (build_tech_map, build_tower_mods,      # noqa: E402
                                keep_round, parse_round_filter)
from pysim.flank import pair_flank_delays, annotate_units, count_delays  # noqa: E402
from pysim.skills import events_from_skill_actions                     # noqa: E402
from pysim.battlefield.effects.equipment import (                      # noqa: E402
    EQUIPMENT_BATTLE_SPECS, EQUIPMENT_RUNTIME_SPECS,
    SELECTED_RUNTIME_EQUIPMENT_IDS)

DEFAULT_CORPUS = os.path.join(ROOT, "local_data", "humen_rounds.json")
STATIC_IDS = frozenset(EQUIPMENT_BATTLE_SPECS)
SELECTED_IDS = frozenset(SELECTED_RUNTIME_EQUIPMENT_IDS)
DEFERRED_IDS = frozenset({13030010, 13040001})


def bucket_of(eq_ids):
    if not eq_ids:
        return "none"
    if eq_ids & SELECTED_IDS:
        return "selected"
    if eq_ids & DEFERRED_IDS:
        return "deferred"
    if eq_ids <= STATIC_IDS:
        return "static_only"
    return "other"


def arm_active_ids(arm):
    if arm == "baseline":
        return frozenset()
    if arm == "on":
        return None                     # 全部生效
    if arm.startswith("id:"):
        return frozenset({int(arm[3:])})
    raise ValueError("unknown arm %r" % arm)


def map_units(units, active_ids):
    """语料 `equipment` -> 引擎 `equipmentId`; 不在 active 集合的 ID 清零。
    active_ids=None 表示全部保留。"""
    out = []
    for u in units:
        eid = int(u.get("equipment", 0) or 0)
        keep = eid != 0 and (active_ids is None or eid in active_ids)
        out.append(dict(u, equipmentId=eid if keep else 0))
    return out


def outcome_digest(b):
    res = b.result(0)
    res.pop("trace", None)
    blob = json.dumps(res, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def run_pair(gd, pair, arm, cfg):
    active = arm_active_ids(arm)
    u0, u1 = pair["p0"]["units"], pair["p1"]["units"]
    if cfg["deploy"] == "fight":
        u0 = pair["p0"].get("units_fight") or u0
        u1 = pair["p1"].get("units_fight") or u1
    flank_unlock = cfg.get("_flank_unlock")
    if cfg["sneak"] != "off" and cfg["deploy"] == "fight":
        d0, d1 = pair_flank_delays(pair, mode=cfg["sneak"],
                                   unlock_state=flank_unlock,
                                   delay=cfg["opts"].get("sneak_delay"))
        u0 = annotate_units(u0, d0)
        u1 = annotate_units(u1, d1)
    else:
        d0 = d1 = ()
    tm0 = build_tech_map(gd, pair["p0"], cfg["techs"],
                         {int(u["id"]) for u in u0})
    tm1 = build_tech_map(gd, pair["p1"], cfg["techs"],
                         {int(u["id"]) for u in u1})
    tw0 = build_tower_mods(pair["p0"]) if cfg["tower_skills"] else None
    tw1 = build_tower_mods(pair["p1"]) if cfg["tower_skills"] else None
    twr0 = twr1 = None
    if cfg["towers"]:
        twr0 = [int(x) for x in (pair["p0"].get("towerStrengthen_raw") or [0, 0])][:2]
        twr1 = [int(x) for x in (pair["p1"].get("towerStrengthen_raw") or [0, 0])][:2]
    sk0 = sk1 = None
    ev0 = events_from_skill_actions(pair["p0"].get("skill_actions"))
    ev1 = events_from_skill_actions(pair["p1"].get("skill_actions"))
    if cfg["skills"]:
        sk0 = ev0 or None
        sk1 = ev1 or None
    bl0 = bl1 = None
    if cfg["buildings"]:
        bl0 = parse_constructions(pair["p0"])
        bl1 = parse_constructions(pair["p1"])
    of0 = pair["p0"].get("officers") if cfg["officers"] else None
    of1 = pair["p1"].get("officers") if cfg["officers"] else None
    b = battle_from_units(gd, map_units(u0, active), map_units(u1, active),
                          tech_map0=tm0, tech_map1=tm1, opts=dict(cfg["opts"]),
                          tower_mods0=tw0, tower_mods1=tw1,
                          towers0=twr0, towers1=twr1,
                          skills0=sk0, skills1=sk1,
                          buildings0=bl0, buildings1=bl1,
                          officers0=of0, officers1=of1)
    if b.alive_count(0) == 0 or b.alive_count(1) == 0:
        return None
    winner = b.simulate()
    res = b.result(winner)
    return {
        "sim_winner": int(winner),
        "correct": bool((winner == 0) == (pair["label"] == "Win"))
        if winner in (0, 1) else False,
        "draw": winner == -1,
        "end_time": res["end_time"],
        "damage": res["stats"]["damage"],
        "kills": res["stats"]["kills"],
        "survivors_p0": res["survivors"][0]["mechs"],
        "survivors_p1": res["survivors"][1]["mechs"],
        "outcome_digest": outcome_digest(b),
    }


def parse_constructions(pdata, bld_cids=None):
    out = []
    for c in (pdata.get("constructions_raw") or []):
        try:
            cid = int(c.get("id", 0))
            if cid not in (1, 2, 3, 4) or (bld_cids and cid not in bld_cids):
                continue
            out.append({"cid": cid, "x": float(c["x"]), "y": float(c["y"]),
                        "index": int(c["index"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rounds", default=DEFAULT_CORPUS)
    ap.add_argument("--arms", default="baseline",
                    help="comma list: baseline,on,id:<eid>")
    ap.add_argument("--out", default=os.path.join(
        ROOT, "local_data", "equipment_runtime_ab", "run"))
    ap.add_argument("--limit", type=int, default=0, help="max pairs per arm")
    ap.add_argument("--round-filter", default="all")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--skills", action="store_true")
    ap.add_argument("--techs", default="mdefull")
    ap.add_argument("--deploy", default="fight", choices=("fight", "snap"))
    ap.add_argument("--sneak", default="card")
    ap.add_argument("--no-towers", dest="towers", action="store_false")
    ap.add_argument("--no-buildings", dest="buildings", action="store_false")
    ap.add_argument("--no-officers", dest="officers", action="store_false")
    ap.add_argument("--tower-skills", action="store_true")
    ap.add_argument("--opt", default="", help="k=v,k=v (engine opts)")
    args = ap.parse_args()

    opts = {"seed": 1401}
    for kv in (args.opt or "").split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            try:
                opts[k] = int(v)
            except ValueError:
                try:
                    opts[k] = float(v)
                except ValueError:
                    opts[k] = v

    rounds = json.load(open(args.rounds, encoding="utf-8"))
    if args.num_shards > 1:
        rounds = rounds[args.shard::args.num_shards]
    corpus_bytes = open(args.rounds, "rb").read()
    corpus_version = hashlib.sha256(corpus_bytes).hexdigest()[:16]
    corpus_all = json.load(open(args.rounds, encoding="utf-8"))
    dup_names = {f for f in (r["file"] for r in corpus_all)
                 if [x["file"] for x in corpus_all].count(f) > 1}
    # 重复文件名 → pair_id 纳入回放内容 hash (任务书 T0)
    dup_content = {r["file"]:
                   hashlib.sha256(json.dumps(r, sort_keys=True,
                                             ensure_ascii=False)
                                  .encode("utf-8")).hexdigest()[:12]
                   for r in corpus_all if r["file"] in dup_names}

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    rf = parse_round_filter(args.round_filter)
    cfg = {"techs": args.techs, "deploy": args.deploy, "sneak": args.sneak,
           "towers": args.towers, "buildings": args.buildings,
           "officers": args.officers, "tower_skills": args.tower_skills,
           "skills": args.skills, "opts": opts}
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    os.makedirs(args.out, exist_ok=True)
    files = {arm: open(os.path.join(args.out, "pairs_%s.jsonl" % arm),
                       "w", encoding="utf-8") for arm in arms}
    stats = {arm: {"total": 0, "correct": 0, "draws": 0, "skipped": 0}
             for arm in arms}
    t0 = time.perf_counter()
    flank_unlock = {0: False, 1: False}
    cfg["_flank_unlock"] = flank_unlock
    n_replays = 0
    for replay in rounds:
        n_replays += 1
        fname = replay["file"]
        # step9 'game' 侧 flank 解锁状态跨 round 保留 (分片按整局)
        flank_unlock[0] = flank_unlock[1] = False
        for pidx, pair in enumerate(replay["pairs"]):
            if not keep_round(rf, pair["round"]):
                continue
            u0 = pair["p0"].get("units_fight") or pair["p0"]["units"]
            u1 = pair["p1"].get("units_fight") or pair["p1"]["units"]
            eq_ids = {int(u.get("equipment", 0) or 0) for u in u0 + u1}
            eq_ids.discard(0)
            ident = fname + ("#" + dup_content[fname] if fname in dup_names
                             else "")
            pair_id = hashlib.sha1(
                ("%s|%s|%d|%d" % (corpus_version, ident,
                                  pair["round"], pidx))
                .encode("utf-8")).hexdigest()[:16]
            rec = {"pair_id": pair_id, "file": fname, "round": pair["round"],
                   "label": pair["label"], "bucket": bucket_of(eq_ids),
                   "equipment_ids": sorted(eq_ids), "skilled":
                   bool(pair["p0"].get("skill_actions")
                        or pair["p1"].get("skill_actions")),
                   "n0": len(u0), "n1": len(u1)}
            if args.limit and all(stats[a]["total"] >= args.limit
                                  for a in arms):
                break
            for arm in arms:
                s = stats[arm]
                if args.limit and s["total"] >= args.limit:
                    continue
                r = run_pair(gd, pair, arm, cfg)
                if r is None:
                    s["skipped"] += 1
                    continue
                s["total"] += 1
                if r["draw"]:
                    s["draws"] += 1
                elif r["correct"]:
                    s["correct"] += 1
                out = dict(rec)
                out.update(r)
                files[arm].write(json.dumps(out, ensure_ascii=False) + "\n")
            # paired flips are resolved from the jsonl arms in summarize()
        if args.limit and all(stats[a]["total"] >= args.limit for a in arms):
            break
    for f in files.values():
        f.close()
    meta = {
        "runner": "run_pysim_mechanism_ab", "version": 1,
        "corpus": os.path.basename(args.rounds),
        "corpus_version": corpus_version,
        "corpus_bytes": len(corpus_bytes),
        "duplicate_replay_names": sorted(dup_names),
        "arms": arms, "shard": args.shard, "num_shards": args.num_shards,
        "limit": args.limit, "round_filter": args.round_filter,
        "skills": args.skills, "techs": args.techs, "deploy": args.deploy,
        "sneak": args.sneak, "towers": args.towers,
        "buildings": args.buildings, "officers": args.officers,
        "tower_skills": args.tower_skills, "opts": opts,
        "selected_equipment_ids": sorted(SELECTED_IDS),
        "static_equipment_ids": sorted(STATIC_IDS),
        "deferred_equipment_ids": sorted(DEFERRED_IDS),
        "replays_in_shard": n_replays,
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    json.dump(meta, open(os.path.join(args.out, "meta.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    summarize(args.out, arms)
    print(json.dumps(json.load(open(os.path.join(args.out, "summary.json"),
                                    encoding="utf-8")),
                     ensure_ascii=False, indent=1))


def summarize(out_dir, arms):
    """Aggregate paired flips per ID + bucket stats from the jsonl arms."""
    per_arm = {}
    for arm in arms:
        rows = []
        path = os.path.join(out_dir, "pairs_%s.jsonl" % arm)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    rows.append(json.loads(line))
        per_arm[arm] = {r["pair_id"]: r for r in rows}
        tot = sum(1 for r in rows if not r["draw"])
        ok = sum(1 for r in rows if r["correct"])
        per_arm[arm + "#agg"] = {"total": len(rows), "decided": tot,
                                 "correct": ok,
                                 "acc": round(100.0 * ok / tot, 2) if tot else None}
    flip = {}
    base = per_arm.get("baseline", {})
    for arm in arms:
        if not arm.startswith("id:"):
            continue
        eid = int(arm[3:])
        onarm = per_arm[arm]
        good = bad = both_ok = both_bad = 0
        for pid, r in onarm.items():
            b = base.get(pid)
            if b is None:
                continue
            on_ok, off_ok = r["correct"], b["correct"]
            if on_ok and not off_ok:
                good += 1
            elif off_ok and not on_ok:
                bad += 1
            elif on_ok and off_ok:
                both_ok += 1
            else:
                both_bad += 1
        flip[str(eid)] = {"pairs": good + bad + both_ok + both_bad,
                          "good_flips": good, "bad_flips": bad,
                          "net_flips": good - bad,
                          "both_correct": both_ok, "both_wrong": both_bad}
    buckets = {}
    for arm in arms:
        agg = {}
        for r in per_arm[arm].values() if isinstance(per_arm[arm], dict) else []:
            bk = agg.setdefault(r["bucket"], [0, 0])
            bk[0] += 0 if r["draw"] else 1
            bk[1] += 1 if r["correct"] else 0
        buckets[arm] = {k: {"decided": v[0],
                            "acc": round(100.0 * v[1] / v[0], 2) if v[0] else None}
                        for k, v in agg.items()}
    summary = {"arms": {a: per_arm.get(a + "#agg") for a in arms},
               "paired_flips_vs_baseline": flip, "buckets": buckets}
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
