# -*- coding: utf-8 -*-
"""step30 O1 前置2: 专家/officers 相关代码与数据定位。"""
import io, sys, os, json, glob, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator"

hits = []
for p in glob.glob(os.path.join(GITHUB, "pysim", "**", "*.py"), recursive=True):
    try:
        txt = open(p, encoding="utf8").read()
    except Exception:
        continue
    if "20036" in txt or "EXPERT" in txt.upper() or "expert" in txt:
        for i, ln in enumerate(txt.splitlines(), 1):
            if "20036" in ln or "EXPERT" in ln or "expert_" in ln.lower():
                hits.append("%s:%d: %s" % (os.path.relpath(p, GITHUB), i, ln.rstrip()[:130]))
print("\n".join(hits[:40]))

# gamedata officers 表: 找兵种专家
gd = json.load(open(os.path.join(GITHUB, "data", "gamedata.json"), encoding="utf8"))
offs = gd.get("officers") or {}
print("officers n=", len(offs))
EXPERT_IDS = [20029, 20033, 20036, 20037, 20038, 20039]
for oid in EXPERT_IDS:
    o = offs.get(str(oid)) or {}
    print(oid, json.dumps(o, ensure_ascii=False)[:260])
# 找 unitIds/expert 字段样例
cnt = 0
for k, o in offs.items():
    s = json.dumps(o, ensure_ascii=False)
    if "unitId" in s or "专家" in s:
        print("sample", k, s[:200])
        cnt += 1
        if cnt >= 8:
            break
