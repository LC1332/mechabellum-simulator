# -*- coding: utf-8 -*-
"""step30: dll.log 失败段 + 真实回放 researchQueue/equipmentDatas 结构。"""
import io, sys, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
import xml.etree.ElementTree as ET

log = open(r"C:\Users\chengli\Documents\mech\RouteC\data\oracle_work\out\dll.log",
           encoding="utf8", errors="replace").read()
lines = log.splitlines()
for tag in ("s30_o0c", "s30_o2r0", "s30_o2r1"):
    hits = [ln for ln in lines if tag in ln]
    for h in hits:
        i = lines.index(h)
        print("== %s (%d hits) context ==" % (tag, len(hits)))
        for j in range(max(0, i - 6), min(len(lines), i + 10)):
            print("  ", lines[j])
        print()

import craft_replay as cr
sys.path.insert(0, r"C:\Users\chengli\Documents\mech\RouteC\tools")
g = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator\data\伤害标定.grbr"
prefix, xml, suffix, vlen = cr.split_grbr(g)
root = cr.parse_xml(xml)
shown = 0
for pi, pr in enumerate(root.find("playerRecords").findall("PlayerRecord")):
    rrs = pr.find("playerRoundRecords").findall("PlayerRoundRecord")
    for ri, rr in enumerate(rrs):
        pd = rr.find("playerData")
        if pd is None:
            continue
        rq = pd.find("researchQueue")
        eq = pd.find("equipmentDatas")
        rq_s = ET.tostring(rq, encoding="unicode").replace("\r", "").replace("\n", "") if rq is not None else "None"
        eq_s = ET.tostring(eq, encoding="unicode").replace("\r", "").replace("\n", "")[:300] if eq is not None else "None"
        if (rq is not None and len(rq)) or (eq is not None and len(eq)):
            print("p%d round-idx%d researchQueue=%s equipmentDatas=%s" % (
                pi, ri, rq_s[:200], eq_s))
            shown += 1
        if shown >= 8:
            break
    if shown >= 8:
        break
