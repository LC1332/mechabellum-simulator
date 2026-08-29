# -*- coding: utf-8 -*-
"""step30: 查看 s30 oracle 记录摘要。用法: python tools/step30_view.py [name过滤]"""
import io, sys, os, json, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator"
EXP = os.path.join(GITHUB, "data", "exp", "s30")
gd = json.load(open(os.path.join(GITHUB, "data", "gamedata.json"), encoding="utf8"))
NAME = {int(k): (v.get("name") or "?") for k, v in gd["mechs"].items()}
pat = sys.argv[1] if len(sys.argv) > 1 else ""
for f in sorted(glob.glob(os.path.join(EXP, "*.json"))):
    base = os.path.basename(f)
    if pat and pat not in base:
        continue
    rec = json.load(open(f, encoding="utf8"))
    o = rec.get("oracle") or {}
    if not o.get("ok"):
        print("%-14s FAIL %s" % (base, o.get("err")))
        continue
    print("%-14s sround=%-3s winner=%s alive=%s score=%s cost=%ss" % (
        base, rec.get("sround"), o.get("winner"), o.get("alive"),
        o.get("score"), rec.get("cost_s")))
    for un in o.get("units") or []:
        print("    team=%d uid=%-5s(%s) rectype=%s mechid=%-4s dmgMax=%-9s dmgReal=%-9s kills=%s" % (
            un["team"], un["uid"], NAME.get(int(un["uid"]), "?"),
            un["rectype"], un.get("mechid"), un.get("dmgMax"),
            un.get("dmgReal"), un.get("kills")))
