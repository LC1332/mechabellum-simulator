#!/usr/bin/env python
"""Render information/pysim未实现战斗机制.md from the battlefield mechanic
registry + the replay corpus mechanism census (committed document the user
requested: which combat mechanics pysim does NOT implement)."""
import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

REGISTRY = os.path.join(ROOT, "local_data", "battlefield_registry.json")
OUT = os.path.join(ROOT, "information", "pysim未实现战斗机制.md")

# canonical Chinese names for the ids the registry knows about (from the
# step3/step4 任务书 tables); unknown ids render as-is
NAMES = {
    200001: "电磁冲击 EMP", 200002: "巨型电磁冲击", 200003: "光子投射",
    300001: "导弹打击", 300002: "导弹风暴", 300003: "轨道轰炸",
    300004: "核弹", 300005: "闪电风暴", 300006: "离子轰炸",
    300007: "轨道标枪", 100002: "燃烧弹", 500002: "酸液弹",
    600002: "烟雾弹", 1200001: "空降兵召唤", 1200002: "犀牛召唤",
    1200003: "爬虫召唤", 1200004: "战舰召唤", 1200005: "火神召唤",
    1200006: "移动信标(召唤变体)", 1500001: "移动信标",
    1500002: "移动信标II", 1100001: "强化训练", 1000001: "再部署",
    900001: "战地回收", 400002: "黏油弹",
    10001: "飞弹炮塔", 20001: "护盾装置", 30001: "未知装置",
    1: "快速补给", 2: "(未观测)", 3: "批量征召", 4: "精英征召",
    5: "强化瞄准", 6: "高速移动",
}


def census_mechanisms(chunks):
    """Count raw mechanism usage across the corpus chunks (best effort)."""
    skills = Counter()
    equips = Counter()
    contrs = Counter()
    bps = Counter()
    towers = Counter()
    skill_names = {}
    for path in chunks:
        try:
            games = json.load(open(path))
        except OSError:
            continue
        for g in games:
            if g.get("info", {}).get("matchMode") != "VS_1_1":
                continue
            for p in g["players"]:
                for r in p.get("rounds", []):
                    for e in r.get("commanderSkills_raw") or []:
                        try:
                            if isinstance(e, dict):
                                slot = int(e.get("index"))
                                sid = int(e.get("id"))
                            else:
                                slot, sid = int(e[0]), int(e[1])
                            skills[sid] += 1
                            skill_names.setdefault(sid, slot)
                        except (TypeError, ValueError, IndexError, KeyError):
                            pass
                    for u in r.get("units") or []:
                        eq = int(u.get("equipment", 0) or 0)
                        if eq:
                            equips[eq] += 1
                    for e in r.get("energyTowerSkills_raw") or []:
                        try:
                            towers[int(e.get("id") or e.get("skill") or 0)] += 1
                        except (AttributeError, TypeError, ValueError):
                            pass
                    for a in r.get("actions") or []:
                        t = a.get("type")
                        if t == "ReleaseContraption":
                            cid = str(a.get("ContraptionID"))
                            contrs[cid] += 1
                        elif t == "ActiveBlueprint":
                            bps[int(a.get("ID", 0) or 0)] += 1
    return skills, equips, contrs, bps, towers, skill_names


def section(rows, headers, align=None):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=REGISTRY)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--chunks-glob",
                    default="local_data/rl_phase1/v1_full/corpus_chunks/"
                            "new_*.json")
    ap.add_argument("--with-census", action="store_true")
    args = ap.parse_args()

    BP_NAMES = {1: "黏油弹研究", 2: "战地回收研究", 3: "移动信标研究",
                4: "攻击专家I", 5: "防御专家I", 401: "攻击专家II",
                501: "防御专家II"}
    OFFICER_NAMES = {10004: "额外部署位", 10007: "军医", 10008: "教练",
                     10009: "军械官", 20003: "润滑专家"}
    reg = json.load(open(args.registry))["registry"]
    lines = [
        "# pysim 尚未实现的战斗机制清单",
        "",
        "> 生成方式:`tools/build_fidelity_report.py` 读取",
        "> `local_data/battlefield_registry.json`(mechanism_registry_v1)",
        "与回放语料的机制出现频次自动渲染。任何机制实现后重跑该工具更新。",
        "支持度四轴:`transition_complete`(转移层)/ `battle_fidelity`",
        "(exact|approximate|unsupported)/ `confidence` / `effect_complete`。",
        "",
    ]
    for mech, mech_name in (("commander_skill", "指挥官技能"),
                            ("equipment", "装备"),
                            ("contraption", "装置"),
                            ("tower_skill", "能量塔技能"),
                            ("blueprint", "蓝图"),
                            ("officer", "专家")):
        entries = reg.get(mech) or []
        unimpl, approx, exact = [], [], []
        for info in entries:
            ident = info.get("ident")
            fid = info.get("battle")
            conf = info.get("confidence")
            transition_ok = all(info.get(k) == "complete"
                                for k in ("decode", "legality", "economy",
                                          "persistent_state"))
            name = NAMES.get(int(ident) if str(ident).isdigit() else ident,
                             ident)
            if mech == "blueprint":
                name = BP_NAMES.get(int(ident), ident)
            if mech == "officer":
                name = OFFICER_NAMES.get(int(ident), ident)
            evidence = "; ".join((info.get("evidence") or ())[:2])
            row = (ident, name, "是" if transition_ok else "否",
                   {"missing": "缺失", "approximate": "近似",
                    "complete": "完整"}.get(fid, fid), conf, evidence)
            if fid == "missing":
                unimpl.append(row)
            elif fid == "approximate":
                approx.append(row)
            else:
                exact.append(row)
        lines.append("## %s(%d 项)" % (mech_name, len(entries)))
        if unimpl:
            lines.append("")
            lines.append("**未实现战斗效果(battle_fidelity=unsupported)**:")
            lines.append("")
            lines.append(section(unimpl,
                                 ["id", "名称", "转移层", "战斗保真",
                                  "置信度", "备注"]))
        if approx:
            lines.append("")
            lines.append("**近似实现(approximate)**:")
            lines.append("")
            lines.append(section(approx,
                                 ["id", "名称", "转移层", "战斗保真",
                                  "置信度", "备注"]))
        if exact:
            lines.append("")
            lines.append("已 exact 实现:%s" % ", ".join(
                "`%s %s`" % (r[0], r[1]) for r in exact))
        lines.append("")

    lines += [
        "## RL Phase 1 的处理口径(2026-08-28 用户裁决)",
        "",
        "- 未实现的指挥官技能/装备按 **执行了但没有效果** 处理:",
        "  teacher forcing 与 arena 中 receipt 记 accepted + fidelity flag",
        "  (NOOP_REASON_CODES),回放不中断,数据覆盖最大化;",
        "- approximate 机制照常执行,样本打 fidelity 标记,Silver 分层单独报表;",
        "- gold 主指标排除 unsupported 机制为主的样本(fidelity 分桶可见)。",
        "",
        "## 已知的行为级残差(语料审计)",
        "",
        "- 建筑回收(ReleaseCommanderSkill + ConstructionIndex):真实游戏有",
        "  退款,pysim 无效果。快照锚定的推导收入会吸收该退款,计划继续;",
        "- techMap 字段为回合后语义:回合内科技购买被跳过(已由快照包含),",
        "  费用由推导收入吸收(896/896 次验证);",
        "- 约 2% 的玩家-回合存在未解释移动(step4 审计),对应 walk 在",
        "  first-failure 截断,进入 Silver/诊断。",
    ]

    if args.with_census:
        import glob as _glob
        chunks = sorted(_glob.glob(os.path.join(ROOT, args.chunks_glob)))
        skills, equips, contrs, bps, towers, skill_names = \
            census_mechanisms(chunks)
        lines.append("")
        lines.append("## 语料出现频次(new corpus 1106 局)")
        lines.append("")
        lines.append("**指挥官技能**(槽出现次数, id→槽): " + ", ".join(
            "`%s`×%d(slot%s)" % (NAMES.get(k, k), v,
                                 skill_names.get(k, "?"))
            for k, v in skills.most_common(12)))
        lines.append("")
        lines.append("**装备(场上绑定)**: " + ", ".join(
            "`%s`×%d" % (NAMES.get(k, k), v)
            for k, v in equips.most_common(15)))
        lines.append("")
        lines.append("**装置**: " + ", ".join(
            "`%s`×%d" % (NAMES.get(int(k), k), v)
            for k, v in contrs.most_common()))
        lines.append("")
        lines.append("**蓝图研究**: " + ", ".join(
            "`%s`×%d" % (NAMES.get(k, k), v)
            for k, v in bps.most_common()))

    with open(args.out, "w", encoding="utf8") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote %s (%d lines)" % (args.out, len(lines)))


if __name__ == "__main__":
    main()
