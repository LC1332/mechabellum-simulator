# PySim Battlefield 重构计划：从可运行闭环到机制完备

> 本文承接 [`transition实现任务书.md`](transition实现任务书.md) 与
> [`transition-v0.1正规化任务书.md`](transition-v0.1正规化任务书.md)，并以
> **2026-08-27 当前代码**为准重新审计 `pysim`、`pysim/transition` 和审计游戏。
>
> 两份旧任务书中的 v0/v0.1 总结仍然有效，但不能直接当作当前能力表：此后仓库又补了
> 开局 catalog、能力分类器、部分蓝图、装置、能量塔技能和战场技能。另一方面，当前
> 分类器也会把少数只有“入库”或“扣费”、但完整效果尚未闭合的卡牌判成已支持。因此
> 本计划统一使用“动作解析、合法性、经济、持久状态、战斗效果、结算”六段闭合口径。

## 0. 结论先行

当前 `pysim` 已经不是“缺一个 transition”的阶段，而是进入了两个问题互相放大的阶段：

1. `pysim/engine.py` 已超过 4,000 行，单位、科技、专家、塔、建筑、装置、战场技能和
   召唤机制共享一套 numpy SoA 与大量开关，继续直接往主循环加机制会越来越难审计；
2. transition 的主闭环已经跑通，但不少外围机制仍停在 raw tuple、passthrough、近似值
   或仅扣费状态，特别是**单位装备**，已经成为审计游戏连续可玩前缀的主要阻塞项。

建议采用“契约先行、旁路抽取、逐机制接管”的重构，而不是一次性重写战斗引擎：

- 先冻结现有结果与公开 API；
- 把战场输入、效果注册、事件、状态效果和输出从 `Battle` 中抽出；
- 优先打通装备的完整纵向链路；
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
- 当前引擎的单位、科技、专家加成、塔、建筑、部分装置/战场技能、召唤与经验机制。

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

因此本轮验收必须继续把 deployment、battle、settlement、episode 四层指标分开。

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
| `UnitCard.equipment_id` | `equipment_id: int | None` + 玩家装备库存 | 获得、装备、目标限制、替换/消耗、出售 |
| `commander_skills_raw` | `CommanderSkillState` | 库存槽、是否可用、CD、次数、目标类型 |
| `constructions_raw` | `ConstructionState` | 稳定 ID、类型、位置、耐久/存活、跨回合更新 |
| `tower_mods_raw` | `RoundEffectState` | 来源、层数、持续时间、叠加规则 |
| `devices_raw` | `WorldObjectState` 或本回合事件 | 类型、位置、拥有者、是否持久 |
| `skill_events_raw` | `TimedBattleEvent` | 技能 ID、落点、目标、触发时刻 |
| `officers` 混合列表 | 专家、单位强化卡、蓝图 buff 分栏 | 避免经济/战斗/部署效果互相误判 |

旧字段先由 adapter 兼容读写一个 schema 版本；不在同一提交里删除旧格式。

## 3. 机制缺口总表

### 3.1 P0：当前会直接阻塞连续游戏或造成半效果

| 机制 | 当前状态 | 缺口 | 计划 |
|---|---|---|---|
| 单位装备 | 回放能读 `EquipmentID`，`UnitCard` 能保存；战斗适配器不消费；`UseEquipment` 仍 passthrough；装备增援卡被能力扫描阻塞 | 没有装备库存、装备合法性、24 种效果、battle modifier/event、出售与升级交互 | 见 §4，作为第一条完整纵向机制 |
| 增援卡支持度 | 单位获得、强化、专家、技能库存大多会被接收 | 当前按“类别”粗判；部分卡只存入 `officers`，其部署/经济/战斗效果未必完整 | capability 改为按 `(item_id, effect)` 六段检查 |
| 专家 10004 额外部署位 | 卡牌会持久化 | `BASE_BUY_LIMIT` 未读取该效果 | 加入 deployment modifier，并用回放 BuyCount 探针冻结时序 |
| 专家 20003 高效研发 | 卡牌会持久化 | `tech_price()` 未应用每个科技 −50 | 价格唯一入口接入专家 modifier，补账本与 oracle |
| 专家 10007/10008 装置强化 | 卡牌会持久化 | 护盾 +40%、飞弹伤害 +200% 未传给装置事件 | effect registry 编译装置参数时应用 |
| 专家 10009 快速传送 | 旧 replay checker 支持侧翼延迟减半 | `battle_from_state()` 未传 `spawn_at`，连续 env 丢失该机制 | 把 flank unlock/延迟变成持久规则与 UnitBattleInput 字段 |
| 蓝图 3 精英征召 | 当前 deploy 会令其后的购买等级 +1 | 与 v0.1 §10 的语料结论“蓝图 2/3 均不提升购买等级，+1 只来自精英专家/精英卡”冲突 | 先做定向 fixture 复核；裁决前降为 provisional，不得宣称完整支持 |
| 单位升级经验 | deploy 允许低经验升级，legal candidates 要求经验足够，历史对手又会先补经验 | 三个入口的规则不一致，且用户 Q13 的纠正尚未形成统一规则 | 分离真实玩家 legality 与反事实历史 override，并将经验消费/升级时点版本化 |
| `GiveUp` | 当前仅接受为“无部署效果 marker” | transition 本身不立即终局，仍可能进入战斗 | 新增 typed `SURRENDER`，原子进入 TERMINAL，双方视角与 reward 明确 |
| 技能库存/CD | 可以获得技能并记录 raw inventory | 释放后不消费次数、不更新 CD，`advance_round()` 不 tick | typed 技能状态 + release legality + round tick |
| 布阵合法性 | 只检查全图 `abs(x/y)` 边界 | 缺部署区域、侧翼区、占位/重叠、旧单位移动限制、部署模块/再部署权限 | 建独立 `PlacementRules`，legal mask 与执行共用 |

### 3.2 P1：战场外围机制未完整覆盖

| 机制 | 已有部分 | 未闭合部分 |
|---|---|---|
| 装置 | 10001 飞弹、20001 护盾有 provisional battle event | 30001 未映射；射程/CD/HP 等仍有校准值；先进装置专家未生效 |
| 能量塔技能 | 5 射程、6 移速已接入且支持叠加 | 1/3/4 未映射；持续时间、目标范围与费用仍需证据表 |
| 战场技能 | 已有导弹打击、燃烧地面、护盾和两类召唤框架 | 轨道轰炸、核弹、闪电风暴、离子轰炸、轨道标枪、酸液、烟雾、EMP、光子投射、犀牛/战舰/火神、移动信标等仍缺失或待校准；部分技能 ID 与卡牌 ID 需要统一证据映射 |
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

### 3.4 P2：模式与长期扩展

- 2v2 的队伍、共享/独立经济、战场坐标、伤害和终局；
- 特殊模式与特殊地图；
- 2119/2203/2207 以外版本的数据 migration；
- 真正从空状态生成 shop、开局与全部外生随机事件；
- PettingZoo/Gymnasium wrapper 与训练侧 tokenizer，不应反向污染核心规则。

## 4. 单位装备专项实施计划

装备是最适合验证新架构的第一类机制：它同时经过“增援选择 → 库存 → UseEquipment →
单位持久状态 → battle modifier/event → settlement/下一回合”，能够检验六段闭合是否有效。

语料表见 [`增援卡牌-回放全量信息.md`](增援卡牌-回放全量信息.md)：共 24 种，
被选择 2318 次。高频且规则简单的四种为激光瞄具 448、改良火控 305、重型装甲 240、
速攻模块 175，合计 1168/2318 = 50.4%，适合作为首批。

### E0：装备证据与契约

- [ ] 从现有 JSON 生成版本化 `EquipmentDef`，禁止运行时解析中文描述；
- [ ] 每个定义记录：目标限制、费用、静态 modifier、动态 trigger、持续时间、来源；
- [ ] 探明选择装备后是否必须当回合使用、能否跨回合库存、同单位能否替换装备、
      被替换装备去向、出售带装备单位后的行为；
- [ ] 确认 `UseEquipment` 的 EquipmentID/UnitIndex 字段与快照 `EquipmentID` 一致；
- [ ] 将未知规则标为 provisional/unsupported，不能填默认值假装完成。

### E1：transition 纵向链路

- [ ] `PlayerState` 增加 typed `equipment_inventory`；
- [ ] `ActionKind.USE_EQUIPMENT` 与 `UseEquipmentArgs` 进入 canonical plan；
- [ ] 选择装备卡时扣费并将物品放入库存，不再直接拒绝整类卡；
- [ ] 校验目标单位存在、`can_add_equipment`、空中/地面/巨型限制、库存所有权；
- [ ] 成功后原子更新库存与 `UnitCard.equipment_id`，失败不改状态；
- [ ] Undo 仍只由 normalizer 折叠；transition 只消费最终装备动作；
- [ ] 出售、升级、save/load、state digest、observation、前端 GameView 全部保留装备。

### E2：静态装备首批

- [ ] 激光瞄具：射程 +20；
- [ ] 重型装甲：生命 +75%；
- [ ] 改良火控：攻击 +65%；
- [ ] 速攻模块：移速 +5、攻击 +35%；
- [ ] 超重型装甲、增幅核心、强化模块、试验级巨山装甲；
- [ ] 明确装备、科技、专家、蓝图、等级之间的加法/乘法和烘焙顺序；
- [ ] 每种装备至少一个无装备/有装备 A/B golden test。

### E3：护盾、恢复与免疫

- [ ] 纳米维修包、汲取模块；
- [ ] 便携式护盾、保护屏障、超级屏障；
- [ ] 光子涂层、抗干扰模块；
- [ ] 定义 EMP、引燃、酸液、退化、核心爆炸等统一 `StatusKind`，装备免疫通过
      status registry 实现，禁止在各伤害分支写装备 ID 特判。

### E4：生产、召唤与特殊触发

- [ ] 坦克/野马/钢球生产线复用统一 summon scheduler；
- [ ] 深渊信标；
- [ ] 先进寄生弹药、先进酸性弹药；
- [ ] 统御核心的收入、死亡全灭与胜负交互；
- [ ] 应激电容的低血量 EMP 触发；
- [ ] 召唤物是否计分、给经验、持久化及受装备/专家影响必须写入规则版本。

### E5：装备完成 gate

- [ ] 24/24 装备都有显式 registry 状态与证据，不存在“未知但 accepted”；
- [ ] `UseEquipment` 的 replay assignment 对拍 exact；
- [ ] 已声明 supported 的装备在 legality/economy/state/battle/settlement 六段全绿；
- [ ] 装备候选不再因为“整类装备”被扫描器阻塞，未实现单卡仍按 ID 精确禁用；
- [ ] 同 seed、同装备、同输入的 BattleOutcome digest 一致；
- [ ] 不装备时八库基线与重构前完全一致。

## 5. Battlefield 内部重构次序

### B0：冻结行为基线

- [ ] 记录当前 Git commit、schema/ruleset/engine version、八库结果与各库 digest；
- [ ] 保存 public `result()`、`outcome_cards()`、`team_score()` 的 characterization fixture；
- [ ] 同输入同 seed 连跑两次，断言 outcome/trace digest 确定；
- [ ] 建立 old/new differential runner，结构重构期间逐场比较。

完成标准：任何结构性 PR 都不能改变现有 2349 场结果；确需改变物理规则时单独提交，
附带 A/B 证据并提升 engine version。

### B1：抽取输入编译与效果注册

- [ ] `battle_adapter.py` 先改为生成 `BattleInput`，再由 legacy adapter 喂给 `Battle`；
- [ ] 科技、专家、蓝图、装备、塔技能不再直接散落在 `finalize()` 条件分支；
- [ ] registry 输出统一的属性 modifier、状态免疫、定时事件、on-hit/on-death trigger；
- [ ] capability scanner 直接查询同一 registry，不再维护另一套白名单；
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
- [ ] 伤害管线明确顺序：命中 → 层级/护盾 → 免疫/减伤 → 护甲 → 生命 →
      吸血/经验 → on-hit/on-death；
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

- [ ] capability 从 raw type/卡牌类别白名单切换为逐 ID、逐 effect 完整度；
- [ ] 专家 10004、20003、10007、10008、10009；
- [ ] legal candidates 与 deploy 接受条件重新做一致性测试；当前升级候选仍看经验门槛，
      而 deploy 对历史升级采用不同口径，应拆成真实玩家 legality 与历史对手 override；
- [ ] 出售检查 `can_be_sold`，并冻结装备、精英等级和强化卡对退款的影响；
- [ ] `GiveUp` typed terminal。

### M2：高频战场技能与装置

- [ ] 先按回放出现次数排序，不按实现方便程度排序；
- [ ] 为每个技能建立 cast legality、扣费/CD、事件编译、battle effect、trace、测试；
- [ ] 补齐能量塔技能 1/3/4 与装置 30001；
- [ ] 清理技能卡 ID、Release ID、SkillIndex 三套编号的映射，所有转换集中在 codec；
- [ ] provisional 数值必须显示在报告与前端，不能标记 verified。

### M3：建筑、侧翼与布阵规则

- [ ] `constructionIndex` 变为稳定对象引用；
- [ ] 建筑结果进入 `BattleOutcomeV2`，按真实规则决定下一回合是保留、损坏还是消失；
- [ ] 将 [`pysim/flank.py`](../pysim/flank.py) 的侧翼规则接入连续 state compiler；
- [ ] 建模快速传送、再部署、部署模块和“新单位/旧单位”的移动权限；
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
| capability | scanner 结果与 runtime 接受/拒绝完全一致 |
| episode | 玩家+历史对手和 random policy 都能跨回合运行 |

长期 dashboard 至少保留：

```text
deployment_unit_exact / deployment_full_card_exact / supply_exact
mechanic_occurrence_coverage / effect_complete_round_rate
battle_winner_exact / card_damage_error / card_kill_exact / survival_exact
settlement_hp_exact / settlement_exp_exact
scanner_runtime_disagreement = 0
silent_half_effect_count = 0
state_invariant_failures = 0
determinism_failures = 0
```

“支持率”和“正确率”必须继续分开：unsupported 不计入正确率分母，但必须计入覆盖率；
provisional 可运行，却不能与 verified 合并统计。

## 8. 推荐提交/里程碑顺序

1. `battlefield: freeze legacy outcome and benchmark digests`
2. `battlefield: add versioned input outcome and effect contracts`
3. `transition: replace raw peripheral state with typed adapters`
4. `capability: derive effect completeness from mechanic registry`
5. `equipment: add inventory use action and four static items`
6. `equipment: add sustain shield immunity and summon items`
7. `transition: complete expert deployment economy device and flank effects`
8. `skills: complete high-frequency battlefield actions and cooldowns`
9. `structures: persist construction identity and battle outcomes`
10. `match: type surrender placement rules shop and external events`
11. `battlefield: extract world events status and damage pipelines`
12. `engine: split hot systems with differential gates`
13. `ruleset: close hidden economy and special tech data`
14. `modes: add 2v2 special maps and version migration`

## 9. 第一阶段完成定义

第一阶段不要以“`engine.py` 变短了”作为完成标准。以下条件同时满足才算完成：

- [ ] 旧 `Battle` API 保持兼容，非装备场景八库结果零变化；
- [ ] `BattleInput`/`BattleOutcomeV2`/effect registry 已版本化并可 digest；
- [ ] capability 不再把半效果卡判成完整支持；
- [ ] 装备库存和 `UseEquipment` 进入 typed transition；
- [ ] 高频四件装备完成六段闭合并有 A/B golden fixture；
- [ ] 专家 10004/20003/10007/10008/10009 的完成度被准确分类，已实现项有测试；
- [ ] scanner/runtime disagreement、silent half effect、determinism failure 均为 0；
- [ ] 文档中的指标可由命令重新生成，不依赖手工抄写。

达到这一里程碑后，再继续扩 24 件装备和战场技能；否则直接往 `engine.py` 添加更多
ID 特判，只会让当前的机制缺口更难测、更难回滚。
