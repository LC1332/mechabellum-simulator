# -*- coding: utf-8 -*-
"""battlefield 重构计划指标再生命令 (§9: 文档中的指标可由命令重新生成).

三个输出, 全部来自单一规则源, 不依赖手工抄写:
  1. 机制注册表 dump (equipment 25 IDs 六段状态 + confidence、技能 CD、
     专家/蓝图/装置/塔技能支持度) -> local_data/battlefield_registry.json
  2. 确定性哨兵: 固定场景同 seed 连跑两次, 断言 BattleInput digest 与
     BattleOutcomeV2 digest 一致 (determinism_failures = 0)
  3. 八库 legacy gate (--bench): 每库 agree count 对比冻结值
     data/calib/battlefield/baseline_freeze.json; 装备 PR 必须完全一致

用法 (仓库根):
  python tools/battlefield_report.py                # registry + 确定性哨兵
  python tools/battlefield_report.py --bench        # 追加八库对拍 (约 1 小时)
  python tools/battlefield_report.py --freeze-bench # 写入新的冻结基线 (定版时用)
"""
import argparse
import io
import json
import os
import sys

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                     # noqa: E402
from pysim.transition.battle_adapter import run_battle  # noqa: E402
from pysim.transition.model import (UnitCard, PlayerState, EnvironmentState,  # noqa: E402
                                    Phase)
from pysim.battlefield import registry                  # noqa: E402

FREEZE_PATH = os.path.join(ROOT, "data", "calib", "battlefield",
                           "baseline_freeze.json")
REPORT_PATH = os.path.join(ROOT, "local_data", "battlefield_registry.json")

GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))


def _sentinel_state(seed_pair):
    """Two fixed characterization scenarios (B0): digest must reproduce."""
    def mk(mech, x, side, k, lv=1):
        return UnitCard(entity_id=side * 100 + k, mech_id=mech, level=lv,
                        exp=0, x=x, y=-150.0 if side == 0 else 150.0,
                        replay_index=side * 100 + k)
    p0 = PlayerState(hp=4500, max_hp=4500, supply=500,
                     pre_round_fight_result=None,
                     units=(mk(10, -60.0, 0, 0), mk(21, -30.0, 0, 1, 2),
                            mk(6, 0.0, 0, 2)),
                     unlocked_mechs=frozenset({10, 21, 6}), tech_map=())
    p1 = PlayerState(hp=4500, max_hp=4500, supply=500,
                     pre_round_fight_result=None,
                     units=(mk(10, 60.0, 1, 0), mk(10, 90.0, 1, 1),
                            mk(27, 30.0, 1, 2)),
                     unlocked_mechs=frozenset({10, 27}), tech_map=())
    return EnvironmentState(schema_version="sentinel",
                            ruleset_version="normal_1v1_replay_v0",
                            engine_version="pysim-step29", round=2,
                            phase=Phase.PRE_BATTLE, players=(p0, p1))


def determinism_sentinel():
    """同 seed 连跑两次 -> BattleInput/OutcomeV2 digest 一致 (B0 gate)."""
    failures = []
    digests = []
    for seed in (7, 20220822, 99):
        st = _sentinel_state(seed)
        r1 = run_battle(st, GD, battle_seed=seed, with_trace=True)
        r2 = run_battle(st, GD, battle_seed=seed, with_trace=True)
        _, e1 = r1
        _, e2 = r2
        d = {"seed": seed, "battle_input_digest": e1["battle_input_digest"],
             "outcome_v2_digest": e1["outcome_v2_digest"],
             "winner": r1[0].winner,
             "score_by_team": list(r1[0].score_by_team)}
        digests.append(d)
        if e1["battle_input_digest"] != e2["battle_input_digest"] or \
                e1["outcome_v2_digest"] != e2["outcome_v2_digest"] or \
                r1[0].winner != r2[0].winner:
            failures.append(seed)
    return {"determinism_failures": len(failures), "failed_seeds": failures,
            "characterization": digests}


def run_bench(write_freeze=False):
    """八库 legacy gate: 每库 agree count vs 冻结值 (纯重构/装备 PR 必须零变化)."""
    sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
    from run import LIB_ORDER, run_side, load_scen_by_name
    from pysim.gamedata import GameData as GD2
    gd = GD2(os.path.join(ROOT, "data", "gamedata.json"))
    frozen = {}
    if os.path.exists(FREEZE_PATH):
        frozen = json.load(open(FREEZE_PATH, encoding="utf8")).get("libs", {})
    results = {}
    ok = True
    for lib in LIB_ORDER:
        exp_dir = os.path.join(ROOT, "data", "exp", lib)
        if not os.path.isdir(exp_dir):
            continue
        by = load_scen_by_name(lib)
        files = sorted(f for f in os.listdir(exp_dir) if f.endswith(".json"))
        n = agree = 0
        for f in files:
            rec = json.load(open(os.path.join(exp_dir, f), encoding="utf8"))
            s = by.get(rec["name"]) or rec
            res = run_side(gd, s, {})
            wo = rec.get("winner_oracle")
            if wo is not None:
                n += 1
                agree += (wo == res["winner"])
        results[lib] = "%d/%d" % (agree, n)
        if lib in frozen:
            if results[lib] != frozen[lib]:
                ok = False
                print("  [DIFF] %s: %s vs frozen %s" % (lib, results[lib],
                                                        frozen[lib]))
            else:
                print("  [ok]   %s: %s" % (lib, results[lib]))
    total_a = sum(int(v.split("/")[0]) for v in results.values())
    total_n = sum(int(v.split("/")[1]) for v in results.values())
    if write_freeze:
        os.makedirs(os.path.dirname(FREEZE_PATH), exist_ok=True)
        from pysim.transition.model import (SCHEMA_VERSION, RULESET_VERSION,
                                            ENGINE_VERSION)
        payload = {
            "_note": "battlefield B0 冻结基线: 八库每库 agree count + "
                     "characterization digests; 装备/纯重构 PR 必须零变化",
            "schema_version": SCHEMA_VERSION, "ruleset_version": RULESET_VERSION,
            "engine_version": ENGINE_VERSION, "libs": results,
            "total": "%d/%d" % (total_a, total_n)}
        json.dump(payload, open(FREEZE_PATH, "w", encoding="utf8"),
                  ensure_ascii=False, indent=1)
        print("frozen -> %s" % FREEZE_PATH)
    return {"libs": results, "total": "%d/%d" % (total_a, total_n),
            "matches_frozen": ok,
            "frozen_total": (frozen.get("__total__")
                             if isinstance(frozen, dict) else None)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench", action="store_true",
                    help="运行八库 legacy gate 对拍 (约 1 小时)")
    ap.add_argument("--freeze-bench", action="store_true",
                    help="把八库结果写入新的冻结基线 (仅定版时使用)")
    a = ap.parse_args()

    report = {"registry": registry.registry_dump(),
              "determinism": determinism_sentinel()}
    print("== mechanism registry ==")
    s = report["registry"]["summary"]
    print("  equipment: %d ids, %d with battle specs (top-4 coverage %s)"
          % (s["equipment_total"], s["equipment_battle_implemented"],
             s["equipment_selection_coverage_top4"]))
    print("  verified mechanisms: %d / provisional: %d"
          % (s["verified_mechanisms"], s["provisional_mechanisms"]))
    print("== determinism sentinel ==")
    d = report["determinism"]
    print("  determinism_failures = %d (seeds %s)"
          % (d["determinism_failures"], d["failed_seeds"]))
    if a.bench or a.freeze_bench:
        print("== eight-library legacy gate ==")
        report["bench"] = run_bench(write_freeze=a.freeze_bench)
        print("  total %s (matches frozen: %s)"
              % (report["bench"]["total"], report["bench"]["matches_frozen"]))
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    json.dump(report, open(REPORT_PATH, "w", encoding="utf8"),
              ensure_ascii=False, indent=1)
    print("report -> %s" % REPORT_PATH)


if __name__ == "__main__":
    main()
