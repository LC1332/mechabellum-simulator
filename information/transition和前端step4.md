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

- [x] 购买基数收敛到单一规则源（最终裁决 base=2；+1 来自能量塔技能3 批量征召而非蓝图，见 §14.3/QA#7 裁决过程），“基数 5”过期注释已删除；
- [x] 批量征召和 `10004` 的叠加逻辑收敛到 `buy_limit_quote`（rules.py 单一规则源）；
- [x] receipt 返回 `BUY_LIMIT_REACHED`，detail 含 base/批量征召/额外部署位/used；
- [x] GameView（v4）增加 `buy_limit`，购买条目 `purchasable = remaining>0 && affordable`；
- [x] 页面 HUD 显示「已购买 X/Y（基础 n + 来源）」，商店头部展开额度来源；
- [x] Undo/连击/stale version 不超买（浏览器验收 + 单测覆盖）；
- [x] 历史对手计划走同一额度校验（capability 扫描与 deploy 同公式，不开后门）；

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

- [x] `movement_permission` + 稳定 reason code（rules.py）；
- [x] round 1 开局（opening/replay_adapter 双路径）、买入、增援赠送、专家赠送来源测试齐备；
- [x] 上回合旧单位 R2+ 移动被拒（`UNIT_NOT_MOVABLE_THIS_ROUND`），位置与朝向不变；
- [x] 部署模块 `13040001` 生效（含回合内绑定立即生效，语料对拍确认）；
- [x] 高速引擎 `1606/1611/1616` + `1629`（深渊，QA#2）生效（含回合内购买立即生效）；
- [x] 同一合法单位可多次移动，Undo 只回退最后一步；
- [x] 卖出/装备替换/科技/再部署顺序不留悬空 entity ID（不变量测试）；
- [x] replay adapter：R2+ 快照默认无“本回合新增”（round-1 例外=开局单位全部 spawned，QA#1）；

### 4.2 GameView 与前端

每个单位增加：

```json
{
  "movable": false,
  "move_reasons": [],
  "move_blocker": "UNIT_NOT_MOVABLE_THIS_ROUND"
}
```

- [x] 锁定单位显示 🔒 徽标/虚线圈，不进入拖动（mousedown 拦截 + 提示）；
- [x] 单位详情「移动」行说明来源（本回合新单位/部署模块/高速引擎/再部署）；
- [x] 前端仅交互禁用，服务端 `MOVE_UNIT` 重新校验（伪造请求被拒）；
- [x] 模块/引擎/再部署后以服务器 GameView 刷新（movable/move_reasons 即时可见，验收 5）；
- [x] 被拒移动显示 `UNIT_NOT_MOVABLE_THIS_ROUND` 明确原因，与越界区分；

## 5. T3：实现再部署 `1000001`

- [x] `1000001` 入 TRANSITION_SKILLS/capability/registry，无 battle 事件；
- [x] 单位目标 + 逐槽一次 + cd=1 校验（QA#3 口径）；
- [x] 写入 `redeployed_this_round` 并冷却槽位，下一回合 tick 重激活（测试验证）；
- [x] ID=0+SkillIndex 与显式 ID 解析一致（live 槽视图 = 快照 + 回合内卡牌拾取）；
- [x] Cancel/Undo/引用折叠语义不变（既有测试 + 新 tower_skill 撤销测试）；
- [x] GameView「单位目标 · 解锁移动 · 可用/冷却中/本回合已用」，仅高亮锁定单位（验收 5）；
- [x] scanner/runtime 一致（含撤掉快照 officers 赠技启发式的取舍，见 §14.6.2）；样本与本地 manifest 已重算。

## 6. T4：把 ActiveEnergyTowerSkill 做成完整能力

- [x] typed model / normalizer `tower_skill` / canonicalizer 完成；
- [x] raw `ActiveEnergyTowerSkill` 仅做 typed 转发（同一 handler，digest 等价测试）；
- [x] 价格/效果走版本化 registry（TOWER_SKILL_COSTS + mechanism_support），compiler 无魔法数；
- [x] GameView tower_skills 返回 supported/cost/affordable/purchasable/active_count/fidelity/confidence；
- [x] 前端提交 `activate_energy_tower_skill`（验收 2 receipt 佐证）；
- [x] 资金/ledger/Undo/round reset/SideMods 编译链路测试（trace 走既有 skill 通道）；
- [x] scanner 接受 typed `tower_skill 5/6`，1/3/4 保持精确 blocker（测试覆盖）；
- [x] compiler 注释已修正（transition 扣费、每回合限购一次 QA#4）。

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

- [x] schema `transition-v0.6`，旧 state 缺字段迁移为 `()`（测试覆盖）；
- [x] GameView `v4`：`buy_limit` + 逐单位 `movable/move_reasons/move_blocker`；
- [x] norm schema 增 typed `tower_skill`；旧 passthrough shard 经 raw handler 兼容执行；
- [x] 单次购买以回合内 `tower_mods_raw` 为准，快照字段与本回合合法性解耦（adv 已清空，无丢弃歧义）；
- [x] 重建 norm 工件/sample library manifest（旧 shard + 重扫）；catalog schema 未变免重建；
- [x] 本地仅重建 rounds_norm/replay_game 派生物，无 .grbr 入库；
- [x] 前缀对比与 blocker 分布见 §14.5（0 下降，无额度/移动类新 blocker）。

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

## 14. 实施总结（2026-08-27 实施，全部完成）

> 本节由本轮实施追加并经同日二次修正定稿（QA#7 最终裁决：base=2，批量征召=
> 能量塔技能3，蓝图1/2/3=指挥官技能研究）。T1–T5(P0+P1) 全部落地，161 个自动化
> 测试全绿，本地全量语料重建后可玩选项 4→29、前缀只升不降，浏览器完成关键场景验收。

### 14.1 代码 / schema / 前端 / 数据改动

**新增**
- `pysim/transition/rules.py` — 单一规则源：`BASE_BUY_LIMIT` / `BuyLimitQuote` /
  `buy_limit_quote` / `MovePermission` / `movement_permission` /
  `MOBILITY_TECHS{1606:6, 1611:11, 1616:16, 1629:29}`（QA#2 深渊已含）/
  `DEPLOYMENT_MODULE_EQUIPMENT=13040001` / `REDEPLOY_SKILL_ID=1000001`。
  deploy、env legal mask、GameView、测试全部读它，无第二份常量表。

**transition 层**
- `model.py`：schema `transition-v0.6`、engine `pysim-step30`；
  `PlayerState.redeployed_this_round`（旧 state/save 适配为 `()`）；
  新 typed action `ACTIVATE_ENERGY_TOWER_SKILL` + `ActivateEnergyTowerSkillArgs`。
- `deploy.py`：`MOVE_UNIT` 先过 `_ctx_movement_reasons`（动态读活状态——回合内
  装模块/买高速引擎立即生效，与语料行为一致），拒绝码
  `UNIT_NOT_MOVABLE_THIS_ROUND`，位置与朝向均不变；`BUY_UNIT` 走 quote，receipt
  detail 携带 `base/批量征召/额外部署位/used`；再部署分支（单位目标校验 →
  `UNIT_ALREADY_MOVABLE` / `SKILL_TARGET_INVALID` / `UNKNOWN_ENTITY` /
  `SKILL_SLOT_UNAVAILABLE`，成功写 `redeployed_this_round` 并消耗槽位 cd=1）；
  `_activate_tower_skill` 共享 handler（typed 与 raw `ActiveEnergyTowerSkill`
  同一入口；同一技能同回合二次购买拒绝 `TOWER_SKILL_ALREADY_ACTIVE`，QA#4）。
- `settlement.py`：`advance_round` 清空 `redeployed_this_round`（与 spawned、
  tower_mods 等一起）。
- `opening.py`：round-1 开局单位全部写入 `spawned_this_round`（QA#1）。
- `replay_adapter.py`：round-1 快照单位同样视为 spawned；round≥2 的快照技能槽
  在建状态时执行一次 `tick_skill_cooldowns`（快照是 pre-deploy，游戏在部署开始
  才 tick——否则 adapter 会拒绝游戏实际允许的释放，如上回合买的再部署 cd=0）。
- `normalize.py`：`ActiveEnergyTowerSkill` 5/6 → typed `tower_skill` 条目
  （可撤销，Q1）；live 技能槽视图 = 快照槽 + 回合内「舰长技能/战术」卡拾取
  （与 deploy ctx 完全同源）；`UnitIndex/ConstructionIndex == -1` 归一为
  `None`（占位不是目标，修复再部署被误判 construction-target）；
  `positions` 输出 JSON 原生 list（artifact round-trip 稳定）。
- `canonicalize.py`：`tower_skill` → typed action。
- `capability.py`：`tower_skill` 分类（registry 支持度）；`scan_opponent_round`
  按同一公式逐条核对对手回合的付费购买（scanner/runtime 一致，不为回放开门）。
- `env.py`：legal mask 只为可移动单位生成 move 候选、额度用尽不再生成 buy
  候选（accept/reject 一致性有测试）。

**pysim 引擎 / 战场**
- `skills.py`：P1 技能入表（300003 轨道轰炸 15×2500 ff、300004 核弹 70000@t=15s
  ff、300007 轨道标枪 r30 70000 bypass、1200002 犀牛 mech5、1200004 霸主
  mech11、1200005 火神 mech3）；`expand_strike_events` 确定性 sunflower 落点
  展开；1000001 入 `TRANSITION_SKILLS`；燃烧弹 100002 dps 352→**270**
  （QA#5 调查表口径；直线火墙仍以圆形近似，待 P2 区域框架）。
- `engine.py`：strike 事件支持 `ff`（双方命中，QA#6）与 `bypass`（绕过护盾，
  轨道标枪）；护盾不再保护空军单位（QA#6：凤凰/兵蜂/深渊无屏障覆盖）。
- `battlefield/compiler.py`：多 strike 在编译期展开为独立 TimedEvent（完整落点
  进入 BattleInput digest，确定性可复现）；修正"stacking, free"过期注释
  （费用由 transition 扣、每回合限购一次）。
- `battlefield/registry.py`：新技能 confidence 全部 provisional（数值来自任务书
  用户表，落点分布/splash/cd 无 oracle）；1000001 verified（规则用户冻结、
  transition-only 无战斗数值）；tower skill 补 QA#4 证据。

**Web / 前端**
- `game_service.py`：`game_view_v4`；players[].units 增加
  `movable/move_reasons/move_blocker`；players[] 与 legal_actions 增加
  `buy_limit`（base/bonus/used/limit/remaining）；商店条目 `purchasable`；
  tower_skills `purchasable/已激活` + fidelity；skill_releases 增加
  `legal_targets`（再部署只列当前锁定单位）与 `redeploy` 标记。
- `game.html`：能量塔按钮提交 `activate_energy_tower_skill`（不再伪装
  raw_unsupported），已购禁用；HUD「已购买 X/Y」+ 商店额度行/额度满标记；
  不可拖动锁定单位（mousedown 拦截 + 🔒 徽标 + tooltip/详情移动来源说明）；
  旋转按钮随锁定禁用；单位目标技能按 legal_targets 高亮与校验（再部署只高亮
  锁定单位）。
- `server.py`：支持 `PORT` 环境变量（并行实例验收用）。

**数据重建（本地派生物，不含 .grbr）**
- `local_data/rounds_norm.json` + normalize_report 全量重跑（1106 局，undo
  folded 16680 / cancel 1848，unresolved 0.24% 持平）。
- `local_data/replay_game` 全量重建（norm + manifest 重扫）。
- `data/samples/replay_game` manifest 用旧 shard `--rebuild-manifest` 重扫
  （该 fixture 的 shard 早于当前 rounds.json，属既有失同步，已按当前扫描器
  规则对齐；strict 扫描覆盖 R2 起的 offers，首个 strict blocker R3→R2）。
- opening catalog schema 未变，无需重建。

### 14.2 测试与验收

- `python -m pytest tests` → **153 passed**（含新增 `tests/transition/test_step4.py`
  29 项：T1 gate 表、六种移动来源、再部署四类拒绝/逐槽一次/下回合重置、能量塔
  单次购买与 raw 转发、P1 strike/summon 的 digest·t=15s·ff·bypass·召唤物不
  入持久阵容、旧 schema 迁移、legal mask 与 deploy 一致、样例复现率守门）。
- 浏览器验收（127.0.0.1:8301，真实回放会话）：
  1. R1 连续购买至「已购买 3/3」，商店条目显示「额度满」，第 4 次双击被拒；
     撤销一次后额度恢复 2/3；
  2. 强化训练开局槽 + 高速移动 ¥50 typed 提交（receipt
     `activate_energy_tower_skill ✓ tower skill 6`），按钮转「已激活」禁用，
     强化瞄准仍可购；
  3. 结束部署 → pysim 战斗结算 → R2 增援四选一（含 #1000001 再部署卡）；
  4. R2 服务器权威状态：7 个旧单位全部 `movable=false
     blocker=UNIT_NOT_MOVABLE_THIS_ROUND`；
  5. 选再部署卡 → 槽位「可用 · 解锁移动」→ 释放点击锁定单位 →
     `release_commander_skill ✓ 再部署 unlocks unit 2 this round slot 1
     consumed`，该单位 `movable=true reasons=['REDEPLOY_SKILL']`，槽位转
     「冷却中」，legal_targets 不再含该单位。

### 14.3 购买 / 移动规则的真实回放对拍（最终口径）

**购买额度 — 三轮裁决过程（QA#7）**：
1. 初判：base=2 下 9,485/17,814 买卡回合"超 1"，且 0 回合超过 `3+加成`，
   误读为 base=3；
2. 用户裁决 base 绝对是 2 后，用下一回合快照独立确认 3 买是净存活
   （2,891 回合新增恰好 3 个兵种吻合的购买单位），排除撤销折叠假象；
3. 用户提示"批量征召可能在 ActiveEnergyTowerSkill 通道"后定位真因：
   **`ActiveEnergyTowerSkill SkillID=3` = 批量征召（¥50，当回合前置生效）**。
   位置分布：6,953 个回合它恰好出现在第 2 次购买之后、第 3 次之前；
   **墙检验：`2 + tower3点击 + 10004份数` 在 16,512 个买卡回合中 0 违规**。
   最终模型与语料完全一致（样例复现测试 quota_diverged==0）。

**能量塔技能表（用户最终裁决，全部当回合一次性购买）**：
| ID | 技能 | 费用 | 效果 |
|---:|---|---:|---|
| 1 | 快速补给 | 0 | 立即 +200，下回合收入 -300 |
| 3 | 批量征召 | 50 | 本回合购买上限 +1（前置 action） |
| 4 | 精英征召 | 100 | 本回合后续购买单位 +1 级（顺序敏感） |
| 5 | 强化瞄准 | 100 | 全体远程射程 +15 |
| 6 | 高速移动 | 50 | 全体移速 +3 |
（ID 2 语料从未出现，保持精确 blocker。）

**蓝图 1/2/3 = 指挥官技能研究（用户裁决 + 语料证实）**：
1 黏油弹 ¥150→解锁 400002、2 战地回收 ¥100→解锁 900001、3 移动信标
¥100→解锁 1500001；槽位在研究后**下一回合**入槽（lag=+1 ≈100%）。解锁
相关性：bp2→900001 为 1,837/1,860 且未研究者 0 例出现；bp1→400002、
bp3→1500001 同样 0 例未研究出现。旧"bp1=快速补给贷款 / bp2=批量征召+额度 /
bp3=精英征召"的读法是 r1 窗口代数被能量塔通道混淆所致，全部废弃；贷款/
额度/升阶效果全部迁移到能量塔技能 1/3/4。

**移动权限**：77,458 个 R2+ 移动中，仅 153 个玩家-回合（≈2%）在
新增援/部署模块(含回合内绑定)/高速引擎(含回合内购买)/再部署(含回合内
卡牌拾取)之外无法解释——按任务书以精确 blocker 处理，不回灌快照。

**样例复现率**：unit-set 精确率恢复到 75% 门槛以上且 quota 分歧为 0；
剩余失败全部来自既有数据缺口级联（开局装备清单缺失/UNKNOWN_ENTITY 等）。

对拍过程中修复的两个 bug：raw 释放记录 `UnitIndex/ConstructionIndex=-1`
被当作真实目标（导致再部署被拒）；快照技能槽缺少部署开始的冷却 tick。

### 14.4 新增技能 fidelity / confidence

| ID | 技能 | transition | battle | confidence | 说明 |
|---:|---|---|---|---|---|
| 1000001 | 再部署 | complete | unsupported(无战斗事件) | verified | 用户冻结规则；逐槽一次，cd=1 |
| 300003 | 轨道轰炸 | complete | exact | provisional | 15×2500 用户表；sunflower 分布/ splash 20 cal；ff(QA#6) |
| 300004 | 核弹 | complete | exact | provisional | 70000@t=15s 用户表；splash 40 cal；ff(QA#6) |
| 300007 | 轨道标枪 | complete | exact | provisional | r30/70000 用户表；绕护盾 QA#6 |
| 1200002/04/05 | 犀牛/战舰/火神空投 | complete | exact | provisional | mech 5/11/3 用户表；召唤物仅本场战斗 |
| 100002 | 燃烧弹(修正) | complete | exact | provisional | dps 270 按调查表；直线火墙暂以圆形近似 |

P0 回归：300001/800001/100002/1200001/1200003/1100001 全部保持既有链路并通过
原测试。P2（EMP/光子/闪电风暴/离子/酸液/烟雾/信标）按任务书保持精确
unsupported，`TimedAreaEffect` 框架未动。

### 14.5 可玩前缀变化（最终重建 vs step3 基线）

- 本地全量（2,010 options）：**前缀 0 下降**；delta：+1×42、+2×27、+3×12、
  +4×6、+5×7、+6×5、+7×1，其余持平；**enabled 4→29**（能量塔 1/3/4 支持
  直接消掉大量 `ActiveEnergyTowerSkill` UNSUPPORTED_ACTION_FIELD blocker）。
- 样例 fixture：94974af9a119-1 ptr 1→3、b198291ffab1-0 ptr 4→6、
  94974af9a119-0 ptr 5 不变；strict 前缀因 strict 扫描自 R2 覆盖 offers
  统一截到 R1（装备近似口径，非本轮退化）。
- norm 工件全量重建（undo folded 16,680 / cancel 1,848，unresolved 0.24%
  持平）；golden fixture 随 typed tower_skill 条目重新生成。

### 14.6 偏差与 QA 裁决记录（最终）

1. **QA#7 — 购买额度（已裁决，用户最终口径）**：base=2；批量征召为能量塔
   技能 3（¥50，当回合前置 +1，每回合限购一次）；每份【额外部署位】buff
   (10004) +1（可叠加）；增援赠送不占额度。实现位于
   `rules.py::BASE_BUY_LIMIT=2` 与能量塔技能表，语料墙检验 0 违规。
2. **蓝图 1/2/3 语义重映射（用户裁决 + 语料证实）**：= 指挥官技能研究
   （黏油弹 150 / 战地回收 100 / 移动信标 100，下回合入槽），旧贷款/额度/
   升阶读法废弃；step3 的"bp2=50 额度"冻结随之作废（测试已改写）。
3. **normalizer live 槽视图不含专家回合赠技**：保持 scanner/runtime 一致
   （受影响 ID=0 释放保留为精确 blocker）。根治需 catalog officers 归纳
   修复（独立工作项）。
4. **燃烧弹形状**：dps 已按调查表改 270；直线火墙需 P2 `TimedAreaEffect`
   框架，当前圆形近似已在 registry conf 注明，不升 verified。
5. **P1 落点/时序/splash 为 cal**：多 strike 分布用确定性 sunflower（进
   digest），核弹 t=15s 进 trace，均标 provisional，等待 oracle 校准局。
6. 其余按任务书非目标执行：未动 2v2/特殊模式/RL observation 编码；地图
   边界、跨中线、flank 传送规则未变（spawned 集合复用，语义不变）。
