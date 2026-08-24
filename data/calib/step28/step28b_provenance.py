# -*- coding: utf-8 -*-
"""step28b 溯源落盘: 本轮 (2026-08-24 续接会话) A/B 数字 → JSON。

臂命名:
  s28e_*  = tools/step28_ab.py (s28 1004 场并行评估)
  s28b_*  = tools/step27_ab.py (四库 s24/s25/s26/s27 门禁)
基线: s28e_base 792/1004 = 78.9% (新库 ratchet 起点, = step28_run refresh)
"""
import json, os, sys, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
AB = os.path.join(R, "data", "step27_ab")


def fourlib(tag):
    p = os.path.join(AB, "s28b_%s.json" % tag)
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf8"))
    return {lib: "%d/%d" % (r["agree"], r["n"])
            for lib, r in d.get("libs", {}).items()}


def s28arm(tag):
    p = os.path.join(AB, "s28e_%s.json" % tag)
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf8"))
    return "%d/%d = %.1f%%" % (d["agree"], d["n"], d["acc"])


out = {
    "_note": "step28b (2026-08-24 下午续接): swing_fix 齐射守恒 + 野马buff + "
             "沙虫复制 + 召唤上限; 详见 对路线C的探索-step28-开发任务.txt 续接段",
    "baseline_s28": s28arm("base"),
    "arms_s28": {t: s28arm(t) for t in
                 ("mus055", "mus06_unused", "sws1", "cap48", "swfix", "combo_a")
                 if s28arm(t)},
    "gates_fourlib": {t: fourlib(t) for t in
                      ("mus055", "mus06", "sws1", "cap48", "swfix", "final")
                      if fourlib(t)},
    "discoveries": {
        "swing_branch": "swing_pin 分支 (atk_dur>=2 慢重挥击: 长弓/霸主/暴雨/"
                        "狂蝎/沙虫/深渊/魔眼/鬼鳐/先知/猎犬/铁锤/凤凰/剑齿虎)"
                        "不应用 pc_map (霸主 pc_set=11:2 失效, st176 实测 4 发"
                        "散射 x1185) 且不乘 w_count (暴雨 2 武器齐射只打单武器"
                        "量, st056 实测 96.5/发 = 386/4, 表值应 772/轮)。"
                        "barrage_same=12,26 对暴雨/先知也因此失效 (两者皆 swing)。",
        "storm_hp_match": "st706/st056: 暴雨/狂蝎 双向 dmg 与击杀数两边几乎"
                          "一致, 战斗时长差异 (pysim 16s 团灭 vs oracle 100+s) "
                          "主因是对手清杂速度, 暴雨本体 volley 减半是 swing 分支 bug。",
        "thunder_proxy": "雷霆 r=2.94 翻错多为对手 (霸主/野马/兵蜂) 弱化的下游"
                         "效应; 对单体局 r=1.00 kills 精确。",
        "fortress_tank": "堡垒 vs 泰山·巨山装甲 r 高达 19-29x (pysim 堡垒炮击"
                         "打坦太快), 需 oracle HP 时间线仪表 (step29)。",
        "spider_mine": "蜘蛛雷 (tech 11024, skill 24002: 2发/15s max99 召唤"
                       "自爆雷) 完全未建模; s28 未覆盖该科技, 语料可能出现。",
        "oracle_summon_cap": "召唤行 24 上限截断表值满额 (尖牙/爬虫制造 32/"
                             "卡); oracle st764 94 存活 ≈ 3 堡垒 x 32 满额。",
    },
}
p = os.path.join(R, "data", "calib", "step28", "step28b_provenance.json")
json.dump(out, open(p, "w", encoding="utf8"), ensure_ascii=False, indent=1)
print("->", p)
print(json.dumps(out, ensure_ascii=False, indent=1)[:1200])
