# -*- coding: utf-8 -*-
"""step30 O1: 语料 UnlockUnit 字段与 unlocked 状态结构侦察。"""
import io, sys, os, json, itertools
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = r"C:\Users\chengli\Documents\GitHub\mechabellum-simulator"

# rounds_norm.json 是 324MB 大文件 —— 只读样例片段
path = os.path.join(GITHUB, "local_data", "rounds_norm.json")
with open(path, encoding="utf8") as f:
    head = f.read(200000)
print("head sample (first 3000 chars):")
print(head[:3000])
