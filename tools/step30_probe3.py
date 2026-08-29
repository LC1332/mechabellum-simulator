# -*- coding: utf-8 -*-
"""step30: 伤害标定.grbr 的剑齿虎等级/科技/装备编码 + 全轮次科技分布。"""
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
import xml.etree.ElementTree as ET
sys.path.insert(0, r"C:\Users\chengli\Documents\mech\RouteC\tools")
import craft_replay as cr

g = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator\data\伤害标定.grbr"
prefix, xml, suffix, vlen = cr.split_grbr(g)
root = cr.parse_xml(xml)
mds = root.find("matchDatas").findall("MatchSnapshotData")
print("snapshots:", len(mds), "rounds:", [m.findtext("round") for m in mds])

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
            uid = u.findtext("id")
            if uid and int(uid) == 21:
                print("p%d r-idx%d 剑齿虎 NewUnitData: %s" % (
                    pi, ri, ET.tostring(u, encoding="unicode").replace("\r", "").replace("\n", "")[:600]))
        at = pd.find("activeTechnologies")
        if at is not None and len(at):
            for ud in at.findall("UnitData"):
                print("p%d r-idx%d activeTechnologies: %s" % (
                    pi, ri, ET.tostring(ud, encoding="unicode").replace("\r", "").replace("\n", "")[:300]))
        rq = pd.find("researchQueue")
        if rq is not None and len(rq):
            print("p%d r-idx%d researchQueue: %s" % (
                pi, ri, ET.tostring(rq, encoding="unicode").replace("\r", "").replace("\n", "")[:400]))
        eq = pd.find("equipmentDatas")
        if eq is not None and len(eq):
            ids = [e.findtext("id") for e in eq.findall("EquipmentData")]
            print("p%d r-idx%d equipmentDatas ids: %s" % (pi, ri, ids))
