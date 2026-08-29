# -*- coding: utf-8 -*-
"""step30: 卡表科技 id 形态核对。"""
import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
gd = json.load(open(r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator\data\gamedata.json",
                    encoding="utf8"))
for mid in (19, 25, 28, 22, 21, 12):
    c = (gd.get("cards") or {}).get(str(mid)) or {}
    print("card", mid, c.get("name"), "techs:", c.get("technologies"))
techs = gd["techs"]
for tid in (719, 725, 425, 5322, 11028, 10321, 720, 726, 426, 5323):
    t = techs.get(str(tid))
    print("tech", tid, t and t.get("name"), t and t.get("family"))
