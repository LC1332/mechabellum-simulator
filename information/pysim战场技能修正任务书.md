# PySim 战场技能修正任务书：Step 5 §3.1 专项

> 来源：[`transition和前端step5.md`](transition和前端step5.md) 第 3.1 节。  
> 编写基线：commit `e83c26b`，2026-08-28。  
> 当前状态：**仅编写任务书，尚未实施代码、采集 Windows oracle 或修改支持度。**  
> 本文中的复选框均表示未来施工状态；只有代码、专项 oracle 和回归报告都落地后才能勾选。

## 0. 换机后先填写

当前仓库不包含 Windows oracle 的实现、启动方式或游戏安装位置。开始施工前，先在另一台
机器补全本节；不得把机器相关绝对路径写死进 pysim 源码或提交含账号/token 的配置。

| 项目 | 待填写 |
|---|---|
| Windows oracle 仓库根目录 | `________________________________________` |
| Windows oracle 入口脚本/工程 | `________________________________________` |
| oracle 启动、注入或附加命令 | `________________________________________` |
| oracle 场景输入目录 | `________________________________________` |
| oracle 遥测输出目录 | `________________________________________` |
| 游戏本体根目录 | `________________________________________` |
| 游戏可执行文件 | `________________________________________` |
| 游戏版本 / build / 区服 | `________________________________________` |
| 本仓库在 Windows 上的位置 | `________________________________________` |
| Python/虚拟环境及依赖安装命令 | `________________________________________` |
| oracle 与本仓库之间的文件同步方式 | `________________________________________` |
| 本轮操作者、日期、目标 commit | `________________________________________` |

补全后还要记录以下可复现信息：oracle commit、游戏文件版本或 hash、注入模块版本、场景
seed、地图 ID、帧率/tick、坐标系和一次完整采集命令。仅写“本机测过”不能作为证据。

## 1. 本轮目标与边界

本专项的目标是闭合 Step 5 §3.1 中的技能/装置链路：

```text
回放 raw action
  -> 正规化与技能槽解析
  -> typed release（完整保留一次释放的所有 Positions/目标）
  -> EnvironmentState
  -> BattleInput
  -> pysim 战斗效果
  -> BattleOutcome / 下一回合持久状态
  -> capability、报告和前端 fidelity 展示
```

不能把“回放不再 BLOCKED”当作唯一完成标准。每个机制必须分别说明：

1. 是否能正确 decode；
2. 目标和释放是否合法；
3. 是否改变资金；
4. 是否改变跨回合持久状态；
5. pysim 是否执行战斗效果；
6. settlement 是否需要写回；
7. 证据是 `verified`、`provisional` 还是 `unsupported`。

### 1.1 机制分组

| 分组 | ID / 通道 | 归属 | 本专项目标 |
|---|---|---|---|
| A | `900001 + ConstructionIndex` | transition / construction | 建筑回收、退款、稳定对象引用；无战斗事件 |
| A | `ID=0 + SkillIndex` | codec / slot lifecycle | 还原真实 ID；不能用猜测绕过 blocker |
| A | 快速补给 | economy / round tick | `+200` 当回合、下一回合收入 `-300`；确认真实动作通道 |
| B | `1500001/1500002` | battle movement | 移动信标的选区、保持相对位置、两段路径和途中交战 |
| C | `400002` | persistent area/status | 黏油、护盾裁剪、减速、点燃转换、跨回合地面残留 |
| C | `200001/200002` | damage/status | 小/巨型 EMP、护盾、科技失效和减速 |
| C | `200003` | buff/status immunity | 光子减伤和多类 status 免疫 |
| C | `300005` | timed damage/status | 闪电风暴的落雷、tick 和减速，需反向标定 |
| C | `300006` | moving area damage | 离子轰炸的移动圆区、速度、DPS 和护盾交互 |
| C | `500002` | persistent area/status | 酸液区域、百分比伤害、易伤、持续和叠加 |
| C | `600002` | persistent area/status | 烟雾区域、射程降低、护盾阻挡和持续 |
| D | contraption `30001` | identity / world object | 先识别再实现；身份未知时保持精确 blocker |

### 1.2 明确不在本专项内

- 不顺带重写整个 `pysim/engine.py` 的移动、索敌、弹道或伤害系统；
- 不支持 2v2、特殊模式、非标准地图；
- 不把现有圆形燃烧弹近似自动升级为 verified；
- 不为了回放通过而跳过技能、自动生成目标、补钱或读取未来快照；
- 不用 pysim 自己生成的结果充当真实游戏 oracle；
- 不在 `COMMANDER_SKILLS` 中先填一组猜测数字再宣称机制完成。

## 2. 已冻结规则与仍待标定项

本节只把用户已经给出的规则作为实现输入。没有数字或交互结论的字段继续标为 `待 oracle`。

### 2.1 已冻结为实现口径

1. **建筑回收 `900001`**：卖建筑为高频动作；墙返还 `50`，炮返还 `100`。具体
   construction cid 到“墙/炮”的映射必须从游戏/oracle 确认，不能按外观或价格猜。
2. **快速补给**：立即获得 `200` 补给，下一回合收入减少 `300`。
3. **移动信标**：第一个位置定义半径 `40m` 的选区；选中的单位保持相对选区中心的
   offset，先走向第二位置对应的中心，再走向第三位置对应的中心。多模组单位按实际成员
   是否落入选区分别决定参与。移动途中若射程内有敌人会攻击；pysim v1 可实现为停下攻击，
   射程内无敌人后继续路径。
4. **黏油/烟雾/酸液形状**：从第一个点的半径 `30m` 圆扫到第二个点的半径 `30m` 圆，
   即线段与半径 30 的 swept circle/capsule，而不是两个独立圆。
5. **黏油**：落下时护盾覆盖的地面不生成黏油；区内单位移动速度降低 `55%`；被点燃后
   转化为火焰并在本场战斗结束后消失；未点燃时下一场战斗仍存在，总寿命按“两回合”处理。
   鬼鳐科技黏油弹和火神黏油弹不继承这里的跨回合规则。
6. **EMP `200001`**：约半径 `60m`；命中护盾对护盾造成 `20000` 伤害；护盾内地面单位
   不受 EMP；被命中单位的科技暂时失效、移动速度降低 `40%`、持续 `25s`。
7. **巨型 EMP `200002`**：与小 EMP 共用效果，只修改半径；真实半径待 oracle。
8. **离子轰炸 `300006`**：从 A 到 B 以半径 `20m` 的圆扫过并持续伤害；移动速度、DPS、
   tick、友伤和护盾交互待 oracle。
9. **光子投射 `200003`**：覆盖范围内友军获得 `20s` 光子效果；受到伤害降低 `30%`，
   并免疫 EMP、引燃/燃烧、酸液和退化光束。原记录中的“印染”在实现前需通过游戏文案或
   oracle 确认是否就是“引燃”。
10. **烟雾 `600002`**：与黏油使用相同 swept-circle 范围；落下时也被护盾阻挡。射程降低
    数值、持续和叠加待 oracle。

### 2.2 不能直接写死的规则

- `SkillIndex` 是槽位索引，不是技能 ID。不能全局写成 `SkillIndex=0 => 快速补给`；当前
  回放快照中的槽 0 可以装其他技能。应先区分 `ActiveEnergyTowerSkill` 与
  `ReleaseCommanderSkill` 通道，再重建该玩家该回合的槽表。若 Windows oracle 证明某一
  特定通道/版本确有特殊编码，再把映射连同版本和证据登记。
- 一次 `ReleaseCommanderSkill` 的多个 `Positions` 不能展开成多个独立技能。移动信标的
  三点和黏油/酸液/烟雾的两点必须保留顺序和同一 `release_ref`。
- `30001` 不能映射成 `13030001` 激光瞄具；两者通道和 ID 空间不同。确认身份前不创建
  无效果装置，也不静默 drop。
- “持续一回合”不能未经测量直接等于整场 120 秒；跨回合地面效果还必须明确战斗结束时
  剩余时长如何转换成下一回合状态。
- `confidence=verified` 只由真实游戏 oracle 或可审计的用户冻结规则产生；代码路径跑通
  只能得到 provisional。

## 3. 先升级契约，禁止继续丢语义

当前 `PlayerState.skill_events_raw` 只保存 `(sid, x, y)`，`TimedEvent` 也只有一个
`position`；`deploy._release_commander_skill()` 会把多个 Positions 拆成多个单点事件。
这套结构无法表达移动信标三点路径或 swept-circle 两端点，是实现前必须先修的 blocker。

### T0：版本化 typed release

- [ ] 在 transition 层新增 typed `CommanderSkillRelease`，至少保存：
  `release_ref / skill_id / skill_index / side / ordered_positions / unit_ref /
  construction_ref / source_raw_index`；
- [ ] `ordered_positions` 原样保序；禁止 compiler 根据技能类型前先展开；
- [ ] `PlayerState` 用新的 round-scoped typed releases 替代或旁路兼容
  `skill_events_raw`，升级 `SCHEMA_VERSION`；
- [ ] 为旧 save/state 提供只读迁移：旧 `(sid,x,y)` 可迁为单点 release，但不能伪造缺失点；
- [ ] `normalize -> canonicalize -> deploy` 的 Cancel/Undo 仍以一次 release 为原子操作；
- [ ] 一个 release 只消费一个技能槽，不因有 2/3 个 Positions 消费多次；
- [ ] `BattleInput.digest()` 包含完整有序点列、形状和所有参数；同 seed 双跑 digest 稳定；
- [ ] 未知 ID 和不合法点数返回带 `skill_id/skill_index/raw_index/positions` 的精确 receipt。

建议将战场契约扩成以下方向，字段名可调整，但表达能力不能缩水：

```python
TimedAreaEffect(
    ref, source_id, owner_side,
    shape,                 # circle | capsule | moving_circle
    points, radius,
    starts_at, expires_at,
    affects,               # enemy | friendly | both
    layers,                # ground | air | both
    shield_rule,
    tick_interval,
    params,
)

StatusSpec(
    kind, magnitude, duration,
    stacking, refresh,
    suppression_mask,
    source_ref,
)

WaypointOrder(
    ref, owner_side, selection_center, selection_radius,
    waypoints, member_rows, member_offsets,
    engagement_policy,
)
```

`TimedAreaEffect` 和 `StatusSpec` 应放在 `pysim/battlefield/` 的公开契约/效果模块中；
`legacy_engine.py` 是唯一允许把这些对象翻译为 `Battle` 内部数组的桥。transition、前端和
capability 不得读取 numpy row。

## 4. Windows oracle 数据契约

### T1：先打通可重复采集，不先调参

- [ ] 新增机器无关的场景 schema，例如 `battlefield-skill-oracle-v1`；
- [ ] 输入必须记录：case ID、游戏 build、地图、seed、双方单位/等级/科技/装备/位置、建筑、
  护盾、技能 ID、完整 Positions、释放方、对照组；
- [ ] 输出必须至少记录：每帧或固定 tick 的单位位置/HP/护盾、技能/科技 active 状态、
  移速、射程/当前目标、伤害事件、status 起止、区域生成/消失、胜负和 end time；
- [ ] 多模组单位应有 group/card ID 与 member/unit ID，不能只输出整卡汇总；
- [ ] 每个采集文件保存原始遥测和归一化摘要，归一化器不能覆盖原始文件；
- [ ] 采集失败、字段缺失、游戏崩溃单独记 manifest status，不得生成全 0“成功”样本；
- [ ] 同一场景至少复跑 3 次；固定机制应完全一致，随机机制保留 seed 与分布统计；
- [ ] oracle 产物中不得包含本机账户、token、用户名或无关进程信息。

建议在本仓库落以下可提交产物：

```text
data/battlefield_skill_scenarios/        # 人工可读、机器可跑的输入场景
data/battlefield_skill_oracle/<build>/   # 脱敏后的真实游戏遥测/摘要
data/calib/battlefield_skills/           # 拟合值、容差和版本冻结
tools/build_battlefield_skill_cases.py   # 生成/校验输入，不运行游戏
benchmarks/run_skills.py                 # pysim 对同一批场景重算和 diff
```

若 Windows oracle 已有自己的 schema，优先写 adapter，不复制第二套事实定义。

### 4.1 每类技能的最小 A/B 矩阵

| 维度 | 至少包含的对照 |
|---|---|
| 形状 | 轴向/斜向；刚好在边界内、边界外；A/B 重合；A/B 反向 |
| 阵营 | 敌军、友军、双方同时位于区域内 |
| 空地 | 单独地面、单独空军、同位置空地叠放 |
| 护盾 | 无盾、满盾、技能伤害击破盾、盾未破、单位部分在盾边缘 |
| 多模组 | 全组入选、部分成员入选、中心在外但成员在内、反向情况 |
| 时间 | t=0、效果中点、到期前后一个 tick；跨回合技能再跑下一场 |
| 叠加 | 同 ID 重叠、同类不同来源重叠、刷新前后、先后顺序互换 |
| 免疫 | 光子 vs EMP/燃烧/酸液/退化光束；护盾 vs 各区域效果 |
| 移动 | 静止靶、穿过区域、从区域内离开、移动信标途中遇敌/脱离射程 |

专项验收不得只比较最终 winner。至少比较几何命中集合、首次/末次生效时刻、累计伤害、
status 持续和关键路径点；winner 只作最后一层回归指标。

## 5. transition 与 codec 修正

### T2：建筑回收 `900001`

- [ ] 建立 typed `ConstructionState`，含稳定 `entity_id/ref`、回放 index、cid、side、位置、
  当前 HP/存活、跨回合标记；
- [ ] 从游戏/oracle 冻结 cid → 名称/类别 → 回收价表；已知目标是墙 `50`、炮 `100`；
- [ ] `ConstructionIndex` 只允许引用己方仍存在且可回收对象；未知、敌方、已回收均原子拒绝；
- [ ] 成功时删除/标记回收该对象、增加 supply、写 ledger/receipt，并消费正确技能槽；
- [ ] 回收发生在战斗前，因此该对象不得进入本回合 `BattleInput.world_objects`；
- [ ] Cancel/Undo 后同时恢复对象、资金、slot active/CD 和 digest；
- [ ] 确认墙/炮以外 cid、能量塔、飞弹/护盾装置是否允许回收；未确认类型继续精确拒绝；
- [ ] `ObjectOutcome` 的建筑死亡/HP 写回另立 settlement 测试，避免下一回合引用幽灵建筑。

### T3：`ID=0 + SkillIndex` 与快速补给

- [ ] 用一套 slot allocator 合并：回合快照、专家 `activeRound` 赠技、蓝图下一回合赠技、
  同回合增援卡、重复同 ID 槽、active/CD、Cancel/Undo；
- [ ] resolution 顺序冻结为：显式非零 ID → 当前 typed slot → 本回合已确认 grant event → blocker；
- [ ] 不从下一回合 snapshot 回灌，不按目标形状猜 ID，不按“最常见技能”兜底；
- [ ] 单独审计 `ActiveEnergyTowerSkill SkillID=1` 的快速补给链路。当前实现已有 `+200/-300`，
  需要用 oracle/回放核对动作通道、一次/回合、Undo 与连续两回合债务叠加；
- [ ] 若 unresolved `ReleaseCommanderSkill ID=0 SkillIndex=0` 被证明也是快速补给，增加带
  game-version/channel 条件的 codec 规则；未证明前不污染通用 slot 0；
- [ ] blocker 报告输出 resolved_from、slot history 和失败节点，不能只报 `ID=0`。

## 6. 共享区域与状态框架

### T4：几何、护盾裁剪和跨回合区域

- [ ] 实现 `circle`、`capsule(A,B,r)`、`moving_circle(A->B,r,speed)` 的统一命中函数；
- [ ] 明确边界比较、单位半径是否计入、地图裁剪和浮点容差；
- [ ] 区域命中按 unit row/member，而不是只按 card center；
- [ ] 护盾阻挡地面生成时，在释放瞬间保存被裁剪后的区域/遮罩；盾以后消失不能补生成黏油；
- [ ] 持久地面效果进入 transition state，`advance_round` 递减 round TTL，compiler 在下一场
  重新生成 BattleInput；
- [ ] 引燃把该黏油对象转换为火焰对象，而不是同时保留油和火；
- [ ] area/status 的来源、所有者、起止时间、tick 和叠加顺序进入 trace 与 digest；
- [ ] 现有燃烧弹可以迁到同一框架，但必须保持旧 benchmark 基线或在报告中解释差异。

### T5：status 与 damage pipeline

- [ ] status 至少支持：`SLOW`、`TECH_DISABLED`、`DAMAGE_TAKEN_MULT`、
  `RANGE_MULT/ADD`、`PHOTON_IMMUNITY`、`ACID_DOT`；
- [ ] 每个 status 定义 apply/refresh/stack/expire，不允许散落成互相覆盖的临时数组；
- [ ] EMP 的科技失效复用现有 engine 的 EMP tech-disable 能力，但来源和 25s 生命周期独立；
- [ ] 光子 `-30% damage taken` 接到统一伤害顺序，明确对普攻、技能、百分比 DoT、护盾和
  self damage 是否都生效；
- [ ] 护盾先承伤、屏蔽 status、区域地面裁剪是三个不同 hook，不用一个布尔值混写；
- [ ] trace 至少包含 `area_create/area_expire/status_apply/status_expire/status_blocked/
  shield_damage/area_tick`，便于逐事件对 oracle。

## 7. 分技能施工包

### T6：黏油 `400002` 与烟雾 `600002`

- [ ] 两者使用同一 `capsule(A,B,30)` 几何，不复制命中代码；
- [ ] 黏油对命中单位施加 `move_speed × 0.45`，并实现未点燃两回合、点燃转火焰的生命周期；
- [ ] 用 oracle 冻结“两个回合”的准确 tick 时点、是否友伤、是否影响空军、重叠规则；
- [ ] 烟雾用 oracle 冻结射程降低值、加法/乘法、友伤、空军、持续时间和叠加；
- [ ] 两者都覆盖护盾边界 A/B fixture；
- [ ] registry 分别登记证据，不能因为共用框架就一起升 verified。

### T7：EMP `200001/200002`

- [ ] `200001` 建 `circle(r=60)`，释放时先计算护盾相交与 `20000` 护盾伤害；
- [ ] 盾未破时，受保护地面单位不加 EMP status；盾在本次伤害中被击破时的同 tick 穿透规则
  用 oracle 冻结；
- [ ] 未受保护目标施加 `TECH_DISABLED(25s)` 与 `move_speed × 0.60 (25s)`；
- [ ] 验证建筑、装置、空军、无科技单位、已有单位 EMP 科技和光子状态；
- [ ] `200002` 复用同一 effect spec，仅由 registry 覆盖半径；不得复制一套 EMP 逻辑；
- [ ] 用 oracle 冻结巨型 EMP 半径、CD、两次命中的 refresh/stack。

### T8：光子投射 `200003`

- [ ] 从回放确认 Positions 数和实际覆盖形状/半径；
- [ ] 只给释放方友军施加 20s 光子状态，敌军/中立对象不受益；
- [ ] 统一伤害管线中实现 `damage_taken × 0.70`；
- [ ] 分别验证 EMP、引燃/燃烧、酸液 DoT、酸液易伤、退化光束是否被阻止或移除；
- [ ] 明确先中负面状态再获得光子时，是清除既有 status 还是只免疫后续；
- [ ] 明确多个光子区域、装备减伤和单位科技减伤的叠加顺序。

### T9：酸液 `500002`

- [ ] 使用 `capsule(A,B,30)`；
- [ ] oracle 冻结是否为 `3% maxHP/s`、tick、`damage taken ×2.5`、持续和影响对象；
- [ ] 百分比伤害与易伤分开建模，避免易伤再次放大自身 DoT，除非 oracle 证明会放大；
- [ ] 验证护盾裁剪、护盾承伤、空军、建筑/装置、友伤、光子免疫和引燃交互；
- [ ] 验证离开酸液后的 status 是否立即消失或保留，以及重叠区域的叠加方式。

### T10：离子轰炸 `300006`

- [ ] 使用 `moving_circle(A,B,20)`，事件从 A 按 oracle 速度移动到 B；
- [ ] DPS 转成固定 tick 伤害，最后一个不完整 tick 的取舍必须与 oracle 一致；
- [ ] 分离“光束当前圆覆盖”与“走过后留下地面效果”；默认不留尾迹，除非 oracle 证明；
- [ ] 标定速度、DPS、tick、起始延迟、方向、友伤、空地、护盾和同目标重复命中；
- [ ] 斜向与短距离 A/B 场景必须通过，防止只对水平线成立。

### T11：闪电风暴 `300005`

- [ ] 先采集不少于 20 个固定场景、多个 seed 的落雷时空点，不在采集前假设 sunflower；
- [ ] 判断落雷是固定序列、seed 随机、单位引导还是区域内均匀抽样；
- [ ] 标定总持续、首次延迟、间隔、单次伤害、splash、减速幅度/持续和重复命中；
- [ ] seed 必须进入 `BattleInput` 与事件生成；同 seed 重放一致，不同 seed 分布可统计；
- [ ] oracle 数据不足时允许实现 provisional 分布，但 strict-effect 继续截断并显示待校准。

### T12：移动信标 `1500001/1500002`

- [ ] 证明两个 ID 的来源与效果相同后，共用一个 effect spec；否则保留独立参数；
- [ ] release 必须恰好保存三个有序点：选区中心 A、第一中心 B、第二中心 C；
- [ ] t=0 按每个 unit member 的位置选择 `distance(member,A) <= 40`；冻结边界容差；
- [ ] 每个入选 member 保存 offset `member_pos - A`，目标依次为 `B+offset`、`C+offset`；
- [ ] 未入选 member 不跟随；同一 card 部分成员被选中时允许阵型拆开，card outcome 仍正确聚合；
- [ ] 路径运动不等同部署阶段瞬移，不修改跨回合 `UnitCard.x/y`；
- [ ] v1 engagement policy：途中射程内存在合法敌人则停下并正常攻击；无目标后恢复当前 waypoint；
- [ ] 验证转向、碰撞/分离、目标死亡、被控制、单位自身钻地/冲锋能力、空军和静态对象；
- [ ] 验证能否越中线/侧翼边界，不能套用 deployment placement rules；
- [ ] 多次信标、两方同时信标、途中死亡与 120s 超时必须确定且无 hang；
- [ ] `1500001/1500002` 的 CD、取消和重复释放按各自槽验证。

移动信标风险最高。若部分成员路径会迫使 engine 大改，允许分两阶段交付：

1. contract/codec 完整、release 不再丢三点，但 battle 仍明确 `unsupported`；
2. member-level waypoint engine 完成并通过 oracle 后才把 battle 标成 complete。

不能用“整卡中心是否在圆内 + 整卡瞬移”作为临时 exact 实现。

### T13：未知装置 `30001`

- [ ] 从 raw 回放筛选所有 `ReleaseContraption ContraptionID=30001`，输出轮次、位置、专家、
  相邻动作和下一场 FightReport 索引；
- [ ] 在游戏本体确认名称、图标、tooltip、模型、可选位置、费用、每回合上限；
- [ ] oracle 空场放置，记录是否生成可攻击 world object、HP、半径、攻击/护盾/status；
- [ ] 分别带 10007/10008 测试，确认先进护盾/飞弹装置是否影响；
- [ ] 身份确认后再登记 `CONTRAPTIONS[30001]` 和 registry；
- [ ] 若该 ID 在当前游戏 build 已废弃或只属于旧版本，按 game version 做 codec/registry，
  不把新版本观察强套给旧回放。

## 8. registry、capability 与前端

### T14：一个支持度事实源

- [ ] 每个 ID 在 `pysim/battlefield/registry.py` 登记六段状态、confidence、evidence、
  game build 和 oracle case IDs；
- [ ] `capability.classify_norm_entry()`、compiler、battle warning 和 GameView 都读同一对象；
- [ ] decode/transition 完成而 battle 尚未完成时，普通 session 的产品口径必须显式裁决：
  若允许近似继续，显示 `battle_approximate`；若效果会严重改变路径（移动信标），默认继续
  precise blocker，不能无效果放行；
- [ ] 前端区分 `SESSION_BLOCKED`、`已模拟·待校准`、`未模拟` 和普通玩家非法动作；
- [ ] blocker detail 显示技能名、ID、slot、完整 Positions、原始索引、目标、失败阶段和证据；
- [ ] 完成一个机制后重新生成 Step 5 occurrence/first-blocker 报告，记录实际消除量。

## 9. 测试与回归 Gate

### 9.1 单元与契约测试

- [ ] `tests/transition/test_normalize.py`：多点顺序、一次 Cancel/Undo、ID=0 slot history；
- [ ] `tests/transition/test_step5_skills.py`：建筑回收、快速补给、slot 消费/CD、拒绝不变性；
- [ ] `tests/transition/test_battlefield_skills.py`：几何、状态、护盾、叠加、跨回合、移动信标；
- [ ] 每个 rejected action 验证 state digest 不变；
- [ ] 每个成功 release 验证只消费一次槽；
- [ ] save/load 覆盖 typed releases、持久 area、construction 和 schema migration；
- [ ] 同 seed 双跑 BattleInput/Outcome/trace digest 一致。

### 9.2 oracle 专项 Gate

每个机制单独出一行，不用一个总 winner rate 掩盖失败：

| ID | case 数 | 几何命中 | 时序 | 伤害/状态误差 | winner | confidence | 未闭合项 |
|---:|---:|---:|---:|---:|---:|---|---|
| `400002` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `200001` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `200002` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `200003` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `300005` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `300006` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `500002` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `600002` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `1500001` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| `1500002` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| contraption `30001` | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

误差容差必须从 oracle 的 tick/量化方式推导并写入 `data/calib/battlefield_skills/`，不能在
测试失败后临时放宽。随机闪电风暴使用分布 Gate，其他确定性技能优先要求命中集合和时序
exact，再报告数值误差。

### 9.3 全仓回归 Gate

- [ ] `python -m pytest tests` 全通过；
- [ ] `python tools/battlefield_report.py` 确定性和 registry 报告通过；
- [ ] `python benchmarks/run.py --lib all` 与冻结八库逐库对比，任何下降都列出具体 case；
- [ ] 现有装备专项、技能/装置测试无退化；
- [ ] 完整 `.grbr -> norm -> shard -> manifest` 重建成功；
- [ ] Step 5 报告分别给出 runtime/strict 前缀和 §3.1 每个 ID 的 blocker occurrence 前后值；
- [ ] `/game` 实际历史对手停止回合与 scanner 预测一致。

新增技能可能合理改变含该技能的新专项库结果，但不应悄悄改变无技能八库。若共享 damage、
movement 或 status 管线导致八库变化，必须提交逐 case diff 和原因，不得只更新冻结数字。

## 10. 推荐实施顺序与提交边界

建议按以下顺序分批，每批都能独立回滚、测试和审阅：

1. **S0：oracle 接口与基线**——补全 §0，建立 schema/manifest/空 runner，冻结当前报告；
2. **S1：typed release 契约**——完整 Positions、schema migration、digest、无机制效果变化；
3. **S2：transition 高频墙**——建筑回收、槽生命周期、快速补给通道核验；
4. **S3：共享 area/status**——纯几何、生命周期、护盾 hook、trace；
5. **S4：黏油 + 烟雾**——先闭合一个持久区域和一个非伤害区域；
6. **S5：EMP + 光子**——科技禁用、减速、减伤、免疫；
7. **S6：酸液 + 离子**——DoT/易伤和 moving area；
8. **S7：闪电风暴**——在随机性被 oracle 识别之后实现；
9. **S8：移动信标**——member-level waypoint 与途中交战，单独做高风险回归；
10. **S9：装置 30001**——身份确认后插入合适批次；未确认则不阻塞其他技能交付；
11. **S10：registry/UI/全量重建**——逐 ID 升级支持度并记录前缀变化。

每个提交只允许一种主要语义变化。尤其不要在“补 EMP”提交中同时调普通单位移动/索敌
参数，否则 oracle diff 无法归因。

## 11. Definition of Done

本专项整体完成需同时满足：

- §0 的 Windows oracle 与游戏路径已填写，采集命令可由另一人复现；
- raw release 的完整 Positions、目标和 slot 信息端到端保留，无多点展开造成的语义丢失；
- `900001` 建筑回收与快速补给通过 transition/economy/Undo/连续回合测试；
- 所有可解释的 `ID=0` 由真实 slot 事实解析，剩余项携带证据精确阻塞；
- 移动信标按 member 选区、相对 offset、两段 waypoint 和途中交战执行；
- 黏油、烟雾、EMP、光子、酸液、离子、闪电均通过各自专项 oracle Gate；
- `30001` 已被真实识别并实现，或明确记录为版本化 unsupported，绝不猜测映射；
- capability/registry/compiler/前端没有第二套互相矛盾的技能表；
- provisional 与 verified 有明确证据边界，未校准数值不冒充 exact；
- 全量测试、八库非技能基线、技能专项库、完整回放重建和 `/game` scanner/runtime 一致性
  均通过；
- 实施结果、命令、oracle case 数、指标变化、偏差和遗留 QA 追加到本文第 13 节。

## 12. 开工前 QA（待填写）

### QA-1：Windows oracle 能力

1. 当前 oracle 能否直接构造技能卡/装置，还是必须走真实商店与回合流程？
2. 能否输出逐 member 坐标、HP、护盾、状态和科技 active，采样频率是多少？
3. 能否固定战斗 seed，并取得闪电风暴的随机 seed/落点事件？
4. 能否保存地面区域对象并连续跑到下一回合战斗？

答复：

> 待填写。

### QA-2：尚未冻结的游戏规则

1. construction cid 中哪些是墙、炮，哪些可被 `900001` 回收？
2. `ReleaseCommanderSkill ID=0 SkillIndex=0` 与快速补给究竟是否同一通道/版本编码？
3. 巨型 EMP 半径是多少；EMP 是否影响空军、建筑和装置？
4. 光子“印染免疫”是否指引燃；先有负面 status 后获得光子是否清除它？
5. 烟雾的射程降低数值、酸液的百分比基数与易伤倍率是否为 §7 的 provisional 口径？
6. 移动信标两个 ID 是否完全同效果，能否影响空军和静态单位？
7. `30001` 的游戏内身份是什么？

答复：

> 待填写。

## 13. 后续实施记录（施工完成后追加）

### 13.1 环境与版本

> 待填写 Windows oracle commit、游戏 build、pysim commit、采集日期和完整命令。

### 13.2 实际完成项

> 待填写代码文件、schema 迁移、逐技能实现与未实现项。

### 13.3 测试与指标

> 待填写 pytest、八库、专项 oracle、blocker occurrence、runtime/strict prefix、
> scanner/runtime 一致性结果。

### 13.4 与任务书的偏差及遗留问题

> 待填写。所有偏差必须说明证据和对 fidelity/confidence 的影响。
