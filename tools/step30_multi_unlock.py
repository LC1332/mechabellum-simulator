# -*- coding: utf-8 -*-
"""step30 U1: 多动作回合里第 2+ 次 UnlockUnit 是否真的成功 (下一 pair 快照确认)。"""
import io, sys, os, json
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(GITHUB, "local_data", "rounds_norm.json"), encoding="utf8"))

dist = Counter()
samples = []
for g in data:
    for sk in ("p0", "p1"):
        pairs = g.get("pairs", [])
        for i, pair in enumerate(pairs):
            cur = pair.get(sk) or {}
            acts = [int(a["UID"]) for a in (cur.get("actions") or [])
                    if a.get("type") == "UnlockUnit" and a.get("UID") is not None]
            if len(acts) < 2:
                continue
            # 下一 pair 快照
            nxt = None
            for j in range(i + 1, len(pairs)):
                nxt = pairs[j].get(sk) or {}
                break
            unlocked_next = set(int(x) for x in ((nxt or {}).get("unlocked_units") or [])) if nxt else None
            unlocked_cur = set(int(x) for x in (cur.get("unlocked_units") or []))
            per = []
            for uid in acts:
                if uid in unlocked_cur:
                    per.append("same")
                elif unlocked_next is None:
                    per.append("unconfirmed")
                elif uid in unlocked_next:
                    per.append("ok")
                else:
                    per.append("fail")
            n_ok = per.count("ok") + per.count("same")
            dist[(len(acts), tuple(sorted(per)))] += 1
            if n_ok >= 2 and len(samples) < 30:
                samples.append({"file": g.get("file"), "round": cur.get("round"),
                                "side": sk, "uids": acts, "verdict": per})
print("multi-action rounds verdict dist:")
for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
    print("  acts=%d %s : %d" % (k[0], k[1], v))
print("samples with >=2 success:")
for s in samples:
    print(" ", json.dumps(s, ensure_ascii=False))
