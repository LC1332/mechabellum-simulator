现在整体的实现已经非常不错了

我觉得再修复下面的bug就可以做学习了

## 默认情况下只能购买2个单位

默认情况下一回合只能购买2个单位

例外是消费50购买批量征召的时候 可以再购买一个

另外每有一个 [额外部署位] 专家 是可以额外部署一个的

## 默认情况下上回合已经参与战斗的单位这回合不能移动

也就是默认只有 新增援 、 专家给予的增援（比如犀牛专家第四回合的犀牛） 以及新购买的单位可以移动

例外是 装备 部署模块的单位可以移动

购买了 高速移动 科技的 单位（蜜蜂/霸主/凤凰）可以移动

战场技能 再部署 可以一回合一次，将一个单位从不可移动状态变为可移动

## 指挥官技能

另外 ReleaseCommandSkill 战场技能尽可能帮我再补一些
实现
这些可能会改动到pysim

## ActiveEnergyTowerSkill

这个应该大多是 高速移动(cost 50) 和 射程（cost 100）
这个应该很容易实现 帮我实现（要修改pysim）

将任务书续写到本md，如果对于游戏内容有问题可以留一个QA的section我来回答

---

# Transition 与前端 Step 4 任务书：部署额度、移动权限与战场技能

> 本任务书根据上方 review 编写，是
> [`前端step3实现.md`](前端step3实现.md) 的下游施工书。本轮先冻结规则、公共契约、
> 实施顺序和验收标准，不在本任务中修改实现代码。后续实际完成情况、测试结果、与任务书
> 的偏差及遗留项继续追加在本文末尾。
>
> 文中的复选框表示实施状态，不表示当前仓库已经完成；只有代码、测试和浏览器验收均有
> 证据时才能勾选。

## 0. 现状审计与本阶段终点

当前实现与本次规则有四处关键差距：

1. `pysim/transition/deploy.py::BASE_BUY_LIMIT` 当前是 `5`，与默认每回合只能购买
   `2` 个单位冲突；批量征召和额外部署位的加法链路已经存在，但基数错误。
2. `MOVE_UNIT` 当前只检查单位存在和地图边界，任何旧单位都能移动；
   `spawned_this_round` 已经记录本回合购买/赠送的单位，但尚未用于移动合法性。
3. 再部署 `1000001` 已能被 normalizer 识别为单位目标技能，但 transition 和 capability
   仍将它精确阻塞。
4. 能量塔技能 `5/6` 已有费用、回合状态和 pysim 增益，但仍通过
   `RAW_UNSUPPORTED`/passthrough 执行；前端也在提交 raw action，公共 typed contract
   和回放正规化尚未闭环。

本阶段的终点是：`/game`、历史对手回放和 RL transition 共用同一套部署合法性；服务端
能解释每个单位为什么可移动或不可移动；已支持的能量塔/指挥官技能均走 typed action；
新补的 pysim 技能效果有明确 fidelity 与校准状态，不再用错误近似提高覆盖率。

## 1. 已确认规则与实施口径

### 1.1 每回合购买额度

购买额度统一按下式计算：

```text
buy_limit = 2
          + 本回合批量征召(ActiveBlueprint 2)成功激活次数
          + 当前持有的额外部署位专家(10004)数量
```

- 默认额度是 `2`，不是当前代码中的 `5`；
- 批量征召每次费用 `50`，成功激活后本回合额度 `+1`；
- 每持有一个 `10004`，每回合额度 `+1`，同 ID 多份按份数叠加；
- 只有付费 `BUY_UNIT` 消耗额度；开局单位、增援卡直接赠送单位、专家定时赠送单位、
  战场技能临时召唤物均不消耗购买额度；
- 解锁、升级、买科技、移动、装备、释放技能也不消耗额度；
- 拒绝的购买不增加 `bought_this_round`，Undo 必须同时恢复资金、单位、额度和 receipt；
- `bought_this_round` 在 `advance_round` 清零，永久专家加成不清除，本回合蓝图加成清除。

### 1.2 每回合移动权限

默认情况下，参加过上一回合战斗的持久单位在本回合不可移动。单位满足以下任一条件时
可以移动：

1. 本回合新购买；
2. 本回合由普通增援卡直接赠送；
3. 本回合由专家/开局能力定时赠送，例如犀牛专家第 4 回合的犀牛；
4. 装备部署模块 `13040001`；
5. 所属兵种已经购买高速引擎科技：兵蜂 `mech 6 / tech 1606`、霸主
   `mech 11 / tech 1611`、凤凰 `mech 16 / tech 1616`；
6. 本回合被再部署 `1000001` 选中。

实施默认口径：round 1 的全部开局单位视为“尚未参加过上一回合战斗”，因此可以移动；
一个已经获得权限的单位在本部署阶段可多次修正位置，权限不会因第一次拖动而消耗。
跨越中线与“是否有权移动”是两条独立规则：继续保留现有地图 bounds/跨中线口径。

### 1.3 再部署技能

- `1000001` 是单位目标、transition-only 的指挥官技能，不产生 pysim 召唤或伤害事件；
- 每回合最多成功释放一次，目标必须是己方当前不可移动的持久单位；
- 成功后把该单位加入本回合移动许可，技能槽置为已使用；下一回合重新可用；
- 对本来就可移动的单位释放应拒绝为 `UNIT_ALREADY_MOVABLE`，不消费槽位；
- 目标不存在、属于敌方、技能槽未激活或本回合已使用时均原子拒绝；
- Undo 同时恢复技能槽和目标单位的移动权限；结束部署后不可再释放。

### 1.4 能量塔技能

本轮必须完整支持：

| SkillID | 名称 | 费用 | 本回合战斗效果 |
|---:|---|---:|---|
| `5` | 强化瞄准 | `100` | 远程单位射程 `+15` |
| `6` | 高速移动 | `50` | 全体单位移速 `+3` |

费用、合法性、ledger、GameView 和 pysim 编译必须消费同一个 registry。回合效果在
`advance_round` 清空；资金不足或未知 ID 时 state/version 不变。当前实现已经有数值路径，
本阶段重点是去掉 raw passthrough、补 typed action、正规化、支持度与前端闭环。

## 2. 公共状态与规则契约

### 2.1 购买额度报价

新增纯函数/只读对象，作为 deploy、legal mask、GameView 和测试的唯一真源：

```python
@dataclass(frozen=True)
class BuyLimitQuote:
    base: int                         # 2
    blueprint_bonus: int              # 本回合批量征召次数
    officer_bonus: int                # 10004 持有份数
    used: int                         # bought_this_round
    limit: int
    remaining: int

def buy_limit_quote(player: PlayerState) -> BuyLimitQuote: ...
```

不得让前端重新计算额度，也不得在 capability、deploy 和 GameView 中各保留一份常量。

### 2.2 移动权限判定

复用现有 `spawned_this_round` 表达“本回合新进入持久阵容”的 entity ID；新增回合字段：

```python
PlayerState.redeployed_this_round: tuple[int, ...] = ()
```

新增唯一判定入口：

```python
@dataclass(frozen=True)
class MovePermission:
    allowed: bool
    reasons: tuple[str, ...]

def movement_permission(player: PlayerState, unit: UnitCard) -> MovePermission:
    # NEW_THIS_ROUND / DEPLOYMENT_MODULE / MOBILITY_TECH / REDEPLOY_SKILL
    ...
```

规则要求：

- round 1 opening builder 将开局单位 entity ID 放入 `spawned_this_round`；
- `BUY_UNIT`、单位增援和专家赠送继续向该集合写入；
- `advance_round` 清空 `spawned_this_round` 和 `redeployed_this_round`；
- 部署模块和高速引擎从单位装备/兵种级 `tech_map` 动态推导，不复制成永久布尔位；
- `MOVE_UNIT` 必须先调用 `movement_permission`，失败返回
  `UNIT_NOT_MOVABLE_THIS_ROUND`；
- save/load、copy、digest、diff、不变量和旧 schema adapter 均覆盖新字段；
- 旧 state 缺失新字段时迁移为 `()`，不能误把全部旧单位设为可移动。

### 2.3 Typed action

新增：

```python
class ActionKind(str, Enum):
    ACTIVATE_ENERGY_TOWER_SKILL = "activate_energy_tower_skill"

@dataclass(frozen=True)
class ActivateEnergyTowerSkillArgs:
    skill_id: int
```

再部署继续使用既有 `RELEASE_COMMANDER_SKILL`，但 registry 将 `1000001` 标为
`target_kind=unit`、`transition_complete=true`、`battle_fidelity=exact`。能量塔动作从
浏览器、normalizer、canonicalizer 到 deploy 全程使用 typed action；raw adapter 仅为旧
fixture/旧 shard 的兼容边界，内部立即转成同一个 typed handler。

## 3. T1：修复购买上限

- [ ] 将购买基数从 `5` 改为 `2`，删除所有“基数 5”的过期注释和前端假设；
- [ ] 把批量征召和 `10004` 的叠加逻辑收敛到 `buy_limit_quote`；
- [ ] receipt 在达到上限时返回 `BUY_LIMIT_REACHED`，detail 包含
  `used/limit/base/blueprint_bonus/officer_bonus`；
- [ ] GameView 增加 `buy_limit`，每个购买按钮按 `remaining > 0` 和资金共同决定可用性；
- [ ] 页面 HUD 显示“本回合已购买 1/2”，额度来源可展开查看；
- [ ] Undo、快速连续点击和 stale version 场景不允许超买；
- [ ] 历史对手计划也走同一额度校验，不为回放动作开后门。

### T1 完成 Gate

| 场景 | 允许购买数 |
|---|---:|
| 默认 | `2` |
| 批量征召一次 | `3` |
| 一个额外部署位 | `3` |
| 批量征召一次 + 一个额外部署位 | `4` |
| 两个额外部署位 | `4` |

以上场景均需覆盖第 `limit + 1` 次购买被拒、拒绝 digest 不变、Undo 后可再次购买。

## 4. T2：实现跨回合移动权限

### 4.1 Transition

- [ ] 实现 `movement_permission` 和稳定 reason code；
- [ ] round 1 开局单位、本回合买入、增援赠送、专家赠送分别具备来源测试；
- [ ] 上回合已有普通单位在 round 2+ 移动时被拒，位置和朝向均不改变；
- [ ] 部署模块 `13040001` 赋予其绑定单位每回合移动权；更换装备后下回合不再享有；
- [ ] 高速引擎 `1606/1611/1616` 赋予同兵种现有及之后新买单位移动权；
- [ ] 同一合法单位可多次移动，Undo 只回退最后一次位置；
- [ ] 卖出单位、装备替换、科技购买顺序和再部署顺序均不留下悬空 entity ID；
- [ ] replay adapter 从任意 round 快照初始化时默认没有“本回合新增”单位，随后由该回合
  action 建立权限。

### 4.2 GameView 与前端

每个单位增加：

```json
{
  "movable": false,
  "move_reasons": [],
  "move_blocker": "UNIT_NOT_MOVABLE_THIS_ROUND"
}
```

- [ ] 不可移动单位显示锁定样式，不进入拖动/落点模式；
- [ ] 可移动单位在详情中说明来源，例如“本回合新单位”“部署模块”“高速引擎”或
  “再部署”；
- [ ] 前端只做交互禁用，服务端仍必须重新校验伪造请求；
- [ ] 使用部署模块、购买高速引擎或释放再部署后，用服务器新 GameView 立即刷新单位状态；
- [ ] 被拒移动显示明确原因，不把它混同于地图越界。

## 5. T3：实现再部署 `1000001`

- [ ] 将 `1000001` 加入 transition 技能 registry 和 capability，不加入 pysim battle event；
- [ ] `_release_commander_skill` 对它执行单位目标合法性和本回合次数校验；
- [ ] 成功写入 `redeployed_this_round` 并消费/冷却槽位，下一回合重新激活；
- [ ] normalizer 的 `ID=0 + SkillIndex` 与显式 `ID=1000001` 解析一致；
- [ ] `CancelReleaseCommanderSkill`、Undo 和 action-local 单位引用保持现有折叠语义；
- [ ] GameView 将它显示为“单位目标 · 本回合可用/已用”，只高亮当前不可移动的己方单位；
- [ ] capability scanner 与 runtime 接受/拒绝一致，重新计算样本和本地 manifest 可玩前缀。

## 6. T4：把 ActiveEnergyTowerSkill 做成完整能力

- [ ] typed model、JSON codec、normalizer `tower_skill` 条目和 canonicalizer 完成；
- [ ] 旧 `RAW_UNSUPPORTED(ActiveEnergyTowerSkill)` 兼容入口只做 typed 转发；
- [ ] `5/6` 的价格和效果迁到版本化 registry，deploy 与
  `pysim/battlefield/compiler.py` 不再各写魔法数；
- [ ] GameView 返回 `supported/cost/affordable/active_count/fidelity/confidence`；
- [ ] 前端提交 `activate_energy_tower_skill`，不再伪装成 `raw_unsupported`；
- [ ] 资金、ledger、Undo、save/load、round reset、battle input digest 和 trace 全链路测试；
- [ ] scanner 接受正规化后的 `tower_skill 5/6`，其他 ID 保持精确
  `UNSUPPORTED_ACTION_FIELD`；
- [ ] 修正当前 compiler 中“stacking, free”的过期注释，费用由 transition 在战前扣除。

## 7. T5：继续扩展 ReleaseCommanderSkill 与 pysim

### 7.1 优先级与交付边界

本阶段按“可复用现有事件模型、用户选择覆盖率、数值可信度”分批实现。每个技能都必须
先完成 decode、目标校验、费用/库存、持久或回合状态、battle/settlement、trace、
registry 和测试六段，不能只把 ID 加进白名单。

#### P0：本阶段必做

- 再部署 `1000001`（T3）；
- 回归现有导弹打击 `300001`、空投护盾 `800001`、燃烧弹 `100002`、地底威胁
  `1200001`、呼叫机群 `1200003`、强化训练 `1100001`；
- 校正现有燃烧弹定义与 committed 调查表的差异：调查表为直线火墙、`270/s`，当前
  pysim 是点圆形场、`352/s`。未取得裁决前保留 `provisional`，不得升级为 verified。

#### P1：优先补齐可复用现有 strike/summon 调度器的技能

| ID | 技能 | 目标实现 |
|---:|---|---|
| `300003` | 轨道轰炸 | 15 枚、每枚 2500 的多 strike 序列，落点分布进入 seed/digest |
| `300004` | 核弹 | `at=15s`、70000 伤害的延迟 strike，trace 显示预警与命中 |
| `300007` | 轨道标枪 | 半径 30、70000 伤害，并显式绕过护盾吸收 |
| `1200002` | 犀牛来袭 | 复用 summon event，空投 1 个犀牛 |
| `1200004` | 呼叫战舰 | 复用 summon event，空投 1 个霸主 |
| `1200005` | 天降火神 | 复用 summon event，空投 1 个火神 |

技能召唤物只存在于本场 battle，不写回持久 `PlayerState.units`，不获得下一回合移动权。

#### P2：需要新增状态/区域效果框架，完成多少以 Gate 为准

| ID | 技能 | 主要引擎能力 |
|---:|---|---|
| `200001/200002` | 电磁冲击/巨型电磁冲击 | 护盾伤害、科技暂时失效、减速 40%/25s |
| `200003` | 光子投射 | 友军 20s 减伤 30% 与负面状态免疫 |
| `300005` | 闪电风暴 | 区域周期伤害与减速 65%/20s |
| `300006` | 离子轰炸 | 随时间移动的持续伤害区域 |
| `500002` | 酸液弹 | 跨整场/一回合区域、百分比掉血与承伤倍率 |
| `600002` | 烟雾弹 | 区域内射程 `-35%` |
| `1500002` | 移动信标 | 多单位目标与路径/waypoint 控制 |

P2 需要先抽象 `TimedAreaEffect`/status lifecycle，不能把所有效果继续塞进 `_burns`。
未完成的 ID 保持 unsupported；实现路径完整但数值未校准的标为
`battle_fidelity=exact, confidence=provisional, effect_complete=false`。

### 7.2 技能共同契约

- 技能费用在选择增援卡时扣除，释放动作不重复扣费；
- 释放必须有当前 active slot，显式 ID 和 `SkillIndex` 解析到同一槽；
- position、line、unit、multi-unit 等目标类型进入定义表，前端不从中文说明猜目标；
- 多落点/直线区域必须保存完整目标数据，不能只取 `positions[0]`；
- `BattleInput`、trace 和 fidelity warning 可定位到 `skill_id/slot/ref`；
- 未知 ID、错误目标、冷却中、重复释放均稳定拒绝且不消费技能槽；
- capability scanner 直接读取同一 registry，禁止另建支持白名单。

## 8. Schema、兼容与数据重建

- [ ] Transition schema 升级，旧 state 缺少 `redeployed_this_round` 时补空 tuple；
- [ ] GameView schema 升级，增加购买额度和逐单位移动权限；
- [ ] norm schema 增加 typed `tower_skill`，旧 passthrough shard 读取时兼容转换；
- [ ] replay snapshot 的 `energyTowerSkills_raw` 在 adapter 中保留或显式证明不影响 5/6
  合法性，不能继续无说明丢弃；
- [ ] 重建 `data/samples/rounds.json` 的 norm 工件、sample replay library、opening catalog
  （若 schema 变化）和 committed manifest；
- [ ] 本地全量语料只重建派生产物，不提交原始 `.grbr` 或大型本地数据；
- [ ] 输出重建前后 runtime/strict-effect 前缀变化和新增 blocker 分布。

## 9. 测试任务

### 9.1 单元与属性测试

- 购买额度：默认、蓝图、单/多专家、叠加、拒绝、Undo、round reset；
- 移动权限：六种允许来源、普通旧单位拒绝、多次合法移动、换装备、买科技先后顺序；
- 再部署：显式 ID/槽解析、单回合一次、非法目标不消费、Undo、下一回合重置；
- 能量塔：5/6 费用、叠加/次数、资金不足、round reset、SideMods 编译；
- 新 commander skill：target shape、slot/CD、battle input digest、数值、trace、确定性；
- 所有拒绝路径验证 state digest、ledger 和 session version 均不变；
- save/load round-trip 和旧 schema migration。

### 9.2 回放与 oracle 测试

- 从真实回放抽取 round 2+ 旧单位移动样本，逐条解释其权限来源；
- 单独冻结部署模块、高速引擎、再部署、增援赠送和专家赠送 fixture；
- 对手历史 plan 出现非法旧单位移动时返回精确 blocker，不回灌下一回合快照；
- scanner/runtime disagreement 为 `0`；
- 对已实现技能比较真实 FightReport/trace 可观测量，数值未校准时不标 verified。

### 9.3 API 与浏览器验收

1. 普通开局 round 1 连买 2 个单位后，第三个购买按钮禁用且伪造请求被拒；
2. Undo 一个购买后剩余额度恢复为 1；
3. 激活批量征召后可以购买第三个单位；持有 `10004` 时同样增加一个额度；
4. round 2 普通旧单位不可拖动，本回合新单位可拖动；
5. 部署模块、高速引擎和再部署分别能把对应单位变为可移动；
6. 再部署本回合第二次释放被拒，下一回合恢复；
7. 强化瞄准扣 100、高速移动扣 50，按钮提交 typed action，battle input 出现正确增益；
8. 至少各完成一个 P1 strike 与 summon 技能的页面释放、trace 播放和战斗结算；
9. 历史对手和人类全程使用同一合法性，没有 silent ignore 或客户端真状态。

## 10. 实施顺序与依赖

1. 冻结现有测试、样例 state digest、manifest 前缀和无技能 battle digest；
2. 完成 T1 购买额度单一规则源；
3. 完成 T2 移动权限模型、schema 与 transition；
4. 完成 T3 再部署，随后更新前端移动交互；
5. 完成 T4 能量塔 typed action，重建 norm/sample 数据；
6. 按 P1 顺序扩展 commander skill 与 pysim，逐个落 registry/测试，不做一次性大开关；
7. 若时间允许，先建立 P2 区域/status 框架，再接入一个代表技能；
8. 跑完整测试、回放 scanner、样例多回合游戏和浏览器验收；
9. 将实际结果、前缀变化、偏差和 QA 裁决追加到本文实施总结。

推荐任务拆分：T1/T2/T3 必须连续完成，因为购买/新增单位/移动许可共享回合状态；T4 可在
其后独立完成；T5 的每个技能按“一个 registry 定义 + 一个 compiler/engine handler + 一组
测试”纵向切片，避免只完成前端或只完成 pysim。

## 11. Definition of Done

- 默认每回合只能购买 2 个单位，批量征召与每份额外部署位严格相加；
- 所有移动均能由服务器给出允许来源，普通旧单位不能移动；
- 部署模块、高速引擎、再部署和所有本回合新增单位的例外正确；
- 再部署 `1000001` 不再阻塞回放，并且不产生错误 battle effect；
- 能量塔 `5/6` 从 API 到 pysim 全程 typed，价格和效果正确；
- P1 至少完成一个多/延迟 strike 和一个召唤技能，其余未完成技能保持精确 unsupported；
- 前端 legal state、transition runtime 和 capability scanner 使用同一规则源；
- 完整测试、样例数据重建、多回合审计游戏和浏览器验收通过；
- 无负资金、超买、非法移动、重复 entity ID、悬空移动权限、silent ignore 或 rejected mutation；
- 实施总结已写回本文。

## 12. 非目标

- 不在本阶段改变单位移动的地图边界、跨中线和 flank 传送规则；
- 不让临时技能召唤物进入长期阵容或购买额度；
- 不为通过历史回放而跳过非法移动/超额购买；
- 不把 `confidence=provisional` 宣称为真实游戏数值已验证；
- 不要求一次完成全部 21 种舰长技能，但已宣布支持的技能必须完成全链路；
- 不扩展 2v2、特殊模式、Windows 注入器或新的 RL observation 编码。

## 13. QA：需要确认的游戏规则

以下问题不阻塞任务书编写；实施前应由用户裁决并把答案更新到“已确认规则”。

1. **round 1 开局单位**：是否全部可以自由移动？本任务书暂按“没有参加过上一回合战斗，
   因此可移动”处理。

对的 开局单位全部可以移动

2. **深渊高速引擎**：gamedata 中深渊也有 `tech 1629`，描述同样写着每回合可改变部署
   位置；是否应与兵蜂/霸主/凤凰一起获得移动权？本任务书当前仅冻结用户明确列出的三种。

是的 高速移动都有这个功能

3. **再部署次数**：若玩家拥有两份 `1000001`，是每位玩家全局每回合只能用一次，还是
   每个技能槽各可用一次？本任务书暂按“全局一次”处理。

这取决于玩家有几个再部署 如果只有一个 就只能一次 （如果有多个再部署技能是多次的）
也就是再部署使用之后再部署会进入冷却

4. **能量塔重复释放**：强化瞄准/高速移动同一回合是否允许重复购买并叠加？当前实现和
   UI 按可重复叠加处理，但需求只确认了费用与单次效果。

只能单次购买

5. **燃烧弹**：以调查表的直线火墙、270/s 为准，还是保留当前 pysim 的圆形区域、
   352/s？还需要确认持续时间和直线宽度。

如果是战场指挥官技能 应该是直线火墙 调查表里面的对

如果是火神的燃烧弹科技落点是个圆型

6. **P1 技能的友伤/护盾规则**：轨道轰炸、核弹是否会被护盾吸收、是否命中友军；轨道
   标枪已按描述暂定绕过护盾。若有游戏内实测，应优先提供这三类校准局。

简单来说护盾会保护区域内的陆军友军（但是对于凤凰蜜蜂这样的空军是没有保护效果的）

轨道轰炸 核弹 都会 对友军造成伤害

## 14. 实施总结（完成后续写）

实际开发完成后在此追加，至少包含：

- 实际代码、schema、前端和数据改动；
- 自动化测试数量、命令与浏览器验收结果；
- 购买/移动规则的真实回放对拍结果；
- 每个新增 commander skill 的 fidelity、confidence 和校准证据；
- runtime/strict-effect 可玩前缀变化；
- 与本任务书的偏差、QA 裁决和仍未支持的技能。
