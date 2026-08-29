# -*- coding: utf-8 -*-
"""step30: 失败记录 err 明细 + 构造局科技 XML vs 真实回放 XML 对比。"""
import io, sys, os, json, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator"
EXP = os.path.join(GITHUB, "data", "exp", "s30")
for nm in ("o0c.11", "o2r0.11", "o2r1.11"):
    p = os.path.join(EXP, nm + ".json")
    rec = json.load(open(p, encoding="utf8"))
    o = rec.get("oracle") or {}
    print(nm, "oracle keys:", {k: v for k, v in o.items() if k != "units"})
print("== dll.log 尾部 ==")
log = open(r"C:\Users\chengli\Documents\mech\RouteC\data\oracle_work\out\dll.log",
           encoding="utf8", errors="replace").read().splitlines()
for ln in log[-25:]:
    print(" ", ln)

# 真实回放 techs 编码 (伤害标定.grbr)
sys.path.insert(0, r"C:\Users\chengli\Documents\mech\RouteC\tools")
import craft_replay as cr
g = os.path.join(GITHUB, "data", "伤害标定.grbr")
prefix, xml, suffix, vlen = cr.split_grbr(g)
root = cr.parse_xml(xml)
import xml.etree.ElementTree as ET
n_shown = 0
for pi, pr in enumerate(root.find("playerRecords").findall("PlayerRecord")):
    rrs = pr.find("playerRoundRecords").findall("PlayerRoundRecord")
    for ri, rr in enumerate(rrs):
        pd = rr.find("playerData")
        if pd is None:
            continue
        at = pd.find("activeTechnologies")
        if at is None or len(at) == 0:
            continue
        for ud in at.findall("UnitData"):
            s = ET.tostring(ud, encoding="unicode")
            print("real replay p%d round-idx%d UnitData: %s" % (pi, ri, s[:500]))
            n_shown += 1
            if n_shown >= 4:
                break
        if n_shown >= 4:
            break
    if n_shown >= 4:
        break
# 真实回放 units 的 EquipmentID
n_shown = 0
for pi, pr in enumerate(root.find("playerRecords").findall("PlayerRecord")):
    rrs = pr.find("playerRoundRecords").findall("PlayerRoundRecord")
    for ri, rr in enumerate(rrs):
        pd = rr.find("playerData")
        if pd is None:
            continue
        ue = pd.find("units")
        if ue is None:
            continue
        for u in ue.findall("NewUnitData"):
            eq = u.findtext("EquipmentID")
            if eq and eq.strip() not in ("0", ""):
                print("real replay p%d round-idx%d unit id=%s EquipmentID=%s" % (
                    pi, ri, u.findtext("id"), eq))
                n_shown += 1
                if n_shown >= 6:
                    break
        if n_shown >= 6:
            break
    if n_shown >= 6:
        break
print("real replay units with equipment:", n_shown)
