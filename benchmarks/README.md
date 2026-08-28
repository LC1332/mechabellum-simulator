# benchmarks/ —— 批量对拍与一致率

## 是什么

用当前 pysim 引擎（默认出厂 opts、固定种子 `SEED=20220822`）重算每个基准库的
全部场景，与 `data/exp/<lib>/` 中**活体游戏内 oracle** 跑出的真值
（`winner_oracle`）对比，输出胜负一致率。这是模拟器保真度的核心指标。

> oracle 真值来自 Windows 注入管线（未迁移进本仓库）；`data/exp/` 是其定版产物，
> 作为只读真值资产入库。

## 用法（在仓库根执行）

```
python benchmarks/run.py --lib s24        # 单库
python benchmarks/run.py --lib all       # 全部 8 库（约 2349 场）
python benchmarks/run.py --lib s24 --only u001,u002   # 场景过滤
python benchmarks/run.py --lib s24 --opt kite_dist=40 # 自定义 opts 臂
python benchmarks/run.py --lib all --regen-exp
    # 重算记录（含刷新臂）写到 local_data/exp_regen/<lib>/，不改动 data/exp/
```

当前历史定版数字（step29 烘焙默认，`data/calib/step29/bench_ver.json`）：

| 库 | 语义 | 一致率 |
|---|---|---|
| s24 | 全覆盖库（33 玩家兵种+建筑 两两交会等经济采样） | 271/320 ≈ 84.7% |
| s25 | 锚批库（真实对局锚点批量重放） | 140/186 ≈ 75.3% |
| s26 | 官方阵容库（天梯常见成套阵容） | 284/450 ≈ 63.1% |
| s27 | 无科技对照库（两塔正中布局，机制探针） | 124/140 ≈ 88.6% |
| s28 | 兵种×单科技伪兵种库（本轮主战场） | 803/1004 ≈ 80.0% |
| s29p | step29 机制探针（剑齿虎/沙虫 NT 对照等） | 39/57 ≈ 68.4% |
| s29cal | 维修剑齿虎标定局 | 20/42 ≈ 47.6% |
| s29c | 爬虫推挤测试矩阵 | 112/150 ≈ 74.7% |
| **合计** | | **1793/2349 ≈ 76.3%** |

`run.py` 输出会与 `bench_ver.json` 的定版数字自动比对，不一致时打 `!!` 标记。

## 各库规模与场景文件

| 库 | 场景文件 | 真值目录 |
|---|---|---|
| s24 | `data/step24_scenarios.json` | `data/exp/s24/` |
| s25 | `data/step25_scenarios.json` | `data/exp/s25/` |
| s26 | `data/step26_scenarios.json` | `data/exp/s26/` |
| s27 | `data/step27_scenarios.json` | `data/exp/s27/` |
| s28 | `data/step28_scenarios.json` | `data/exp/s28/` |
| s29p | `data/step29_scenarios.json` | `data/exp/s29p/` |
| s29cal | `data/step29_cal_scenarios.json` | `data/exp/s29cal/` |
| s29c | `data/step29_crawler_scenarios.json` | `data/exp/s29c/` |

每组 exp 记录包含：场景内嵌（p0/p1/techs/constructions/towers）、oracle 遥测
（逐单位伤害/击杀）、`winner_oracle`，以及刷新用的 pysim 臂结果（`arms.factory`）。

## 溯源

- `data/calib/step24..step29/*.json` —— 每轮机制修正的 A/B 数字与定版溯源
  （`step29_provenance.json` 为 step29 烘焙口径的权威来源）
- `data/calib/<N>/`（数字目录）—— 早期逐轮校准覆写历史
- web 播放器 `http://127.0.0.1:8300/bench` 可以逐场回放对拍差异

## 动态装备场景包（step32）

- `run_equipment.py` —— 静态 E2 装备 9 场景 A/B（`data/equipment_scenarios.json`）
- `run_equipment_runtime.py` —— step32 动态装备 12 场景 control/treatment 双臂
  （`data/equipment_runtime_scenarios/equipment-runtime-v1.json`），校验机制方向
  expect（数值 provisional）；oracle 记录落 `data/equipment_oracle/` 后才能升级
  confidence（见该目录 README）。
