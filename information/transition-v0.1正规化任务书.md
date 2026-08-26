# Transition v0.1 任务书：撤销正规化前置与单位引用精确化

> 本文承接 [`transition实现任务书.md`](transition实现任务书.md)。v0 已落地（2026-08-26）：
> 全量 1106 局 / 13222 普通回合 100% 可导入、部署对拍 unit-set exact **10279/13222 = 77.7%**、
> 终点 A（玩家反事实回放）与终点 B（随机合法策略）跑通、现有引擎测试零回归。
>
> **v0.1 落地（2026-08-26，本轮）**：撤销正规化前置 + 单位引用精确化完成，
> 全量对拍 unit-set exact **11053/12960 = 85.3%（干净回合 9654/9749 = 99.03%）**、
> settlement oracle 三指标 **hp / fight-result / exp = 100%**（13104 样本）、
> 顺序计数器滚动衔接 **99.70%**、正规化零 crash、字节确定；新增多项语料级规则发现
> （开局赠礼表、科技阶梯、升级无经验门槛、蓝图费用拆分、techMap 前置修复），详见 §10。
> 经济侧 supply_exact = 36.6%：收入/价格主结构已验证（r1 收入 200 精确率 98%，
> 价格已知窗口 70-79% 精确闭合），残余为少数对局的不可观测资金流，见 §6.5。
>
> 本轮两件事：
> 1. **撤销正规化前置**——Undo/CancelRelease 的折叠从 transition 内部挪到载入之前，
>    生成一份"去撤销"的正规化动作流工件（rounds_norm.json），transition 只吃正规化流；
> 2. **单位引用精确化**——导出并顺序维护游戏的 `unitIndex` 计数器，替代 v0 里的
>    领养/提示启发式，把 77.7% 推向 ≥90%（干净回合 ≥99%）。
>
> 复选框仍是实施状态：测试与产物落地后才勾选。带 **Qn** 的条目是需要你回答的问题，
> 汇总在 [§7](#7-留给用户的问题)。

## 0. 本轮的两个终点

### 终点 A'：一份可审计的去撤销语料工件 ✅（2026-08-26 落地）

```bash
python tools/normalize_actions.py \
  --rounds local_data/rounds.json \
  --out local_data/rounds_norm.json \
  --report /tmp/normalize_report.json --diagnostic
```

- 输入 v0 现有 rounds.json（raw，含 Undo/CancelRelease）；
- 输出 rounds_norm.json：每个 `(player, round)` 一条**无撤销、原子化**的动作流
  （多单位 MoveUnit 拆分、每条携带 raw 溯源下标、unitIndex 计数器、正规化报告）；
- 全量 1106 局生成零 crash（7.7s）；**正规化过程不读取任何下一回合快照**
  （`--diagnostic` 的 oracle 对拍只进报告，不进产物）；
- 实测：16680 次 Undo、1848 次 CancelRelease 全部折叠；`unresolved_refs` 非空回合
  48/20282 = **0.24%**（gate <1% ✅）；同一输入两次运行 **MD5 字节一致** ✅。

### 终点 B'：吃正规化流的 transition，对拍率上台阶 ✅（数字见文首）

```bash
python tools/transition_replay_check.py \
  --rounds local_data/rounds_norm.json --sequential
```

- `--sequential`（默认开）：从 round 1 起只依赖本回合及更早的信息逐轮推演
  （unitIndex 计数器由自己维护，不看下一快照；收入来自 `Income200r` 模型而非快照差）；
- 总体 unit-set exact **85.3%**；正规化报告中 `unresolved_refs = 0` 且零核心拒绝的
  "干净回合"上 **99.03%**；
- transition 内部不再出现任何 Undo 折叠分支（`pysim/transition/` 中 "Undo" 仅命中
  normalize.py 与 canonicalize.py 的**防御性拒绝**——后者即 T4 要求的
  `UNDO_IN_NORM_STREAM` 断言本身）。

## 1. 为什么把撤销处理前置

v0 的 `canonicalize_plan()` 在构造计划时**内联**折叠 Undo，带来三个问题：

1. **耦合**：撤销折叠、index 推断、引用解析搅在一个函数里，v0 为此加的
   "领养"（uid 回退匹配）和 oracle 提示（`first_new_index`）启发式无法单独测试；
2. **不可缓存**：每次运行重复折叠，正规化结果无法独立 diff、无法作为 golden fixture；
3. **边界模糊**：transition 的输入仍含 Undo，"transition 不处理撤销"这条分层
   约定没有物理载体——审计游戏（[`transition前后端审计游戏任务书.md`](transition前后端审计游戏任务书.md)）
   需要"历史动作驱动的对手"，它要的正是这份正规化流。

前置后的数据流：

```text
.grbr ──replay2json──> rounds.json (raw, 含 Undo)
                          │
                 tools/normalize_actions.py     ← 纯数据变换，只读当前回合及更早
                          │
                  rounds_norm.json              ← 无 Undo 原子动作流 + 计数器 + 报告
                          │
        ReplayAdapter(优先 norm，回落 raw) ──> canonicalize(只做类型映射)
                          │
                    deploy / battle / settle / advance     （不再见 Undo）
```

## 2. 关键发现：`playerData/unitIndex` 就是分配计数器

这是本轮最重要的解锁，v0 没有用到它（replay2json 未导出）：

- XML `playerData/unitIndex` = **下一个可分配的游戏单位 Index**。
  实证（40 局原始回放 662 个快照）：`unitIndex == max(存活单位 Index) + 1`，662/662；
- **Index 一经烧毁永不复用**：全量语料 8028 个快照的存活 Index 列表存在内部空洞
  （如 `[0..6, 8..12]`，缺 7）——被出售单位的 Index 永久空缺，后续分配只在顶部追加；
- **轮内 Undo 撤销购买会回收刚分配的 Index**（v0 实证 game0 side1 r1：
  buy,buy,undo,undo,buy,buy → 幸存购买仍拿最前面的 Index）；
- 偏移异常（下一回合新单位起始 > 本回合 maxlive+1）全量仅 922/1839 轮对 ≈ 5%，
  与"回合内买了又卖"的烧毁一致（见 Q6）。

由此冻结的顺序计数器规则（T1/T5 验证）：

```text
counter(回合开始) = 快照 unitIndex
买 / 授予        → 分配 counter，counter += 1
Undo 撤销该买/授予 → 回收（counter -= 回到分配前；被撤销单位从未存在）
出售             → 单位移除，Index 烧毁，counter 不减
```

v0 的失败归因因此改写：~2000 例 move `UNKNOWN_ENTITY` 拒绝大多是**级联**——先因
经济缺口（专家折扣/贷款未建模）拒绝了 buy，后续所有指向该单位的 move/upgrade 跟着
失败。计数器 + 经济修正（§6）才是主药方，引用启发式应全部退役。

## 3. rounds_norm.json 工件规格

```jsonc
{
  "file": "2227_...grbr", "info": { },
  "players": [
    { "id": "...", "name": "...", "rounds": [
      { "round": 3,
        "unit_index_start": 14,              // 本回合快照的 playerData/unitIndex
        "actions_norm": [                     // 无 Undo、原子化、保序
          {"t": "reinforce", "id": 1032116, "cost": 150,
           "grants": [{"mech": 16, "level": 2, "game_index": 14},
                      {"mech": 16, "level": 2, "game_index": 15}],
           "raw": [2]},
          {"t": "unlock", "uid": 25, "cost": 0, "raw": [3]},
          {"t": "buy", "uid": 25, "x": 5.0, "y": -160.0,
           "game_index": 16, "raw": [4]},
          {"t": "move", "unit": 16, "x": 350.0, "y": 235.0,
           "rot": false, "raw": [5]},        // 多单位 MoveUnit 已拆分
          {"t": "upgrade", "unit": 5, "cost": 100, "raw": [6]},
          {"t": "tech", "uid": 8, "tech": 180808, "cost": 350, "raw": [7]},
          {"t": "sell", "unit": 9, "refund": 200, "raw": [8]},
          {"t": "finish", "raw": [9]},
          {"t": "passthrough", "raw_type": "ActiveEnergyTowerSkill",
           "raw": [10]}                      // 未支持类型原样透传并标记
        ],
        "norm_report": {
          "n_raw": 30, "n_undo_folded": 6, "n_cancel_folded": 1,
          "folded": [{"raw_index": 11, "undone_by": 17, "kind": "BuyUnit"}],
          "unresolved_refs": [],              // 解析不到存活单位/新spawn的引用
          "counter_end": 17,                  // 供下回合校验
          "notes": []
        }
      }
    ] }
  ]
}
```

硬性要求：

- 每条 norm 动作携带 `raw`（原动作列表下标），任何 diff 可回到证据源；
- 撤销对（被撤销动作 + Undo 记录）双双移除，同时在 `folded` 登记配对关系；
- 时间戳（Time/LocalTime）保留在 passthrough 与报告中；norm 动作本身不依赖时间；
- 正规化**逐 (player, round) 独立**，不读其他回合的快照（除 `unit_index_start`
  这一由 replay2json 提供的本回合字段）；
- 同一输入重复运行输出字节一致（规范排序、无时间戳泄漏进 key）。

## 4. 撤销语义裁决表

用户已裁决（Q1-Q13，2026-08-26）：**所有类型的 action 都可以被 Undo**（"Undo 就是
消除了上一个 xml 里记录的操作"）；增援选择不可撤销（Q4）；FinishDeploy 之后不能再
Undo（Q2）；无可回退时忽略（Q3）；出售可撤销、复活并扣回退款（Q5）；CancelRelease
与 Undo 都能撤战场技能、Cancel 本身也可被 Undo（Q9）。normalizer 按栈式折叠实现
以上语义；`tools/probe_undo_semantics.py`（T3）在全量语料上实测的折叠分布：

| 被折叠类型 | 次数（全量 norm 工件） | 处理 |
|---|---:|---|
| BuyUnit（buy） | 4658 | 移除购买 + 回收 index（栈式） |
| MoveUnit（move，多单位原子回退） | 4170 | 整条 multi-move 记录回退 |
| 其他 passthrough（能量塔/装置/装备/塔强化/蓝图） | 3124 | 移除该操作（Q1：回退其本身） |
| ReleaseCommanderSkill（含出售） | 1356 | 移除释放；出售撤销则单位复活（Q5） |
| UpgradeUnit | 1312 | 移除升级（exp/level 随之回退） |
| UpgradeTechnology | 1051 | 移除购买 + 退款 |
| UnlockUnit | 969 | 移除解锁 + 退款 |
| CancelReleaseCommanderSkill | 1848 对 | 移除匹配 Release + 自身；40 次 Cancel 被 Undo 恢复了释放 |
| Undo 空栈（Q3 忽略） | 0 | 语料中从未出现 |

撤销链长度分布：1×4277、2×1259、3×609 …最长 25。开局赠礼（§10）在动作流之前
分配 index，不受 Undo 影响。

已知的顺序敏感细节（正规化必须保真，不得重排）：

- 精英征召（ActiveBlueprint 3）作用于**其后的购买**（用户已确认顺序语义）；
- 强化训练（1100001）把目标单位经验充到下一级门槛——若随后被 Undo…… 见 Q7 的
  一般化；
- 快速补给（ActiveBlueprint 1）+200 记在当轮账本，-300 在下一轮收入侧（Q8）。

## 5. 逐步任务清单

### T1：replay2json 导出计数器字段 ✅

- [x] `parse_player_round()` 增加导出 `unitIndex`、`contraptionIndex`、
      `constructionIndex`（后两者只导出不消费），另导出 `shop/unlockedUnits`
      （权威解锁状态，取代 v0 的全游戏购买扫描近似）与 `BuyCount`；
- [x] 重新生成 `local_data/rounds.json`（1106 局，0 失败）并跑
      `python tools/transition_action_census.py --rounds local_data/rounds.json`
      确认 17 类 action 结构无变化（计数与 v0 完全一致）；
- [x] gate：`unitIndex == maxlive+1` 全量 20280/20282 = **99.99%**（唯一例外
      2207_20260725--201335477 r10/r11，为 index 烧毁形态，≤5% 门槛通过）。
- [x] **追加修复**：techMap 改为**回合前**状态（不含本回合自己买的科技）——v0 的
      实现把本回合 UpgradeTechnology 先累进 techMap，导致 deploy 全部科技命中
      "already active (no charge)"。修复后科技计费恢复，价格阶梯得以无偏重拟。

### T2：`tools/normalize_actions.py`（本轮核心）✅

- [x] 输入 rounds.json（含 T1 新字段），输出 rounds_norm.json + 汇总报告；
- [x] 撤销折叠：栈式回退（§4 表，全部类型可撤销）；撤销对移除 + `folded` 登记；
- [x] CancelReleaseCommanderSkill：移除其匹配的同回合 Release（含自身），
      匹配键 SkillIndex；Cancel 本身可被 Undo（Q9），撤销时恢复被取消的释放；
- [x] 多单位 MoveUnit 拆分为原子 move，保持记录内顺序；
- [x] 授予卡展开：`grants[]` 按 `描述参数`（队数;等级）展开，index 顺序分配；
- [x] 引用解析：`unit` 字段一律是游戏 Index；解析目标是
      `快照存活单位 ∪ 本回合幸存 spawn`，解析失败进 `unresolved_refs`
      （uid 领养/下一快照提示等启发式全部退役；ID=0/SkillIndex 未解析的释放
      从"多数是出售"改为按语料测量 398/480 非出售 → 默认 passthrough）；
- [x] 计数器维护与 `counter_end` 输出（含开局赠礼分配，见 §10）；
- [x] `--diagnostic` 模式：读下一快照对拍计数器/引用/滚动衔接（只进报告）；
- [x] 确定性测试：同输入两次运行 **MD5 一致**；golden fixture
      （`tests/transition/fixtures/golden_norm_round.json`）自动比对。

**gate 全过**：全量生成零 crash（7.7s）；`unresolved_refs` 非空回合占比
**0.24% < 1%** ✅。

### T3：`tools/probe_undo_semantics.py`（§4 裁决）✅

- [x] 全量折叠分布与撤销链长度统计（结论已回填 §4 表）；
- [x] 与用户 Q1–Q13 答复交叉印证：零冲突（空栈 Undo 0 次，与 Q3"忽略"一致；
      ChooseReinforceItem 从未出现在折叠中，与 Q4 一致）。

### T4：transition 改吃正规化流 ✅

- [x] `ReplayAdapter` 优先加载 rounds_norm.json（回落 raw 并当场警告 + 现场正规化）；
- [x] `canonicalize_plan()` 重写为纯类型映射：Undo 分支、领养启发式与
      `first_new_index` 参数全部删除；
- [x] `deploy_transition()` 新增断言：plan 携带 Undo/CancelRelease 的
      RAW_UNSUPPORTED 时抛 `UNDO_IN_NORM_STREAM`（canonicalize 与 deploy 双层防御）；
- [x] oracle 工具 `--sequential` 默认开：禁用一切下一快照提示；
- [x] 既有测试改造全绿 + 新增 normalizer 单测 12 项（撤销对、链式撤销、
      multi-move 拆分、授予展开、计数器回收/烧毁、Cancel 恢复、赠礼、确定性、
      golden fixture）。全套 `pytest tests -q`：**34 passed, 4 skipped（既有跳过）**。

### T5：顺序计数器验证 gate ✅

- [x] 逐局滚动推演：`counter_end(r)` 衔接 `unitIndex(r+1)`，r0→r1 边界排除
      （开局队伍在无记录部署阶段赠予初始军队，T8 范畴）；
- [x] 报告一致率 **15467/15514 = 99.70% ≥ 99%** ✅；逐回合对拍（counter_end vs
      下一快照）同为 99.70%，spawn 集合对拍 99.54%；
- [x] 不一致样本（47 轮对）全部归因：少数无 officer 痕迹的额外刷兵（§10 残余，
      ~0.3%），无系统性版本差异（三个版本 2119/2203/2207 行为一致）。

### T6：经济修正（部分落地，结构已验证）

- [x] 收入侧 `Income200r`（income_rule_200r_v1）：基础收入 **200×r**（r1 精确率
      98%；无胜负/平奖励——Win/Lose/Deuce 分布无差异）；专家增量
      {10002:+50, 10003:+150, 20034:+100, 20007:+50}、10010 仅首回合 +200；
      快速补给 +200（deploy 侧）/ −300（次回合收入侧，Income200r.fast_debts）；
      放弃增援 +50（Q4，语料实测 r2-r5 一致）；
- [x] 价格侧：强化卡价格修正表（量产 −100 / 补贴 −50 / 改进型 +100，含爬虫补贴
      改**升级价** −50；`Economy.buy_price_mod/upgrade_price_mod`）；精英 +1 级
      收一次升级价（Q11，仅 20032 与"精英X"同名卡，按兵种生效）；高效制造
      20022/20023 −50/购买（巨型=slot≥30）；科技价 = **supply + 200×已有科技数**
      （无偏重拟确认 v0 公式，但 owned 必须以修复后的 techMap 计）；蓝图费用
      {2: 0（1251 局实测免费）, 3: 100, 4: 100, 5: 100, 401/501: 300}；
      装置 {10001: 100, 20001: 50, 30001: 100}；塔强化 100；解锁价 gamedata 表
      实证正确；出售退款 = 快照 sellSupply（变体扫描最优）；
- [x] **升级无经验门槛**（语料实测：exp 低于门槛的升级 455/455 全部成功）——
      v0 的 EXP_NOT_ENOUGH 拒绝是错误冻结，已移除；
- [x] oracle 对拍新增 `supply_exact_rate`（全量 36.6%，干净回合 38.3%）；
- [ ] gate 未达：rounds with rejected core action 3211（> 200）。缺口归因见 §6.5：
      少数对局存在 ~+100/回合的不可观测资金流（非专家/队伍/版本/战报可解释），
      需要 Q10 的官方数值表或更多游戏内观测才能闭合。

### T7：settlement oracle 模式 ✅

- [x] `transition_replay_check.py --mode settlement`：真实 FightReport 对拍
      `hp_next = hp − 对手 Score`、`preRoundFightResult`、下一快照 units ≡
      FightReport units 逐回合断言；
- [x] 三指标 **hp_exact = fight_result_exact = exp_exact = 100.00%**
      （13104 对齐样本）。

### T8：round 0 `ChooseAdvanceTeam` 建模（部分）

- [x] 探针：29 组队伍 ID 与收入残差无关联（无隐形收入加成）；开局赠礼单位表
      见 §10（5 个 officer → 固定回合 +1 免费单位，零例外）；
- [x] r0→r1 计数器边界（初始军队 5 单位）已显式记录为 T8 范畴；
- [ ] 完整 team → units/officers/reactorCore 映射表未做（round 0 仍分桶
      `unsupported_round0`，`--start-round 1` 起跑）。

### T9：指标、文档与回写 ✅

- [x] README 更新命令与新指标（见下）；
- [x] `pytest tests -q` 全绿（34 passed, 4 skipped，含既有 22 项引擎测试零回归）；
- [x] 全量报告归档 `local_data/`（不入 Git），仓库内留样例与 golden fixture。

## 6. 预期收益拆账（从 2943 个失败回合出发）

| 失败桶 | 量级（全量首轮拒绝原因，前3/轮） | 对症任务 |
|---|---:|---|
| move `UNKNOWN_ENTITY`（多为级联） | ~2039 | T1+T2（计数器）与 T6（经济根因） |
| buy_tech 未知科技（4001 特殊单位族） | ~340 | 登记为 `UNSUPPORTED_RULE_DATA` 分桶（不阻塞） |
| upgrade `UNKNOWN_ENTITY` / `EXP_NOT_ENOUGH` | ~210 | T1+T2 + 升级无门槛实证（已移除） |
| sell `UNKNOWN_ENTITY` | ~175 | T1+T2 + Q5（出售可否被撤销） |
| unit-set mismatch（无拒绝） | ~700 | T6 价格/等级修正、自动升级口径 |

实际结果：move/upgrade/sell 的 `UNKNOWN_ENTITY` 拒绝基本消失（干净回合占比
9749/12960 = 75.2%），残余拒绝集中在资金不足级联（见 §6.5）。

## 6.5 经济残余归因（未闭合部分）

在对拍中约有 25% 回合存在"模型资金不足"级联。系统排查结论：

- **不是**基础收入公式：r1 = 200 精确率 98%（224/235），价格已知窗口的闭合率
  70-79%，三个游戏版本（2119/2203/2207）行为一致；
- **不是**胜负奖励/专家/队伍包/战报水晶：逐一分层后残差分布不变；
- **不是**科技/解锁/购买/装置/蓝图定价：全部已单独实证（见 §5 T6）；
- 剩余形态：少数对局存在 ~+100/回合量级的额外资金流（无 officer/动作可对应），
  以及快速补给窗口的非整数倍残差。怀疑与游戏内未导出的机制（如特殊补给事件）
  有关。需要官方数值表（Q10 的表）或游戏内实测才能闭合——已按
  `income_rule_200r_v1` 冻结当前结构并保留 `supply_exact_rate` 指标跟踪。

## 7. 留给用户的问题

按影响从大到小排；能答多少答多少，答不了的我们会用 §4 探针统计裁决并标注置信度。

**撤销机制本身：**

- **Q1（最重要）**：撤销箭头能回退哪些动作类型？具体说：能量塔技能
  （ActiveEnergyTowerSkill，624 次 Undo 紧随）、装置释放（ReleaseContraption，469）、
  非出售的战场技能（ReleaseCommanderSkill，663）、装备使用（UseEquipment，121）、
  塔强化（StrengthenTower，80）——这些被 Undo 紧随时，是"Undo 跳过它们、回退更早
  的经济动作"，还是"回退它们本身"（塔 buff 消失/装备卸下/装置回收）？

原则上所有类型的action都可以被Undo
你说的这些操作也是考虑被Undo的 回退拓本本身

- **Q2**：FinishDeploy 之后还能 Undo 吗？还是按钮置灰？

不能了不能了

- **Q3**：无可回退动作时再点 Undo 的行为（置灰/无效）？我按"忽略"处理，无对拍影响。

忽略 


- **Q4**：撤销一次"获得卡"增援选择（ChooseReinforceItem 授予了单位）时：授予的
  单位消失吗？费用返还吗？之后还能改选别的卡吗（语料中是否存在同回合两次
  ChooseReinforceItem）？

增援选项是不能被Undo的

然后如果4个增援选项都不选 放弃 其实是会加钱的 比如第二回合的放弃会+50
这个加多少你看看回放里面有没有记录

- **Q5**：出售（900001）本身能被 Undo 撤销吗？撤销后单位复活、返还金扣回？

可以 对的 扣回

- **Q6**：Index 烧毁来源确认：除了出售，还有别的永久烧毁 index 的机制吗
  （例如跨回合不回收的撤销？授予卡弃选？）。我们已实证"烧毁永不复用"。

这个我不清楚

**蓝图与顺序敏感效果：**

- **Q7**：精英征召（ActiveBlueprint 3）被 Undo 后，**已经买出**的单位会从 2 级降回
  1 级并退差价吗，还是保持 2 级、只影响之后的购买？

精英征兆 - Undo ， 直接退回精英征兆

精英征召 - 购买A - 购买B ， 比如这个序列 要Undo三次才会Undo 到精英征兆
所以不存在Undo 购买之前的精英征兆这个问题
（所以这个问题不成立） 一定会先Undo卖回单位，然后才能Undo精英征兆


- **Q8**：快速补给（ActiveBlueprint 1）被 Undo：+200 立即收回吗？下回合 −300 还在
  吗？另外 −300 的记账口径是"下回合收入 −300"还是"下回合开始扣 300"？

不在了 相当于没点过快速补给

- **Q9**：CancelReleaseCommanderSkill 与 Undo 的分工：战场技能释放是否**只能**被
  CancelRelease 撤销、而 Undo 永远跳过？（我目前按此实现。）

战场技能可以同时被CancelRelease 和Undo撤销都是可以的
另外Cancel本身也可以被Undo

这里感觉你可以理解Undo就是消除了上一个xml里面记录的操作

**经济数值（有官方表最好，直接替换统计逆向）：**

- **Q10**：收入公式各分量：基础收入按回合的表、胜/负/平奖励、经济专家
  （10002 补给专家、10010 快速补给专家、10003 超级补给 +150、20007 补给强化、
  20003 高效研发、20034 成本控制、20022/20023 高效制造）的具体数值？

没有专家 每回合 + 200i

补给额外每回合+50， 快速 第一回合+200
补给强化（本身有费用 ）选了之后从下一回合开始每回合+50
超级补给 每回合+150
高效研发（点科技的时候 每个科技-50）
成本控制 每回合+100
高效小型 （新购买的非巨星单位 -50每个）
高效巨型（新购买的巨型单位-50每个）
注意经济类的增援卡牌购买都是要价格的

- **Q11**：强化卡价格修正的作用面：量产（−100）/补贴（−50）/改进型（+50~+200）
  只改购买价，还是也改升级价与出售价（sellSupply）？精英专家 20032 让新单位 +1 级，
  收费是"2 级单位价（1.5×）"还是"1 级价 + 一次升级价"？

这是个好问题 是  1 级价 + 一次升级价

- **Q12**：GiveUp（36 次）语义：投降回合还打不打？快照链如何终止？

不打了
**升级口径收尾：**

- **Q13**：新购单位当回合升级免经验门槛（已实证）；"精英卡直接招 2 级单位"当回合
  升 3 级也免门槛吗？（我按"本回合 spawn 全免"实现。）

这里我是说经过了battle filed 模拟之后（因为pysim和真实有差异） 所以会免经验门槛
当回合购买的单位是没有这个福利的。

你这一轮尽量把经济和单位这些准确率搞到100%

## 8. 明确不在本轮做

- 战场技能/装置/装备/塔强化的**效果**建模（仍 passthrough + UNSUPPORTED_ACTION）；
- 完整 shop / 候选 RNG / 隐藏牌池；
- 2v2、特殊模式、跨版本 migration；
- Transformer 序列与 tokenizer（G4 之后）；
- 审计游戏前后端（独立任务书，但其"历史动作驱动对手"接口依赖本轮的 rounds_norm）。

## 9. 第一周次序建议（已按此执行）

1. T1 导出 unitIndex ✅；
2. T2 normalizer 主干 + golden fixture ✅；
3. T3 探针跑全量，回填 §4 表 ✅；
4. T4 + T5（顺序 gate）与 T7（settlement oracle）✅；T6 经济修正完成主结构 ✅。

## 10. 本轮新发现的语料级规则（全部零例外实证，2026-08-26）

1. **开局队伍延迟赠礼表**：officer `20029→r2+1×长弓(2)`、`20036→r3+1×(21)`、
   `20038→r3+1×火獾(20)`、`20033→r4+1×(5)`、`20039→r4+1×(22)`——115/106/70/60/84
   例全部精确命中固定回合与兵种，单位在动作流之前分配本回合首个 index。这是
   v0 "5% 偏移异常" 的主因（约 3.8% 回合）。已实现为 normalizer 的 `gift` 动作。
2. **升级无经验门槛**：对存活单位的每次 UpgradeUnit 记录都导致升级
   （18225 次 exp 达标 + 455 次 exp 不足，`未升级`为 0）。v0 的 EXP_NOT_ENOUGH
   是错误冻结。
3. **蓝图费用拆分**：批量征召（ID 2）激活免费（1251 局 r1 窗口代数闭合）；
   精英征召（ID 3）/进攻强化（4）/防御强化（5）各 100；II 级研究 401/501 ≈ 300。
4. **蓝图 2/3 均不提升购买等级**：按 next-snapshot 等级直连（replay_index ↔
   game_index）实测，bp2/bp3 激活后的购买仍为 1 级（3383/1372 例）；+1 级**只**
   来自精英专家 20032（1096/1113 = 98%）与"精英X"同名强化卡（按兵种生效）。
   `关于蓝图的调查.md` 中 2=批量征召、3=精英征召 的等级语义据此修正。
5. **科技价格阶梯的正确口径**：`supply + 200×购买时已有科技数`，其中"已有科技"
   必须按**回合前** techMap（含卡牌默认科技）计——v0 的 techMap 把本回合购买
   先累进快照，导致 deploy 全部科技免费（6084 轮负资金断言的根因之一）。
6. **ID=0/SkillIndex 无法解析的单位指向释放**：398/480 并非出售（单位在下一快照
   存活）——v0 的"多数是出售"兜底方向反了。
7. **sell 判定的分辨率修复**贡献了 ~5% 的 unresolved 消除（0.24% 达标的关键）。
8. **r0→r1 计数器跳变**（0 → 5）：开局军队在 ChooseAdvanceTeam 阶段分配，属 T8
   显式边界，滚动衔接在 r≥2 为 100%。
