# -*- coding: utf-8 -*-
"""step30: 唯一配额违规候选局的动作上下文。"""
import io, sys, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(GITHUB, "local_data", "rounds_norm.json"), encoding="utf8"))
TARGET = "2207_20260722--67191813"
for g in data:
    if TARGET not in (g.get("file") or ""):
        continue
    for sk in ("p0", "p1"):
        for pair in g.get("pairs", []):
            cur = pair.get(sk) or {}
            acts = cur.get("actions") or []
            ul = [a for a in acts if a.get("type") == "UnlockUnit"]
            if not ul:
                continue
            print("%s %s r%s supply=%s unlocked_in=%s officers=%s" % (
                sk, cur.get("round"), cur.get("round"), cur.get("supply"),
                cur.get("unlocked_units"), cur.get("officers")))
            for a in acts:
                print("   ", json.dumps(a, ensure_ascii=False)[:220])
        # 找 round 4 的完整动作表
        for pair in g.get("pairs", []):
            cur = pair.get(sk) or {}
            if cur.get("round") == 4 and sk == "p1":
                print("== p1 round4 全动作 ==")
                for a in acts:
                    print("   ", a.get("type"), json.dumps({k: v for k, v in a.items() if k != 'type'}, ensure_ascii=False)[:200])
