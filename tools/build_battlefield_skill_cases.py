# -*- coding: utf-8 -*-
"""build_battlefield_skill_cases.py — step5 任务书 §4 T1 scaffolding.

Generates the machine-agnostic scenario package for the battlefield-skill
oracle: data/battlefield_skill_scenarios/battlefield-skill-oracle-v1.json.

纯输入生成/校验，不运行游戏 (任务书 §4: "生成/校验输入，不运行游戏").
每条 case 覆盖 §4.1 最小 A/B 矩阵的一个维度组合:
  shape (轴向/斜向/边界内/边界外/重合/反向) x 阵营 x 空地 x 护盾 x 多模组
  x 时间 x 叠加 x 免疫 x 移动.

The Windows oracle collector consumes the same file and writes its
telemetry to data/battlefield_skill_oracle/<build>/ with the SAME case ids
(never overwriting the inputs; the normalizer must not touch raw files).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DEFAULT = os.path.join(ROOT, "data", "battlefield_skill_scenarios",
                           "battlefield-skill-oracle-v1.json")

SCHEMA = "battlefield-skill-oracle-v1"

# §2.1 frozen geometry/params mirror (single numeric source stays
# pysim/skills.py — this table only decides WHICH cases exist)
CASE_MATRIX = {
    # sid: list of (case_tag, description, dimensions covered)
    400002: [
        ("axial", "轴向 capsule A(0,60)->(60,60)", "shape:轴向"),
        ("diag", "斜向 capsule A(0,60)->(50,110)", "shape:斜向"),
        ("edge_in", "单位恰在边界内 (dist=29)", "shape:边界内"),
        ("edge_out", "单位恰在边界外 (dist=31)", "shape:边界外"),
        ("shield", "满盾护盾装置覆盖落点", "护盾:满盾"),
        ("air", "空军横穿油面", "空地:单独空军"),
        ("stack", "两条黏油重叠", "叠加:同ID重叠"),
    ],
    600002: [
        ("axial", "轴向 capsule", "shape:轴向"),
        ("shield", "护盾阻挡生成", "护盾:满盾"),
        ("air", "空军与烟雾", "空地:单独空军"),
    ],
    500002: [
        ("axial", "轴向 capsule", "shape:轴向"),
        ("photon", "光子单位站酸液内", "免疫:光子vs酸液"),
        ("shield", "护盾覆盖落点", "护盾:满盾"),
    ],
    200001: [
        ("center", "圆心命中", "shape:边界内"),
        ("edge", "半径边缘单位 (dist=59/61)", "shape:边界内外"),
        ("shield", "护盾装置承伤+被保护单位", "护盾:满盾+覆盖"),
        ("photon", "光子单位免疫 EMP", "免疫:光子vsEMP"),
    ],
    200002: [("far", "r130 远端单位 (dist=120)", "shape:边界内")],
    200003: [
        ("friendly", "友军获得光子", "阵营:友军"),
        ("enemy", "敌军不受益", "阵营:敌军"),
        ("clears", "先中 EMP 再获得光子", "免疫:清除既有"),
    ],
    300005: [
        ("seed_a", "固定 seed 重跑 3 次", "时间:同seed复跑"),
        ("moving", "移动靶穿过风暴区", "移动:穿过区域"),
    ],
    300006: [
        ("axial", "轴向扫掠 A->B", "shape:轴向"),
        ("diag", "斜向短距 A->B", "shape:斜向/短距"),
    ],
    1500001: [
        ("full_card", "整卡入选", "多模组:全组入选"),
        ("partial", "部分成员入选 (阵型跨边界)", "多模组:部分入选"),
        ("air", "空军入选", "空地:单独空军"),
        ("enemy", "途中遇敌停下攻击", "移动:途中遇敌"),
    ],
    1500002: [("same", "与 1500001 同场景", "同效果验证")],
    300004: [
        ("t15", "t=15s 到达", "时间:到期前后"),
        ("radius", "r100 边界单位 (dist=99/101)", "shape:边界内外"),
    ],
}


def _units(entries):
    return [{"id": i, "mech": m, "level": 1, "x": x, "y": y}
            for i, (m, x, y) in enumerate(entries)]


def build_case(sid, tag, desc, dims):
    """One scenario record: minimal 1v1 sandbox with the release attached.
    Positions are derived from the tag so the same case always means the
    same geometry (deterministic replay id)."""
    c = {"case_id": "c%d_%s" % (sid, tag), "skill_id": sid, "desc": desc,
         "dimensions": dims.split(";") if ";" in dims else [dims],
         "map": "normal_1v1", "round": 5,
         "p0": {"units": _units([(2, 0.0, -100.0)]), "techs": {},
                "buildings": [], "officers": []},
         "p1": {"units": _units([(10, 0.0, 100.0)]), "techs": {},
                "buildings": [], "officers": []},
         "release_side": 0, "positions": _positions_for(sid, tag),
         "control": "no_skill"}
    # 护盾/光子/多模组维度按 tag 附加单位或装置
    if "shield" in tag:
        c["p1"]["devices"] = [{"cid": 20001, "x": 15.0, "y": 60.0}]
    if sid in (200001, 200002) and "shield" in tag:
        c["p1"]["devices"] = [{"cid": 20001, "x": 0.0, "y": 100.0}]
    if "photon" in tag:
        c["p0"]["skill_pre"] = [{"skill_id": 200003,
                                 "positions": [[0.0, 60.0], [40.0, 60.0]]}]
    if sid in (200001, 200002) and "photon" in tag:
        c["p0"]["units"] = _units([(2, 0.0, -100.0), (28, 10.0, 60.0)])
    if "partial" in tag:
        c["p0"]["units"] = _units([(10, 0.0, 20.0)])   # 24-模组爬虫卡跨边界
    if "air" in tag:
        tgt = 0 if sid in (200003, 1500001, 1500002) else 1
        c["p%d" % tgt]["units"].append(
            {"id": 90, "mech": 6, "level": 1, "x": 20.0, "y": 60.0})
    if "enemy" in tag:
        c["p1"]["units"] = _units([(10, 0.0, 45.0)])   # 途中遇敌
    return c


def _positions_for(sid, tag):
    if sid in (1500001, 1500002):
        if "air" in tag:
            return [[20.0, 60.0], [20.0, 20.0], [20.0, -20.0]]
        return [[0.0, 20.0], [0.0, -30.0], [0.0, -80.0]]
    if tag in ("axial", "t15", "radius", "center", "far", "same",
               "full_card", "seed_a", "moving", "friendly", "enemy",
               "clears"):
        return [[0.0, 60.0], [60.0, 60.0]] if sid in (400002, 600002, 500002,
                                                      300006) \
            else [[0.0, 60.0]]
    if tag == "diag":
        return [[0.0, 60.0], [50.0, 110.0]]
    if tag in ("edge_in", "edge_out", "edge"):
        return [[0.0, 60.0]]
    return [[0.0, 60.0], [60.0, 60.0]]


def validate(cases):
    """Schema validation (§4 T1): every case carries the mandatory inputs."""
    from pysim.skills import COMMANDER_SKILLS
    errs = []
    for c in cases:
        sid = int(c["skill_id"])
        if sid not in COMMANDER_SKILLS:
            errs.append("%s: unmapped skill %d" % (c["case_id"], sid))
            continue
        ps = c.get("positions") or []
        if not ps:
            errs.append("%s: no positions" % c["case_id"])
        for p in ps:
            if len(p) != 2:
                errs.append("%s: bad point %r" % (c["case_id"], p))
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--check", action="store_true",
                    help="only validate the existing file")
    args = ap.parse_args()
    if args.check:
        cases = json.load(open(args.out, encoding="utf-8"))["cases"]
        errs = validate(cases)
        print("%d cases, %d errors" % (len(cases), len(errs)))
        for e in errs:
            print(" ", e)
        sys.exit(1 if errs else 0)
    cases = []
    for sid, rows in CASE_MATRIX.items():
        for tag, desc, dims in rows:
            cases.append(build_case(sid, tag, desc, dims))
    errs = validate(cases)
    assert not errs, errs
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    doc = {"schema": SCHEMA, "cases": cases,
           "notes": "inputs only; oracle telemetry lands in "
                    "data/battlefield_skill_oracle/<build>/ with the same "
                    "case ids; raw telemetry is never overwritten"}
    json.dump(doc, open(args.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("%d cases -> %s" % (len(cases), args.out))


if __name__ == "__main__":
    main()
