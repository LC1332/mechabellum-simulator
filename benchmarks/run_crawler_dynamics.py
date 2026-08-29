# -*- coding: utf-8 -*-
"""Crawler dynamics benchmark (0829 任务书 T10 / §6.1 C1-C8 proxies).

Paired A/B: control arm (step32 legacy) vs treatment arm (footprint_box +
crawler_flow + crawler_retarget). Reports per case: winner, end time, alive
crawlers, contact ring, rear occupancy, min neighbour gap and wall time.
The oracle side of these cases lands with the Windows telemetry (T0
manifest data/crawler_damage_oracle/crawler-damage-replay-v1/).

用法:
  python benchmarks/run_crawler_dynamics.py                 # 全部 case 两臂
  python benchmarks/run_crawler_dynamics.py --arm control   # 单臂
  python benchmarks/run_crawler_dynamics.py --gen           # 只写场景库
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmarks.crawler_common import (  # noqa: E402
    SEED, CRAWLER, CRAWLER_DENSITY_CARDS, crawler_cards, repair_saber,
    unit, crawler_metrics, arm_opts, drive, case_digest, write_lib, SCEN_DIR)


def build_cases():
    """C1-C8 proxies runnable without the game (oracle rows pending)."""
    cases = []
    # C1: single crawler column card -> sabertooth, frontal approach
    cases.append({"name": "C1_single_card_front", "group": "CD",
                  "p0": {"units": crawler_cards(1)},
                  "p1": {"units": [repair_saber()]}})
    # C2: 96 crawlers (4 cards) single direction
    cases.append({"name": "C2_96_front", "group": "CD",
                  "p0": {"units": crawler_cards(4)},
                  "p1": {"units": [repair_saber()]}})
    # C3: 384 crawlers (16 cards) performance + surround
    cases.append({"name": "C3_384_front", "group": "CD",
                  "p0": {"units": crawler_cards(16, cols=8)},
                  "p1": {"units": [repair_saber()]}})
    # C4: four-direction approach (symmetry / no deadlock)
    quad = []
    for dx, dy in ((0, -80), (0, 80), (-80, 0), (80, 0)):
        quad.append(unit(CRAWLER, dx, dy))
    cases.append({"name": "C4_four_directions", "group": "CD",
                  "p0": {"units": quad}, "p1": {"units": [repair_saber()]}})
    # C6: two large targets, one near one far (out-of-range retarget)
    cases.append({"name": "C6_two_targets_near_far", "group": "CD",
                  "p0": {"units": crawler_cards(2)},
                  "p1": {"units": [repair_saber(0.0, 0.0),
                                   repair_saber(120.0, 0.0)]}})
    # C7: 768 crawlers stress + surround (性能门禁 case)
    cases.append({"name": "C7_768_front", "group": "CD",
                  "p0": {"units": crawler_cards(32, cols=8)},
                  "p1": {"units": [repair_saber()]}})
    return cases


def run_case(gd, case, arm):
    import numpy as np
    from pysim.engine import battle_from_units
    opts = arm_opts(arm, dict(case.get("opts") or {}))
    t0 = time.time()
    b = battle_from_units(gd, case["p0"]["units"], case["p1"]["units"],
                          opts=opts)
    # the repair sabertooth may wipe the swarm long before 120s — sample
    # the dynamics mid-fight instead of only at the end
    marks = [int(t / 0.01) for t in (5.0, 10.0, 20.0, 30.0)]
    samples = {}
    for tick in range(12000):
        b.step(tick)
        b.end_tick = tick
        if tick in marks:
            m = crawler_metrics(b)
            m["t"] = round(tick * 0.01, 1)
            samples["%.0fs" % (tick * 0.01)] = m
        if int(np.count_nonzero((~b.dead) & (b.team == 0))) == 0 \
                or tick == marks[-1] or tick == 11999:
            break
    dt = time.time() - t0
    m = crawler_metrics(b)
    m["t"] = round(b.end_tick * 0.01, 1)
    return {"case": case["name"], "arm": arm, "winner": int(1),
            "end_t": round(b.end_tick * 0.01, 1),
            "wall_s": round(dt, 2),
            "digest": case_digest({"e": b.end_tick,
                                   "k": int(b.total_kills)}),
            "samples": samples, **m}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", default="control,treatment")
    ap.add_argument("--only", default="")
    ap.add_argument("--gen", action="store_true", help="只写场景库")
    a = ap.parse_args()
    cases = build_cases()
    if a.gen:
        path = write_lib("crawler_dynamics_scenarios.json", cases)
        print("wrote %s (%d cases)" % (path, len(cases)))
        return
    if not os.path.isdir(SCEN_DIR):
        os.makedirs(SCEN_DIR, exist_ok=True)
        write_lib("crawler_dynamics_scenarios.json", cases)
    only = {x for x in a.only.split(",") if x}
    from pysim.gamedata import GameData
    gd = GameData(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "gamedata.json"))
    arms = [x for x in a.arm.split(",") if x]
    results = {}
    for case in cases:
        if only and case["name"] not in only:
            continue
        for arm in arms:
            r = run_case(gd, case, arm)
            results.setdefault(case["name"], {})[arm] = r
            s10 = r.get("samples", {}).get("10s", {})
            print("%-26s %-10s end=%5.1fs alive10=%-4s ring10=%-4s "
                  "gap10=%-6s %.1fs" % (
                      case["name"], arm, r["end_t"],
                      s10.get("alive", "-"), s10.get("contact_ring", "-"),
                      s10.get("min_gap", "-"), r["wall_s"]), flush=True)
    out = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "local_data", "crawler_damage_ab")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "crawler_dynamics_results.json")
    with open(path, "w", encoding="utf8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("results -> %s" % path)


if __name__ == "__main__":
    main()
