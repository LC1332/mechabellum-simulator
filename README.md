# mechabellum-simulator

Mechabellum（钢铁指挥官）战场模拟器：纯 Python + numpy 的战斗引擎、
FastAPI 前端（布阵沙盘 + benchmark 播放器）、八个基准库与 oracle 真值。
服务于强化学习路线（见 [`information/rl-roadmap.md`](information/rl-roadmap.md)）——
模拟器是那个路线里廉价的环境：

```python
battle_simulate( f_0 , f_1 ) --> battle_result
```

```python
from pysim.gamedata import GameData
from pysim.engine import battle_from_units

gd = GameData("data/gamedata.json")
b = battle_from_units(
    gd,
    [{"id": 10, "level": 1, "x": -100.0, "y": 0.0}],   # p0: 爬虫
    [{"id": 2,  "level": 1, "x":  100.0, "y": 0.0}])   # p1: 长弓
winner = b.simulate()          # 0 / 1 / -1
print(b.result(winner))        # 幸存、击杀、统计、终局时间 ...
```

## 快速开始

Python ≥ 3.10。

```bash
pip install -r requirements.txt
start_server.bat            # Windows；Linux/macOS: ./start_server.sh
# 打开 http://127.0.0.1:8300/        布阵沙盘（拖卡布阵 + 回放导入 + 模拟）
# 打开 http://127.0.0.1:8300/bench   benchmark 播放器（逐场对拍回放）

python benchmarks/run.py --lib all    # 全库对拍一致率（当前 ≈76.3%）
pytest tests -q                       # 引擎单元测试
```

## Benchmark 现状

引擎默认口径为 step29 烘焙定版（口径与未烘焙项见
[`information/engine-opts.md`](information/engine-opts.md)），
与活体游戏 oracle 真值的胜负一致率：

| 库 | 语义 | 一致率 |
|---|---|---|
| s24 | 全覆盖（33 兵种 + 建筑两两等经济） | 271/320 |
| s25 | 锚批（真实对局锚点重放） | 140/186 |
| s26 | 官方阵容 | 284/450 |
| s27 | 无科技对照 | 124/140 |
| s28 | 兵种×单科技伪兵种 | 803/1004 |
| s29p / s29cal / s29c | 机制探针 / 维修标定 / 爬虫推挤 | 39/57 · 20/42 · 112/150 |
| **合计** | | **1793/2349 ≈ 76.3%** |

库定义、规模与解读见 [`information/benchmark.md`](information/benchmark.md)，
跑法见 [`benchmarks/README.md`](benchmarks/README.md)。

## 回放接入（.grbr 历史对局）

官方 `.grbr` 回放 → 逐回合模拟语料 → 重放对拍，两步（`tools/replay2json.py`
纯标准库解析，内嵌 BattleRecord XML → 每回合一条 pair：双方布阵/科技/RNG/建筑
快照 + 该回合胜负标签）：

```bash
# 1) 转换：回放目录 -> rounds 语料（1106 局 humen_replay 全量约 10 分钟，0 失败）
python tools/replay2json.py C:\Users\chengli\Downloads\humen_replay local_data/rounds.json

# 2) 逐回合重放对拍：总准确率 + r1-2/r3-4/... 分桶 + 逐回合号准确率
python -m pysim.replay_check --rounds local_data/rounds.json
#    常用探针：--round-filter r1-2 / r7+   --limit 50   --trace data/trace.txt
```

语料写到 `local_data/rounds.json` 后，web 首页的「回放导入」会自动加载它
（回落链 `local_data/rounds_new11.json` → `local_data/rounds.json` →
`data/samples/rounds.json`）：`GET /api/replays` 列出对局与各回合标签，
`GET /api/replay/{idx}/{round}` 取单回合布阵（含科技/塔等级/绕后延迟），
`POST /api/simulate` 现场模拟。

Python 侧单独重放一个回合（完整可运行脚本见
[`examples/replay_round.py`](examples/replay_round.py)）：

```python
import json
from pysim.gamedata import GameData
from pysim.engine import battle_from_units
from pysim.replay_check import build_tech_map
from pysim.flank import pair_flank_delays, annotate_units

gd = GameData("data/gamedata.json")
pair = json.load(open("local_data/rounds.json", encoding="utf8"))[0]["pairs"][3]

u0 = pair["p0"]["units_fight"]          # 布阵阶段结束时的实际参战卡
u1 = pair["p1"]["units_fight"]          # （快照 units 是布阵前状态）
d0, d1 = pair_flank_delays(pair, mode="card")   # 绕后空降的等待秒数
b = battle_from_units(
    gd, annotate_units(u0, d0), annotate_units(u1, d1),
    tech_map0=build_tech_map(gd, pair["p0"], "mdefull",
                             {int(u["id"]) for u in u0}),
    tech_map1=build_tech_map(gd, pair["p1"], "mdefull",
                             {int(u["id"]) for u in u1}),
    towers0=[int(x) for x in (pair["p0"].get("towerStrengthen_raw") or [0, 0])][:2],
    towers1=[int(x) for x in (pair["p1"].get("towerStrengthen_raw") or [0, 0])][:2],
    officers0=pair["p0"].get("officers"), officers1=pair["p1"].get("officers"))
winner = b.simulate()                   # 0/1/-1
print(winner == 0, pair["label"])       # 与回放标签 (Win/Lose) 对拍
print(b.result(winner))                 # 幸存卡、击杀、终局时间、拆塔数 ...
```

### 真实历史对局逐回合准确率（humen_replay 语料）

1106 局 2026-07 的真实对局 `.grbr` 回放（`C:\Users\chengli\Downloads\humen_replay`），
8228 个带胜负标签的回合全部可解析、可构建；其中每局第 0 回合（先手方空场）
1106 对因一方无可战部队被跳过，其余 **7122 回合全部完成模拟，总体胜负一致率
4053/7122 = 56.9%**（与步进标定期 full-938 语料的 ~58% 持平，是该口径在
更新版本真实对局上的外推表现）。逐回合号：

| 回合 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 对数 | 951 | 955 | 974 | 981 | 982 | 889 | 672 | 407 | 188 | 89 | 26 | 7 | 1 |
| 一致率 | 61.5% | 60.2% | 56.4% | 56.2% | 55.9% | 56.8% | 56.2% | 50.1% | 47.9% | 57.3% | 38.5% | 71.4% | 1/1 |

分桶看：r1-2 60.9%、r3-4 56.3%、r5-6 56.3%、r7+ 53.2% —— 前两回合最高
（兵力少、机制简单），r8 起规模变大、指挥官技能/经验等级累积后下滑，
r10+ 样本很小仅作参考。复现：转换后跑
`python -m pysim.replay_check --rounds local_data/rounds.json`
（单进程全量约 4 小时；`tools/replay_check_parallel.py local_data/rounds.json 8`
按回放记录切分 8 进程并行约 75 分钟，汇总含逐回合准确率）。

## 目录结构

```
pysim/          战斗引擎（engine.py ~4000 行 numpy SoA 模拟器；纯 Python，Linux 通用）
web/            FastAPI 服务 + 前端（static/index.html 沙盘、static/bench.html 播放器）
data/           gamedata 数值表、calib 校准溯源、8 库场景与 oracle 真值、samples/ 小样例语料
benchmarks/     批量对拍脚本（run.py）与用法
examples/       接入示例（replay_round.py：加载 rounds 语料重放单局/单回合）
tests/          pytest 单元测试
tools/          数据生成器留档（gamedata/回放解析管线，仅标准库）
information/    文档（RL 路线、benchmark、engine opts）
local_data/     本地大数据（完整回放语料，gitignored；见其 README）
```

回放语料回落加载：`local_data/rounds_new11.json` → `local_data/rounds.json`
→ `data/samples/rounds.json`（仓库内 2 局真实样例）；全缺失时服务器照常启动，
仅首页"回放导入"为空。

## 已知边界

- 外围经济模拟（RL 路线中的 transition 函数）尚未实现，是下一步开发重点
- oracle 注入管线（Windows 绑定）不在本仓库；`data/exp/` 真值为其定版产物
- s29cal / s26 一致率仍低（47% / 63%），差异可在 `/bench` 页逐场回放定位
