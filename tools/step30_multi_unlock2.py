# -*- coding: utf-8 -*-
"""step30 U1b: 统计 >=2 个不同 UID 同回合解锁成功的回合 (配额违规候选)。"""
import io, sys, os, json
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(GITHUB, "local_data", "rounds_norm.json"), encoding="utf8"))

n_rounds_multi = 0
violations = []
distinct_ok_dist = Counter()
dup_logged = 0
for g in data:
    for sk in ("p0", "p1"):
        pairs = g.get("pairs", [])
        for i, pair in enumerate(pairs):
            cur = pair.get(sk) or {}
            acts = [int(a["UID"]) for a in (cur.get("actions") or [])
                    if a.get("type") == "UnlockUnit" and a.get("UID") is not None]
            if len(acts) < 2:
                continue
            n_rounds_multi += 1
            nxt = None
            for j in range(i + 1, len(pairs)):
                nxt = pairs[j].get(sk) or {}
                break
            unlocked_next = set(int(x) for x in ((nxt or {}).get("unlocked_units") or [])) if nxt else None
            unlocked_cur = set(int(x) for x in (cur.get("unlocked_units") or []))
            ok_uids = set()
            for uid in acts:
                if uid in unlocked_cur or (unlocked_next is not None and uid in unlocked_next):
                    ok_uids.add(uid)
            distinct_ok_dist[len(ok_uids)] += 1
            if len(ok_uids) >= 2:
                violations.append({"file": g.get("file"), "round": cur.get("round"),
                                   "side": sk, "acts": acts, "ok_uids": sorted(ok_uids)})
            # 重复记录统计: 去重后动作数
            if len(set(acts)) < len(acts):
                dup_logged += 1
print("multi-action rounds:", n_rounds_multi, "| with duplicate-uid logging:", dup_logged)
print("distinct-ok-uid dist:", dict(distinct_ok_dist))
print(">=2 distinct ok rounds:", len(violations))
for v in violations[:40]:
    print(" ", json.dumps(v, ensure_ascii=False))
