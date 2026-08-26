# Transition 与序列化 Schema 验收规划

> 本文承接 [`rl-roadmap.md`](rl-roadmap.md) 中的 `battle_field`、`action` 和转移函数
> 设计。目标不是一次性把 XML 改写成最终 token，而是先冻结一套可验证的结构化语义，
> 再将它规范序列化为适合 Transformer 的语言，并用真实回放证明 transition 可用。
>
> 当前目标范围为固定游戏版本下的 Normal 1v1。`round 0 -> round 1`、终局、2v2 和
> 无法恢复候选列表的回合必须单独分桶，不得混进普通回合的总准确率。
>
> 对应的逐文件、逐提交施工清单见
> [`transition实现任务书.md`](transition实现任务书.md)。

## 1. 最终要证明什么

需要证明的不是“单位数量大致相同”，而是以下四件事同时成立：

1. **Schema 完整**：XML 中属于环境状态和动作语义的字段没有被静默丢失；
2. **序列可逆且唯一**：同一结构化状态生成同一规范序列，序列可以无损解析回来；
3. **Transition 正确**：`state_i + actions_i + battle_result_i` 能逐字段得到
   `state_(i+1)`；
4. **接口可用于 RL**：合法动作 mask 与 transition 一致，非法动作可审计，保存/恢复
   后轨迹可复现，模型不需要学习本来确定的规则。

完成判定写成：

```text
XML_i
  -> parse -> StructuredState_i
  -> serialize -> tokens -> deserialize
  -> deploy(actions_i)
  -> settle(battle_result_i)
  -> PredictedState_(i+1)
  == parse(XML_(i+1))
```

其中整数、枚举、集合和离散坐标应精确相等。只有 XML 原本就是连续小数的字段才允许
使用预先声明的容差。

### 1.1 当前基线结论

现有回放调查已经提供了几条很强的证据：普通 1v1 中相邻回合 `reactorCore` 与对手
`FightReport.Score` 的 13,222 个可对齐玩家回合全部精确匹配；`10004` 的 337 个
可对齐增援样本和 `32301` 的 4 个样本都在下一回合进入并持续保留在 `officers`。
这些证据支持“下一回合快照是累计状态”以及当前 battle 三字段白名单假设。

但仓库当前的 `build_units_fight()` 只重放了 Buy/Undo/Move/Upgrade 的部分单位语义，
没有形成完整经济、累计状态、CD 和 settlement transition。因此当前状态应视为
**验收数据源基本成立，transition 尚未达到 G1/G3**，不能仅凭已有派生
`units_fight` 宣称可用于 RL rollout。

## 2. 不把 transition 做成一个黑盒

路线图中的总接口可以继续写成：

```text
o' = T(o, A0, A1, battle_result)
```

实现与测试时必须拆成三层：

```text
deploy_transition(player_state_i, offer_i, canonical_actions_i)
    -> pre_battle_player_state_i + action_receipts

battle_transition(pre_battle_state_i, seed)
    -> battle_result_i

settle_transition(pre_battle_state_i, battle_result_i, round_rules)
    -> player_state_(i+1)
```

完整环境再组合为：

```text
joint_deploy(state_i, offer_i, A0, A1)
    -> pre_battle_state_i
battle(pre_battle_state_i)
    -> battle_result_i
settle(pre_battle_state_i, battle_result_i)
    -> settled_state_i + reward + done
advance_round(settled_state_i, environment_rng)
    -> state_(i+1) + next_offer
```

这样部署错误、战斗模拟误差、战后结算错误和候选生成错误可以分别定位。未来将
`pysim` 替换成真实战场结果时，也只需要替换 `battle_transition`。

### 2.1 当前的 battle 影响白名单

先把下面内容作为 v1 待证伪契约：在相同 `state + actions + round_rules` 下，改变
`battle_result` 只允许改变：

- `reactorCore`；
- 下一回合的 `preRoundFightResult`；
- 每个持久单位的累计 `Exp`。

单位在单场战斗中死亡，不表示持久单位卡从下一回合的 `units` 中删除。`supply`、
单位集合、位置、科技、专家、蓝图、装备和技能 CD 默认属于 action/round tick 的结果，
不是 battle result 的结果。

这是一条需要用全量回放验证的假设，不是允许写死后忽略反例的结论。若出现可复现的
反例，应先将字段归因到 action、round tick、初始化或 battle，再版本化地扩充白名单。
`randomStateData` 和 `round 0 -> round 1` 的初始血量修正单独处理。

## 3. Schema 的分层

不要让一个 `battle_field` 同时承担环境真状态、玩家观测和战斗输入三种含义。

### 3.1 EnvironmentState：transition 的完整输入

至少包含：

- `schema_version`、`ruleset_version`、`serializer_version`；
- round、phase、地图、模式；
- 双方 `PlayerState`；
- 当前候选卡/开局候选，以及候选的可见性；
- 候选生成和确定性复现所需的环境 RNG；
- 稳定实体 ID 的分配状态；
- 终局状态。

### 3.2 PlayerState：单个玩家的长期状态

对应回放中的 `playerData`，至少覆盖：

- `reactorCore`、`supply`、`preRoundFightResult`、`IsSpecialSupply`；
- `units` 与 `unitIndex`；
- `officers`、`activeTechnologies`、`bluepints`；
- `commanderSkills`、`energyTowerSkills`、`towerStrengthenLevels`；
- `equipmentDatas`、`shop`；
- `contraptions`、`constructionSnapshotDatas` 及其 index；
- `researchQueue`；
- 玩家 RNG 状态。

第一版可以声明部分字段为 `opaque/pass-through`，但不能省略后假装已经完整支持。
每个字段必须属于以下状态之一：

```text
modeled | pass_through | observation_only | hidden_env | unsupported
```

`unsupported` 字段存在时，该样本不能计入“全状态 transition exact match”。

### 3.3 UnitCard：长期实体

每个单位卡至少保留：

- 环境内部稳定 `entity_id`；
- 回放 `Index` 和兵种 ID 的映射信息；
- 统一后的等级、累计经验；
- 位置、朝向；
- 装备、回收价、`RoundCount`、`Durability`；
- 其他会影响合法动作或战斗输入的字段。

内部 `entity_id` 用于跨回合回写经验和装备，不直接暴露给模型。模型看到的是由当前
观测规范排序后生成的局部 `handle`。

### 3.4 Observation：模型可见的 battle_field

Observation 是 EnvironmentState 的显式投影。必须规定：

- 哪些自己的/对手的字段可见；
- 候选卡是否完整可见；
- 玩家 1 是否镜像为“己方永远在同一侧”的坐标系；
- 环境 RNG、隐藏牌池和内部实体 ID 是否剔除；
- 战斗前、战斗后和部署阶段分别使用哪个 phase。

Transformer 的 `battle_field` 指 Observation 的规范序列，不应直接等同于完整
`playerData` 或 EnvironmentState。

### 3.5 Action 保留 raw 与 canonical 两层

- `RawActionLog`：忠实保存 XML 中的 UI 行为，包括中间移动、Undo、Cancel 和时间戳；
- `CanonicalActionPlan`：表达最终意图，供行为克隆和 RL 使用。

Raw 到 canonical 的转换必须可审计。例如多次移动同一单位只保留最终位置，Undo
抵消对应操作；顺序相关的动作保留依赖关系，不可为了排序而改变语义。

购买单位时，模型只能创建本 action plan 内的临时引用 `new_k`：

```text
BUY_UNIT [mech_id, new_ref] [x, y]
MOVE_UNIT [new_ref, rotate] [x, y]
```

永久 `entity_id` 由 transition 确定性分配，不能由模型发明。

## 4. `keyword + int list + float list` 序列语言

### 4.1 建议语法

第一版可以采用一条记录一个 keyword：

```text
record := KEYWORD "[" int* "]" "[" float* "]"
sequence := record* END
```

示意：

```text
SCHEMA [1 2207 1] []
ROUND [4 1 0 0] []
PLAYER [0 3940 4500 850 1 0] []
UNIT [0 10 2 287 0 0 100 3 0] [-120.0 -180.0]
OFFICER [10004] []
TECH [10 702] []
COMMANDER_SKILL [0 31 1 2] []
TOWER_STRENGTH [0 2] []
END_PLAYER [] []
END_STATE [] []
```

动作示意：

```text
BEGIN_ACTIONS [0] []
CHOOSE_REINFORCE [2 10004] []
UNLOCK_UNIT [10] []
BUY_UNIT [10 0] [-40.0 -160.0]
UPGRADE_UNIT [0] []
MOVE_UNIT [0 1] [-20.0 -140.0]
BUY_TECH [10 702] []
END_DEPLOY [] []
```

这里的数字位置必须由 keyword registry 固定定义。例如 `UNIT` 的第 3 个整数永远是
level，不能在另一个版本里悄悄改成 exp。任何字段增删都必须增加 schema version。

### 4.2 记录设计规则

1. keyword 决定 int/float 的数量、顺序、单位和枚举表；
2. 布尔值写成 `0/1`，枚举写成稳定整数 ID；
3. set/map 拆成多条记录并规范排序，不把可变长度对象塞入一个不透明 list；
4. 多目标技能用 `CAST_BEGIN`、若干 `TARGET_*`、`CAST_END`，避免参数含义依赖长度；
5. 时间戳只属于 RawActionLog，默认不进入 CanonicalActionPlan；
6. 缺失值必须有明确 sentinel，且 sentinel 不得与合法值重叠；
7. 不允许 NaN、Infinity、科学计数法和依赖 locale 的小数格式；
8. 每个序列显式携带 schema/ruleset/serializer version。

### 4.3 float 与 tokenizer

Schema 层为了与 XML 无损对拍，可以暂时保留 `x/y` 等 float；模型层不应让通用 BPE
随意拆小数。冻结 tokenizer 前应二选一：

- 若位置实际落在离散格点，将坐标转成 `<x_i>`、`<y_j>` 或整数 grid index；
- 若必须保留连续值，规定定点精度和数位编码，例如乘 `1000` 后作为整数序列。

状态 round-trip 使用规范化后的精确值；只有原始解析对拍允许例如 `1e-6` 的显式容差。

### 4.4 规范顺序与消歧

State 记录建议按以下顺序生成：

```text
版本/全局 -> player 0 -> player 1 -> offer -> END
```

每个玩家内部：

```text
标量 -> 单位 -> 专家 -> 科技 -> 技能/CD -> 装备 -> 建筑/装置 -> END_PLAYER
```

集合全部按语义字段排序。单位先按兵种、规范坐标、等级、经验、装备等可观测字段排序，
再分配 observation-local handle。不可用内部随机 ID 打破平局，否则会泄漏无语义信息。
两个单位在所有可观测属性上都相同时，本来就观测等价，不强行制造区别。

CanonicalActionPlan 采用“依赖图 + 稳定拓扑排序”，大类顺序建议为：

```text
候选选择
-> 解锁/科技/蓝图/塔强化
-> 买兵/升级/装备
-> 最终布阵
-> 战场技能/装置
-> END_DEPLOY
```

购买必须先于对 `new_k` 的引用；真实顺序会影响费用或合法性时必须保留原依赖。

## 5. 字段归属与允许修改者

每个字段只能由明确的一层修改。v1 的预期归属如下：

| 字段 | deploy/action | battle result | round advance | 验收方式 |
|---|---:|---:|---:|---|
| `reactorCore` | 否 | 是 | 开局特例 | 对手 `FightReport.Score` 与下一快照 exact |
| `preRoundFightResult` | 否 | 是 | 初始化特例 | 下一快照 exact |
| 单位 `Exp` | 升级规则可能读取 | 是 | 否 | 按稳定实体逐个 exact |
| `supply` | 是 | 否 | 收入/弃选金 | 金额与逐 action delta exact |
| 单位集合 | 购买等 | 否 | 特殊召唤另列 | 实体集合 exact |
| level/位置/朝向/装备/卖价 | 是 | 否 | 规则另列 | 逐实体逐字段 exact |
| officers/tech/blueprints | 是 | 否 | 累计状态 | 集合 exact |
| 技能 active/CD | 使用动作 | 默认否 | 是 | `(id,index,active,cd)` exact |
| 塔强化/建筑/装置 | 是 | 待逐机制确认 | tick/持久化 | 实体与字段 exact |
| RNG | 否 | battle RNG 单列 | 是 | state digest/消费次数 exact |

如果实现中某层写了不属于它的字段，测试应立即失败，而不是等最终 state diff 才发现。

## 6. 回放 oracle 与 fixture 设计

### 6.1 每个普通回合形成的验收样本

```text
input:
  original playerData_i
  original actionRecords_i
  original MatchSnapshotData_i / ruleset metadata

battle oracle:
  original FightReport_i

expected:
  original playerData_(i+1)
```

fixture 必须保存 replay 文件 hash、玩家、round、游戏版本、解析器版本和原始 XML 定位，
使每一个 diff 都能回到证据源。

### 6.2 避免循环验证

当前 `tools/replay2json.py` 中的 `units_fight` 是由 `build_units_fight()` 根据 action
推导的派生字段，不是原始 XML 自带的开战前快照。因此：

- 不得用同一 transition 实现生成 `units_fight`，再拿它作为 expected 验证自己；
- `units_fight` 可以作为方便查看的缓存，但不能单独充当独立 oracle；
- deployment 中间态应由 action 语义 golden tests、FightReport 参战实体、下一回合
  持久状态和端到端 state diff 共同验证；
- 为每种 action 至少保留人工核对的最小 XML golden fixture，稀有 action 全部保留。

### 6.3 样本分桶和排除规则

至少按以下维度单独报告：

- replay 游戏版本/ruleset；
- round 0 初始化、普通回合、终局；
- 1v1、2v2/其他模式；
- action 类型与 action 组合；
- 专家、增援、装备、技能、建筑机制；
- 是否有完整 FightReport、候选和下一回合快照；
- `supported`、`pass_through`、`unsupported`。

排除必须使用预先定义的 reason code，并同时报告数量和占比。不能在看到 diff 后临时
删掉样本来提高准确率。

## 7. 核心验收指标

### 7.1 数据与 schema 指标

| 指标 | 定义 | Gate |
|---|---|---:|
| XML 字段登记率 | 已登记字段路径 / 全量出现字段路径 | 100% |
| Raw action 解析率 | 能无损保存 type 与字段的 action / 全部 action | 100% |
| Structured round-trip | `decode(encode(x)) == x` | 100% |
| Canonical 幂等率 | `canon(canon(x)) == canon(x)` | 100% |
| 序列规范唯一率 | 同一 canonical state 得到相同字节序列 | 100% |
| 未知 ID 率 | 无版本登记的兵种/技能/科技/专家 ID | 0；否则显式 unsupported |
| float 非法率 | NaN/Inf/非规范格式 | 0 |

Schema round-trip 只证明表示没有丢信息，不证明 transition 正确，两类指标必须分开。

### 7.2 Action 覆盖与执行指标

同时报告下面四个数：

```text
action_type_coverage
action_occurrence_coverage
fully_supported_round_rate
fully_supported_replay_rate
```

每个 action 的执行结果必须返回：

```text
accepted | rejected
reason_code
resource_delta
created_entity_id (如有)
state_digest_after_action
```

验收要求：

- 声明支持的合法回放 action 接受率 100%；
- 未声明支持或非法 action 不得 silent ignore；
- transition 与 legal mask 对每个 action prefix 的判断一致率 100%；
- rejected action 除审计日志外不得修改状态；
- action-local ref 不可重复、不可引用未来实体、不可跨 plan 泄漏；
- RawActionLog 折叠后的 canonical plan 与 raw 重放最终状态一致率 100%。

### 7.3 单位指标

单位不能只比较数量。先按稳定实体 ID 对齐；回放侧使用已经验证的
`(player, unit Index)` 生命周期映射。至少报告：

- 单位集合 precision / recall / F1；
- 单位数量 exact-match rate；
- 兵种 ID、level、Exp、装备、朝向、卖价逐字段 exact-match rate；
- x/y 在规范格点上的 exact match，或声明容差内 match；
- **完整 UnitCard exact-match rate**；
- **整回合所有单位 exact-match rate**；
- 首个错误 action index 和首个错误字段。

完成 gate 是受支持样本的实体集合和所有 modeled 字段 100% exact。F1 只用于诊断，
不能代替完成标准。

### 7.4 经济指标

至少报告：

- 每个 action 的 expected cost、actual delta 和 reason；
- 回合结束 `supply` exact-match rate；
- `predicted_supply - replay_supply` 的分布；
- 负资源次数、免费购买次数、重复扣费次数；
- 回合收入、弃选金额、专家/卡牌修正的分项账本。

受支持样本要求最终 supply 和每一笔可观测 delta 100% exact，资源不变量违反数为 0。

### 7.5 累计状态与技能指标

以下内容都比较“集合内容 + 元素字段”，不能只比较数量：

- officers exact set；
- active technologies/tech chains exact set；
- blueprints exact set；
- equipment inventory 与装备归属 exact；
- commander skill 的 `(index,id,isActive,coolingRound)` exact；
- energy tower skill 和 tower strengthen exact；
- construction/contraption 的实体、位置、状态和 index exact。

每类还需报告“新增、保留、删除”三种 transition 的准确率，防止累计列表每回合被错误
重建或丢失。

### 7.6 Battle settlement 指标

对普通 1v1 eligible round：

```text
hp_next[p] = hp_before[p] - opponent_report.score
fight_result_next[p] = outcome_from_perspective(p)
exp_next[entity] = exp_before[entity] + exp_delta[entity]
```

至少报告：

- `reactorCore` exact-match rate；
- `preRoundFightResult` exact-match rate；
- 逐实体 Exp exact-match rate与整回合 Exp exact-match rate；
- 找不到实体、重复映射、Exp 倒退次数；
- battle influence whitelist violation count。

前三项在 eligible 数据上都必须达到 100%，whitelist violation 必须为 0。若真实
FightReport 无法提供逐单位经验，则该部分用下一回合原始快照作 oracle，并明确无法将
总差异进一步归因到具体击杀事件。

### 7.7 端到端 next-state 指标

最终必须报告两种口径：

1. `modeled-field exact`：所有已建模字段是否完全一致；
2. `full-state exact`：modeled + pass-through 字段是否完全一致。

并提供：

- 玩家级 exact-match rate；
- round pair 级双方同时 exact-match rate；
- replay 级所有普通回合同时 exact-match rate；
- 每个字段的 mismatch count；
- first-divergence reason 分布。

确定性外围规则的目标是受支持样本 100% exact，不使用“平均 99%”掩盖某个机制必错。

## 8. 性质测试与运行稳定性

除回放对拍外，还必须有以下 property tests：

### 8.1 确定性与恢复

- 相同 state、actions、battle result、seed 重复运行，输出 digest 完全相同；
- `save -> load -> continue` 与不中断轨迹逐步相同；
- serializer/parser 不改变 RNG 消费次数；
- transition 不修改输入对象。

### 8.2 对称性

- 交换 player 0/1 并做规定坐标镜像，transition 结果应相应交换；
- 双重镜像恢复原 canonical state；
- reward 满足 `r0 = -r1`。

### 8.3 状态不变量

- entity ID 全局唯一且单调/确定分配；
- 单位引用均存在；
- 坐标、部署区、朝向合法；
- 等级和经验满足版本规则；
- supply 不因合法 action 变负；
- phase 单向合法流转，终局后不可继续 step；
- battle 不能删除普通持久单位卡；
- 改变 battle result 时，白名单外字段保持不变。

### 8.4 Fuzz 与长轨迹

- 按 legal mask 随机采样至少 1,000 个完整 episode；
- 无 crash、hang、NaN、重复 ID、非法 phase 和状态泄漏；
- 对随机 action prefix，mask 与 transition 接受/拒绝完全一致；
- 对结构化 state/action 做 parser fuzz，非法序列必须给稳定错误码，不能部分执行。

## 9. 分阶段 Gate

### G0：Schema 可冻结

- 全量 XML 字段和 action 类型完成 registry；
- Raw parse 与 structured/token round-trip 100%；
- canonical serializer 幂等且确定；
- ruleset、坐标、等级、ID、缺失值语义已版本化；
- 不支持字段全部显式标记。

### G1：Deployment Transition v0

- `BuyUnit`、`MoveUnit`、`UpgradeUnit`、`UnlockUnit`、`UpgradeTechnology` 等核心动作
  单类支持率 100%；
- 核心 action occurrence coverage 至少 95%，并同时公布 fully-supported round rate；
- 支持样本上单位、位置、等级和对应资源变化 100% exact；
- legal mask 一致率 100%，silent ignore 为 0；
- 每类 action 有独立 XML golden fixture，不依赖派生 `units_fight` 自证。

### G2：Settlement Transition v0

- 普通 1v1 的 HP、fight result、逐单位 Exp 在 eligible round 上 100% exact；
- battle influence whitelist violation 为 0；
- round 0、终局和缺 report 样本单独报告；
- 全量语料的支持率与正确率分开输出。

### G3：Replay Transition 可用

- 目标 ruleset 中 fully-supported round rate 至少 95%；
- 支持 round 的 modeled-field exact 和 full-state exact 都为 100%；
- 任何剩余不支持机制均有 reason code、样本数和 feature flag；
- 同 replay 中连续多回合 rollout 不依赖重新读取中间 XML 快照仍能逐回合 exact；
- 保存/恢复、确定性、对称性和 1,000 episode fuzz 全部通过。

达到 G3 才可以称 transition 对目标范围“可用于生成 RL trajectory”。95% 是覆盖率
门槛，不是正确率门槛；确定性规则在声明支持的样本上仍要求 100% exact。

### G4：Transformer 序列可用

- 全量受支持语料 `structured -> sequence -> structured` 100% round-trip；
- action grammar mask 与 environment legal mask 对所有 prefix 100% 一致；
- unknown token 为 0；
- 坐标/ID/金额不会被不稳定子词拆分；
- 报告 observation、action 和完整 trajectory 的 token 长度分布及截断率；
- canonical action 执行后与原 raw action 的最终状态 100% 等价。

## 10. 验收报告格式

每次运行应同时产出人读 Markdown 和机器读 JSON。JSON 至少包含：

```text
run_id
git_commit
schema_version / ruleset_version / serializer_version
corpus_hash / replay_count / round_count
eligibility_counts + exclusion_reason_counts
action_type_counts + action_coverage
field_match_counts
unit/economy/skill/settlement metrics
full_round_exact counts
first_divergence buckets
property_test results
representative failing fixture ids
```

CI 中保存失败样本的最小 state diff，例如：

```text
replay=... player=1 round=5 action_index=7 action=USE_EQUIPMENT
path=players[1].units[entity=42].equipment
expected=301 actual=0
reason=unsupported_equipment_effect
```

总览表必须同时展示 numerator/denominator。只显示百分比会隐藏样本量和排除规模。

## 11. 推荐实现顺序

1. 建立 XML path/action keyword/字段类型 registry；
2. 定义结构化 `EnvironmentState`、`PlayerState`、`UnitCard`、Raw/Canonical Action；
3. 实现 state diff、digest、canonical serializer 和 round-trip tests；
4. 从全量回放生成不可变 fixture 索引，不复制当前 transition 产物为 oracle；
5. 先实现核心 deployment action，并逐 action 返回 receipt；
6. 实现 supply 分项账本、累计状态和 CD tick；
7. 接入 FightReport，完成 HP/result/Exp settlement；
8. 做跨多回合 rollout 与 property/fuzz tests；
9. 达到 G3 后冻结 `keyword + ints + floats` schema v1；
10. 最后设计 numeric codec/tokenizer 和 grammar/legal mask，达到 G4。

## 12. Definition of Done

Transition v1 完成时，应能对任意一条目标范围内的回放回答：

- 输入状态包含什么、哪些是隐藏环境状态、哪些是模型观测；
- 每个 action 是否合法、花了多少钱、修改了哪些字段；
- 新单位如何获得稳定 ID，后续动作如何引用；
- battle result 为什么只修改允许的字段；
- 任意 next-state diff 最早从哪个 action/settlement rule 开始；
- 该样本是否受支持，若不支持，具体被哪个机制阻塞；
- 相同输入是否可复现出完全相同的序列、状态和后续轨迹。

只有这些问题都有机器可检查的答案，transition 和 schema 才算真正建立完成。
