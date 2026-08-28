# -*- coding: utf-8 -*-
"""T0 pair_id 碰撞检查 (pysim动态装备与伤害管线修正任务书-2026-08-28).

对语料全量计算 tools/run_pysim_mechanism_ab.py 的 pair_id 规则, 断言无碰撞。
pair_id = sha1(corpus_version | 完整文件名[+重复名内容hash] | round | pair序号)[:16]

用法: python tools/check_pair_id_collision.py [--rounds local_data/humen_rounds.json]
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=os.path.join(
        ROOT, "local_data", "humen_rounds.json"))
    a = ap.parse_args()
    rounds = json.load(open(a.rounds, encoding="utf-8"))
    corpus_version = hashlib.sha256(open(a.rounds, "rb").read()).hexdigest()[:16]
    names = [r["file"] for r in rounds]
    dup = {f for f in names if names.count(f) > 1}
    ids = set()
    n = coll = 0
    for r in rounds:
        fname = r["file"]
        ident = fname + ("#" + hashlib.sha256(
            json.dumps(r, sort_keys=True, ensure_ascii=False)
            .encode()).hexdigest()[:12] if fname in dup else "")
        for pidx, pair in enumerate(r["pairs"]):
            pid = hashlib.sha1(("%s|%s|%d|%d" % (corpus_version, ident,
                                                 pair["round"], pidx))
                               .encode()).hexdigest()[:16]
            n += 1
            coll += pid in ids
            ids.add(pid)
    print("corpus_version=%s" % corpus_version)
    print("replays=%d duplicate_names=%d pairs=%d unique_pair_ids=%d "
          "collisions=%d" % (len(rounds), len(dup), n, len(ids), coll))
    if coll:
        sys.exit(1)


if __name__ == "__main__":
    main()
