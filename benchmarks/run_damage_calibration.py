# -*- coding: utf-8 -*-
"""120s single-target damage calibration runner (0829 任务书 T10 / §6.2).

For every attacker mech in the calibration list: a standalone control case
vs the 9 级维修剑齿虎 (equipment 13030006 + 战地维修 10321), fixed level/
distance/orientation/120s window, calib_ledger probes on, CalibrationRow
report per mech (volleys / first_fire_at / actual damage / attacks).

Oracle-diff comes later from the Windows telemetry; this runner produces
the PySim side of the same schema.

用法:
  python benchmarks/run_damage_calibration.py --arm control
  python benchmarks/run_damage_calibration.py --mechs 12,22 --orient 0,90
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmarks.crawler_common import (  # noqa: E402
    SEED, repair_saber, unit, case_digest, write_lib, SCEN_DIR)

# P0 高估 + P1 低估复核名单 (任务书 §5/T6; ID -> 名称)
DEFAULT_MECHS = [27, 29, 17, 12, 23, 22, 1,          # P0
                 31, 14, 2002, 11, 5, 6, 7, 20]      # P1
ORIENTS = {"0": (0.0, -1.0), "90": (-1.0, 0.0), "180": (0.0, 1.0)}
START_DIST = 80.0     # fixed start surface distance (within most ranges)


def mech_name(gd, mech):
    md = gd.mechs.get(mech)
    return md.name if md else str(mech)


def build_cases(gd, mechs, orients):
    cases = []
    for mech in mechs:
        for okey in orients:
            ox, oy = ORIENTS[okey]
            # attacker starts START_DIST away from the target centre, on
            # the approach axis given by the orientation
            ax, ay = ox * START_DIST, oy * START_DIST
            cases.append({
                "name": "D_mech%d_o%s" % (mech, okey), "group": "CAL2",
                "p0": {"units": [unit(mech, ax, ay)]},
                "p1": {"units": [repair_saber(0.0, 0.0)]},
                "opts": {"calib_ledger": 1, "bld_term": 2},
                "desc": "%s 120s single-target window vs 9级维修剑齿虎"
                        % mech_name(gd, mech)})
    return cases


def run_case(gd, case):
    from pysim.engine import battle_from_units
    from pysim.calibration import summarize
    opts = {"seed": SEED, "calib_ledger": 1, "bld_term": 2}
    opts.update(case.get("opts") or {})
    t0 = time.time()
    b = battle_from_units(gd, case["p0"]["units"], case["p1"]["units"],
                          opts=opts)
    w = b.simulate()
    dt = time.time() - t0
    rep = summarize(b, case["name"])
    rep["winner"] = int(w)
    rep["end_t"] = round(b.end_tick * 0.01, 1)
    rep["wall_s"] = round(dt, 2)
    rep["ttk_note"] = "killed-target cases report TTK via end_t (D4 gate)"
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mechs", default=",".join(str(m) for m in DEFAULT_MECHS))
    ap.add_argument("--orient", default="0")
    ap.add_argument("--gen", action="store_true")
    ap.add_argument("--out", default="local_data/crawler_damage_ab/"
                                     "damage_calibration_results.json")
    a = ap.parse_args()
    from pysim.gamedata import GameData
    gd = GameData(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "gamedata.json"))
    mechs = [int(x) for x in a.mechs.split(",") if x.strip()]
    orients = [x for x in a.orient.split(",") if x.strip()]
    cases = build_cases(gd, mechs, orients)
    if a.gen:
        path = write_lib("damage_calibration_scenarios.json", cases)
        print("wrote %s (%d cases)" % (path, len(cases)))
        return
    results = {}
    for case in cases:
        rep = run_case(gd, case)
        results[case["name"]] = rep
        att = rep["rows"].get("0", {})
        print("%-16s winner=%2d end=%5.1fs volleys=%-5s first_fire=%-6s "
              "dmg=%.0f impacts=%d" % (
                  case["name"], rep["winner"], rep["end_t"],
                  att.get("volleys", "-"), att.get("first_fire_at", "-"),
                  att.get("actual_damage", 0.0), att.get("impacts", 0)),
              flush=True)
    out = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("results -> %s" % out)


if __name__ == "__main__":
    main()
