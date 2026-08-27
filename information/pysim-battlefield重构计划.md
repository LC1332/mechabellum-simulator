# PySim Battlefield 重构计划：从可运行闭环到机制完备

> 本文承接 [`transition实现任务书.md`](transition实现任务书.md) 与
> [`transition-v0.1正规化任务书.md`](transition-v0.1正规化任务书.md)，并以
> **2026-08-27、commit `314e597`（前端 Step 3 完成）后的当前代码**为准重新审计
> `pysim`、`pysim/transition` 和审计游戏。
>
> 两份旧任务书中的 v0/v0.1 总结仍然有效，但不能直接当作当前能力表：此后仓库又补了
> 开局 catalog、能力分类器、部分蓝图、装置、能量塔技能和战场技能；Step 3 又完成了
> 装备的 transition/state 链路、统一报价、科技购买和 typed 技能释放。另一方面，当前
> 分类器仍会把部分只有“事件已接入”、但数值仍是 provisional 的机制标成 `exact`。
> 因此本计划统一使用“动作解析、合法性、经济、持久状态、战斗效果、结算”六段闭合，
> 并将“是否实现”与“证据置信度”分开。

## 0. 结论先行

当前 `pysim` 已经不是“缺一个 transition”的阶段，而是进入了三个问题互相放大的阶段：

1. `pysim/engine.py` 已超过 4,000 行，单位、科技、专家、塔、建筑、装置、战场技能和
   召唤机制共享一套 numpy SoA 与大量开关，继续直接往主循环加机制会越来越难审计；
2. Step 3 已让装备完成“获得 → 库存 → 绑定 → 持久化 → warning”的 transition 链路，
   它不再阻塞 runtime；但 **24 张装备卡 + 次级增幅核心共 25 个装备 ID 的战斗效果仍为
   0/25**，现在是 strict-effect fidelity 的主要缺口；
3. 现有八库 benchmark 冻结了非装备战斗基线，却没有装备字段。它能证明重构没有破坏
   旧场景，不能证明新增装备、状态或技能效果准确；必须同步补机制专项 oracle 库。

建议采用“契约先行、旁路抽取、逐机制接管”的重构，而不是一次性重写战斗引擎：

- 先冻结现有结果与公开 API；
- 把战场输入、效果注册、事件、状态效果和输出从 `Battle` 中抽出；
- 先让现有 `battle_adapter.py` 经过版本化 compiler/registry 再进入旧 `Battle`；
- 优先完成高频四件静态装备的 battlefield 链路，覆盖语料中 50.4% 的装备选择；
- 再补齐技能、装置、专家、建筑和连续环境缺口；
- 最后才拆分移动、索敌、弹道、伤害等高风险热循环。

## 1. 已有基线与正确解读

### 1.1 已经完成的基础设施

以下内容不应在重构中推倒重来：

- raw 回放到 `rounds_norm.json` 的撤销正规化、动作原子化、溯源和确定性输出；
- `unitIndex` 顺序计数器、稳定 `entity_id`、买入/授予分配、出售烧毁；
- 买兵、移动、升级、解锁、科技、回收、结束部署等核心 transition；
- 结构化状态、receipt、reason code、资金账本、digest、save/load；
- `EnvironmentState -> BattleOutcome -> settlement -> advance_round` 闭环；
- 玩家历史动作反事实回放和 RandomLegalPolicy episode；
- 开局队伍 catalog、历史对手能力扫描和 `/game` 审计界面；
- 当前引擎的单位、科技、专家加成、塔、建筑、部分装置/战场技能、召唤与经验机制；
- Step 3 的 `PriceQuote`、专家解锁/科技折扣、场上兵种科技购买、三项费用修正；
- Step 3 的 25 项 `EquipmentDef`、库存多重集、typed `USE_EQUIPMENT`、opening/replay/
  save-load/GameView 链路，以及逐装备 ID 的 battle approximation warning；
- Step 3 的 typed `RELEASE_COMMANDER_SKILL`、正确技能 ID 映射、专家定时技能/装备发放；
- runtime playable prefix 与 strict-effect prefix 两轴能力报告。

对应的现有边界主要位于：

- [`pysim/transition/model.py`](../pysim/transition/model.py)
- [`pysim/transition/deploy.py`](../pysim/transition/deploy.py)
- [`pysim/transition/battle_adapter.py`](../pysim/transition/battle_adapter.py)
- [`pysim/transition/capability.py`](../pysim/transition/capability.py)
- [`pysim/engine.py`](../pysim/engine.py)

### 1.2 当前指标不能混为一个“准确率”

| 指标 | 当前结果 | 它证明了什么 | 它没有证明什么 |
|---|---:|---|---|
| 正规化 unresolved refs | 48/20282 = 0.24% | 动作引用与撤销流基本可用 | 这些动作的游戏效果都已实现 |
| 干净回合 unit-set exact | 99.03% | 核心部署单位集合接近回放 | 资金、装备、技能、战斗完全正确 |
| settlement oracle HP/胜负/经验 | 100% | 给定真实 FightReport 后结算公式正确 | pysim 自己能生成真实 FightReport |
| supply exact | 36.6% | 当前真实收入模型尚有隐藏变量 | 不能用注入资金掩盖缺口 |
| 八库 battle winner | 1793/2349 ≈ 76.3% | 当前 pysim 战斗保真度基线 | 单位伤害、技能与复杂阵容已足够精确 |
| 装备 transition/state | 25/25 ID 已登记并可绑定 | Step 3 链路不再阻塞 runtime | 不代表这些装备已在战斗中生效 |
| 装备 battle effect | 0/25 | warning 能诚实暴露近似 | 任一装备数值或触发准确 |

因此本轮验收必须继续把 deployment、battle、settlement、episode 四层指标分开；八库
winner、装备/技能专项 oracle、机制覆盖率也不能合并成一个“总体准确率”。

## 2. 重构后的职责边界

### 2.1 三层模型

```text
Match / Transition（跨回合持久规则）
  资金、商店、装备库存、单位装备、科技、专家、技能库存/CD、建筑耐久、HP
                           │
                           ▼
Battlefield Compiler（纯编译层）
  EnvironmentState + 本回合事件 -> BattleInput + EffectSpecs
                           │
                           ▼
Battle Engine（单场瞬时模拟）
  移动、索敌、攻击、弹道、伤害、状态、召唤、塔/建筑/装置 -> BattleOutcome
```

硬约束：

1. transition 不访问 `Battle` 私有数组；
2. engine 不读取 replay/XML/raw action；
3. battle compiler 不修改持久状态；
4. settlement 只消费版本化 `BattleOutcome`；
5. 一个机制只有六段闭合后才能标记 `effect_complete`，禁止“只扣费也算支持”。

### 2.2 新增公开契约

建议新增 `pysim/battlefield/`，先包住旧引擎，不立即搬动热循环：

```text
pysim/battlefield/
  model.py             BattleInput、UnitBattleInput、WorldObject、TimedEvent
  outcome.py           BattleOutcomeV2、EntityOutcome、ObjectOutcome、digest
  compiler.py          EnvironmentState -> BattleInput
  registry.py          机制 ID -> EffectSpec + 支持度
  effects/
    equipment.py       装备定义与 modifier/event 编译
    technology.py      科技效果编译
    officer.py         专家/强化卡效果编译
    skill.py           战场技能、能量塔技能、装置
    structure.py       塔和建筑
  legacy_engine.py     对现有 Battle 的兼容调用，迁移期使用
```

第一版建议冻结以下对象：

```python
BattleInput(
    ruleset_version,
    engine_version,
    seed,
    units: tuple[UnitBattleInput, ...],
    world_objects: tuple[WorldObject, ...],
    events: tuple[TimedEvent, ...],
)

UnitBattleInput(
    entity_id,
    side,
    mech_id,
    level,
    exp,
    position,
    rotation,
    tech_ids,
    equipment_id,
    effect_ids,
    spawn_at,
)

MechanicSupport(
    decode,
    legality,
    economy,
    persistent_state,
    battle,
    settlement,
    confidence,       # verified / provisional / unsupported
    evidence,
)
```

`pysim.engine.Battle` 与 `battle_from_units()` 在迁移期继续保留；新实现通过 façade 转发，
直到 benchmark 和 API 完全等价后再决定是否移动旧入口。

### 2.3 持久状态去 raw 化

当前以下字段仍是 raw tuple 或弱类型数据，应逐步替换：

| 当前字段 | 目标类型 | 需要补充的语义 |
|---|---|---|
| `equipment_inventory` + `UnitCard.equipment_id` | `EquipmentInventory` + `EquipmentSlot` | Step 3 已完成获得/绑定/替换/持久化；仍需 typed 数量、出售与特殊跨回合效果 |
| `commander_skills_raw` | `CommanderSkillState` | 库存槽、是否可用、CD、次数、目标类型 |
| `constructions_raw` | `ConstructionState` | 稳定 ID、类型、位置、耐久/存活、跨回合更新 |
| `tower_mods_raw` | `RoundEffectState` | 来源、层数、持续时间、叠加规则 |
| `devices_raw` | `WorldObjectState` 或本回合事件 | 类型、位置、拥有者、是否持久 |
| `skill_events_raw` | `TimedBattleEvent` | 技能 ID、落点、目标、触发时刻 |
| `officers` 混合列表 | 专家、单位强化卡、蓝图 buff 分栏 | 避免经济/战斗/部署效果互相误判 |

旧字段先由 adapter 兼容读写一个 schema 版本；不在同一提交里删除旧格式。

## 3. 机制缺口总表

### 3.1 P0：当前会阻塞严格效果、造成半效果或破坏规则一致性

Step 3 已完成的 `20003` 科技 `-50`、装备 E1a、统一报价和 typed 技能释放不再列为
未完成项。当前 P0 是：

| 机制 | 当前状态 | 缺口 | 计划 |
|---|---|---|---|
| 单位装备战斗效果 | 25 个 ID 的 transition/state 完成，battle adapter 只输出 warning | 0/25 modifier/event/trigger；特殊装备还跨 economy/placement/settlement | 见 §4，先完成高频四件，再按共享管线分组 |
| 支持度/置信度 | 已有 transition × battle 两轴 | `COMMANDER_SKILLS` 中含 `cal` 的 provisional 数值仍被 capability 标为 `exact`；增援卡仍有类别级判断 | registry 按 `(mechanism, id, effect)` 输出六段状态 + confidence + evidence |
| 技能库存/CD | typed release 和专家定时发放已完成 | 释放后不消费次数、不更新 active/CD，`advance_round()` 不 tick | 在扩更多技能前先完成 typed `CommanderSkillState` 生命周期 |
| 专家 10004 额外部署位 | 卡牌会持久化 | `BASE_BUY_LIMIT` 未读取该效果 | 加入 deployment modifier，并用回放 BuyCount 探针冻结时序 |
| 专家 10007/10008 装置强化 | 卡牌会持久化 | 护盾 +40%、飞弹伤害 +200% 未传给装置事件 | effect registry 编译装置参数时应用 |
| 专家 10009 快速传送 | 旧 replay checker 支持侧翼延迟减半 | `battle_from_state()` 未传 `spawn_at`，连续 env 丢失该机制 | 把 flank unlock/延迟变成持久规则与 UnitBattleInput 字段 |
| 装备跨层规则 | 强化模块/部署模块/统御核心都可绑定 | 升级费 `-100`、每回合移动、每回合 `+50` 与死亡全灭均未生效 | 不等静态 25 件全做完；分别接 economy、PlacementRules、round tick/death trigger |
| 蓝图 3 精英征召 | 当前 deploy 会令其后的购买等级 +1 | 与 v0.1 §10 的语料结论“蓝图 2/3 均不提升购买等级，+1 只来自精英专家/精英卡”冲突 | 先做定向 fixture 复核；裁决前降为 provisional，不得宣称完整支持 |
| 单位升级经验 | deploy 允许低经验升级，legal candidates 要求经验足够，历史对手又会先补经验 | 三个入口的规则不一致，且用户 Q13 的纠正尚未形成统一规则 | 分离真实玩家 legality 与反事实历史 override，并将经验消费/升级时点版本化 |
| `GiveUp` | 当前仅接受为“无部署效果 marker” | transition 本身不立即终局，仍可能进入战斗 | 新增 typed `SURRENDER`，原子进入 TERMINAL，双方视角与 reward 明确 |
| 布阵合法性 | 买入已检查己方半场，移动仍主要是全图 bounds | 缺侧翼区、占位/重叠、旧单位移动限制、部署模块/再部署权限 | 建独立 `PlacementRules`，legal mask 与执行共用 |

### 3.2 P1：战场外围机制未完整覆盖

| 机制 | 已有部分 | 未闭合部分 |
|---|---|---|
| 装置 | 10001 飞弹、20001 护盾有 provisional battle event | 30001 未映射；射程/CD/HP 等仍有校准值；先进装置专家未生效 |
| 能量塔技能 | 5 射程、6 移速已接入，费用/叠加/本回合重置已完成 | 1/3/4 未映射；5/6 的效果数值与范围仍需证据表 |
| 战场技能 | 正确 ID 的导弹、燃烧、护盾和两类召唤已接入 | 现有五项仍含 provisional 数值；轨道轰炸、核弹、闪电、离子、标枪、酸液、烟雾、EMP、光子、犀牛/战舰/火神、移动信标仍缺失 |
| 再部署 | 有历史动作与前端入口基础 | 目标单位当回合移动权限、与侧翼传送/部署模块的交互未成为统一规则 |
| 建筑 | engine 可模拟 cid 1–4，opening/replay 可加载快照 | transition 不根据 `ObjectOutcome` 更新建筑存活/耐久；`constructionIndex` 尚未成为稳定对象引用；召建/修复/替换动作未闭合 |
| 塔 | 强化等级和战斗瘫痪已实现 | 能量塔技能缺表；塔/建筑/装置在真实扣血 Score 中是否计分仍需独立确认 |
| 蓝图 | 1/2/3/4/5/401/501 已有部署语义 | 批量/精英的购买上限与等级口径需要继续用合法性样本验证；效果完成度应由 registry 给出，不再靠 raw type 白名单 |
| 单位强化卡 | 多数静态数值由 officer 表进入 battle，部分价格修正已实现 | 特殊行为型强化、作用目标、同类叠加/替换、出售价格变化仍需逐 ID 核验 |
| 特殊科技/单位 | 大部分 gamedata 科技与行为科技已进入 engine | 4001 族约 340 次仍是 `UNSUPPORTED_RULE_DATA`；复杂科技叠加仍是 s26 的主要误差来源 |

### 3.3 P1：连续 Match 规则未闭合

1. **经济隐藏变量**：约 25% 的 1v1 回合仍出现模型资金不足级联，`supply_exact_rate`
   只有 36.6%；需要官方数值表、游戏内对照实验或回放新增字段，不能继续拟合硬补。
2. **完整 shop / 增援候选 RNG / 隐藏牌池**：当前候选主要来自回放注入；开局另外三个
   选项是 catalog 上的确定性生成，不等同于还原游戏 RNG。
3. **开局**：29 组 package 已可执行，但应继续核验每组 units、officers、HP、初始
   解锁、技能库存、建筑和初始装备；开局生成器与真实候选分布要分版本命名。
4. **回合事件**：延迟赠礼已建表，但残余约 0.3% 额外 spawn 仍无归因。
5. **投降与回放耗尽**：需要统一为 terminal reason，而不是 runner/session 各自判断。

### 3.4 明确非目标

- 2v2、非标准 1v1、特殊模式与特殊地图，本计划不实现；
- 2119/2203/2207 以外版本的数据 migration；
- 真正从空状态生成 shop、开局与全部外生随机事件；
- PettingZoo/Gymnasium wrapper 与训练侧 tokenizer，不应反向污染核心规则。

## 4. 单位装备专项实施计划

装备是最适合验证新架构的第一类机制：它同时经过“增援选择 → 库存 → UseEquipment →
单位持久状态 → battle modifier/event → settlement/下一回合”，能够检验六段闭合是否有效。

语料表见 [`增援卡牌-回放全量信息.md`](增援卡牌-回放全量信息.md)：增援池中共 24 种，
被选择 2318 次；另有增幅专家发放的 `13030009` 次级增幅核心，因此 registry 的完成
分母是 **25 个装备 ID**，而“卡牌选择覆盖率”的分母仍是 2318。高频且规则简单的四种
为激光瞄具 448、改良火控 305、重型装甲 240、速攻模块 175，合计
1168/2318 = 50.4%，适合作为第一批 battlefield 实现。

### E0：装备证据与契约

- [x] Step 3 已冻结 25 项 `EquipmentDef` 的 ID、名称、费用、目标限制和 battle warning；
- [x] 已确认 `UseEquipment` 的 EquipmentID/UnitIndex 与快照 `EquipmentID` 链路；
- [ ] 将 `EquipmentDef` 扩展为 battle 定义：静态 modifier、动态 trigger、状态免疫、
      持续时间、叠加规则、数值来源与 confidence；
- [ ] 用回放/受控游戏继续核验库存跨回合、替换去向、出售带装备单位、装备后升级，
      不把 Step 3 的 transition v1 裁决自动当作 verified 游戏真值；
- [ ] 补齐 `13030009` 次级增幅核心的明确数值证据；证据不足时保持 provisional；
- [ ] 未知规则标为 provisional/unsupported，禁止用描述猜值后宣称 exact。

### E1：compiler/registry 纵向链路

- [x] `equipment_inventory`、typed `USE_EQUIPMENT`、扣费、目标合法性、原子绑定；
- [x] normalizer/Undo、opening/replay、save/load、digest、GameView、历史对手；
- [x] 未实现装备逐 ID 输出 `battle_approximate` warning；
- [ ] `UnitBattleInput.equipment_id` 进入 `BattleInput` digest；
- [ ] compiler 只把装备 ID 编译为 registry 中声明的 `EffectSpec`，不直接改 engine 私有数组；
- [ ] legacy adapter 消费通用 modifier/event/trigger，单件完成后只移除该 ID 的 warning；
- [ ] capability、compiler、warning 使用同一 registry，不再各维护一张装备表。

### E2：静态装备首批

- [ ] 激光瞄具：射程 +20；
- [ ] 重型装甲：生命 +75%；
- [ ] 改良火控：攻击 +65%；
- [ ] 速攻模块：移速 +5、攻击 +35%；
- [ ] 第二批纯静态项：超重型装甲、增幅核心；次级增幅核心待 E0 数值取证后加入；
- [ ] 强化模块拆成 battle 攻击/生命 `+25%` 与 transition 单位升级费 `-100` 两个 effect；
- [ ] 明确装备、科技、专家、蓝图、等级之间的加法/乘法和烘焙顺序；
- [ ] 每种装备至少有无装备/有装备 A/B、等级/科技/专家叠加和 digest 确定性测试；
- [ ] 新建带真实游戏 oracle 的装备静态专项库；旧八库不含装备，不能代替本项。

### E3：护盾、恢复与免疫

- [ ] 纳米维修包、汲取模块；
- [ ] 便携式护盾、保护屏障、超级屏障；
- [ ] 光子涂层、抗干扰模块；
- [ ] 试验级巨山装甲的“每次受击阻挡 750”进入 damage pipeline，不作为生命静态值；
- [ ] 定义 EMP、引燃、酸液、退化、核心爆炸等统一 `StatusKind`，装备免疫通过
      status registry 实现，禁止在各伤害分支写装备 ID 特判。

### E4：生产、召唤、地形与特殊触发

- [ ] 坦克/野马/钢球生产线复用统一 summon scheduler；
- [ ] 深渊信标；
- [ ] 先进寄生弹药、先进酸性弹药；
- [ ] 应激电容的低血量 EMP 触发；
- [ ] 召唤物是否计分、给经验、持久化及受装备/专家影响必须写入规则版本。

### E5：跨回合/部署型装备

- [ ] 部署模块接入 `PlacementRules`：只有装备单位获得每回合再次移动权限；
- [ ] 统御核心回合开始 `+50` 进入统一 ledger，单位死亡触发己方全灭并明确胜负/计分；
- [ ] 强化模块的升级折扣同时进入报价、执行、receipt、ledger、legal candidates；
- [ ] 出售、替换、升级、强化训练和 settlement 均不丢失装备，也不错误复制装备；
- [ ] 这些跨层装备分别有多回合 episode fixture，不能只做单场 battle A/B。

### E6：装备完成 gate

- [ ] 25/25 装备 ID 都有显式 registry 状态、confidence 与证据，不存在“未知但 accepted”；
- [x] `UseEquipment` transition assignment、库存与持久归属已有 Step 3 回归；
- [ ] 已声明 supported 的装备在 legality/economy/state/battle/settlement 六段全绿；
- [x] 已知装备候选不再阻塞 runtime，未完成项仍缩短 strict-effect prefix；
- [ ] 同 seed、同装备、同输入的 BattleOutcome digest 一致；
- [ ] 不装备时八库每库 agree count 与 outcome digest 均与重构前一致；
- [ ] 装备专项 oracle 报告 winner、逐 card damage/kills/survival，不只测“数值有变化”。

## 5. Battlefield 内部重构次序

### B0：冻结行为基线

- [ ] 以 Step 3 commit `314e597` 记录 schema/ruleset/engine version；八库 winner 基线冻结为
      `1793/2349`，并冻结每库 agree count（不能只存总百分比）；
- [ ] 保存 public `result()`、`outcome_cards()`、`team_score()` 的 characterization fixture；
- [ ] 同输入同 seed 连跑两次，断言 outcome/trace digest 确定；
- [ ] 建立 old/new differential runner，输出 winner、逐 card damage/kills/survival、trace/
      outcome digest 和首个差异；
- [ ] 建立两级 gate：PR 跑快速 characterization/sentinel 集，全量 2349 场作为合并前或
      nightly gate；CI 必须先安装 `requirements.txt` 后运行完整测试，不能因缺 FastAPI
      只跑核心子集；
- [ ] 新建 equipment/skill/status 专项 oracle 库；oracle 必须来自真实游戏或冻结的外部
      真值，不得用当前 pysim 输出生成后再反过来证明自己正确。

Gate 分三类：

1. **纯结构重构 PR**：八库每库 agree count、2349 场 winner 和无关场景 outcome digest
   必须零变化；
2. **新增装备/技能 PR**：无该机制场景 digest 必须零变化，八库总分默认不得下降；当前
   八库没有装备输入，所以装备 PR 的八库结果必须完全等于 `1793/2349`；
3. **物理/数值校准 PR**：必须与结构提交分离，附专项 oracle A/B、受影响场景清单并提升
   engine version。若总分或任一库下降，先保留为 opt，不烘焙为默认；只有证据明确且经
   单独裁决后才允许接受 trade-off。

也就是说，“准确率不能掉太多”的默认执行口径是**不下降**；不能用总分持平掩盖某个库
或某类机制明显退化。

### B1：抽取输入编译与效果注册

- [ ] `battle_adapter.py` 先改为生成 `BattleInput`，再由 legacy adapter 喂给 `Battle`；
- [ ] `equipment_id`、技能/装置来源和所有 provisional effect 都进入 input digest 与 trace；
- [ ] 科技、专家、蓝图、装备、塔技能不再直接散落在 `finalize()` 条件分支；
- [ ] registry 输出统一的属性 modifier、状态免疫、定时事件、on-hit/on-death trigger；
- [ ] capability scanner 直接查询同一 registry，不再维护另一套白名单；
- [ ] registry 同时输出 `battle_fidelity` 与 `confidence`；“代码有事件”不能自动等同
      `verified`；
- [ ] effect 顺序形成版本化 pipeline，例如：基础值 → 等级 → 科技 → 专家/强化卡 →
      蓝图 → 装备 → 本回合塔 buff。

### B2：抽取世界对象与事件调度

- [ ] 塔、建筑、装置、护盾统一为 `WorldObject`，保留类别差异；
- [ ] 战场技能、装备生产线、科技召唤统一为 `TimedEvent`/scheduler；
- [ ] 召唤物获得稳定 battle entity ID，不能依赖会因插入顺序变化的 numpy row；
- [ ] `ObjectOutcome` 返回存活、剩余耐久、击杀者、计分贡献；
- [ ] transition settlement 只回写声明为 persistent 的对象。

### B3：抽取状态与伤害管线

- [ ] 统一 EMP、减速、引燃、酸液、烟雾、光子涂层、瘫痪、免疫的生命周期；
- [ ] 伤害管线明确顺序：命中 → 无视护盾规则/护盾 → 免疫/减伤 → 固定格挡/护甲 →
      生命 → 吸血/经验 → on-hit/on-death；
- [ ] 所有效果携带 source entity/effect ID，便于 trace 和 oracle diff；
- [ ] 不允许技能、装备、科技各自维护一套 duration/stacking 规则。

### B4：最后拆热循环

只在 B0–B3 稳定后，依次抽取：

1. target selection；
2. movement/facing/separation；
3. weapon cadence/projectile；
4. damage/status/death；
5. summon/event tick；
6. termination/scoring。

每次只移动一个 system，使用旧/新 differential runner 保证逐场输出不变。不要在同一个
PR 中同时“搬代码”和“修物理”。

## 6. 其余机制的建议落地顺序

### M1：修复已经被误判为完整支持的规则

- [ ] capability 从 raw type/卡牌类别白名单切换为逐 ID、逐 effect 完整度，并增加
      `confidence/evidence`；先修正当前 provisional 技能被标成 `exact` 的口径；
- [x] 专家/增援 `20003` 高效科技研发已在 Step 3 进入统一科技报价；
- [ ] 实现高频 `10004` 额外部署位（语料选择 397 次），再实现 `10007/10008` 装置强化、
      `10009` 快速传送；
- [ ] 在新增技能前先完成库存槽消费、active/CD 更新与 round tick；
- [ ] 强化模块升级折扣、部署模块移动权、统御核心收入/死亡作为独立 effect 注册；
- [ ] legal candidates 与 deploy 接受条件重新做一致性测试；当前升级候选仍看经验门槛，
      而 deploy 对历史升级采用不同口径，应拆成真实玩家 legality 与历史对手 override；
- [ ] 出售检查 `can_be_sold`，并冻结装备、精英等级和强化卡对退款的影响；
- [ ] `GiveUp` typed terminal。

### M2：高频战场技能与装置

- [ ] 先校准已经可运行但仍 provisional 的空投护盾 395、呼叫机群 327、导弹打击 281、
      地底威胁 273、燃烧弹 256；没有专项 oracle 前不升级为 verified；
- [ ] 缺失技能原则上按语料收益排序：EMP 301、犀牛来袭 289、轨道轰炸 227、再部署
      201、离子轰炸 183、轨道标枪 143、闪电风暴 142、烟雾 114、巨型 EMP 102、
      核弹 101、酸液 70、移动信标 60、光子投射 59，再处理更低频召唤；
- [ ] 实际提交按共享原语成组：先技能生命周期/codec，再静态 strike/barrier/summon，
      再 EMP/光子/酸液/烟雾状态管线，避免每个 ID 各写一套；
- [ ] 为每个技能建立 cast legality、扣费/CD、事件编译、battle effect、trace、测试；
- [ ] 能量塔技能 1/3/4 与装置 30001 先取证后实现；未知语义不因 ID 高频而猜测放行；
- [ ] 清理技能卡 ID、Release ID、SkillIndex 三套编号的映射，所有转换集中在 codec；
- [ ] provisional 数值必须显示在报告与前端，不能标记 verified。

### M3：建筑、侧翼与布阵规则

- [ ] `constructionIndex` 变为稳定对象引用；
- [ ] 建筑结果进入 `BattleOutcomeV2`，按真实规则决定下一回合是保留、损坏还是消失；
- [ ] 将 [`pysim/flank.py`](../pysim/flank.py) 的侧翼规则接入连续 state compiler；
- [ ] 建模快速传送、再部署、部署模块和“新单位/旧单位”的移动权限；这三项共用
      `PlacementRules`/round permission，不能分别在 UI、legal candidates、deploy 特判；
- [ ] 冻结 `UnitCard.round_count` 的含义，并在回合推进时按规则更新；
- [ ] 增加部署区域、侧翼区域、占位碰撞与地图 codec。

### M4：经济、shop 与开局闭合

- [ ] 通过官方表或受控游戏实验定位隐藏资金流；
- [ ] `supply_exact_rate ≥ 90%` 后才默认启用真实经济 ruleset；
- [ ] 完整实现增援候选池、等级/费用、去重、隐藏牌池与 seed；
- [ ] 开局 package、开局候选生成和真实游戏 RNG 分开版本化；
- [ ] 收入、费用、退款、快速补给债、统御核心收入全部进入同一 ledger。

### M5：引擎保真度专项

当前裸机制库较高，但官方阵容与维修标定仍低。结构稳定后再处理：

- [ ] `chaff_xsep`、雷霆 cycle、泰山多武器、熔点换靶升温、蜘蛛雷、沙虫潜地等
      [`engine-opts.md`](engine-opts.md) 中尚未烘焙开关；
- [ ] s26 复杂科技叠加和 s29cal 维修机制；
- [ ] 真实 FightReport Score、超时胜负、召唤/塔/建筑/装置计分；
- [ ] 从 winner exact 扩展到逐 card damage/kills/survival/exp 的误差指标。

这里不顺手开启 2v2、特殊地图或完整 shop RNG；它们不属于本次 battlefield 重构目标。

## 7. 测试与验收矩阵

每加入一个机制，至少同时增加以下测试：

| 层 | 必测项 |
|---|---|
| codec/normalizer | raw 字段解析、Undo 后工件、ID 映射、未知字段拒绝 |
| legality | 合法/非法目标、资源、区域、库存、CD；拒绝时 digest 不变 |
| economy | cost/refund/income ledger exact，不能只断言最终 supply |
| state | 持久字段更新、round tick、save/load、schema migration |
| compiler | state 精确编译为 unit/world-object/event/effect |
| battle | 无效果/有效果 A/B、叠加顺序、持续时间、确定性 |
| settlement | 只回写白名单字段，持久对象/经验/HP exact |
| capability | scanner 结果与 runtime 接受/拒绝一致；fidelity/confidence 与证据一致 |
| episode | 玩家+历史对手和 random policy 都能跨回合运行 |
| benchmark | 无关场景 digest 零变化；受影响场景对真实 oracle 报 winner/card 指标 |

长期 dashboard 至少保留：

```text
deployment_unit_exact / deployment_full_card_exact / supply_exact
mechanic_occurrence_coverage / effect_complete_round_rate
battle_winner_exact / card_damage_error / card_kill_exact / survival_exact
equipment_id_implemented / equipment_selection_coverage / equipment_oracle_accuracy
skill_id_implemented / skill_occurrence_coverage / verified_vs_provisional
settlement_hp_exact / settlement_exp_exact
scanner_runtime_disagreement = 0
silent_half_effect_count = 0
state_invariant_failures = 0
determinism_failures = 0
```

“支持率”和“正确率”必须继续分开：unsupported 不计入正确率分母，但必须计入覆盖率；
provisional 可运行，却不能与 verified 合并统计。

### 7.1 Benchmark 分层 Gate

| Gate | 运行频率 | 通过条件 |
|---|---|---|
| 快速 PR gate | 每个提交 | 完整单元/transition 测试、characterization、机制 sentinel、确定性 |
| 八库 legacy gate | 合并前/nightly | 每库 agree count 不低于冻结值；纯重构和装备 PR 必须完全一致 |
| 装备专项 oracle | 每个装备批次 | 覆盖 carrier/target/等级/科技/专家叠加；winner 与 card damage/kills/survival |
| 技能/状态专项 oracle | 每个技能批次 | 落点、延时、范围、护盾/免疫/叠加、trace 与确定性 |
| Episode gate | 每个跨回合机制 | 经济、库存/CD、持久对象、terminal、save/load 多回合一致 |

当前八库不含装备效果，所以即使仍是 `1793/2349`，也只说明“没有把旧战斗搞坏”；装备
准确率必须由新专项库给出。反过来，新增专项库也不能替代八库 legacy 回归。

## 8. 推荐提交/里程碑顺序

0. `[done] step3: equipment transition/state, typed release, quotes and fidelity warning`
1. `battlefield: freeze legacy outcomes per-lib counts and differential digests`
2. `battlefield: add versioned input outcome compiler and effect contracts`
3. `capability: derive six-stage support and confidence from mechanic registry`
4. `equipment: compile four high-frequency static items and add oracle fixtures`
5. `transition: complete skill slot cooldown surrender and 10004 deployment rules`
6. `placement: unify flank redeploy deployment-module and fast-teleport permissions`
7. `equipment: add sustain shield immunity damage-block and cross-round items`
8. `battlefield: extract world events status damage and summon pipelines`
9. `skills: calibrate existing provisional skills then add high-frequency missing actions`
10. `structures: persist construction identity objects and battle outcomes`
11. `ruleset: close hidden economy special tech and reinforcement-card gaps`
12. `engine: split hot systems one at a time with differential gates`

不安排 2v2/特殊模式里程碑。

## 9. 第一阶段完成定义

第一阶段不要以“`engine.py` 变短了”作为完成标准。以下条件同时满足才算完成：

- [ ] 旧 `Battle` API 保持兼容，非装备场景八库结果零变化；
- [ ] `BattleInput`/`BattleOutcomeV2`/effect registry 已版本化并可 digest；
- [ ] capability 不再把半效果卡或 provisional 数值判成 verified 完整支持；
- [x] 装备库存和 `UseEquipment` 已在 Step 3 进入 typed transition；
- [ ] 高频四件装备完成六段闭合，既有 A/B golden fixture，也有真实游戏 oracle 专项样本；
- [ ] 专家 10004/10007/10008/10009 的完成度被准确分类并实现；20003 保持 Step 3 回归；
- [ ] 技能槽消费/CD/tick、typed surrender 与基础 PlacementRules 闭合；
- [ ] scanner/runtime disagreement、silent half effect、determinism failure 均为 0；
- [ ] 八库每库 agree count 不下降，装备 PR 保持 `1793/2349` 完全不变；
- [ ] 文档中的指标可由命令重新生成，不依赖手工抄写。

达到这一里程碑后，再继续扩剩余 21 个装备 ID 和战场技能；否则直接往 `engine.py` 添加更多
ID 特判，只会让当前的机制缺口更难测、更难回滚。

## 10. 前端 Step 3 移交项：装备近似运行与未实现技能

> 本节承接 [`前端step3实现.md`](前端step3实现.md)。Step 3 先完成装备和技能的
> transition/state 链路；pysim 尚未实现的战斗效果继续由本计划负责。

### 10.1 支持度口径调整

Step 3 后不再用单一 supported/unsupported 表示机制完成度，统一拆为：

```text
transition_complete: bool
battle_fidelity: exact | approximate | unsupported
confidence: verified | provisional | unsupported
```

装备完成库存、合法性和单位绑定后允许审计游戏继续运行，但在 battlefield 效果完成前
只能标记为：

```text
transition_complete = true
battle_fidelity = approximate
confidence = provisional | unsupported
effect_complete = false
```

`battle_fidelity=exact` 只能表达实现路径完整，还不能单独证明数值可信；最终
`effect_complete` 必须同时要求六段闭合且 `confidence=verified`。当前
`mechanism_support()` 尚未输出 confidence，是本计划首先要修的口径债务。

因此需要同时保留：

- runtime playable prefix：transition 能否无损执行动作和持久状态；
- strict-effect prefix：战斗效果是否也已完整实现；
- 首次 approximation 回合及机制 ID；
- `silent_half_effect_count = 0`，所有近似必须进入报告、GameView 和 battle warning。

未知装备 ID、未知目标限制或尚不能落入持久 state 的动作仍是 hard blocker，不能用
approximate 放行。

### 10.2 装备 E1 拆分

原 §4 的 E1 拆成两个阶段：

**E1a：Step 3 transition/state 链路**

- [x] 装备定义、库存多重集、`UseEquipment` typed action；
- [x] reinforcement/opening 获得装备、目标限制、替换和持久归属；
- [x] normalizer、replay adapter、save/load、digest、GameView、历史对手；
- [x] capability 标记 `transition_complete + battle_approximate`；
- [x] battle adapter 明确忽略装备效果并输出装备 ID warning。

**E1b：本计划 battlefield effect 链路**

- [ ] `UnitBattleInput.equipment_id` 进入 compiler 与 effect registry；
- [ ] 按单件装备实现 modifier/event/trigger，不允许整类白名单；
- [ ] 每件装备完成 A/B、叠加顺序、确定性和 trace 测试；
- [ ] 单件完成后只移除该 equipment ID 的 approximation warning；
- [ ] 25/25 装备 ID 完成前，未实现项继续保持 `battle_approximate`。

§4 E2–E5 仍是装备战斗/跨回合效果的正式实施清单，不因 transition 已能绑定装备而视为
完成。§4 E6 的 `effect_complete` gate 继续要求六段全绿且 confidence verified。

### 10.3 已接通技能的正确 ID

Step 3 只接通已有可信效果，并先修复旧映射错位：

| Release ID | 技能 | Step 3 后状态 |
|---:|---|---|
| `300001` | 导弹打击 | battle 已实现；伤害有 wiki 证据，范围仍 provisional |
| `800001` | 空投护盾 | battle 已实现；HP 有证据，半径仍 provisional |
| `100002` | 燃烧弹 | battle 已实现；DPS/范围/持续仍 provisional |
| `1200001` | 地底威胁 | summon 已实现；数量/参数 provisional |
| `1200003` | 呼叫机群 | summon 已实现；数量/参数 provisional |
| `1100001` | 强化训练 | transition effect，不进入 battlefield |

必须永久保留以下错配回归：

- `200001` 是 EMP，未实现前不得调用燃烧地面效果；
- `1000001` 是再部署，未实现前不得调用召唤效果；
- capability 由统一 skill registry 查询 ID，不能再复制 raw type 白名单。

表中标为 provisional 的技能可以运行，但必须在指标中与 verified 分开；数值证据不足时
不能仅因“引擎已有一个近似事件”就升级为 verified。

### 10.4 未实现技能 backlog

以下技能进入 battlefield 后续实现范围：

| 类别 | ID/技能 |
|---|---|
| 状态/区域 | `200001` 电磁冲击、`200002` 巨型电磁冲击、`200003` 光子投射 |
| 轰炸 | `300003` 轨道轰炸、`300004` 核弹、`300005` 闪电风暴、`300006` 离子轰炸、`300007` 轨道标枪 |
| 持续地形 | `500002` 酸液弹、`600002` 烟雾弹 |
| 部署规则 | `1000001` 再部署、`1500002` 移动信标 |
| 召唤 | `1200002` 犀牛来袭、`1200004` 呼叫战舰、`1200005` 天降火神 |
| 其他语料 ID | `400002` 黏油弹及 replay/census 中尚未进入版本化定义的变体 |

每个技能必须独立完成：

1. 卡牌 ID、Release ID、SkillIndex 的 codec；
2. 获得时序、库存槽、目标类型、可用性和 CD；
3. 费用与 ledger；
4. transition legality 和拒绝不变性；
5. `TimedEvent`/目标对象编译；
6. battle effect、持续/叠加/免疫规则；
7. trace、BattleOutcome 与确定性；
8. 无效果/有效果 A/B 和 replay/corpus 证据；
9. capability/runtime 一致性；
10. 按单个技能 ID 移除 unsupported/approximation，禁止整类一次性宣称支持。

### 10.5 后续完成 Gate

- [x] E1a 完成后装备不再阻塞 runtime，strict-effect prefix 保持 approximation；
- [ ] 每个装备/技能的 support 状态都来自同一 registry；
- [ ] 可运行但未实现/未校准的装备与技能均有具体 ID + confidence warning；真正
      unsupported 的技能必须在 transition 阶段阻塞，不能进入 battle 后才 warning；
- [ ] `200001`、`1000001` 的错误效果回归测试长期保留；
- [ ] 任一单项升级为 verified 时都有真实 oracle A/B、trace、确定性和证据记录；
- [ ] 单项完成不得改变无该机制场景的旧 benchmark digest；
- [ ] 全部装备和技能完成前，不修改文档口径把 transition 完整等同于 effect complete。
