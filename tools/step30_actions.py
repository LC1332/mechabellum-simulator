# -*- coding: utf-8 -*-
"""step30: 真实回放 actionRecords 的 PAD_* 动作 XML 形态 (BuyUnit/UnlockUnit/UpgradeTechnology)。"""
import io, sys, os, glob, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
import xml.etree.ElementTree as ET
sys.path.insert(0, r"C:\Users\chengli\Documents\mech\RouteC\tools")
import craft_replay as cr

files = sorted(glob.glob(r"C:\Users\chengli\Downloads\humen_replay\*.grbr"))[:6]
seen = {}
for f in files:
    try:
        prefix, xml, suffix, vlen = cr.split_grbr(f)
        root = cr.parse_xml(xml)
    except Exception as e:
        print(f, "ERR", e)
        continue
    for pi, pr in enumerate(root.find("playerRecords").findall("PlayerRecord")):
        rrs = pr.find("playerRoundRecords").findall("PlayerRoundRecord")
        for ri, rr in enumerate(rrs):
            ar = rr.find("actionRecords")
            if ar is None:
                continue
            for a in ar.findall("MatchActionData"):
                t = a.get("{http://www.w3.org/2001/XMLSchema-instance}type") or "?"
                if t in ("PAD_BuyUnit", "PAD_UnlockUnit", "PAD_UpgradeUnit",
                         "PAD_UpgradeTechnology", "PAD_UseEquipment"):
                    if t not in seen:
                        seen[t] = ET.tostring(a, encoding="unicode").replace("\r", "").replace("\n", "")
    if len(seen) >= 5:
        break
for t, s in seen.items():
    print("==", t, "==")
    print(s[:700])
