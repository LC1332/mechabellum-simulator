# 引擎 opts 定版口径（step29）

> 权威来源：`data/calib/step29/step29_provenance.json`；
> 逐轮 A/B 溯源：`data/calib/step24..step29/*.json`。
> 所有 opts 都可以在 `battle_from_units(gd, p0, p1, opts={...})` 逐场覆写。

## 烘焙默认（step29 定版，已写进 engine.py opts 字典）

- `no_stack_set="27"` —— 雷霆齐射不向同目标叠加（每目标至多 1 发，不足打空）
- `inc_stack=1` —— 双发 countIncrease 全部炮弹打同一锁定目标
- `summon_max_batch=3` —— 周期型制造科技总批次上限（母舰/尖牙/爬虫制造）
- `scatter5=1` —— 熔点能量散射 = 5 条 17% 射线（优先不同目标）

其余 step24-28 定版默认见 `pysim/engine.py` 的 opts 字典注释，要点：

- `swing_fix` / `cycle_set` / `pc_set="11:2"` / `hack_gate` / `chaff_cover`
- `timeout_judge=score`（超时按分数判胜负）
- `atk_mul="21:1.25,3:1.5,7:0.55"`（对甲/对空等伤害倍率修正）
- `barrage_same="12,26"`（齐射同靶兵种）
- `bld_term=2`（建筑局终局口径：机动单位全灭即终局；由调用方按场景 group
  B/CAL/BP 注入，见 `benchmarks/run.py` 与 `web/server.py`）

## 已知未烘焙 opts（A/B 证据在溯源文件，默认关）

| opt | 语义 | A/B 证据 |
|---|---|---|
| `chaff_xsep` | 爬虫碰撞只留爬虫互撞 | 爬虫专用库 +4 场 / s28 +7 / s26 −8 |
| `cycle_set` 追加 27 | 雷霆击杀后机枪化修复 | st292/354 方向正确 / s27 −5 |
| `wc_set="2002:2"` | 泰山 2 武器 | 泰山 flip −3 / s25 −6 |
| `beam_pair=1` | 熔点换靶重升温 | st963 方向正确 / s25 −3 |
| `mine_summon` | 蜘蛛雷 | oracle 实测该科技在无主动激活的回放中不生效 |
| `sw_dive` | 沙虫潜地不可锁定 | 语义实锤但 s25 沙虫科技局 −4 |

这些未烘焙项的共同特征：个别库/场景受益但总分下降，故保持默认关闭、
留 opts 旋钮待后续机制修正后再评估。

## 校准与溯源索引

- `data/calib.json` —— 少量校准覆写
- `data/calib/<stepNN>/` —— 每轮机制修正的 A/B 数字（step24-step29）
- `data/calib/<N>/`（数字目录）—— 早期逐轮校准覆写历史
- 当前一致率总表：`data/calib/step29/bench_ver.json`（详见
  `information/benchmark.md`）
