# -*- coding: utf-8 -*-
"""装备静态专项库 runner (battlefield E2/E6).

每个 data/equipment_scenarios.json 场景跑 A/B 两臂 (A 无装备, B 绑定指定
装备), 输出 winner/逐 card damage/kills/survival 与 A/B 差异。oracle 真值
放 data/equipment_oracle/<name>.json (真实游戏对照, 格式见 ORACLE_KEYS);
未提供 oracle 的场景只报告 A/B, 不计准确率 —— E6 gate: 任一装备升级为
verified 前必须先有 oracle 记录。

用法 (仓库根):
  python benchmarks/run_equipment.py            # 全部场景 A/B
  python benchmarks/run_equipment.py --only eq_lasersight_ranger
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCEN_PATH = os.path.join(ROOT, "data", "equipment_scenarios.json")
ORACLE_DIR = os.path.join(ROOT, "data", "equipment_oracle")
SEED = 20220822

# oracle 记录字段 (真实游戏对照; 由用户从游戏内截图/回放抄录)
ORACLE_KEYS = ("winner", "survived_p0", "survived_p1", "damage_dealt_p0",
               "damage_dealt_p1", "note")


def run_arm(gd, s, equipment_id):
    from pysim.engine import battle_from_units
    u0 = [dict(u, equipmentId=(equipment_id
                               if int(u["id"]) == int(s["target_mech"]) else 0))
          for u in s["p0_units"]]
    b = battle_from_units(gd, u0, s["p1_units"], opts={"seed": SEED})
    w = b.simulate()
    return {"winner": int(w),
            "alive": [b.alive_count(0), b.alive_count(1)],
            "score": [round(b.team_score(0), 1), round(b.team_score(1), 1)],
            "cards": [{"mech": c["mech"], "team": c["team"],
                       "damage": c["damage"], "kills": c["kills"],
                       "survived": c["survived"]}
                      for c in b.outcome_cards()],
            "end_t": round(b.end_tick * 0.01, 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help="逗号分隔场景名过滤")
    a = ap.parse_args()
    only = set(x for x in a.only.split(",") if x)

    from pysim.gamedata import GameData
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    lib = json.load(open(SCEN_PATH, encoding="utf8"))
    n_oracle = agree = 0
    print("%-28s %-22s %-22s %s" % ("scenario", "A (no equipment)",
                                    "B (equipped)", "oracle"))
    for s in lib["scenarios"]:
        if only and s["name"] not in only:
            continue
        ra = run_arm(gd, s, 0)
        rb = run_arm(gd, s, int(s["equipment_id"]))
        op = os.path.join(ORACLE_DIR, s["name"] + ".json")
        oracle_note = "pending (no oracle record)"
        if os.path.exists(op):
            n_oracle += 1
            orc = json.load(open(op, encoding="utf8"))
            if orc.get("winner") == rb["winner"]:
                agree += 1
            oracle_note = "winner=%s %s" % (orc.get("winner"),
                                            orc.get("note", ""))
        print("%-28s w=%s score=%-18s w=%s score=%-18s %s" % (
            s["name"], ra["winner"], ra["score"], rb["winner"], rb["score"],
            oracle_note))
    print("oracle records: %d (%d winner-agree) — verified upgrades require "
          "an oracle per equipment id (E6)" % (n_oracle, agree))


if __name__ == "__main__":
    main()
