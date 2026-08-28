# -*- coding: utf-8 -*-
"""Per-round accuracy + per-unit / unit+tech / battlefield-skill bias scan.

Joins tools/run_pysim_mechanism_ab.py shard outputs (pairs_<arm>.jsonl) with
the replay corpus composition (deployed mechs, bought techs per mech,
battlefield skill actions per side) and reports for every key:

  n           side-observations = (pair, side) where the key is present
  replay_wr   real side win rate from the replay label
  sim_wr      simulated side win rate (draw counts as loss for both sides)
  delta_pp    sim_wr - replay_wr; positive = engine over-estimates the key
  over/under  discordant counts (sim-only win / replay-only win);
              z = (over-under)/sqrt(over+under)  (McNemar-style)
  acc         winner accuracy of the pairs containing the key

usage: python tools/analyze_replay_units.py --scan-dir local_data/units_scan \
           --rounds local_data/humen_rounds.json --out local_data/units_scan/analysis.json
"""
import argparse
import glob
import json
import math
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.skills import CONTRAPTIONS, COMMANDER_SKILLS          # noqa: E402


def load_rows(scan_dir, arm):
    rows = []
    for p in sorted(glob.glob(os.path.join(scan_dir, "c??",
                                           "pairs_%s.jsonl" % arm))):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def build_comp(rounds_path):
    """(file, round) -> label + per-side {units, techs, skills}."""
    rounds = json.load(open(rounds_path, encoding="utf8"))
    comp = {}
    for replay in rounds:
        fname = replay["file"]
        for pair in replay["pairs"]:
            sides = []
            for s in (0, 1):
                pd = pair["p%d" % s]
                units = pd.get("units_fight") or pd["units"]
                us = [(int(u["id"]), int(u.get("level", 1)),
                       int(u.get("equipment", 0) or 0)) for u in units]
                techs = {int(m): [int(t) for t in ts]
                         for m, ts in (pd.get("techMap") or {}).items()}
                sk = Counter()
                for a in (pd.get("skill_actions") or []):
                    try:
                        sk[(str(a.get("type")), int(a.get("id", 0) or 0))] += 1
                    except (TypeError, ValueError):
                        pass
                sides.append({"units": us, "techs": techs, "skills": sk})
            comp[(fname, int(pair["round"]))] = {
                "label": pair["label"], "sides": sides}
    return comp


def new_stat():
    return {"n": 0, "r_win": 0, "s_win": 0, "over": 0, "under": 0,
            "pairs": 0, "pairs_ok": 0}


def add(stats, key, rwin, swin):
    d = stats.setdefault(key, new_stat())
    d["n"] += 1
    d["r_win"] += int(rwin)
    d["s_win"] += int(swin)
    if swin and not rwin:
        d["over"] += 1
    if rwin and not swin:
        d["under"] += 1


def add_pair(stats, key, correct):
    d = stats.setdefault(key, new_stat())
    d["pairs"] += 1
    d["pairs_ok"] += int(bool(correct))


def finalize(stats):
    out = {}
    for k, d in stats.items():
        n = d["n"] or 1
        du = d["over"] + d["under"]
        out[k] = dict(d,
                      replay_wr=round(100.0 * d["r_win"] / n, 2),
                      sim_wr=round(100.0 * d["s_win"] / n, 2),
                      delta_pp=round(100.0 * (d["s_win"] - d["r_win"]) / n, 2),
                      z=round((d["over"] - d["under"]) / math.sqrt(du), 2)
                      if du else 0.0,
                      acc=round(100.0 * d["pairs_ok"] / d["pairs"], 2)
                      if d["pairs"] else None)
    return out


def skill_label(type_, sid):
    if type_ == "contraption":
        d = CONTRAPTIONS.get(sid)
        return (d["name"] if d and isinstance(d, dict) else None), \
               ("contraption" if d and isinstance(d, dict) else "unmapped")
    d = COMMANDER_SKILLS.get(sid)
    return (d["name"] if d else None), \
           ("commander" if d else "unmapped")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan-dir", default=os.path.join(ROOT, "local_data",
                                                       "units_scan"))
    ap.add_argument("--rounds", default=os.path.join(ROOT, "local_data",
                                                     "humen_rounds.json"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-n-mech", type=int, default=60)
    ap.add_argument("--min-n-mechtech", type=int, default=40)
    ap.add_argument("--min-n-skill", type=int, default=25)
    args = ap.parse_args()

    gd = json.load(open(os.path.join(ROOT, "data", "gamedata.json"),
                        encoding="utf8"))
    mech_name = {int(k): v["name"] for k, v in gd["cards"].items()}
    tech_name = {int(k): v.get("name") or ("科技%s" % k)
                 for k, v in gd["techs"].items()}

    comp = build_comp(args.rounds)
    arms = {}
    per_round = {}
    overall = {}
    for arm in ("baseline", "on"):
        rows = load_rows(args.scan_dir, arm)
        arms[arm] = rows
        t = c = dr = 0
        rd = {}
        for r in rows:
            t += 1
            c += int(bool(r["correct"]))
            dr += int(r["draw"])
            k = int(r["round"])
            b = rd.setdefault(k, [0, 0, 0])
            b[0] += 1
            b[1] += int(bool(r["correct"]))
            b[2] += int(r["draw"])
        per_round[arm] = rd
        overall[arm] = {"total": t, "correct": c, "draws": dr,
                        "acc": round(100.0 * c / t, 2) if t else None}

    # paired flip summary baseline -> on
    base_idx = {(r["file"], int(r["round"])): r for r in arms["baseline"]}
    good = bad = same = surv_diff = 0
    et_base = et_on = 0.0
    for r in arms["on"]:
        b = base_idx.get((r["file"], int(r["round"])))
        if b is None:
            continue
        if r["correct"] and not b["correct"]:
            good += 1
        elif b["correct"] and not r["correct"]:
            bad += 1
        else:
            same += 1
        if (r.get("survivors_p0"), r.get("survivors_p1")) != \
           (b.get("survivors_p0"), b.get("survivors_p1")):
            surv_diff += 1
        et_base += r.get("end_time") or 0
        et_on += b.get("end_time") or 0
    n_join = good + bad + same

    mech = {}
    mechtech = {}
    skill = {}
    mech_level = {}
    unmatched = 0
    rows = arms["on"]
    for r in rows:
        cp = comp.get((r["file"], int(r["round"])))
        if cp is None:
            unmatched += 1
            continue
        rwin0 = cp["label"] == "Win"
        tok = (r["file"], int(r["round"]))
        correct = bool(r["correct"])
        pair_mechs = set()
        pair_mt = set()
        pair_sk = set()
        for s in (0, 1):
            sd = cp["sides"][s]
            rwin = rwin0 if s == 0 else (not rwin0)
            swin = (r["sim_winner"] == s) if r["sim_winner"] in (0, 1) else False
            for (mid, lvl, _eq) in sd["units"]:
                add(mech, mid, rwin, swin)
                add(mech_level, (mid, lvl), rwin, swin)
                pair_mechs.add(mid)
                for t in sd["techs"].get(mid, []):
                    add(mechtech, (mid, t), rwin, swin)
                    pair_mt.add((mid, t))
            for (ty, sid), _cnt in sd["skills"].items():
                add(skill, (ty, sid), rwin, swin)
                pair_sk.add((ty, sid))
        for mid in pair_mechs:
            add_pair(mech, mid, correct)
        for k2 in pair_mt:
            add_pair(mechtech, k2, correct)
        for k2 in pair_sk:
            add_pair(skill, k2, correct)

    mech_f = finalize(mech)
    mechtech_f = finalize(mechtech)
    skill_f = finalize(skill)
    mech_level_f = {("%s lv%d" % (mech_name.get(m, m), l)): v
                    for (m, l), v in finalize(mech_level).items()}

    def rows_table(fstats, names, min_n, sort_key):
        out = []
        for k, d in fstats.items():
            if d["n"] < min_n:
                continue
            out.append((names.get(k, k), d))
        out.sort(key=sort_key)
        return out

    result = {
        "overall": overall,
        "paired_flip_baseline_to_on": {
            "joined": n_join, "good_flips": good, "bad_flips": bad,
            "survivor_diff_pairs": surv_diff,
            "end_time_mean_baseline": round(et_on / max(1, n_join), 1),
            "end_time_mean_on": round(et_base / max(1, n_join), 1)},
        "per_round": {arm: {str(k): {"n": v[0], "ok": v[1], "draws": v[2],
                                     "acc": round(100.0 * v[1] / v[0], 2)}
                            for k, v in sorted(rd.items())}
                      for arm, rd in per_round.items()},
        "mech": {mech_name.get(m, m): d for m, d in mech_f.items()},
        "mechtech": {"%s+%s" % (mech_name.get(m, m), tech_name.get(t, t)): d
                     for (m, t), d in mechtech_f.items()},
        "skill": {"%s(%s/%s)" % (skill_label(ty, sid)[0] or
                                 "未映射%s" % sid, ty, sid): d
                  for (ty, sid), d in skill_f.items()},
        "mech_level": mech_level_f,
        "unmatched_pairs": unmatched,
    }
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        json.dump(result, open(args.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("json ->", args.out)

    # console report
    print("baseline: %d pairs %d correct = %.2f%% (draws %d)\non:       "
          "%d pairs %d correct = %.2f%% (draws %d)\nflips base->on: "
          "good %d bad %d (net %+d), survivors differ in %d pairs" % (
              overall["baseline"]["total"], overall["baseline"]["correct"],
              overall["baseline"]["acc"], overall["baseline"]["draws"],
              overall["on"]["total"], overall["on"]["correct"],
              overall["on"]["acc"], overall["on"]["draws"],
              good, bad, good - bad, surv_diff))
    print("\nper-round accuracy (baseline -> on):")
    rd_b = per_round["baseline"]
    rd_o = per_round["on"]
    for k in sorted(set(rd_b) | set(rd_o)):
        b = rd_b.get(k, [0, 0, 0])
        o = rd_o.get(k, [0, 0, 0])
        print("  r%-2d n=%-4d  baseline %.2f%%  on %.2f%%  (%+.2f pp)" % (
            k, o[0], 100.0 * b[1] / b[0] if b[0] else 0,
            100.0 * o[1] / o[0] if o[0] else 0,
            (100.0 * o[1] / o[0] if o[0] else 0) -
            (100.0 * b[1] / b[0] if b[0] else 0)))

    def print_table(title, data, min_n):
        print("\n== %s (n>=%d) ==" % (title, min_n))
        print("%-28s %6s %8s %8s %8s %7s %7s %7s %7s" % (
            "key", "n", "replay%", "sim%", "delta", "over", "under", "z",
            "acc%"))
        for name, d in data:
            print("%-28s %6d %8.2f %8.2f %+8.2f %7d %7d %7.2f %7.2f" % (
                name[:28], d["n"], d["replay_wr"], d["sim_wr"],
                d["delta_pp"], d["over"], d["under"], d["z"], d["acc"] or 0))

    over_m = rows_table(mech_f, mech_name, args.min_n_mech,
                        lambda kv: (-kv[1]["z"], -kv[1]["delta_pp"]))
    under_m = rows_table(mech_f, mech_name, args.min_n_mech,
                         lambda kv: (kv[1]["z"], kv[1]["delta_pp"]))
    print_table("兵种 sim胜率偏高 TOP", over_m[:15], args.min_n_mech)
    print_table("兵种 sim胜率偏低 TOP", under_m[:15], args.min_n_mech)
    over_t = rows_table(mechtech_f,
                        {k: "%s+%s" % (mech_name.get(k[0], k[0]),
                                       tech_name.get(k[1], k[1]))
                         for k in mechtech_f},
                        args.min_n_mechtech,
                        lambda kv: (-kv[1]["z"], -kv[1]["delta_pp"]))
    under_t = rows_table(mechtech_f,
                         {k: "%s+%s" % (mech_name.get(k[0], k[0]),
                                        tech_name.get(k[1], k[1]))
                          for k in mechtech_f},
                         args.min_n_mechtech,
                         lambda kv: (kv[1]["z"], kv[1]["delta_pp"]))
    print_table("兵种+科技 偏高 TOP", over_t[:20], args.min_n_mechtech)
    print_table("兵种+科技 偏低 TOP", under_t[:20], args.min_n_mechtech)
    skill_names = {k: "%s[%s]%s" % (skill_label(k[0], k[1])[0]
                                    or "未映射%d" % k[1], k[0],
                                    "已映射" if skill_label(k[0], k[1])[0]
                                    else "未实现")
                   for k in skill_f}
    over_s = rows_table(skill_f, skill_names, args.min_n_skill,
                        lambda kv: (-kv[1]["z"], -kv[1]["delta_pp"]))
    under_s = rows_table(skill_f, skill_names, args.min_n_skill,
                         lambda kv: (kv[1]["z"], kv[1]["delta_pp"]))
    print_table("战场技能 偏高 TOP", over_s[:15], args.min_n_skill)
    print_table("战场技能 偏低 TOP", under_s[:15], args.min_n_skill)
    if unmatched:
        print("\nunmatched pairs (no composition):", unmatched)


if __name__ == "__main__":
    main()
