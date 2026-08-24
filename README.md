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

## 目录结构

```
pysim/          战斗引擎（engine.py ~4000 行 numpy SoA 模拟器；纯 Python，Linux 通用）
web/            FastAPI 服务 + 前端（static/index.html 沙盘、static/bench.html 播放器）
data/           gamedata 数值表、calib 校准溯源、8 库场景与 oracle 真值、samples/ 小样例语料
benchmarks/     批量对拍脚本（run.py）与用法
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
