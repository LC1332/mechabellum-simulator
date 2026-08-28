# -*- coding: utf-8 -*-
"""step32 动态装备 runtime 场景包 runner (任务书 §7.1 单元微型场景层).

对 data/equipment_runtime_scenarios/equipment-runtime-v1.json 的每场景跑
control/treatment 两臂 (只差 equipmentId), 校验 expect 里的机制方向断言
(不锁 oracle 数值)。oracle 真值落 data/equipment_oracle/ 后由
逐装备 oracle A/B gate 决定 confidence 升级 (任务书 §8.5)。

用法 (仓库根):
  python benchmarks/run_equipment_runtime.py             # 全部
  python benchmarks/run_equipment_runtime.py --only eq_mustang_line
"""
import argparse
import io
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCEN_PATH = os.path.join(ROOT, "data", "equipment_runtime_scenarios",
                         "equipment-runtime-v1.json")
ORACLE_DIR = os.path.join(ROOT, "data", "equipment_oracle")
ORACLE_BUILD = "equipment-runtime-oracle-v1"


def run_arm(gd, arm, seed):
    from pysim.engine import battle_from_units
    u0 = [dict(u, id=int(u["mech"]), equipmentId=int(arm["equipment_id"]))
          for u in arm["units0"]]
    u1 = [dict(u, id=int(u["mech"]), equipmentId=0) for u in arm["units1"]]
    skills0 = arm.get("skills0") or None
    skills1 = arm.get("skills1") or None
    tech_map0 = ({int(arm["units0"][0]["mech"]): list(arm["techs0"])}
                 if arm.get("techs0") else None)
    b = battle_from_units(gd, u0, u1, opts={"seed": seed},
                          skills0=skills0, skills1=skills1,
                          tech_map0=tech_map0)
    w = b.simulate()
    res = b.result(w)
    out = {"winner": int(w), "end_t": res["end_time"],
           "damage": res["stats"]["damage"],
           "kills": res["stats"]["kills"],
           "alive": [b.alive_count(0), b.alive_count(1)],
           "score": [round(b.team_score(0), 1), round(b.team_score(1), 1)]}
    if b._eq_runtime:
        out["summon_batches"] = [e["done"] for e in b._eq_pool]
        out["status_blocked"] = sum(
            1 for e in b.status_events if e["action"] == "status_blocked")
        out["shield_absorbed"] = round(
            sum(r["shield_absorbed"] for r in b.damage_receipts), 1)
        out["barrier_absorbed"] = round(
            sum(r["barrier_absorbed"] for r in b.damage_receipts), 1)
    return out


def check_expect(gd, ctrl, treat, expect):
    """机制方向断言 (灰: 只对实现的语义, 不锁 oracle 数值)。"""
    fails = []
    if not expect:
        return fails
    if expect.get("emp_blocked") is True:
        fails += [] if treat.get("status_blocked", 0) > 0 else ["emp not blocked"]
    if expect.get("emp_blocked") is False:
        fails += [] if treat.get("status_blocked", 0) == 0 else ["emp blocked (should not)"]
    if expect.get("survive_longer") is True:
        if treat["end_t"] <= ctrl["end_t"] and treat["alive"][0] <= ctrl["alive"][0]:
            fails.append("no survival gain")
    if "shield_absorbed" in expect and expect["shield_absorbed"] == "max_hp":
        pass    # 数值断言在 tests/test_equipment_runtime.py
    if "barrier_hp" in expect:
        fails += [] if treat.get("barrier_absorbed", 0) > 0 else ["barrier not absorbing"]
    if expect.get("direction") == "faster_kill":
        if treat["end_t"] > ctrl["end_t"] + 1e-9 \
                and treat["alive"][0] <= ctrl["alive"][0]:
            fails.append("no kill-speed gain")
    if expect.get("direction") == "survival_gain":
        if treat["end_t"] < ctrl["end_t"] - 1e-9:
            fails.append("no survival/lethality gain")
    if "batches" in expect:
        if not treat.get("summon_batches") \
                or treat["summon_batches"][0] < 1:
            fails.append("no summon batch activated")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = set(x for x in args.only.split(",") if x)

    from pysim.gamedata import GameData
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    lib = json.load(open(SCEN_PATH, encoding="utf8"))
    seed = int(lib.get("seed", 20220822))
    n_fail = 0
    print("%-32s %-26s %-26s %s" % ("scenario", "ctrl", "treat", "expect"))
    for s in lib["scenarios"]:
        if only and s["name"] not in only:
            continue
        arms = {}
        for arm in s["arms"]:
            arms[arm["name"]] = run_arm(gd, arm, seed)
        ctrl = arms[s["arms"][0]["name"]]
        treat = arms[s["arms"][1]["name"]]
        fails = check_expect(gd, ctrl, treat, s.get("expect"))
        n_fail += bool(fails)
        op = os.path.join(ORACLE_DIR, ORACLE_BUILD, s["name"] + ".json")
        oracle_note = "oracle pending" if not os.path.exists(op) \
            else "oracle present"
        print("%-32s w=%s t=%-6s w=%s t=%-6s %-12s %s" % (
            s["name"], ctrl["winner"], ctrl["end_t"], treat["winner"],
            treat["end_t"], "PASS" if not fails else "FAIL " + ";".join(fails),
            oracle_note))
    print("scenarios: %d, expect failures: %d (oracle build %s 尚未采集; "
          "confidence 全部 provisional)" % (len(lib["scenarios"]), n_fail,
                                            ORACLE_BUILD))
    if n_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
