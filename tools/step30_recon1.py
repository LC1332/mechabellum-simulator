# -*- coding: utf-8 -*-
"""step30 O1 前置: 专家表 + 增援卡信息结构侦察。"""
import io, sys, os, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator"

rules = open(os.path.join(GITHUB, "pysim", "transition", "rules.py"),
             encoding="utf8").read()
m = re.search(r"UNIT_EXPERT_OFFICERS\s*=\s*\{.*?\n\}", rules, re.S)
print("== UNIT_EXPERT_OFFICERS ==")
print(m.group(0) if m else "NOT FOUND")

p = os.path.join(GITHUB, "information", "增援卡牌-回放全量信息.json")
if os.path.exists(p):
    d = json.load(open(p, encoding="utf8"))
    print("== 增援卡牌信息 ==", type(d), len(d))
    sample = d[:3] if isinstance(d, list) else dict(list(d.items())[:3])
    print(json.dumps(sample, ensure_ascii=False, indent=1)[:2500])
