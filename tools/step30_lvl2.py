# -*- coding: utf-8 -*-
"""step30: pysim battle_from_units 单位创建路径 level 处理。"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator"
eng = open(GITHUB + r"\pysim\engine.py", encoding="utf8").read()
lines = eng.splitlines()
# 找 battle_from_units 定义
for i, ln in enumerate(lines):
    if "def battle_from_units" in ln:
        for j in range(i, min(len(lines), i + 90)):
            print("%5d: %s" % (j + 1, lines[j].rstrip()[:150]))
        break
