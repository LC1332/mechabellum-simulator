# -*- coding: utf-8 -*-
"""Chaff-clearing calibration runner (0829 任务书 T10 / §6.1 C8 + §5/T8).

Fixed crawler densities 24/96/384/768 (1/4/16/32 cards) vs one chaff-clear
attacker; paired arms: crawler_flow OFF/ON so AoE calibration never rides a
stacking-density artifact (坐标重叠伪影, 任务书 T8). Reports kills at
10/30/60/120s, clear time, and the crawler spatial metrics (contact ring /
min gap) per sample.

用法:
  python benchmarks/run_chaff_calibration.py --attackers 3,22 --densities 96,384
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmarks.crawler_common import (  # noqa: E402
    SEED, CRAWLER_DENSITY_CARDS, crawler_cards, unit, crawler_metrics,
    arm_opts, drive, case_digest, write_lib)

DEFAULT_ATTACKERS = [3, 22, 12, 25]   # 火神/台风/暴雨/魔眼 (清杂标定名单)
SAMPLE_TS = (10.0, 30.0, 60.0, 120.0)


def build_cases(attackers, densities):
    cases = []
    for mech in attackers:
        for den in densities:
            n = CRAWLER_DENSITY_CARDS[str(den)]
            cases.append({
                "name": "K_mech%d_d%s" % (mech, den), "group": "CH",
                "p0": {"units": [unit(mech, 0.0, -120.0)]},
                "p1": {"units": crawler_cards(n, y0=-60.0,
                                              cols=min(8, n))},
                "desc": "clear %s crawler modules with mech %d"
                        % (den, mech)})
    return cases


def run_case(gd, case, arm):
    from pysim.engine import battle_from_units
    opts = arm_opts(arm)
    b = battle_from_units(gd, case["p0"]["units"], case["p1"]["units"],
                          opts=opts)
    marks = [int(t / 0.01) for t in SAMPLE_TS]
    kills_at = {}
    metrics_at = {}
    done_tick = None
    import numpy as np
    for tick in range(12000):
        b.step(tick)
        b.end_tick = tick
        if tick in marks:
            kills_at["%.0fs" % (tick * 0.01)] = int(b.total_kills)
            metrics_at["%.0fs" % (tick * 0.01)] = crawler_metrics(b)
        # clear detection: no living enemy module left
        if done_tick is None and \
                int(np.count_nonzero((~b.dead) & (b.team == 1))) == 0:
            done_tick = tick
            break
        if tick == marks[-1]:
            done_tick = tick
            break
    return {"case": case["name"], "arm": arm,
            "clear_t": round(done_tick * 0.01, 1) if done_tick else None,
            "kills_at": kills_at,
            "metrics_at": metrics_at,
            "digest": case_digest({"k": kills_at, "c": done_tick})}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--attackers",
                    default=",".join(str(m) for m in DEFAULT_ATTACKERS))
    ap.add_argument("--densities", default="24,96,384,768")
    ap.add_argument("--arm", default="control,treatment")
    ap.add_argument("--gen", action="store_true")
    a = ap.parse_args()
    attackers = [int(x) for x in a.attackers.split(",") if x.strip()]
    densities = [x for x in a.densities.split(",") if x.strip()]
    cases = build_cases(attackers, densities)
    if a.gen:
        path = write_lib("chaff_calibration_scenarios.json", cases)
        print("wrote %s (%d cases)" % (path, len(cases)))
        return
    from pysim.gamedata import GameData
    gd = GameData(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "gamedata.json"))
    results = {}
    for case in cases:
        for arm in [x for x in a.arm.split(",") if x]:
            r = run_case(gd, case, arm)
            results.setdefault(case["name"], {})[arm] = r
            print("%-18s %-10s clear=%-6s kills@120=%-5s ring=%s" % (
                case["name"], arm, r["clear_t"],
                r["kills_at"].get("120s", "-"),
                r["metrics_at"].get("120s", {}).get("contact_ring", "-")),
                flush=True)
    out = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "local_data", "crawler_damage_ab",
        "chaff_calibration_results.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("results -> %s" % out)


if __name__ == "__main__":
    main()
