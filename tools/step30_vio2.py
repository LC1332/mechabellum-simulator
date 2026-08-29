# -*- coding: utf-8 -*-
"""step30: 违规候选局 p1 r4 完整动作序列。"""
import io, sys, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(GITHUB, "local_data", "rounds_norm.json"), encoding="utf8"))
for g in data:
    if "2207_20260722--67191813" not in (g.get("file") or ""):
        continue
    for pair in g.get("pairs", []):
        cur = pair.get("p1") or {}
        if cur.get("round") != 4:
            continue
        print("p1 r4 supply=", cur.get("supply"),
              "unlocked=", cur.get("unlocked_units"),
              "officers=", cur.get("officers"))
        for a in (cur.get("actions") or []):
            t = a.get("type")
            brief = {k: v for k, v in a.items()
                     if k in ("Time", "LocalTime", "UID", "TechID", "UnitIndex",
                              "EquipmentID", "UIDX")}
            pos = ""
            if "position" in a:
                pos = " pos=%s" % (a["position"],)
            print("  ", t, json.dumps(brief, ensure_ascii=False), pos)
        break
    break
