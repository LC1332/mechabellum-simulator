对于目前8300/game的review

后续的任务书以及任务完成的总结也请写在这个文档

## 专家的经济

专家类的经济是不是没有完全实现？
我选择巨型专家
但是解锁堡垒仍然要200

照理说巨型专家解锁400以上单价的巨型单位是减免200的

（感觉主要是解锁和科技的减免，我看补给专家 快速补给都是对的）

这个可能要在transtion里面修正

## 指挥官技能


Release Command Skill为什么会受限执行？

我看之前在pysim有的回放里面已经实现了 导弹类型的战场技能

这部分能帮我连接上吗？

## 科技升级

科技要考虑能够升级 能够为场地上已经有的兵（所属的兵种）升级科技


## 蓝图费用

强化瞄准费用100
告诉移动费用50
批量征召 费用50

## 装备不支持的问题

这里装备不支持是指单纯pysim中机制不支持
还是transition没办法把携带装备的机制 即不能把action转化为给playerData？

我希望至少transition是支持的
因为后期我有可能RL会直接调用windows里面注入了的真实游戏来进行
而pysim不支持只是模拟有出入而已 这个问题暂时不大
尽量把transition做完备


---

# 前端 Step 3 任务书：经济、科技、技能与装备闭环

> 本任务书根据上方对 `http://127.0.0.1:8300/game` 的 review 编写。
> 本阶段先冻结实施要求，不在本轮编写实现代码；后续任务完成后的实施总结、验证结果、
> 偏差和遗留项继续追加在本文末尾。

## 0. 本阶段目标

让 `/game` 的 transition 和前端操作更加完备：

- 补齐专家对收入、解锁费、科技费的修正；
- 修正强化瞄准、高速移动、批量征召的费用；
- 科技栏目只展示当前场上兵种，并允许购买其首个及后续科技；
- 接通 pysim 已实现的战场技能；
- 装备能够进入库存并绑定单位，pysim 暂不计算效果但不再阻塞游戏；
- 所有近似机制必须显式标注，不能伪装成完整模拟。

本阶段以 **transition 规则完整性优先、pysim 精度显式降级** 为原则。装备只要能够
完整进入持久状态，就允许审计游戏继续；其战斗效果缺失通过 fidelity warning 明示。

## 1. 已确认的产品裁决

1. 装备完成 transition/state 链路后，人类和历史对手均放行；pysim 未实现的装备效果
   标为 `battle_approximate`，不再作为运行时 blocker。
2. `ReleaseCommanderSkill` 接通所有已经有可信 pysim/transition 效果的技能；未实现的
   技能移交 [`pysim-battlefield重构计划.md`](pysim-battlefield重构计划.md)。
3. 科技入口仍放在左侧“科技”栏目，不放入单位详情；栏目只展示当前场上实际存在的
   兵种。
4. 强化瞄准和高速移动虽然在游戏理解上与指令中心增益相近，但回放协议中属于
   `ActiveEnergyTowerSkill`，不是 `ActiveBlueprint`。
5. 本轮不实现 24 种装备的 pysim 战斗效果，不借机扩展 2v2、特殊模式或未知战场技能。

## 2. T1：统一经济报价与专家修正

### 2.1 唯一价格入口

新增服务端 `PriceQuote`：

```python
PriceQuote(
    base_price: int,
    modifiers: tuple[PriceModifier, ...],
    final_price: int,
)

PriceModifier(
    source_id: int,
    name: str,
    amount: int,
)
```

Economy 对外提供带当前 `officers` 的统一报价入口：

- `unlock_quote(mech_id, officers)`；
- `tech_quote(mech_id, tech_id, owned_count, officers)`；
- 现有购买、升级报价继续复用同一 modifier 数据源。

执行、合法动作、GameView、normalizer、replay adapter 和回放经济对拍必须消费同一份
报价。旧的只返回整数的方法可保留兼容 wrapper，但不得再成为业务真源。

### 2.2 专家经济规则

| 来源 ID | 规则 | 时序/范围 |
|---:|---|---|
| `20005` 巨型专家 | 指定巨型/泰坦单位解锁费 `-200` | 使用 gamedata officer `unitIds` |
| `20021` 空军专家 | 指定空军单位解锁费 `-200` | 使用 gamedata officer `unitIds` |
| `20036` 剑齿虎专家 | 剑齿虎科技费 `-50` | mech `21` |
| `20038` 火獾专家 | 火獾科技费 `-50` | mech `20` |
| `20003` 高效科技研发 | 全部科技费 `-50` | 获得卡牌后立即生效 |
| `20032` 精英专家 | 第一回合补给 `+100` | round 1 一次性 |
| `10014` 训练专家 | 第一回合补给 `+50` | round 1 一次性 |

同时保留并回归现有补给专家、快速补给专家、成本控制、补给强化、超级补给和高效制造。

规则约束：

- 多个价格修正按金额相加，最终费用下限为 `0`；
- 巨型/空军范围读取 gamedata 显式 `unitIds`，禁止用“基础价格 ≥400”或 slot size
  启发式代替；
- 堡垒基础解锁费 `200`，巨型专家下最终报价必须为 `0`；
- GameView 同时返回最终价和 modifier breakdown，前端不自行推算；
- ledger 按最终扣费入账，receipt/audit detail 保留基础价与 modifier 来源。

### 2.3 完成 Gate

- 巨型专家开局下，堡垒在 UI、receipt、ledger 中均为 `0`；
- 无巨型专家时堡垒仍为 `200`；
- 所有折扣具备单项、叠加、零下限测试；
- normalizer 估价、runtime 扣费和 GameView 报价不存在分歧。

## 3. T2：修正指令中心与能量塔费用

冻结以下费用：

| 动作 | ID | 费用 |
|---|---:|---:|
| 批量征召 `ActiveBlueprint` | `2` | `50` |
| 强化瞄准 `ActiveEnergyTowerSkill` | `5` | `100` |
| 高速移动 `ActiveEnergyTowerSkill` | `6` | `50` |

实施要求：

- `BLUEPRINT_COSTS[2] = 50`；
- 新增能量塔技能费用单一规则源，至少包含 `{5: 100, 6: 50}`；
- 三项动作在执行前检查资金，成功后原子扣费并记录稳定 ledger reason；
- 资金不足时返回 `INSUFFICIENT_SUPPLY`，state digest 和 session version 不变；
- GameView 返回 cost/affordable，前端只按服务端结果启用按钮；
- Undo 恢复资金、蓝图/本回合 buff、receipt 和 ledger；
- 强化瞄准/高速移动仍是本回合效果，advance round 后清空；
- 批量征召仍提供本回合购买上限 `+1`，但激活本身不再免费。

## 4. T3：场上兵种科技购买闭环

### 4.1 候选生成

- 科技栏候选兵种来自当前人类玩家 `units[].mech_id` 的去重集合；
- 不再只遍历已有 `tech_map`；
- 场上已有兵种、但 `tech_map` 尚无该 mech 条目时，必须从 gamedata 展示首个科技；
- 没有单位在场的兵种不展示；已有科技状态仍保留，未来重新购买该兵种时继续生效；
- 同兵种多个单位只显示一份科技表。

### 4.2 BUY_TECH 合法性

执行端必须重新校验：

1. `mech_id` 在 gamedata 中存在；
2. 当前玩家场上至少有一个该兵种单位；
3. `tech_id` 属于该兵种的 technologies；
4. 科技尚未激活；
5. `previousTechID` 前置满足；
6. 当前 supply 足够支付统一报价。

成功后写入兵种级 `tech_map`。Battle adapter 继续按 mech 为该兵种所有现有单位编译
相同科技，之后新买入的同兵种也自动继承。

### 4.3 前端要求

- 科技栏按场上兵种分组，显示“可购买”和“已激活”；
- 科技名称、说明、前置状态、最终价格和折扣来源均来自 GameView；
- 单位详情只展示所属兵种已激活科技，不新增购买按钮；
- 购买成功后局部状态以新 GameView 重绘，不做前端乐观写入。

## 5. T4：接通已有战场技能

### 5.1 先修正技能 ID 错配

本阶段允许执行的可信映射为：

| Release ID | 技能 | 执行层 |
|---:|---|---|
| `300001` | 导弹打击 | pysim battle event |
| `800001` | 空投护盾 | pysim battle event |
| `100002` | 燃烧弹 | pysim battle event |
| `1200001` | 地底威胁 | pysim summon event |
| `1200003` | 呼叫机群 | pysim summon event |
| `1100001` | 强化训练 | transition 目标单位经验 |

必须删除错误语义：

- `200001` 是 EMP，不得继续当作燃烧弹；
- `1000001` 是再部署，不得继续当作召唤技能；
- 二者在真实效果实现前保持精确受限，不能用近似的错误效果放行。

### 5.2 Typed action

新增：

```python
ActionKind.RELEASE_COMMANDER_SKILL

ReleaseCommanderSkillArgs(
    skill_index: int | None,
    skill_id: int | None,
    positions: tuple[tuple[float, float], ...],
    unit_ref: EntityRef | None,
    construction_index: int | None,
)
```

normalizer/canonicalizer 按“显式 ID 优先，否则由 SkillIndex 查当前库存”的规则解析；
已映射技能不再作为通用 `raw_unsupported` 进入 deploy。

单位回收 `900001 + UnitIndex` 继续走 typed `SELL_UNIT`。建筑回收、未知 ID、错误目标类型
返回包含 `skill_id/skill_index/target_kind` 的精确 blocker，不再只显示
`ReleaseCommanderSkill unsupported`。

### 5.3 专家定时技能发放

- round start 从 gamedata officer 的 `activeRound/cmdSkills` 生成技能库存；
- 逻辑放在共享 transition 回合事件中，人类和历史对手必须共用；
- 导弹专家 `10011` 在第二回合增加两个独立 `300001` 槽位；
- 训练专家 `10014` 在第一回合增加一个 `1100001` 槽位；
- 未映射技能可以进入库存，但 GameView 标为 unsupported，不能释放为错误效果；
- slot 分配稳定且 save/load 后不变化。

### 5.4 GameView 与战斗接入

- `skill_releases` 按槽输出 index、resolved ID、名称、目标类型、支持度和本回合释放数；
- 前端按目标类型进入地图落点或单位目标模式；
- battle 技能写入 `skill_events`，由 battle adapter 送入同一场 pysim；
- battle trace 必须能看到对应 `skill` 事件；
- capability scanner 与 runtime 使用同一技能 registry。

## 6. T5：装备的 transition 完整链路

### 6.1 状态与动作契约

`PlayerState` 增加：

```python
equipment_inventory: tuple[int, ...]
```

它是装备 ID 多重集，不是 set。同 ID 多份装备必须保留份数。

新增：

```python
ActionKind.USE_EQUIPMENT
UseEquipmentArgs(equipment_id: int, unit_ref: EntityRef)
```

Transition schema 升级；旧 state/save 载入时通过 adapter 补空 inventory。

### 6.2 装备定义与合法性

- 生成版本化 `EquipmentDef`，至少记录 ID、名称、费用、目标限制和 battle fidelity；
- runtime 不解析中文描述决定规则；
- 已知增援装备及增幅专家使用的 `13030009` 都必须登记；
- 目标单位需要存在且其 gamedata card `canAddEquipment=true`；
- 针对生产线、保护屏障等装备落实 any/giant/ground_giant 目标限制；
- 未知装备 ID 或未知目标限制返回 `MISSING_RULE_DATA`。

### 6.3 状态转移

- 选择装备增援卡时正常扣费，并向 inventory 增加一份装备；
- 不再因为 `kind == 装备` 拒绝整个 reinforcement；
- `UseEquipment` 成功时从 inventory 消耗一份，并写入目标 `UnitCard.equipment_id`；
- 目标已有装备时，新装备替换旧装备，旧装备消失且不返回库存；
- 没有库存、目标不存在、目标不允许装备或装备类型不适配时拒绝且 state 不变；
- normalizer 将 raw `UseEquipment(EquipmentID, UnitIndex)` 转为 typed action；
- Undo 继续在 normalizer 前置折叠，transition 只消费最终动作；
- unit sell、upgrade、move、save/load、digest 和 settlement 都保留装备归属。

### 6.4 Opening 与回放

- opening catalog 增加 `equipment_inventory`；
- 从 round 1 surviving `UseEquipment` 动作归纳开局装备多重集；
- 增幅专家产生的多份 `13030009` 必须保留份数，不能按 ID 去重；
- replay adapter 从快照 `EquipmentID` 恢复已装备状态；
- 历史对手 typed `UseEquipment` 与人类动作走同一执行路径；
- 对拍至少覆盖选择装备、换目标、同 ID 多份、替换已有装备及下一回合快照归属。

### 6.5 前端交互

- 选择装备增援后进入 deployment，并自动进入装备目标选择态；
- 合法单位高亮，非法单位不能提交；
- ESC/取消只退出目标选择态，装备仍保留在“待装备”库存；
- 左侧增加待装备库存，可再次点击某件装备进入目标选择；
- 单位详情展示装备名称、ID和“pysim 未计算效果”的近似标记；
- 所有提交仍携带 expected session version，拒绝不做本地状态修改。

## 7. T6：支持度与近似模拟分层

### 7.1 两轴支持度

能力 registry 对每个机制输出：

```text
transition_complete: bool
battle_fidelity: exact | approximate | unsupported
```

- `transition_complete` 决定动作是否能安全改变经济和持久 state；
- `battle_fidelity` 描述 pysim 是否真实消费该效果；
- 只有 `transition_complete=true + battle_fidelity=exact` 才是 effect complete。

### 7.2 装备口径

完成 T5 的已知装备标记为：

```text
transition_complete = true
battle_fidelity = approximate
```

因此：

- 人类和历史对手装备动作不再产生运行时 blocker；
- runtime playable prefix 按 transition 可执行性计算；
- strict-effect prefix 在首次出现装备效果处停止；
- 未知装备仍是 hard blocker；
- pysim 明确忽略装备战斗 modifier，但 battle result 必须携带 warning，禁止静默。

### 7.3 Manifest、GameView 与页面

Replay option 增加：

- `runtime_playable_through_round`；
- `strict_effect_through_round`；
- `approximate_from_round`；
- `approximate_mechanics`。

GameView/battle 增加 `fidelity_warnings`。选择页允许正常或受限开始 transition 可执行的
回放，同时用独立 badge/说明展示“从 Rn 起装备效果未模拟”。近似不能混入严格正确率。

## 8. 公共契约与数据版本

- Transition state schema 升级，并提供旧 state 的空装备库存 adapter；
- 新增 `PriceQuote`、`USE_EQUIPMENT`、`RELEASE_COMMANDER_SKILL` typed model；
- GameView schema 升级，增加装备库存、合法装备目标、价格拆分、技能槽和 fidelity warning；
- replay shard/manifest 升级，增加 runtime/strict-effect/approximation 字段；
- manifest loader 继续兼容现有 v1/v2；
- opening catalog 升级并保存装备库存；
- 重建 committed opening catalog 与 `data/samples/replay_game`；
- 用户本地完整语料不提交，只提供独立 rebuild 命令。

## 9. 测试任务

### 9.1 经济与费用

- 巨型专家 + 堡垒：基础 `200`、modifier `-200`、最终 `0`；
- 无专家堡垒仍为 `200`；
- 空军解锁、剑齿虎/火獾科技、高效研发、首回合补给逐项与叠加；
- 强化瞄准扣 `100`、高速移动扣 `50`、批量征召扣 `50`；
- 三项资金不足均拒绝且 digest 不变；
- GameView 报价、receipt resource delta、ledger 和 replay 估价一致。

### 9.2 科技

- 场上兵种没有 `tech_map` 条目时仍显示并可购买首个科技；
- 无场上单位时不显示，伪造请求被拒绝；
- 前置科技链、重复购买、折扣后价格；
- 科技购买后，同兵种全部现有单位的 battle input 都携带该科技；
- 卖掉最后一队后科技从栏目隐藏但 state 保留，重新买入后继续生效。

### 9.3 技能

- 导弹专家 round 2 得到两个稳定槽位；
- 显式 ID 与 `ID=0 + SkillIndex` 解析到同一技能；
- 导弹、护盾、燃烧和两种召唤产生正确 battle event/trace；
- 强化训练只修改目标单位经验；
- `200001` 不产生燃烧效果，`1000001` 不产生召唤效果；
- 未映射技能和建筑回收返回精确 blocker；
- scanner/runtime 接受与拒绝一致。

### 9.4 装备

- reinforcement 扣费并增加库存；
- 合法装备、非法目标、无库存、替换、重复 ID、多份开局装备；
- Undo、save/load、state digest、下一回合快照归属；
- normalizer 的 `UseEquipment + Undo` 折叠；
- 人类与历史对手装备动作均不阻塞 runtime；
- battle 输出装备 approximation warning；
- 未知装备仍阻塞，strict-effect prefix 仍可审计。

### 9.5 API、前端与回归

- GameView 不泄漏内部 dataclass/raw tuple；
- 装备目标态取消后库存仍在，accepted 后退出目标态；
- 科技栏只出现当前场上兵种；
- 选择页正确区分 runtime、strict effect 和 approximation；
- 完整测试集、样例多回合流程和 manifest 重建通过；
- 无装备、无新增技能的既有 battle 结果保持不变。

## 10. 实施顺序

1. 冻结现有测试、样例 manifest 与无装备 battle digest；
2. 实现 `PriceQuote` 和专家价格/收入规则；
3. 修正三项费用并接入 ledger/GameView；
4. 修复科技候选与执行合法性；
5. 校正技能 ID，增加 typed release 和专家定时发放；
6. 增加装备定义、库存、typed action 与 opening/replay adapter；
7. 将 capability 拆为 transition/battle fidelity 两轴；
8. 更新 GameView 与前端交互；
9. 重建 committed catalog/sample manifest；
10. 运行完整测试与浏览器验收，最后在本文追加实施总结。

## 11. Definition of Done

- 上方五类用户反馈均可在 `/game` 中复现验收；
- 堡垒、三项费用和科技栏目显示/扣费正确；
- 已映射战场技能能从库存释放并进入同一次 pysim battle；
- 装备能选择、入库存、绑定单位、保存和回放，不再阻塞 runtime；
- 装备战斗效果缺失始终有可见 warning；
- scanner/runtime disagreement、silent half effect、state invariant failure 为 `0`；
- 未映射技能和未知装备保持精确 unsupported，不用错误近似换覆盖率；
- 完整测试、样例数据重建和浏览器流程通过；
- 实施总结、验证结果、偏差和遗留项已回写本文。

## 12. 非目标

- 本阶段不实现 24 种装备的 pysim 战斗 modifier/trigger；
- 不实现 EMP、核弹、轨道轰炸、烟雾等未映射技能；
- 不为提高可玩前缀而跳过未知动作；
- 不实现 2v2、特殊模式、完整 shop RNG 或 Windows 注入器；
- 不改变无装备场景的既有战斗结果；
- 不把 `battle_approximate` 宣称为 `effect_complete`。

## 13. 实施总结（完成后续写）

本节由实际实施任务在完成后追加，至少包含：

- 实际代码与数据改动；
- 自动化测试和浏览器验收结果；
- runtime/strict-effect 可玩前缀变化；
- 与本任务书的偏差及其证据；
- 仍需移交 battlefield 的装备和技能效果。

### 13.1 实施总结（2026-08-27，已完成）

**实际代码与数据改动**

| 模块 | 改动 |
|---|---|
| `pysim/transition/economy.py` | 新增 `PriceQuote`/`PriceModifier` 与 `unlock_quote`/`tech_quote`/`buy_quote`/`upgrade_quote`；`unlock_price`/`tech_price` 变为薄 wrapper（不再是业务真源）。专家规则：巨型 20005/空军 20021 解锁 `-200`（gamedata `unitIds` 显式范围）、剑齿虎 20036/火獾 20038/高效研发 20003 科技 `-50`、精英 20032/训练 10014 首回合补给 `+100/+50`（`ROUND1_SUPPLY_OFFICERS`，仅 round 1）。多 modifier 相加、下限 0。 |
| `pysim/transition/deploy.py` | `BLUEPRINT_COSTS[2]=50`；新增 `TOWER_SKILL_COSTS={5:100, 6:50}`（强化瞄准/高速移动执行前查资金、原子扣费、ledger reason `tower_skill:*`）；UNLOCK/BUY_TECH 消费 quote（receipt detail 带 breakdown）；BUY_TECH 新校验：tech 属于该兵种 `technologies`、兵种在场（`TECH_MECH_NOT_ON_FIELD`）；typed `RELEASE_COMMANDER_SKILL`（显式 ID 优先、否则 SkillIndex 查库存；1100001 目标单位经验、battle 技能写 `skill_events_raw`、未映射/建筑回收返回含 `skill_id/skill_index/target_kind` 的精确 blocker）；typed `USE_EQUIPMENT`（库存消耗、`canAddEquipment`、any/giant/ground_giant 限制、替换不回库）；装备增援卡扣费入库存；修复 `bought_this_round` 与蓝图回合效果（`blueprints_round`）在逐动作 deploy 调用间不累计的问题（购买上限此前实际未生效）。 |
| `pysim/transition/equipment.py`（新） | 版本化 `EquipmentDef` 注册表（24 张调查表装备 + 增幅专家的 `13030009`），目标限制（any/giant/ground_giant）在表内冻结——runtime 不解析中文描述；巨型集合取 gamedata officer 20005 `unitIds`（不用 slot/价格启发式）；`OFFICER_EQUIPMENT_GRANTS`（10013 → R1 三份 13030009）；`round_officer_skills`/`round_officer_equipment`/`top_up_skill_slots` 共享回合事件。 |
| `pysim/transition/model.py` | schema 升级 `transition-v0.4`；`PlayerState.equipment_inventory`（多重集 tuple）与 `blueprints_round`；`ActionKind.RELEASE_COMMANDER_SKILL`/`USE_EQUIPMENT` + `ReleaseCommanderSkillArgs`/`UseEquipmentArgs`；`BattleOutcome.fidelity_warnings`。 |
| `pysim/skills.py` | ID 修正（§5.1 冻结映射）：`300001` 导弹打击、`800001` 空投护盾、`100002` 燃烧弹（原 200001 为 EMP，移除）、`1200001` 地底威胁、`1200003` 呼叫机群（原 1000001 为再部署，移除）；`TRANSITION_SKILLS={1100001}`；`commander_skill_target_kind`。 |
| `pysim/transition/normalize.py` | 已映射技能释放→typed `release` 条目（含 positions/unit/construction）；`UseEquipment`→typed `equip` 条目（参与 Undo 折叠）；unlock/tech 估价传入快照 officers（同一 quote 源）。 |
| `pysim/transition/canonicalize.py` | `release`→`RELEASE_COMMANDER_SKILL`、`equip`→`USE_EQUIPMENT` typed action。 |
| `pysim/transition/capability.py` | 两轴 `mechanism_support()`（transition_complete × battle_fidelity exact/approximate/unsupported）；已知装备 offer/equip 不再是 runtime blocker；strict 扫描在 approximate 处停止（`APPROXIMATE_REINFORCEMENT_EFFECT`，strict-only）；`scan_option` 新增 `runtime_playable_through_round`/`strict_effect_through_round`/`approximate_from_round`/`approximate_mechanisms`（保留旧字段兼容）。 |
| `pysim/transition/settlement.py` | `advance_round(gd=)` 共享回合事件：按 officer `activeRound/cmdSkills` 发放技能槽（导弹专家 R2 两份 300001、训练专家 R1 一份 1100001），发放装备（增幅专家三份 13030009）；重置 `blueprints_round`。人类/历史对手共用。 |
| `pysim/transition/opening.py` | package 支持 `equipment_inventory`；`build_initial_state(gd=)` 在 round 1 应用技能/装备发放（top-up 语义，不与快照证据重复）。 |
| `pysim/transition/battle_adapter.py` | `run_battle` 输出 `fidelity_warnings`（每个已装备 id 一条 `equipment:N battle effect not simulated (battle_approximate)`）。 |
| `pysim/engine.py` | finalize 时 burn（燃烧弹）patch 补发 trace `E|0.00|skill|team|burn|x,y` 行——仅 trace 通道，模拟数值与既有战斗 digest 不变。 |
| `web/game_service.py` | GameView 升级 `game_view_v3`：unlock/tech/buy 报价拆分（`quote.base_price/modifiers/final_price`）、科技栏按场上兵种分组并含已激活项、能量塔技能 cost/affordable、技能槽（`slot_index/skill_id/target_kind/supported/released_this_round`）、装备库存+合法目标、`fidelity` 前缀段、battle `fidelity_warnings`、增援卡 `battle_fidelity` 徽标；`action_from_json` 支持 typed release/use_equipment。 |
| `web/game_library.py` | manifest 兼容读取新两轴字段（v1/v2 manifest 回退旧字段）。 |
| `web/static/game.html` | 前端：科技栏报价明细与已激活态、装备页签（待装备库存/限制标签/近似标记）、能量塔技能费用、技能槽按目标类型进入地图落点或单位目标模式、装备目标选择态（合法单位高亮、ESC 只退出选择态、库存保留、选卡后自动进入）、单位详情装备名+近似标记、选择页两轴/近似徽标、战斗结果近似警告。 |
| `tools/build_opening_catalog.py` | 归纳 round 1 幸存 `UseEquipment` 为开局装备多重集（Normalizer 折叠 Undo 后计数）。 |
| `tools/build_game_library.py` | manifest option 输出新两轴字段。 |
| 数据 | 重建 `data/game/opening_catalog.json`（29 队，全量 1106 局）与 `data/samples/replay_game`（原 3 局样本，重新 normalize + 重扫）；`local_data/replay_game`（1960 选项）仅本地重建、不入库。 |

**自动化测试与浏览器验收**

- `python -m pytest tests -q`：**96 passed**（基线 54 + 新增 42：`tests/transition/test_step3.py` 39 个覆盖 §9.1–§9.4，`test_game_api.py` 新增 3 个覆盖 §9.5 装备链路/科技视图/typed 释放）。
- 浏览器验收（`http://127.0.0.1:8300/game`，重启 dev server 加载新代码后人工驱动）：
  - 选择页：最佳选项显示「可玩至 R6 · 严格效果至 R2 · 从 R3 起装备效果未模拟」，strict/runtime blocker 分行显示；
  - 训练专家开局 → R1 技能槽「强化训练 槽0 · #1100001 · 单位目标」→ 点选后点己方单位，receipt `强化训练 sets exp to 650`；
  - 科技栏仅显示场上兵种（长弓/狼蛛），购买 receipt 为 `base 250 = 250`（报价明细格式）；首回合收入含训练专家 +50（¥250→购买后 ¥0）；
  - 塔·蓝图页：强化瞄准 ¥100 / 高速移动 ¥50 / 批量征召 ¥50；
  - R3 装备卡「便携式护盾 · 装备 · 战斗近似」可选 → 入库存 → 装备页签自动进入目标选择态、合法单位绿色高亮、非法点击被拒且库存保留 → 绑定后 receipt `equip 13010001(便携式护盾) -> unit 6`、单位详情显示装备名+近似标记；
  - R3 战斗后 `fidelity_warnings=['equipment:13010001 battle effect not simulated (battle_approximate)']`，装备归属跨回合保留（unit 6 仍持有）。

**runtime/strict-effect 可玩前缀变化（重建后样本 manifest）**

| option | 旧 runtime | 新 runtime | 旧 strict | 新 strict_effect | approximate_from |
|---|---:|---:|---:|---:|---:|
| 94974af9a119-0 | R4 | **R5** | R2 | R2 | R3 |
| b198291ffab1-0 | R4 | R4 | R3 | R3 | R4 |
| 其余 4 个 option | 0/1 | 0/1 | =runtime | =runtime | —（前缀内在 R1 即被未映射技能阻断） |

本地全量语料（1960 选项）同步重建；装备增援不再截断 runtime 前缀（最佳样本 R4→R5，本地最佳 R6）。

**与本任务书的偏差及证据**

1. 批量征召 50 / 强化瞄准 100 / 高速移动 50 与 `prices_v1_passive` 的 r1 窗口代数（bp2=0、tower 免费）冲突——按 §3 用户冻结费用执行，偏差已在 `deploy.py` 注释与本节记录。
2. `derive_incomes`（回放注入收入推导）仍用无 officers 的基础价：它从快照差分反推收入，价格只影响「哪些回合可精确推导」的判定，不是报价真源；未在本次扩展（回放 runner 用注入表，audit game 用 Income200r）。
3. 购买价 quote（GameView 与执行一致的部分）仍不含 deploy 层的精英征召等级费/高效制造折扣（原有行为，任务书 T1 gate 未涉及购买口径；精英费在 receipt 层可见）。
4. 增援估价/科技阶梯在 normalizer 内传入了快照 officers，但未重算已提交的 `data/samples/rounds.json` norm 工件以外的大型语料（shard 内 `actions_norm` 已随库重建全部重算）。
5. 装备目标限制表为人工冻结（依据调查表描述），`13030009` 的费用按增幅专家免费获得记 0。
6. `advance_round` 的技能/装备发放需要 `gd` 参数；旧调用（不传 gd）保持原行为（无发放），仅 env/opening 新路径传入。

**仍需移交 [`pysim-battlefield重构计划.md`](pysim-battlefield重构计划.md) 的效果**

- 24+1 种装备的 pysim 战斗 modifier/trigger（当前 `battle_fidelity=approximate`，战斗结果始终带 warning）；
- EMP `200001`、再部署 `1000001`、核弹 `300004`（10012）、`300003..300007` 导弹变体、`400002` 黏油弹、`1200002/1200004+` 信标、装置 `30001` 等未映射技能的真实效果；
- 召唤参数（1200001/1200003 的 mech/count）仍为 cal 标注，校准局后定版；
- 强化瞄准/高速移动对 pysim 的 `tower_mods` 通路已存在（射程+15/移速+3），但费用与 buff 的持续时间语义只按「本回合」建模。
