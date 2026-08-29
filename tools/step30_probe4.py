# -*- coding: utf-8 -*-
"""step30: 检查 o3k4a 构造局 XML (supply/units/actions) 与模板 supply 结构。"""
import io, sys, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
import xml.etree.ElementTree as ET
sys.path.insert(0, r"C:\Users\chengli\Documents\mech\RouteC\tools")
import craft_replay as cr
sys.path.insert(0, r"C:\Users\chengli\Documents\mech\RouteC\tools")
g = r"C:\Users\chengli\Documents\mech\RouteC\data\exp\s30\_grbr\s30_o3k4a_11.grbr"
if not os.path.exists(g):
    g = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator\data\exp\s30\_grbr\s30_o3k4a_11.grbr"
print("grbr:", g, os.path.exists(g))
prefix, xml, suffix, vlen = cr.split_grbr(g)
root = cr.parse_xml(xml)
for pi, pr in enumerate(root.find("playerRecords").findall("PlayerRecord")):
    rrs = pr.find("playerRoundRecords").findall("PlayerRoundRecord")
    pd = rrs[-1].find("playerData")
    sup = pd.find("supply")
    print("p%d supply element:" % pi, ET.tostring(sup, encoding="unicode").replace("\r", "").replace("\n", "")[:300] if sup is not None else "None")
    ue = pd.find("units")
    print("p%d units:" % pi, len(ue) if ue is not None else None)
    ar = rrs[-1].find("actionRecords")
    print("p%d actions:" % pi, len(ar) if ar is not None else None,
          [a.get("{http://www.w3.org/2001/XMLSchema-instance}type") for a in ar] if ar is not None else [])
# 模板 supply 原始结构
TPL = json.load(open(r"C:\Users\chengli\Documents\mech\RouteC\data\step24_scenarios.json",
                     encoding="utf8"))["meta"]["tpl"]
tp, tx, ts_, tv = cr.split_grbr(TPL)
troot = cr.parse_xml(tx)
pr0 = troot.find("playerRecords").findall("PlayerRecord")[0]
pd0 = pr0.find("playerRoundRecords").findall("PlayerRoundRecord")[-1].find("playerData")
sup0 = pd0.find("supply")
print("template p0 supply:", ET.tostring(sup0, encoding="unicode").replace("\r", "").replace("\n", "")[:400] if sup0 is not None else "None")
