# equipment oracle 记录目录 (任务书 §4 / §8.5)

本目录存放**真实游戏受控实验**的归一化记录。没有任何脚本会往这里写
猜测值 —— 代码跑通不等于 oracle 通过 (任务书 §8.5: 不得把表中的
oracle 格自动改成通过)。

## 目录约定

- `equipment-runtime-oracle-v1/` — step32 动态装备本轮取证 build
  - `manifest.json` — 场景/环境元数据 (schema 见下)
  - `<case>.json` — 每个受控 case 的 control+treatment 归一化摘要
  - `raw/` — 原始逐帧遥测 (不提交大文件, 仅本地保存)

- 旧 E2 静态装备 oracle 直接放本目录 `<name>.json`, 由
  `benchmarks/run_equipment.py` 消费 (格式见该文件 ORACLE_KEYS)。

## manifest.json schema (equipment-runtime-oracle-v1)

```json
{
  "oracle_build": "equipment-runtime-oracle-v1",
  "game_build": "<游戏版本号 / exe hash>",
  "unpacked_file_hash": "<拆包定义表文件 hash>",
  "injection_module_version": "<注入遥测模块版本>",
  "map": "<地图>", "seed": "<种子>", "tick_rate": 30,
  "coordinate_system": "<坐标系说明>",
  "commit": "<开工时 pysim commit>",
  "created_at": "<日期>",
  "cases": [
    {
      "case": "<场景名 = benchmarks 场景名>",
      "equipment_id": 1305003,
      "carrier": {"mech": 1, "level": 1, "techs": [], "officers": []},
      "foe": {"mech": 9, "level": 1, "techs": []},
      "arms": ["control", "treatment"],
      "runs": 3,
      "command": "<实验命令, 不含用户路径/账号>"
    }
  ]
}
```

## 归一化 per-case JSON 字段 (任务书 §4.3 输出契约)

```text
t                                    # 帧/固定 tick 时间戳
unit/member id, card id, side, mech, level
position, hp, max_hp, shield_hp, barrier_ref
equipment_id
active_statuses + starts_at + expires_at + magnitude
current_target, attack_state, move_speed, range
damage_event(source, victim, raw, shield_absorbed, hp_damage, tags)
heal_event(source, target, requested, actual)
spawn_event(source_equipment, carrier, mech, level, position)
death_event(killer/source, victim)
winner, end_time, score/fight report fields
```

## 采集纪律 (任务书 §4)

- 一个 case 必须同时有 control 和 treatment, 固定机制至少复跑 3 次;
  存在随机性 (召唤位置/攻击散布) 时保留 seed 并跑 ≥20 次分布。
- 不保存用户名、Steam 账号、token、绝对隐私路径进可提交产物。
- 先静态反向 (定义表/效果类/伤害管线), 再注入遥测验证; 不以 UI 动画估数。
- 逐装备最小实验矩阵见任务书 §4.4; 每个字段的 confidence 升级以
  manifest + case 齐全为前提 (tests/test_equipment_runtime.py 会校验
  runtime spec confidence 仍为 provisional, oracle 齐全前不得手改)。
