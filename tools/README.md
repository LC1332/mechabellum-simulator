# tools/ —— 数据生成器留档

这里留档的是**数值表与语料的生成管线**（仅依赖标准库），用于说明
`data/gamedata.json` 与回放语料的来源、保证可复现性：

| 脚本 | 作用 |
|---|---|
| `mk_gamedata.py` | 游戏客户端解码产物（container.json / skills.json 等，需自备）→ `data/gamedata.json` |
| `mk_bench_rounds.py` | 生成合成的多模块 benchmark 语料（早期 C# 引擎压测用） |
| `replay2json.py` | `.grbr` 官方回放 → `rounds*.json` 逐回合训练 JSON（含 actions/techMap/units_fight 提取） |
| `replay_xml.py` | 从 `.grbr` 中抽出内嵌的 BattleRecord XML |

注意：

- 解码产物本体（classes.json / tech.json / skills.json / container.json 等）
  未入库；`mk_gamedata.py` 期望它们位于本目录
- `replay2json.py` 默认输出到 `local_data/rounds.json`（gitignored）
- Windows 注入 / oracle 管线（oracle_host、oracle_b、craft_replay、
  dump_manager 及全部探针输出）**刻意不在本仓库**；`data/exp/` 的真值
  是该管线在 step29 的定版产物
