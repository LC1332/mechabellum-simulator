# -*- coding: utf-8 -*-
"""step30: buildings 表 + pysim 等级语义核查。"""
import io, sys, os, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator"
gd = json.load(open(os.path.join(GITHUB, "data", "gamedata.json"), encoding="utf8"))
print("== buildings ==")
for k, b in (gd.get("buildings") or {}).items():
    print(" cid", k, json.dumps(b, ensure_ascii=False)[:220])
print("== cards mechCount ==")
for mid in (12, 19, 21, 22, 28, 1, 25, 10):
    c = (gd.get("cards") or {}).get(str(mid)) or {}
    print(" card", mid, c.get("name"), "mechCount=", c.get("mechCount"),
          "baseMoney=", c.get("baseMoney"))
# pysim 等级语义
eng = open(os.path.join(GITHUB, "pysim", "engine.py"), encoding="utf8").read()
for m in re.finditer(r".*levelScale.*", eng):
    print("engine:", m.group(0).strip()[:140])
gdpy = open(os.path.join(GITHUB, "pysim", "gamedata.py"), encoding="utf8").read()
for m in re.finditer(r".*level.*scale.*|.*levelScale.*", gdpy, re.I):
    print("gamedata.py:", m.group(0).strip()[:140])
# level 使用处
for i, ln in enumerate(eng.splitlines(), 1):
    if "level" in ln.lower() and ("life" in ln.lower() or "hp" in ln.lower()
                                  or "damage" in ln.lower() or "mul" in ln.lower()):
        print("engine %d: %s" % (i, ln.strip()[:140]))
