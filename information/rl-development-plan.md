# 强化学习环境开发路径与 Milestones

> 本报告是在 [`rl-roadmap.md`](rl-roadmap.md) 的建模设想上，结合当前仓库的
> battle simulator、回放解析产物和 benchmark 现状，给出一条可落地、可验收的
> 开发顺序。重点是先在模拟器上形成完整环境，再开始策略和值函数训练。
>
> 范围说明：本文只做方案设计，不涉及具体代码实现。时间估算按 1 名熟悉仓库的
> 全职开发者粗略计算，主要用于表达阶段相对大小，不是工期承诺。

## 1. 结论先行

“先做 transition，再约定动作序列和局面描述，token 化后训练模型”的大方向是
对的，但建议把顺序稍微改成：

1. **先冻结结构化状态和动作契约**，同时建立回放对拍工具；
2. **再做 deployment transition**，让历史动作能从部署前快照精确重建
   `units_fight`；
3. **补齐 battle outcome 与 settlement**，形成真正可多回合运行的 `env.step()`；
4. **最后才冻结规范序列和 tokenizer**；
5. 先做行为克隆与战局价值基线，再做单回合策略改进，最后做多回合 self-play。

核心原则是：

- **结构化对象是唯一真值，文本/token 序列只是模型视图。** transition 不应直接
  在 token 上运算。
- **确定性规则不交给模型学习。** transition 可以作为辅助训练任务，但 RL 运行时
  必须调用真实规则实现。
- **先做一个受限但闭环的环境，再逐步补全规则。** 不应等所有增援、专家、装备和
  技能都完美后才跑第一局。
- **每个 milestone 必须有回放对拍或环境不变量作为验收条件。** “看起来能跑”
  不能作为 transition 的完成标准。

建议把第一个真正有价值的总目标定义为：

> 在固定规则版本、固定地图和受限动作集下，两名合法策略可以从初始状态连续打到
> 终局；每步可复现、reward 可审计、状态可序列化，历史回放中的受支持动作可精确
> 对拍。

做到这里以后，才算从 battle simulator 跨到了 RL environment。

### 路线全景

| 阶段 | 产物 | 可以开始做什么 |
|---|---|---|
| M0 规则与数据审计 | 已知/未知规则清单、全量 action census | 不再靠猜测扩展 transition |
| M1 State/Action 契约 | 结构化 schema、回放 fixture、state diff | 稳定开发和逐步对拍 |
| M2 Deployment Transition | 部署前状态经动作得到开战状态 | 精确重放外围操作 |
| M3 Battle + Settlement | 受限但完整的 `env.step()` | 跑随机/启发式完整 episode |
| M4 完整外围规则 | 开局、增援、经济、技能等 Normal 1v1 规则 | 从规则初始状态完整 self-play |
| M5 序列/tokenizer | 可逆、规范、可 mask 的 O/A 序列 | 安全地产生模型训练语料 |
| M6 数据与 baseline | 无泄漏数据集、random/heuristic 基线 | 判断模型是否真正学到东西 |
| M7 监督模型 | `pi_BC`、`V_battle_sim`、`V_battle_real` | 模仿人类并快速评价候选 |
| M8 单回合改进 | 搜索/rerank/候选零和博弈 | 证明 simulator 能改进策略 |
| M9 多回合 self-play | league、`V_episode`、完整 trajectory | 学习经济与长时程决策 |
| M10 sim-to-real | 双域校准、主动真实查询 | 降低模拟器偏差 |

## 2. 当前仓库已经有什么、还缺什么

### 2.1 已有能力

当前 battle simulator 已经远超过“只有胜负”的最小战斗器：

- `battle_from_units(...)` 可以接收双方单位、科技、塔强化、战场技能、建筑和专家；
- 引擎内部已经记录卡级经验、卡级伤害、击杀、存活单位、建筑和塔状态；
- `.grbr` 转换结果保留了每回合部署前的 `units`、原始 `actions`、重放动作后的
  `units_fight`，以及下一回合对应的战斗 report；
- 仓库样例语料中已经出现 16 类 UI action，包括选开局、选增援、解锁、购买、
  移动、升级、科技、装备、蓝图、塔技能、指挥官技能、装置、Undo 和结束部署；
- 回放中还有血量与资源字段、专家、蓝图、技能 CD、塔强化、建筑、RNG 和
  战后 survivor report，可用来建立 transition oracle。

这意味着第一版 transition 最适合走 **replay-driven specification**：用真实回放的
部署前状态和动作作为输入，以 `units_fight`、下一回合快照和 fight report 作为
分层真值。

### 2.2 当前关键缺口

| 缺口 | 对 RL 环境的影响 |
|---|---|
| 完整经济规则尚未实现 | 无法可靠判断购买、解锁、升级、科技等动作是否合法 |
| 当前 `gamedata.json` 没有完整增援候选生成、开局候选和外围经济配置 | 无法自行 `reset()` 并生成真实候选 cards |
| 当前语料记录了选中的增援 ID/Index，但没有显式保存当回合全部候选项描述 | 不能直接训练“在 A–E 中选择”的策略，也无法验证 action mask |
| battle 的公开 `result()` 没有按阵营输出伤害，也没有输出可直接回写的逐卡经验增量 | 还不能按路线图稳定计算 reward 和 settlement |
| battle 内部 card index 是本次构建顺序，不是环境的稳定 entity ID | 战斗结果无法无歧义地映射回长期状态 |
| 外部回放等级是 0 基，引擎内部等级是 1 基 | 若不在 schema 边界统一，会造成隐蔽的训练标签错误 |
| 当前真实历史回放胜负一致率约 56.9% | 在 pysim 中学强不等于在真实游戏中学强，且容易利用模拟器误差 |

另外，README 中 benchmark 合计约 76.3%，而真实历史回放逐回合约 56.9%。两组
指标用途不同：前者主要说明局部机制覆盖，后者更接近 RL 最终面对的分布。训练时
必须显式区分 `sim` 与 `real` 两个 domain。

## 3. 不要把一个 transition 写成一个黑盒

路线图中的：

```text
o' = T(o, A0, A1)
```

在接口层可以保留，但实现与测试时应拆成四段：

```text
部署转移：   (state, offer, A0, A1) -> pre_battle_state + action_results
战斗转移：   (battle_input, battle_seed) -> battle_outcome
战后结算：   (pre_battle_state, battle_outcome) -> settled_state + reward + done
候选生成：   (settled_state, env_rng) -> next_offers + next_observation
```

完整回合流程建议固定为：

```text
OBSERVE
  -> REINFORCEMENT_CHOICE
  -> DEPLOYMENT
  -> BATTLE
  -> SETTLEMENT
  -> NEXT_ROUND / TERMINAL
```

这样拆分有四个好处：

1. 可以先完成“固定候选、固定经济”的闭环环境，不被候选生成规则阻塞；
2. 部署错误、battle 误差和结算错误可以分别定位；
3. 历史回放能为每一层提供不同强度的 oracle；
4. 未来替换 `pysim` 为 `battle_real` 时，只替换 battle transition，不改 policy
   和外围环境。

### 3.1 战斗结束不等于把死亡单位从长期状态中删除

这是 settlement 最容易写错的地方。Mechabellum 中，战斗内死亡的普通已购单位并
不会因此从下一回合阵容中永久消失。battle outcome 中的 survivor 用来计算扣血和
统计，长期 board 通常仍保留战前卡组，只回写经验、技能 CD、血量和其他跨回合量。
建筑耐久、特殊召唤物等是否跨回合保留，应逐项用回放验证。

因此不能简单地写成：

```text
next_units = battle_result.survivors
```

## 4. 建议冻结的环境契约

### 4.1 区分内部真状态、玩家观测和战斗输入

建议定义三个层次，而不是让一个 `f` 承担所有含义。

#### EnvironmentState：规则引擎的完整状态

至少包含：

- `schema_version`、`ruleset_version`、`engine_version`；
- 当前 round、phase、地图和模式；
- 环境 RNG 状态、候选生成所需的隐藏牌池/历史；
- 双方 PlayerState；
- 当前候选增援及其可见性；
- 下一个稳定 entity ID 或其他确定性 ID 分配状态。

#### PlayerState：单个玩家的长期状态

至少包含：

- 当前 HP、最大 HP、资金/资源；
- 专家、已解锁兵种、已购科技、蓝图；
- 研究中心/塔强化；
- 指挥官技能和 CD、塔技能、装备库存；
- 已购单位卡；
- 建筑与其他跨回合实体。

单位卡建议至少包含：

- 内部稳定 `entity_id`；
- 兵种 ID、统一后的 1 基等级、经验；
- 位置、朝向；
- 回收价、装备和其他影响部署合法性的字段；
- 必要时保留来源信息，但不要默认暴露给 policy。

#### Observation：某个玩家实际可见的模型输入

Observation 应从 EnvironmentState 投影得到，并明确：

- 哪些候选、资金、CD 和部署操作是公开的；
- 部署阶段是否能实时看到对方操作；
- 玩家 1 是否镜像到统一的“己方永远在下方”的坐标系；
- RNG、隐藏牌池、对方私有信息是否剔除。

如果生成下一轮候选需要隐藏状态，而 policy 看不到它，那么严格说这是 POMDP。
这没有问题；问题是不要把隐藏 RNG 混进 observation 造成训练时信息泄漏。

### 4.2 Action 不只保留一种表示

建议同时保留两套 action：

#### RawActionLog

忠实保存游戏 UI 操作，用于回放重建、审计和研究人类行为，例如中间多次移动、
Undo、取消施法和 FinishDeploy。

#### CanonicalActionPlan

给行为克隆和 RL 使用的规范动作计划。它应表达最终意图，去掉鼠标抖动和被撤销的
操作。例如同一单位连续移动 8 次，规范动作通常只保留最终落点。

推荐的语义动作族包括：

- `choose_start` / `choose_reinforcement`；
- `unlock_unit`；
- `buy_unit as new_k`；
- `upgrade_unit ref`；
- `buy_technology unit_type tech_id`；
- `use_equipment equipment_id ref`；
- `activate_blueprint`、`strengthen_tower`、`activate_tower_skill`；
- `set_position ref x y rotate`；
- `place_contraption`、`cast_commander_skill`；
- `end_deploy`。

完整 action 类型仍要以 1106 局全量语料 census 为准，不能只以仓库内 2 局样例
定版。

### 4.3 新购买单位不要让模型发明永久 ID

路线图提出 `buy unit as id` 是必要的，因为后续动作需要引用新单位。但建议模型只
创建**本 action plan 内的临时别名**：

```text
buy_unit mech=10 as=new_0
set_position ref=new_0 x=... y=... rotate=...
```

永久 `entity_id` 应由 transition 确定性分配。否则模型可能制造 ID 冲突、引用未来
对象，或者让相同语义状态因随机 ID 不同而无法消歧。

下一次 observation 中，可以按规范排序重新生成模型可见的局部 handle；环境内部
仍保留稳定 ID，以便经验和装备正确回写。

### 4.4 合法性不能“静默过滤”

路线图中写到 transition 过滤不合法操作。建议接口不要静默忽略，而应对每步返回：

```text
accepted / rejected
reason_code
resource_delta
created_entity_id
state_digest_after_action
```

运行模式可分为：

- `strict`：历史回放对拍和测试中，任何非法动作立即失败；
- `masked`：训练/推理时只允许模型采样当前合法 token/action；
- `tolerant`：仅用于检查未约束模型，非法动作产生明确惩罚并记录，不改变状态。

静默过滤会让 policy 学会输出大量无效操作并依赖环境兜底，产生很差的 credit
assignment，也容易形成 simulator exploit。

### 4.5 规范动作顺序

游戏中很多操作顺序对最终局面无影响，但 token 模型会把它们当成不同标签。建议
CanonicalActionPlan 固定大类顺序，例如：

```text
增援选择
-> 解锁/科技/蓝图/塔强化等全局购买
-> 买兵/升级/装备
-> 全部单位最终布阵
-> 战场技能和装置
-> end_deploy
```

某些动作若确实顺序相关，则保留依赖关系并做拓扑排序。不要为了序列唯一性改变
真实语义。

### 4.6 BattleOutcome 必须扩充为 settlement 可用的契约

当前公开结果还不够。建议 BattleOutcome 至少输出：

- winner/draw、结束时间、battle seed；
- 双方 survivor 数量、存活价值和用于扣血的原始 score；
- `damage_to_player[2]`，而不是双方混在一起的总伤害；
- 以环境 `entity_id` 对齐的 `exp_before/exp_delta/exp_after`；
- 每卡伤害、击杀、参与击杀；
- 塔、建筑、装置的结算结果；
- 所有会影响下一回合的状态变化；
- `engine_version` 和关键 opts digest。

当前引擎内部已有 card exp 和 card damage，可以复用；关键是通过 adapter 把本次
构建的 `card_idx` 映射回 EnvironmentState 的稳定 `entity_id`，并通过公开结果返回。

### 4.7 Reward 需要单独版本化

路线图中的扣血比例 reward 可以作为 v1 候选，但应先用真实 fight report 验证以下
语义：

- 双方是否可能同回合都扣血；
- 扣血究竟由 survivor 数、survivor 价值、塔状态还是其他公式决定；
- 终局额外奖励与当回合扣血是否叠加；
- 最大血量变化时如何归一化。

建议 reward 输出原始分量，而不是只返回一个 scalar：

```text
damage_component
terminal_component
invalid_action_component
total
reward_spec_version
```

无论具体公式如何，双人零和环境应验证 `r0 = -r1`。终局 bonus 的量级不要比每回合
伤害大几个数量级，否则前面的布局价值几乎拿不到梯度。

## 5. 回放驱动的 transition 验证方案

### 5.1 三个逐层 oracle

每个历史回合可以形成三类测试样本：

1. **部署 oracle**

   `pre_deploy snapshot + raw actions -> units_fight`

2. **战斗 oracle**

   `units_fight + combat modifiers -> fight report / round winner`

3. **结算 oracle**

   `pre_battle state + fight report -> next-round snapshot`

部署 transition 是确定性外围规则，目标应是精确一致；battle 是近似模拟，应该用
统计指标；settlement 中能观测到的字段应精确一致，不能拿 battle 的 56.9% 胜负
准确率为经济/状态错误开脱。

### 5.2 对拍时必须区分“支持率”和“正确率”

建议每次报告至少给：

- action 类型覆盖率；
- 被支持 action 的执行成功率；
- 最终单位集合、位置、朝向、等级、科技、资金等逐字段 exact match；
- 首个 divergence 的 action index 和 reason；
- 因语料缺字段而不可验证的样本比例；
- 按回合、action 类型、专家/增援类型分桶的结果。

一个只有 100% 正确率、但只覆盖 MoveUnit 的 transition 没有意义。反过来，支持
所有 action 但靠 silently ignore 达到高 final-board match 也没有意义。

### 5.3 必须补的测试类型

- schema 与规范序列的 serialize/deserialize round trip；
- 相同 seed、state、joint action 的确定性；
- 玩家 0/1 交换并镜像后的对称性；
- action 每个 prefix 的 mask 与 transition 合法性一致；
- 资源守恒、ID 唯一、坐标合法、等级/经验不变量；
- Undo/Cancel 的 raw replay 语义与 canonical plan 等价；
- snapshot 保存后恢复，后续 trajectory 完全相同；
- fuzz/property tests：随机合法策略连续运行，不 crash、不产生 NaN、不泄漏状态。

## 6. 推荐开发 Milestones

### M0：规则与数据审计（约 1–2 周）

**目标**：在动 transition 之前，把无法从当前仓库确定的语义列成一份冻结清单。

交付物：

- 全量 1106 局 action census：类型、字段、频率、出现回合和撤销模式；
- `reactorCore`、`supply`、等级、经验、卖价等字段的准确语义说明；
- 开局候选、增援候选、回合收入、购买/升级/解锁/科技/装备价格的权威来源；
- 部署边界、占位、转向、跨线/绕后、技能 CD 和结算规则清单；
- `ruleset_version` 与回放游戏版本的对应关系；
- reward v1 规格和可从回放验证的字段清单。

验收 gate：

- 每类高频 action 都有字段 schema 和预期状态变化；
- 所有未知规则标成 `unknown`，并明确阻塞哪个 milestone；
- 能说明如何获得“未被选择的增援候选”。如果回放本身没有，就决定重新提取、从
  RNG 重建，或让第一版环境使用外部注入候选。

### M1：结构化 State/Action 契约与 replay harness（约 1–2 周）

**目标**：先建立 transition 的输入输出边界和可持续对拍框架。

交付物：

- EnvironmentState、Observation、PlayerState、UnitCard、RawActionLog、
  CanonicalActionPlan 的 schema v1；
- 0 基/1 基等级、坐标系、玩家镜像、ID 分配规则；
- state diff、digest、首个 divergence 定位报告；
- 从回放产生 `(state_before, raw_actions, expected_pre_battle_state)` fixture 的管线；
- ruleset/schema migration 规则。

验收 gate：

- 结构化 schema round trip 100% 无损；
- 同一语义局面生成相同 canonical state；
- 交换双方并镜像两次可恢复原状态；
- fixture 可以覆盖全量回放，并报告缺失字段而不是猜默认值。

### M2：Deployment Transition v0（约 2–4 周）

**目标**：完成最常见外围操作，先把“部署前 -> 开战前”做准确。

首批建议范围：

- 固定地图、普通 1v1；
- 解锁、购买、升级、购买科技、最终位置/转向；
- 基础资金校验、部署区域与单位引用；
- CanonicalActionPlan 不含 Undo 和中间移动，RawActionLog adapter 负责折叠；
- 增援效果先允许由 fixture 注入，不在此阶段强求随机生成候选；
- 技能、装备、蓝图、特殊专家可以 feature flag 分批加入。

验收 gate：

- 对“声明支持”的 raw action，执行语义 100% 明确且无 silent ignore；
- 受支持回放的最终 `units_fight` 在单位、等级、位置、朝向上 exact match；
- 支持率和正确率分开报告；第一阶段可接受支持率不高，但核心高频动作覆盖率应快速
  达到 90% 以上；
- 同一 canonical plan 不因被折叠的 UI 移动轨迹不同而产生不同结果；
- 每个 action prefix 都能给出一致的 legal mask 和 reason code。

### M3：BattleOutcome 与 Settlement v0，形成受限闭环环境（约 2–3 周）

**目标**：把现有 battle simulator 接成可连续多回合运行的环境。

交付物：

- EnvironmentState 到 battle input 的 adapter；
- 稳定 entity ID 与 battle `card_idx` 的双向映射；
- 按阵营伤害、逐卡经验增量、survivor score 等完整 BattleOutcome；
- HP、经验、CD、回合数和收入的 settlement；
- `reset/observe/legal_actions/step_joint/save/load` 的环境语义；
- reward v1 的逐分量日志。

第一版闭环可采用固定开局、固定增援结果或无增援的 sandbox ruleset。它不必立刻
等价于完整游戏，但必须能够从初始状态合法地打到终局。

验收 gate：

- 同 seed 的完整 episode bitwise/字段级可复现；
- 随机合法策略运行至少 1,000 局，无 crash、NaN、负资源、重复 ID 或 phase 错乱；
- `r0 = -r1`，终局后不可继续 step；
- 战后普通单位长期保留，经验正确回写；
- save/load 后继续运行与未中断轨迹一致；
- 可用回放验证的 settlement 字段 exact match。

到 M3 为止，可以认为已经拥有 **RL environment MVP**。

### M4：完整外围规则与候选生成（约 3–6 周）

**目标**：从受限 sandbox 扩展到接近完整 Normal 1v1。

分批加入：

- 开局四选一和增援 A–E；
- 专家/增援单位/增援卡的所有状态影响；
- 装备、蓝图、研究中心、塔技能/强化；
- 指挥官技能、装置和建筑；
- 回合收入、弃选金额、出售/回收、维护等经济项；
- 完整 CD 与候选 RNG/牌池状态。

验收 gate：

- 全量语料 action 类型支持率达到预设目标，建议先以 95% 回合完全可重放为 gate；
- 对支持的确定性外围字段保持 exact match；
- 无法由回放恢复的候选/RNG 字段有单独覆盖测试与权威数据来源；
- 从真实初始规则 `reset()` 后可以不依赖回放 fixture 打完整局；
- 每个未支持机制都可被 feature flag 隔离，不能悄悄走错误默认值。

### M5：规范序列化与 tokenizer v1（约 1–2 周）

**目标**：在 schema 稳定后，为模型建立无歧义、可约束解码的序列语言。

建议：

- O 和 A 共享兵种、科技、技能、坐标等 token；
- action verb、字段名、枚举、entity handle 使用专用 token；
- 位置先映射到真实合法格点，再使用离散 `<x_i>`、`<y_j>`，不要让通用 BPE 拆
  浮点数；
- 资金、经验等数值明确采用离散桶、数位编码或专用整数 grammar；
- 单位按规范字段排序；玩家 1 先做 ego-centric 镜像；
- 每个序列带 schema/ruleset/tokenizer version；
- grammar parser 与 legal mask 共用同一规则来源。

验收 gate：

- `structured -> tokens -> structured` 在全量语料上 100% round trip；
- 规范化后，等价 raw actions 映射到相同 CanonicalActionPlan；
- 任意合法 action prefix 都能生成下一个 token 的语法 mask 和语义 mask；
- tokenizer 不产生 unknown ID，不把一个坐标或 ID 拆成含义不稳定的子词；
- 报告 token 长度分布，确认长局面和长 action plan 不会被训练窗口系统性截断。

### M6：离线数据集与非神经 baseline（约 1–2 周）

**目标**：先证明数据和环境指标正确，再训练模型。

数据集应至少区分：

- `human_real`：真实玩家的 observation/action；
- `pysim_outcome`：pysim 生成的 battle/trajectory 标签；
- `real_outcome`：真实回放或 oracle 标签；
- ruleset、engine、schema、tokenizer 版本；
- 数据来源、回放 ID、round、seed。

切分必须按整局/玩家/时间做，不能随机打散回合，否则相邻回合几乎相同，会造成
严重泄漏。

baseline 至少包括：

- random legal；
- 固定启发式经济 + 随机合法布阵；
- replay action 的重放上界；
- 简单局面统计的 value baseline；
- BC 的高频动作/不动策略基线。

验收 gate：

- 每条训练样本能追溯到原始 replay 或生成 seed；
- train/validation/test 无 replay 和玩家泄漏；
- baseline 可以完整跑环境，并产出固定格式 metrics；
- 明确当前真实人类语料只有约 1106 局、8228 个带标签回合，这个规模不适合从头
  训练大型语言模型。优先考虑小型结构化 Transformer 或微调已有模型。

### M7：监督学习——先分清三个 Value（约 2–4 周）

路线图中统一写作 `V(o)`，实现时建议拆成：

1. `V_battle(pre_battle_state)`：双方部署完成后，预测本回合 battle outcome；
2. `Q_round(o, A, B)`：部署前局面加双方 action，预测本回合收益；
3. `V_episode^pi(o)`：在给定策略分布下，预测直到终局的累计收益。

第一阶段训练：

- `pi_BC(A | O)`：模仿 CanonicalActionPlan；
- `V_battle_sim`：拟合 pysim，用作快速搜索 surrogate；
- `V_battle_real`：拟合真实回合标签；
- 可选 legality head；
- 可选 `O + A -> O'` 辅助头，用于检查表示是否包含完整状态，但不替代真实
  transition。

`V_sim` 与 `V_real` 应是显式的两个 head/domain，不能把标签混起来训练成一个含义
不清的 value。

评估不能只看 action token accuracy：

- reinforcement choice accuracy；
- action 类型和参数准确率；
- 生成计划的语法有效率与语义合法率；
- 执行后的最终资金差、board edit distance 和 battle value regret；
- value 的 Brier score、校准曲线、排序能力和按回合分桶表现。

验收 gate：

- 受 grammar/mask 约束后，生成 action 的语法有效率接近 100%；
- 语义非法动作不依赖 tolerant transition 兜底；
- 执行级指标显著优于“不动”和高频动作 baseline；
- `V_sim` 在未见局面上能可靠排序候选，`V_real` 单独报告 sim-to-real gap；
- 保存模型时绑定 ruleset/schema/tokenizer 版本。

### M8：单回合策略改进与候选博弈（约 2–4 周）

**目标**：先验证“模型可以利用 simulator 改进策略”，再进入长时程 RL。

建议流程：

1. 从历史部署前局面采样；
2. `pi_BC` 为双方生成多样的合法候选计划；
3. transition 执行候选，直接调用 cheap battle simulator 得分；
4. 对候选矩阵求近似零和混合策略，或先做简单 best-response/reranking；
5. 用筛选结果做 rejection sampling、DAGGER 式再训练或离线策略改进；
6. 持续保留 BC policy、旧 checkpoint 和启发式策略作为对手池。

如果 battle simulator 足够便宜，单回合初期应优先使用直接模拟结果，而不是让
`V_battle` 完全替代它。Value 的主要价值是预筛、降低候选矩阵成本和服务后续多回合
rollout。

验收 gate：

- 在冻结的历史局面和对手池上，改进策略的期望模拟 reward 显著高于 BC；
- 非法率不升高，策略不是靠重复无效 action 或数值边界 bug 获利；
- 对手池不同分桶都报告结果，不能只打赢当前一个 checkpoint；
- 候选仍有合理多样性，避免所有输出塌缩到同一阵型；
- 把高收益异常局面加入 simulator exploit 回归集。

### M9：多回合 self-play（约 4–8 周起）

**目标**：学习经济、科技和 HP 管理的长时程价值。

建议在 M8 稳定后再做：

- 从真实初始状态和历史中间状态混合 reset；
- 使用 episode return 训练 `V_episode`；
- policy/value checkpoint league，而不是只用最新策略自博弈；
- 按回合和局面阶段做 prioritized sampling；
- 保留 human BC loss 或 KL anchor，防止早期 RL 丢失基本操作能力；
- 记录每次 trajectory 的完整版本、seed 和对手 checkpoint；
- 定期对 frozen opponent suite、启发式策略和旧版本做回归。

验收 gate：

- frozen opponent suite 上的表现稳定提高，而非只在同步 self-play 中循环占优；
- 训练可从任意 trajectory 快照精确复现；
- 经济、action 数量、阵型多样性和回合长度没有异常漂移；
- simulator exploit 集不退化；
- 多回合策略优于逐回合贪心策略，证明 long-horizon 学习确实带来价值。

### M10：sim-to-real 校准与真实闭环（持续进行）

**目标**：把“擅长 pysim”逐步转成“对真实游戏有效”。

建议：

- 保留 `V_sim`、`V_real` 双头和 domain 标记；
- 真实 oracle 数据用于独立校准和 held-out evaluation，不能全部用于调 simulator；
- 主动挑选 `V_sim` 与 `V_real` 分歧最大、策略最敏感的局面提交真实 battle；
- 训练 simulator uncertainty/error model；
- 对高价值策略候选做少量真实验证，再加入 replay buffer；
- 真实数据按游戏补丁版本隔离。

验收 gate 不应只看总胜负一致率，还应看：

- policy 选择在真实与模拟中的 regret；
- 按兵种、科技、回合和技能分桶的 sim-real gap；
- 模拟器不确定性是否能识别高风险局面；
- 在完全冻结的真实 benchmark 上是否稳定改进。

## 7. 推荐的近期执行顺序

如果现在开始，建议按下面的短路径推进：

### 第一阶段：先把 replay 变成 transition 的单元测试

1. 对全量语料做 action census；
2. 冻结外部等级、坐标、单位 ID、资金和 phase 语义；
3. 生成部署前状态、动作、`units_fight` 三元组；
4. 先支持购买、移动、升级、解锁、科技五类高频动作；
5. 每扩一类 action，都报告覆盖率和 exact match。

### 第二阶段：做一个“规则不全但真正闭环”的环境

1. 固定开局或从历史中间状态 reset；
2. 固定/外部注入 reinforcement，不先实现候选 RNG；
3. 接 battle、经验、扣血、收入和终局；
4. 让 random legal 和 heuristic policy 连续跑 1,000 局；
5. 冻结 env v0，作为后续模型共同依赖的基线。

### 第三阶段：再冻结 token 语言并做 BC + Value

1. RawActionLog 规范化成 CanonicalActionPlan；
2. 做结构化 round trip 和 token grammar mask；
3. 先训练 `pi_BC` 和 `V_battle_sim/V_battle_real`；
4. 用执行后的 board/reward 衡量 policy，而不是只看 token accuracy；
5. 用 simulator 做 best-of-N/reranking，验证策略能否被改进。

这条短路径大约在 **6–11 个开发周**可以到达第一个受限 RL MVP；做到覆盖完整
Normal 1v1 的外围规则，通常还需要额外 **4–8 周或更多**，主要不确定性来自候选
生成、专家/增援效果和经济规则的数据来源，而不是模型本身。

## 8. 模型侧的具体建议

### 8.1 第一版不要让一个模型同时承担所有责任

可以共享 observation encoder，但建议至少把任务边界分开：

- autoregressive policy head：生成规范动作；
- legality/mask：由规则系统保证，模型 head 只是辅助；
- `V_battle_sim` 与 `V_battle_real` 两个 value head；
- 后期再加 `V_episode`；
- transition prediction 只作为 auxiliary loss。

如果一开始把 action、transition、单回合 value、多回合 value 全混成一种 next-token
loss，出现误差时很难知道是规则、表示、policy 还是 value 的问题。

### 8.2 历史动作的目标应是“最终计划”，不是鼠标轨迹

样例中同一回合存在大量连续 MoveUnit 和 Undo。直接模仿 raw action 会浪费模型容量
学习拖动过程。默认训练目标应是 canonical plan；raw log 可以作为单独的人类操作
研究任务，或者用于恢复顺序相关语义。

### 8.3 序列消歧应在 serializer 完成，而不是靠模型自己学

建议使用：

- ego-centric 玩家视角；
- 固定字段顺序；
- 单位按类型、位置、等级、经验等规范排序；
- observation-local handle；
- 新单位使用 action-local `new_k`；
- 坐标离散到实际部署格；
- set/map 全部排序；
- schema/ruleset 版本显式入序列或样本元数据。

必须接受一个事实：如果两个实体在所有可观测属性上完全相同，那么它们本来就是
观测上不可区分的。此时不应为了“唯一序列”泄露内部随机 ID。

### 8.4 数据规模决定第一版模型要克制

约 8228 个带胜负标签的真实回合足够：

- 验证表示和 pipeline；
- 微调小模型做行为克隆；
- 建立 value baseline；
- 作为真实 domain 的校准集。

但不足以从头训练一个同时掌握复杂 action grammar 和长时程策略的大模型。pysim
可以廉价生成 outcome，却不能凭空生成高质量“人类动作”。因此前期更现实的组合是：

```text
真实回放提供行为先验
+ 规则 mask 保证合法
+ simulator 搜索产生改进动作
+ self-play 扩大策略数据
```

## 9. 主要风险与止损线

| 风险 | 表现 | 止损措施 |
|---|---|---|
| transition 规则猜错 | 回放后期资源和状态逐渐漂移 | 每层对拍、首 divergence、未知规则不默认 |
| raw action 多解 | BC token accuracy 很低或学会抖动 | 训练 canonical plan，执行级评估 |
| action 静默失败 | policy 输出大量垃圾仍有 reward | grammar + mask + reason code + invalid metrics |
| simulator exploit | sim 内胜率暴涨、真实更差 | exploit 回归集、双 value head、主动真实查询 |
| value 含义混淆 | 同一 `V` 标签互相冲突 | 分开 battle/round/episode value |
| 数据泄漏 | validation 极高但实战无效 | 按整局、玩家、时间切分 |
| 游戏版本漂移 | 同状态在不同回放规则不同 | ruleset/engine/data version 强绑定 |
| 候选增援缺失 | 选择策略无法训练或 mask 错误 | M0 解决来源；MVP 先 fixture 注入 |
| ID/坐标不规范 | 等价局面产生大量 token 变体 | 内部 ID 与模型 handle 分离、坐标离散 |
| 只看胜负准确率 | 看不到 reward/经验/经济错误 | 多字段 exact match 与分桶指标 |

## 10. 总体 Definition of Done

当以下条件同时成立，才可以说“完整 RL 环境的第一版完成，可以正式进入多回合
训练”：

- 可以从规则初始状态 reset，不依赖历史回放；
- 双方每个 action prefix 都有正确 legal mask；
- joint action 经 deployment、battle、settlement 后产生下一 observation；
- 资金、HP、经验、CD、科技、单位 ID 和候选状态跨回合一致；
- reward 零和、可分解、可审计并绑定版本；
- 固定 seed 完全可复现，可保存/恢复 trajectory；
- 受支持的历史外围 transition 与回放 exact match；
- 随机合法策略能稳定跑大量完整 episode；
- structured/token round trip 无损；
- 模型输出通过 grammar/mask 执行，而不是依赖环境忽略非法动作；
- 所有数据、模型和 trajectory 都绑定 ruleset、engine、schema 与 tokenizer 版本；
- 有独立的 sim benchmark、真实 benchmark 和 simulator-exploit regression suite。

## 11. 最终建议

当前最值得做的并不是立刻决定 Transformer 大小，而是先完成 **M0–M3**：

1. 用全量回放把动作和状态字段审计清楚；
2. 让 deployment transition 对 `units_fight` 精确重放；
3. 扩充 BattleOutcome，尤其是按阵营扣血和逐卡经验回写；
4. 做一个固定候选也能连续跑到终局的 env v0。

然后再做 tokenization。这样 token 语言描述的是一个已经经过验证的规则世界，而
不是把尚未确定的游戏语义固化进训练数据。第一批模型建议是
`pi_BC + V_battle_sim + V_battle_real`，随后通过 cheap simulator 做候选搜索和
单回合策略改进。只有当这条链路能够稳定提升策略且没有明显 simulator exploit，
再进入多回合 self-play。
