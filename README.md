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

** 欢迎反馈 修改 共建**

## 快速开始

Python ≥ 3.10。

```bash
pip install -r requirements.txt
start_server.bat            # Windows；Linux/macOS: ./start_server.sh
# 打开 http://127.0.0.1:8300/        布阵沙盘（拖卡布阵 + 回放导入 + 模拟）
# 打开 http://127.0.0.1:8300/bench   benchmark 播放器（逐场对拍回放）
# 打开 http://127.0.0.1:8300/game    审计游戏（transition 多回合适玩, 见下文）

python benchmarks/run.py --lib all    # 全库对拍一致率（当前 ≈76.3%）
pytest tests -q                       # 引擎 + transition + /game 测试
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
pysim/transition/  外围规则状态转移（部署/战斗适配/结算/开局/能力分类器）
web/            FastAPI 服务 + 前端（index.html 沙盘、bench.html 播放器、
                game.html 审计游戏 + game_service/game_library/game_api）
data/           gamedata 数值表、calib 校准溯源、8 库场景与 oracle 真值、
                game/opening_catalog.json 开局包、samples/ 小样例语料（含 replay_game）
benchmarks/     批量对拍脚本（run.py）与用法
examples/       接入示例（replay_round.py：加载 rounds 语料重放单局/单回合）
tests/          pytest 单元测试（transition/ 引擎 / /game API）
tools/          数据生成器留档（gamedata/回放解析/正规化/开局 catalog/游戏库管线）
information/    文档（RL 路线、benchmark、engine opts、transition 任务书）
local_data/     本地大数据（完整回放语料与 replay_game 游戏库，gitignored）
```

回放语料回落加载：`local_data/rounds_new11.json` → `local_data/rounds.json`
→ `data/samples/rounds.json`（仓库内 2 局真实样例）；全缺失时服务器照常启动，
仅首页"回放导入"为空。

## 已知边界

- transition 经济模型主结构已验证（收入 200×r + 专家增量，购买/科技/解锁/装置/
  蓝图定价实证），少数对局存在不可观测的额外资金流，`supply_exact_rate` 36.6%
  跟踪中（`information/transition-v0.1正规化任务书.md` §6.5）
- oracle 注入管线（Windows 绑定）不在本仓库；`data/exp/` 真值为其定版产物
- s29cal / s26 一致率仍低（47% / 63%），差异可在 `/bench` 页逐场回放定位

## Transition v0.1（外围规则状态转移 + 去撤销正规化）

`pysim/transition/` 在 v0 闭环（结构化 state → canonical 动作 → pysim 战斗适配 →
结算 → 回合推进，动作 receipt + reason code + 资金账本，非法动作不改状态）之上，
v0.1 把 **Undo/CancelRelease 的折叠前置**为独立正规化工件并精确化单位引用：

- `pysim/transition/normalize.py`：栈式撤销折叠（全部动作类型可撤销，用户裁决
  Q1-Q9）、多单位移动原子拆分、授予卡展开、顺序 unitIndex 计数器
  （买/授予分配、撤销回收、出售烧毁）、开局队伍延迟赠礼表、引用解析
  （失败进 `unresolved_refs`，无任何 uid 领养/下一快照启发式）；
- `tools/normalize_actions.py`：生成可审计的 `rounds_norm.json` 工件
  （无撤销原子动作流 + 计数器 + 报告，字节确定）；
- `ReplayAdapter` 优先加载正规化流（回落 raw 时现场正规化并警告）；
  `canonicalize_plan` 只做类型映射；deploy 对 Undo 输入抛
  `UNDO_IN_NORM_STREAM`（防御）；
- 经济模型 `Income200r`：收入 200×r + 专家增量 + 快速补给债 −300 + 放弃增援
  +50；价格侧含强化卡修正表、精英 +1 级收费、高效制造折扣、科技阶梯、
  蓝图/装置/塔强化费用。

全量语料实测（1106 局，2026-08-26）：

| 指标 | 数值 |
|---|---|
| 正规化：undo/cancel 折叠 | 16680 / 1848 全部折叠，零 crash，两次运行 MD5 一致 |
| 正规化：`unresolved_refs` 非空回合 | 48/20282 = **0.24%** |
| 顺序计数器（counter_end vs 下一快照 unitIndex） | **99.70%**（滚动衔接 r≥2 为 100%） |
| spawn 集合对拍 | 99.54% |
| 部署对拍 unit-set exact | **11053/12960 = 85.3%**（干净回合 **99.03%**） |
| settlement oracle：hp / 胜负 / 经验 | **100% / 100% / 100%**（13104 样本） |
| supply_exact_rate | 36.6%（经济残余见任务书 §6.5） |

```bash
# 1) 正规化：rounds.json (raw) -> rounds_norm.json（去撤销工件 + 报告）
python tools/normalize_actions.py --rounds local_data/rounds.json \
    --out local_data/rounds_norm.json --report local_data/normalize_report.json \
    --diagnostic

# 2) 部署对拍（--sequential 默认开：计数器/收入不读下一快照）
python tools/transition_replay_check.py --rounds local_data/rounds_norm.json \
    --report /tmp/transition_report.json

# 3) settlement oracle：FightReport 对拍 hp/胜负/经验
python tools/transition_replay_check.py --rounds local_data/rounds_norm.json \
    --mode settlement

# 4) 撤销语义探针（§4 裁决表数据）
python tools/probe_undo_semantics.py --rounds local_data/rounds_norm.json

# 5) 经济残差归因探针
python tools/supply_residual_probe.py

# 终点 A: 玩家回放反事实重赛（状态只初始化一次，战斗全部来自 pysim）
python examples/replay_player_match.py --rounds data/samples/rounds.json \
    --game 0 --start-round 1 --seed 7 --trajectory /tmp/replay_player_match.json

# 终点 B: 随机合法策略完整 episode（env soak）
python examples/random_rollout.py --episodes 100 --seed 7 \
    --report /tmp/random_rollout_report.json

pytest tests -q                   # 34 passed（transition 24 + 引擎 10）
```

v0.1 明确的未支持机制（receipt 记录 `UNSUPPORTED_ACTION`，不静默）：战场技能/
装置/装备/塔强化/蓝图的效果建模（费用已计）、`ChooseAdvanceTeam`（round 0 特殊
回合，含 r0→r1 计数器边界）、GiveUp（终局标记）。

## 审计游戏 /game（transition 多回合适玩）

[`information/transition前后端审计游戏任务书.md`](information/transition前后端审计游戏任务书.md)
的 v1 实现：`http://127.0.0.1:8300/game` 上由 transition 掌管唯一真状态的本地前后端游戏——
你接管一局真实回放的一方自由操作，另一方严格执行其历史策略，逐动作、逐回合审查：

```text
round 0 开局(4 选 1: 历史项固定原位 + 3 个确定性生成项)
  → 回合开始收入(200×r + 专家) → 增援四选一(回放真实 4 候选)
  → 部署(买兵/移动/升级/科技/解锁/塔强化/蓝图/能量塔技能/战场技能/回收)
  → 对手历史动作原子执行(升级前经验补齐 override, 失败即 BLOCKED)
  → 一次 pysim 战斗(trace + BattleOutcome 同源) → HP/经验/reward 结算
  → 下一回合 / 终局 / 明确报告的 unsupported BLOCKED
```

三层架构（任务书 §2）：transition 层（`pysim/transition/`，唯一状态写入者，含
`opening.py` 开局包执行与 `capability.py` 能力分类器）→ 会话层（`web/game_service.py`
版本化 command 事务、`web/game_library.py` manifest+惰性 shard）→ 前端
（`web/static/game.html`，服务器返回的权威 GameView 重绘，六个审计面板：
双方 receipts / 资金账本 / state diff+digest / 历史动作对照 / pysim 战斗结果与播放）。

```bash
# 1) 构建开局 catalog（29 支队伍, ChooseAdvanceTeam -> round1 证据归纳; ~2 分钟）
python tools/build_opening_catalog.py --replay-dir local_data/humen_replay \
    --out data/game/opening_catalog.json

# 2) 构建游戏库（manifest + 单局 shard + 逐选项能力扫描; 1106 局约 45 秒）
python tools/build_game_library.py --replay-dir local_data/humen_replay \
    --out local_data/replay_game
#    仓库内小样例: data/samples/replay_game/（3 局 fixture, 测试用）
#    加载优先级 local_data/replay_game → data/samples/replay_game; 全缺失时
#    /game 显示 corpus_available=false, 其余页面不受影响

# 3) 打开 http://127.0.0.1:8300/game
pytest tests/test_game_api.py -q     # API/会话/验收/快照污染测试
```

API：`GET /api/game/replays?min_rounds=5`（含每选项可玩前缀 + blockers）、
`POST /api/game/sessions`、`GET/DELETE /api/game/sessions/{id}`、
`POST /api/game/sessions/{id}/commands`（`expected_version` 乐观并发，策略非法动作
返回 rejected receipt 不 bump 版本，409 STALE_SESSION_VERSION / 404 / BLOCKED 稳定码）。

v1 能力边界（capability scanner 与运行时同一分类器，严格镜像）：已完整建模——
买/移动/升级/科技/解锁/回收、增援卡（单位获得/强化/专家/技能库存）、蓝图全部 7 种
（快速补给/批量征召/精英征召/攻防强化 I·II）、塔强化（持久等级）、能量塔技能 5/6、
装置 10001/20001、战场技能（skills.py 已映射表）、强化训练；未支持即阻塞——
**装备卡（engine 无装备机制，仅扣费的接受会构成半效果，已改为拒绝）**、
未映射战场技能与能量塔技能 1/3/4、装置 30001。1106 局语料中可连续 ≥5 回合的严格
选项当前为 0（装备卡在 R4-R5 的候选/选取密度是主要墙），最佳可玩前缀 R4；"受限开始"
允许从前缀 ≥3 的选项开始，运行到前缀尽头以 scanner 预测的同一 blocker 精确 BLOCKED
（`tests/test_game_api.py::test_acceptance_flow_rounds_1_to_prefix_end` 断言该一致性，
本 fixture 在固定 seed 下由 pysim 提前终局时亦满足任务书 0.1.6 验收路径）。

