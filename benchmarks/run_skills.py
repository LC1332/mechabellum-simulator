# -*- coding: utf-8 -*-
"""run_skills.py — step5 任务书 §4: pysim recomputes the battlefield-skill
scenario package and (when present) diffs it against the Windows oracle
telemetry.

用法 (仓库根):
  python benchmarks/run_skills.py                     # 重算 + 摘要
  python benchmarks/run_skills.py --oracle <build>    # 对拍 data/battlefield_skill_oracle/<build>/
  python benchmarks/run_skills.py --case c400002_axial

产出 local_data/skill_bench/<...>/summary.json — 每场 case 的
winner / end_time / 存活 / 事件计数 + BattleInput digest (同 seed 稳定).
未提供 oracle 时报告 pysim 侧摘要并明确标注 no_oracle (不得冒充对拍).
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pysim.gamedata import GameData
from pysim.engine import Battle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))
SCEN = os.path.join(ROOT, "data", "battlefield_skill_scenarios",
                    "battlefield-skill-oracle-v1.json")


def _ev_from_case(c):
    from pysim.skills import (_area_effect_params, RELEASE_POINT_COUNTS,
                              COMMANDER_SKILLS)
    sid = int(c["skill_id"])
    d = COMMANDER_SKILLS[sid]
    pts = [(float(p[0]), float(p[1])) for p in c["positions"]]
    ev = {"kind": d["kind"], "x": pts[0][0], "y": pts[0][1],
          "name": d["name"], "id": sid}
    if d["kind"] == "strike":
        ev.update({"damage": float(d["damage"]),
                   "splash": float(d["splash"]),
                   "t": float(d.get("t", 0.0) or 0.0)})
        if d.get("ff"):
            ev["ff"] = True
        if d.get("bypass"):
            ev["bypass"] = True
        return ev
    if sid in RELEASE_POINT_COUNTS:
        ev["points"] = [list(p) for p in pts]
    ev.update(_area_effect_params(sid, d))
    return ev


def run_case(c, seed):
    b = Battle(GD)
    b._battle_seed = seed
    side = int(c.get("release_side", 0))
    for u in c["p0"]["units"]:
        b.add_card(0, int(u["mech"]), int(u.get("level", 1)),
                   float(u["x"]), float(u["y"]))
    for u in c["p1"]["units"]:
        b.add_card(1, int(u["mech"]), int(u.get("level", 1)),
                   float(u["x"]), float(u["y"]))
    for dev in c["p0"].get("devices") or []:
        b.add_skill_event(0, {"kind": "barrier", "x": float(dev["x"]),
                              "y": float(dev["y"]), "hp": 60000.0,
                              "radius": 30.0, "name": "护盾装置"})
    for dev in c["p1"].get("devices") or []:
        b.add_skill_event(1, {"kind": "barrier", "x": float(dev["x"]),
                              "y": float(dev["y"]), "hp": 60000.0,
                              "radius": 30.0, "name": "护盾装置"})
    for pre in c["p0"].get("skill_pre") or []:
        b.add_skill_event(0, _ev_from_case({**pre, "positions":
                                            pre["positions"]}))
    b.add_skill_event(side, _ev_from_case(c))
    b.finalize()
    winner = b.simulate()
    return {"case_id": c["case_id"], "winner": int(winner),
            "end_time": round(float(b.end_tick) * 0.01, 2),
            "alive0": int(b.alive_count(0)), "alive1": int(b.alive_count(1)),
            "areas": len(getattr(b, "_areas", [])),
            "area_results": [list(r) for r in b.area_results()]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--oracle", default=None,
                    help="oracle build dir under data/battlefield_skill_oracle/")
    ap.add_argument("--case", default=None, help="substring filter")
    ap.add_argument("--seed", type=int, default=20220822)
    args = ap.parse_args()
    doc = json.load(open(SCEN, encoding="utf-8"))
    cases = [c for c in doc["cases"]
             if not args.case or args.case in c["case_id"]]
    out = []
    for c in cases:
        r = run_case(c, args.seed)
        out.append(r)
        print("%-22s winner=%d end=%6.2fs alive=%d/%d areas=%d %s"
              % (r["case_id"], r["winner"], r["end_time"], r["alive0"],
                 r["alive1"], r["areas"], r["area_results"]))
    odir = os.path.join(ROOT, "local_data", "skill_bench")
    os.makedirs(odir, exist_ok=True)
    summary = {"schema": doc["schema"], "seed": args.seed,
               "pysim_version": "pysim-step31", "results": out}
    if args.oracle:
        opath = os.path.join(ROOT, "data", "battlefield_skill_oracle",
                             args.oracle, "summary.json")
        if not os.path.exists(opath):
            print("NO ORACLE at %s — refusing to fabricate a diff" % opath)
            sys.exit(2)
        oracle = json.load(open(opath, encoding="utf-8"))
        by_id = {o["case_id"]: o for o in oracle.get("cases", [])}
        agree = cnt = 0
        for r in out:
            o = by_id.get(r["case_id"])
            if not o:
                continue
            cnt += 1
            agree += int(r["winner"] == o.get("winner"))
        summary["oracle_build"] = args.oracle
        summary["winner_agree"] = "%d/%d" % (agree, cnt)
        print("oracle winner agreement: %d/%d" % (agree, cnt))
    else:
        summary["oracle_build"] = None
    sp = os.path.join(odir, "summary.json")
    json.dump(summary, open(sp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("-> %s (%d cases, oracle=%s)" % (sp, len(out), args.oracle))


if __name__ == "__main__":
    main()
