# 前端 Step 2 实现任务书

> 本文是 [`transition前后端审计游戏任务书.md`](transition前后端审计游戏任务书.md)
> 的前端第二阶段施工书，依据当前 `/game` 实现、Step 0.1 使用反馈和
> `data/screenshots/pic1.jpg`～`pic5.jpg` 编写。
>
> **本文当前状态：任务书已编写，功能尚未实施。** 本文中的复选框全部表示未来实施
> 状态；只有对应代码、测试和浏览器验收完成后才可勾选。本轮工作只编写本文，没有修改
> `web/static/game.html`、Python 服务、transition、测试或数据文件。
>
> 后续若进行实现，必须把实施总结、验证命令、实际偏差和未完成项续写到本文末尾，不能
> 另写一份与任务书脱节的总结。

## 0. Step 2 最终要交付什么

在保留 transition 审计能力的前提下，把：

```text
http://127.0.0.1:8300/game
```

从“能操作的审计面板”升级为“首先像一局游戏、需要时再展开审计信息”的本地单人页面。
Step 2 必须同时解决两类问题：

1. **P0 正确性**：回合口径必须解释清楚；开局单位、双方半场、核心塔、购买/移动落点、
   战斗 trace 和回合复位必须使用一致的坐标及阵营语义。
2. **P1 游戏交互与视觉层级**：开局和增援使用四卡选择层，战场成为主视觉，单位商店、
   科技和部署操作接近原游戏的信息层级，结束部署始终容易找到，审计面板默认不抢占主屏。

Step 2 不是单纯换颜色。只要服务器真状态仍把两方单位放在同一半场，或 UI 仍把源回放
长度误写成可玩长度，即使页面更漂亮也不能验收。

### 0.1 最终浏览器验收场景

至少对“人类接管 player 0”和“人类接管 player 1”各执行一次：

1. 进入回放选择页，能够看到源回放长度、普通可玩前缀、严格前缀和首次阻塞原因；
2. 对可玩前缀不足 5 的回放，可以理解为什么不足，并按规则执行一次“受限开始”；
3. 在全屏四卡层选择一个开局，进入部署后人类始终位于画面下半场、对手位于上半场；
4. 双方开局单位分别出现在自己的核心塔附近，不重叠、不跨中线；
5. 从单位卡进入购买态，看到幽灵预览和合法部署区，在己方半场完成一次购买；
6. 选择并拖动己方单位，松手后收到 `move_unit` receipt；
7. 单位详情打开期间，“撤销”和“结束部署”仍然可见；
8. 结束部署后，对手单位、己方单位、核心塔和 battle trace 的阵营/位置一致；
9. 战斗动画结束后进入下一回合，单位从 GamePlayer/GameView 权威数据复位到上回合
   部署结束时的阵型，而不是留在战斗末帧或回到开局初始阵型；
10. 展开审计抽屉仍可查看双方 receipts、资金账本、state diff、历史动作和 pysim 结果。

任一项失败，Step 2 均不能标记完成。

## 1. 待用户回答的问题

以下问题不阻塞任务书编写。施工开始前若仍未回答，按每项的“推荐默认方案”实施；用户
可以直接在本节对应问题下补充裁决。

### Q1：是否允许修改必要的后端/transition 契约？

- **推荐默认方案 A**：允许。仅修改坐标正确性、服务端部署区校验、GameView 展示字段、
  replay option 口径和相关 fixture/test；不扩展装备、技能等新游戏机制。


- 方案 B：严格只改 HTML/CSS/JavaScript。该方案只能在显示层掩盖部分坐标问题，无法
  修正进入 pysim 的错误真状态，也无法让服务端权威拒绝错误半场，因此 P0 坐标 Gate
  不能完整通过。
- 用户裁决：我理解使用推荐方案 pysim只是一个战场模拟器 不会影响transition契约


### Q2：双击单位卡的精确语义是什么？

- **推荐默认方案 A**：单击选择卡牌，双击作为快速进入购买态；两者都先显示单位幽灵，
  用户在己方半场点击落点后才提交 `BuyUnit`、扣钱和创建单位。
- 方案 B：双击后由系统寻找默认合法位置并立即购买。该方案需要新增可复现的自动落点
  规则、碰撞策略和失败提示，不应由前端临时猜坐标。
- 方案 C：双击场上己方单位，在附近自动购买同类单位。该方案同样需要服务端定义自动
  落点，不属于现有 action 的简单 UI 映射。
- 用户裁决：使用推荐方案 双击进入场地中央 或者也可以用单击+位置的方法 相当于购买+移动

### Q3：审计面板默认如何呈现？

- **推荐默认方案 A**：游戏优先。审计区成为底部可展开抽屉，默认折叠，记住本次页面
  会话内的展开状态。
- 方案 B：保留当前三栏加固定底部审计区，仅调整皮肤。实现较小，但战场继续被压缩，
  与“更像游戏”的目标冲突。
- 方案 C：提供“游戏模式/审计模式”两套完整布局。能力最好，但会显著增加响应式布局和
  浏览器测试范围，不建议在本 Step 首次引入。
- 用户裁决：游戏优先，可以适当和游戏不同（因为html的战场比游戏小一些不用全屏，很多信息可以放在左面右面）

### Q4：缺少合法单位美术资源时如何处理？

- **推荐默认方案 A**：使用统一的 CSS/Canvas 科幻卡框、兵种缩写、类别图标和颜色占位；
  后续有合法素材时通过稳定的资源映射替换。
- 方案 B：用户另行提供单位、专家、科技和增援图片素材后再完成视觉验收。
- 明确禁止：把 `data/screenshots/` 中的画面直接裁切成单位卡或背景资源。截图只作布局、
  比例、层级和交互参考。
- 用户裁决：这里优先还是展示正确性 先不要管素材 目前游戏渲染这边是ok的

### 已确认：下一回合的单位复位目标

用户已确认：GamePlayer 中有重新部署位置数据，该数据等价于上回合部署结束时的阵型。
因此本任务冻结为：

```text
战斗 trace 末帧（临时）
    -> ACK_ROUND_RESULT
    -> 丢弃播放器坐标
    -> 使用新 GameView.players[].units 的权威坐标重绘
```

不得回到本局开局的初始阵型，也不得把战斗中移动后的末帧坐标写回 GamePlayer。

## 2. Step 0.1 review 与现状调查结论

### 2.1 回放选择：“共 10 回合”却没有可连续 5 回合

这部分既有展示问题，也必须保留一次 scanner 正确性审计，不能未经验证就简单把
`enabled` 改成 `round_count >= 5`。

当前仓库样例 `data/samples/replay_game/manifest.json` 的事实是：

- 共 6 个 opponent option；
- 4 个 option 的 `round_count` 为 10，2 个为 5；
- `playable_through_round >= 5` 的 option 为 0；
- 当前最佳普通可玩前缀为 R4；
- 样例中的 `round_count=10` 对应玩家记录 `[0, 1, ..., 9]`，包含特殊开局 round 0，
  页面写成“共 10 回合”容易被理解为能打完 10 个战斗回合；
- `playable_through_round` 是当前运行时能力下可完成的连续前缀；
- `strict_playable_through_round` 要求每回合四张候选都 effect-complete，通常更短；
- 当前 `blockers` 把 strict blocker 和真正截断运行时前缀的 blocker 放在同一个数组，
  前端只取第一项，可能把“严格口径的较早提醒”显示成“实际停止原因”。

因此本项调查结论是：**不能从“源记录较长”直接推出 scanner 算错，但当前 UI 的默认
过滤、命名和 blocker 选择确实会制造判定错误的观感。** Step 2 必须同时改解释和增加
scanner/runtime 一致性测试；若测试证明存在 off-by-one，再修 scanner，不能只改文案。

### 2.2 开局单位落入对方半场

当前开局 catalog 构建注释声称保存“team-0 orientation”，但已提交 catalog 的 formation
Y 坐标实际为正值；`build_initial_state()` 又把两个 package 的 formation 原样用于 player 0
与 player 1，没有按 side 镜像。这会让双方初始单位进入同一世界半场。

这不是纯 CSS 问题。错误坐标会继续进入 opponent plan、battle adapter 和 pysim，必须先
修正权威 state，再做前端的人类视角投影。

### 2.3 购买单位需要点击“对方半场”

当前 Canvas 固定把世界 player 0 画在下方、player 1 画在上方，但单位颜色又固定按
“human=蓝、opponent=红”绘制；当人类接管 player 1 时，视觉阵营、塔位置与合法落点
没有统一转换。前端只在购买点击时做了局部 Y 正负判断，MoveUnit 服务端也没有所属
半场校验，导致用户需要按世界 side 而不是按画面上的“己方”理解操作。

Step 2 必须采用统一的 world/view 坐标转换，不能继续在各事件处理器中散落条件判断。

### 2.4 移动单位后找不到结束部署

当前右侧面板在选中单位时被 `unitHtml()` 完整替换，只显示升级、旋转、回收；结束部署
只存在于未选中单位时的 `summaryHtml()`。这是确定的前端信息架构缺陷。

Step 2 中结束部署是 deployment phase 的固定主操作，不属于“当前选中对象详情”，不能
随单位选中状态消失。

### 2.5 结束部署后双方渲染在同一场地

该现象与 §2.2 的开局真状态坐标错误直接相关，并叠加以下展示问题：

- 编辑态按角色着色，塔却按世界 side 固定着色/定位；
- battle trace 按 engine team 编号绘制，未统一映射到 human/opponent 角色；
- 人类是 player 1 时，编辑态、塔和战斗态没有共用同一视图镜像。

修复必须覆盖 opening state、编辑 Canvas、塔、战斗 trace 和技能事件五处，不能只把红蓝
颜色交换。

### 2.6 下一回合单位复位

复位是预期行为，但目标必须精确：复位到 GamePlayer/GameView 中保存的上回合部署结束
阵型。播放器的 frames、events、playT、spawnDone 和浮字是临时前端状态，离开战斗态后
必须清空；单位等级、经验、装备和权威部署坐标则使用新 GameView 重绘。

### 2.7 “更像游戏”

当前页面把左侧商店、右侧详情和 240px 高审计区永久显示，战场实际面积有限，开局/增援
也被塞进右栏。参考截图的共同信息层级是：

1. 战场是主屏；
2. 双方 HP/身份位于顶部两端，回合和主要阶段操作位于顶部中央；
3. 开局和增援是遮罩之上的四张大卡；
4. 单位购买集中在右下角卡组；
5. 解锁和科技使用临时面板，不永久挤压战场；
6. 审计信息不是原游戏内容，应保留但退居可展开区域。

Step 2 追求上述信息层级和操作反馈，不要求像素级复制原游戏，也不把 pysim 的简化单位
圆点伪装成原游戏 3D 模型。

## 3. 已冻结的产品原则

### 3.1 服务器 state 仍是唯一真值

- 前端不直接扣钱、生成 handle、升级、购买科技或写入单位最终位置；
- 购买、移动、旋转、回收和结束部署都提交 command，并用返回的新 GameView 重绘；
- drag/ghost/hover 只允许成为本地预览；
- rejected action 后必须恢复服务器坐标和资金；
- 价格、收入、HP settlement、科技前置和升级合法性不得复制到 JavaScript。

### 3.2 人类视角固定

- 人类永远显示在屏幕下方、使用蓝色 UI；
- 对手永远显示在屏幕上方、使用红色 UI；
- “下方/上方”只是 view space，不能改变 world state 的 player 编号；
- receipts、ledger 和 historical actions 仍保留真实 player index，并同时展示角色标签。

### 3.3 游戏层与审计层并存

- 游戏层负责选择、布阵、战斗和回合推进；
- 审计层负责解释服务器做了什么；
- 折叠审计区不能减少或改写审计数据；
- BLOCKED、rejected receipt 和 pysim 非真实胜负提示仍必须醒目，不得为“像游戏”而隐藏。

### 3.4 保持现有技术栈

- 保持 FastAPI + 原生 HTML/CSS/JavaScript；
- 不引入 React、Vue、前端构建工具或远程 CDN 运行时依赖；
- 可以把 `game.html` 中的 CSS/JS 拆成同目录静态文件，但必须保证 `start_server.bat`、
  `start_server.sh` 和 `/game` 的零构建启动方式不变；
- `/`、`/bench` 和旧 API 不得回归。

## 4. P0：统一坐标、阵营和部署区契约

### 4.1 世界坐标

冻结世界坐标语义：

```text
x ∈ [-350, 350]
y ∈ [-300, 300]

player 0 部署区：y < 0
player 1 部署区：y > 0
中线：y = 0，不属于任何一方部署区
```

核心塔和 pysim 继续使用 world space。开局、购买、移动、建筑、技能落点和 battle trace
必须明确字段属于 world space 还是 view space，禁止依赖调用者猜测。

### 4.2 人类视图坐标

前端集中定义一对互逆函数，所有 Canvas 绘制和指针落点只通过它们转换：

```text
view_x = world_x
view_y = world_y                    human_player == 0
view_y = -world_y                   human_player == 1

world_x = view_x
world_y = view_y                    human_player == 0
world_y = -view_y                   human_player == 1
```

这样无论接管哪一方，人类的 world 部署区都会投影到 view 的下半场。X 轴不镜像，避免
左右操作和文字方向反转。

### G1：开局 package 坐标正规化

- [ ] catalog schema 明确声明 formation 的坐标空间和朝向；
- [ ] 推荐升级为 `opening_catalog_v2`，离线构建时把 package 冻结为 player 0 世界朝向；
- [ ] 构建 player 0 初始单位时使用 package 原方向；
- [ ] 构建 player 1 初始单位时只镜像 Y；
- [ ] `is_rotate` 的阵营镜像语义有独立测试，不因 Y 镜像重复旋转；
- [ ] 已提交 catalog、仓库 fixture 和 manifest 按新 schema 一次性重建；
- [ ] 旧 schema 若继续兼容，必须显式 adapter，不得通过“看第一只单位 Y 正负”猜版本；
- [ ] 双方 opening package 执行完成后，各自所有普通开局单位位于所属半场。

### G2：服务端部署区权威校验

- [ ] BuyUnit 除地图 bounds 外，还校验 action player 的所属半场；
- [ ] MoveUnit 同样校验所属半场；
- [ ] 越界或跨中线返回稳定 reason code，例如 `POSITION_OUT_OF_DEPLOY_ZONE`；
- [ ] rejected action 不扣资金、不改变单位、不 bump session version；
- [ ] 前端部署区高亮只作引导，不能代替服务端校验；
- [ ] 对手历史 action 若违反同一规则，按既有 strict 语义进入 BLOCKED，不能偷偷放行；
- [ ] capability scanner 与 runtime 对该规则使用同一口径。

### G3：Canvas、塔与 trace 共用映射

- [ ] 编辑态的双方单位先从 world 转 view 再绘制；
- [ ] 两组核心塔按 human/opponent 角色绘制在下/上半场，并读取对应 player 的塔等级；
- [ ] 战斗 frame 中的 engine team 先映射角色，再转换坐标和颜色；
- [ ] tower_down、skill、device、spawn 等 trace 事件使用同一转换；
- [ ] 单位命中检测和拖动预览在 view space 工作，提交时统一转回 world space；
- [ ] 禁止再向 `drawUnit()` 传入伪造的固定 side 以代替真实 player/role；
- [ ] 人类分别为 player 0、player 1 的截图验收均通过。

**P0 坐标 Gate**：开局 state、部署操作、编辑 Canvas、核心塔和 battle trace 对同一个
world position 的解释一致；两种接管方向都不会出现“红方在蓝塔附近”或“必须点对方半场”。

## 5. P0：回放长度、可玩前缀与 blocker

### 5.1 冻结术语

页面不得再用一个“共 N 回合”同时指代以下不同概念：

| 字段/术语 | 精确定义 | 推荐展示 |
|---|---|---|
| 源记录范围 | 双方回放记录共同覆盖的 round 范围，包含 round 0 | `源记录 R0–R9（10 条）` |
| 普通可玩前缀 | 当前 human 可自由选择受支持候选、对手历史 plan 可 strict 执行的最后完整回合 | `当前可玩至 R4` |
| 严格可玩前缀 | 每回合四张增援候选全部 effect-complete 的最后回合 | `四卡全支持至 R3` |
| 首次运行时阻塞 | 真正使普通 session 无法继续的第一个 blocker | `预计 R5：装备效果未支持` |
| 首次严格提醒 | 只截断严格前缀、不一定截断普通 session 的 blocker | `R4 有未支持候选，可改选其他卡` |

`playable_through_round` 必须是“最后能够完整结束的回合”的 inclusive 语义。若 blocker
发生在 R5，则值为 4；测试必须防止 off-by-one。

### G4：Replay option 契约

- [ ] API 提供源记录的 `round_min`、`round_max`、`round_record_count`，不再让前端猜；
- [ ] 保留普通与 strict 两种 prefix；
- [ ] 分别提供 `first_runtime_blocker` 和 `first_strict_blocker`，或提供可稳定过滤的
  `strict` 字段；
- [ ] 前端不得再无条件使用 `blockers[0]` 作为禁用原因；
- [ ] option 明确返回 `start_mode = normal | limited | disabled`；
- [ ] normal/limited 的阈值由服务端决定，前端不复制 `5`、`3` 等规则常量；
- [ ] manifest shard 路径统一使用 `/`，loader 对已有 `\` 路径做安全兼容并拒绝绝对路径/
  `..` 穿越，保证 Windows/Linux 一致。

### G5：选择页行为

- [ ] 默认展示所有满足源记录长度要求的 option，而不是勾选“只显示可连续 ≥5 回合”；
- [ ] 默认按普通可玩前缀降序，再按源记录长度和名称排序；
- [ ] 提供“仅普通开始”“包含受限开始”“全部（含禁用）”筛选；
- [ ] 同一回放的两个对手方向分别显示，不合并；
- [ ] normal option 使用主按钮“开始”；
- [ ] limited option 使用警示按钮“受限开始”，确认层写明预计停止回合和原因；
- [ ] disabled option 可展开查看所有 blocker，但不能创建 session；
- [ ] 当 normal 数量为 0 时，页面仍显示最佳 limited option，不再呈现空列表；
- [ ] “严格前缀”作为审计信息，不误导用户认为一张未支持候选必然阻塞本回合；
- [ ] corpus 缺失、manifest 损坏、shard 缺失分别显示可操作的错误说明。

### G6：Scanner 与 runtime 一致性

- [ ] 对每个 committed fixture，从开局运行到终止或 blocker；
- [ ] 首次 runtime BLOCKED 回合与 `first_runtime_blocker.round` 一致；
- [ ] `playable_through_round` 等于 BLOCKED 前最后完整结算回合；
- [ ] strict blocker 不会被当成普通 session 的首次 runtime blocker；
- [ ] 源记录 `[0..9]` 的 UI 不再写成含糊的“能玩 10 回合”；
- [ ] 若调查发现 scanner 本身错误，修分类器并重建 manifest，禁止只改 UI 数字。

**回放选择 Gate**：用户无需阅读代码，就能回答“原回放有多长、当前能玩到哪里、为什么
停、是否还能受限开始”。

## 6. 页面总体信息架构

推荐采用 Q3 方案 A：主战场 + 浮层/右下操作区 + 底部审计抽屉。

```text
┌ 人类头像/HP/名称 ─────── 回合 · Phase · 主操作 ─────── 对手名称/HP/头像 ┐
│                                                                          │
│                           战场 Canvas 主区域                             │
│                                                                          │
│  状态/提示                                              技能/资金/撤销   │
│                                                        单位购买卡组      │
├────────────────── 审计摘要条（可展开/收起）─────────────────────────────┤
│ Receipts | Ledger | Diff | 历史动作 | Battle（展开后显示）              │
└──────────────────────────────────────────────────────────────────────────┘
```

### G7：顶部 HUD

- [ ] 左侧固定显示人类（蓝）名称、HP/max HP；
- [ ] 右侧固定显示对手（红）名称、HP/max HP；
- [ ] 中央显示当前 round、中文 phase 和当前主操作；
- [ ] deployment 时中央主按钮始终为“结束部署”；
- [ ] round_result 时中央主按钮变为“进入下一回合”；
- [ ] reinforcement/opening 时中央区域只显示阶段标题，选择在遮罩卡层完成；
- [ ] supply 放在靠近购买区的位置，并在 accepted receipt 后用短动画更新；
- [ ] digest、session version、刷新和删除会话移入次级菜单/审计摘要，不占主 HUD；
- [ ] pysim 战斗结果始终保留“模拟结果，非真实胜负”标识。

### G8：战场主区域

- [ ] Canvas 自适应剩余可用空间，保持地图纵横比，不因审计抽屉折叠而拉伸坐标；
- [ ] 下半场使用蓝色轻描边，上半场使用红色轻描边，中线清晰；
- [ ] 购买/移动时高亮合法部署区，非法区覆盖斜纹或暗色；
- [ ] 核心塔比普通单位更醒目，显示塔编号、等级和阵营；
- [ ] 单位至少显示名称缩写、等级、编队规模和选中轮廓；
- [ ] hover 显示简短浮层，click 打开单位详情；
- [ ] 对手单位可查看公开信息但不可拖动或操作；
- [ ] busy、BLOCKED、terminal 都有明确、不可混淆的战场状态层。

### G9：底部审计抽屉

- [ ] 默认折叠时只显示最近一次 command、accepted/rejected、version 和展开按钮；
- [ ] 展开后保留 Human receipts、Opponent receipts、Ledger、State Diff/Audit、
  历史动作、Battle 六个 tab；
- [ ] rejected reason code、opponent exp override、digest 和 pysim 标识不得隐藏；
- [ ] 抽屉展开不销毁 Canvas/播放器状态；
- [ ] 本次页面会话内记住展开状态，不要求跨浏览器持久化；
- [ ] 1366×768 展开时仍至少保留可操作战场，不允许抽屉覆盖结束部署按钮。

## 7. 五张参考截图对应的页面任务

### 7.1 开局四选一：参考 `pic1.jpg`

开局阶段使用覆盖战场的大型四卡选择层，不再塞进右侧 280px 面板。

每张卡必须展示：

- 开局名称/队伍名称；
- 专家名称；
- 初始 HP、初始 supply；
- 初始单位名称、数量、等级；
- `回放记录` 或 `模拟生成` 来源徽标；
- generated 候选的“不代表原回放未记录真实候选”说明；
- 选择按钮和提交中状态。

验收要求：

- [ ] 1920×1080 时四卡同排；
- [ ] 1366×768 时允许缩小或 2×2 排列，但不能横向溢出；
- [ ] 卡片可用键盘聚焦，Enter 选择；
- [ ] 提交中禁用四卡，避免重复 command；
- [ ] 选择成功后完全用新 GameView 进入 deployment。

### 7.2 部署主画面：参考 `pic2.jpg`

- [ ] 战场占据页面大部分面积；
- [ ] 顶部中央固定“部署完成/结束部署”主入口；
- [ ] 右下显示 supply、撤销和当前可购买单位卡；
- [ ] 单位卡显示名称、价格、编队数量、是否可购买；
- [ ] 已解锁与未解锁单位视觉区分；
- [ ] 选中购买卡后出现幽灵预览，不立即扣钱；
- [ ] ESC 或再次点击已选卡取消购买态；
- [ ] rejected 后显示 reason，并保持/退出购买态的规则固定且有测试。

推荐固定：资金不足或服务器拒绝时保留卡牌选择，方便重新落点；成功购买后退出购买态，
避免一次误操作连续购买多队。

### 7.3 解锁单位：参考 `pic3.jpg`

- [ ] 点击“解锁单位”打开居中 modal；
- [ ] 按 gamedata 的可用分类/等级组织卡片；若服务端没有可靠分类，只按名称/价格分组，
  不在前端猜 tier；
- [ ] 提供名称筛选和已解锁标识；
- [ ] 每张卡展示解锁价格、兵种名称和基础标签；
- [ ] 资金不足、规则不支持和已解锁分别使用不同状态；
- [ ] Unlock accepted 后关闭或刷新 modal，并使用新 GameView 更新商店；
- [ ] modal 打开期间仍能看到背景战场，但不能误触战场落点。

### 7.4 单位与科技详情：参考 `pic4.jpg`

- [ ] 点击己方单位打开浮动详情面板，不替换顶部主操作；
- [ ] 显示等级、经验、升级价格、部署坐标、旋转状态、装备和已购科技；
- [ ] 显示服务端提供的公开基础/有效属性；缺失字段明确显示“暂无数据”，不能前端估算；
- [ ] 可购买科技展示名称、价格、前置、描述和 supported 状态；
- [ ] 升级、旋转、回收按钮只按 GameView legal action 启用；
- [ ] 点击空地或 ESC 关闭详情，但不会误提交移动；
- [ ] 对手详情为只读，不显示可操作按钮。

若需要新增 stats/tech description，必须由 GameView serializer 从 gamedata/transition 提供，
JavaScript 不复制攻击、HP、射程或科技效果公式。

### 7.5 增援四选一：参考 `pic5.jpg`

- [ ] reinforcement phase 使用全屏遮罩和四张大卡；
- [ ] 每张展示名称、等级/类别、费用、效果描述和 supported 状态；
- [ ] unsupported 卡保留可见但不可选择，并展示稳定原因；
- [ ] “放弃增援，+50”作为独立按钮，不伪装成第五张普通卡；
- [ ] 用户选择前不能继续部署，但允许查看只读单位信息；
- [ ] 提交中禁用所有选择；
- [ ] 成功后丢弃遮罩并完全使用新 GameView 重绘。

## 8. 部署交互规范

### G10：购买状态机

推荐默认状态机：

```text
idle
  -> 单击/双击可购买卡
placing_buy(mech_id, price, ghost)
  -> 点击合法己方半场 -> command pending
  -> accepted -> idle + 权威重绘
  -> rejected -> placing_buy + 原因提示
  -> ESC/再次点卡 -> idle
```

- [ ] armed 状态不扣钱、不创建本地单位；
- [ ] ghost 使用 view 坐标，提交 payload 使用 world 坐标；
- [ ] 光标旁显示单位名和服务端价格；
- [ ] 非法半场点击只提示，不发送 command；
- [ ] 即使绕过前端发送非法坐标，服务端也稳定拒绝；
- [ ] busy 时禁止第二次购买提交；
- [ ] session version stale 时刷新 GameView，并清除可能过期的 armed state。

### G11：选择、拖动和旋转

- [ ] 单击单位只选择，不产生 MoveUnit；
- [ ] 超过拖动阈值后才进入 drag preview；
- [ ] mouseup 只提交一次 MoveUnit；
- [ ] drag 离开 Canvas 或窗口失焦时有稳定取消/提交规则；
- [ ] 非法区显示拒绝光标且不发送；
- [ ] accepted 使用新坐标重绘，rejected 回到旧坐标；
- [ ] 旋转 command 保持当前 world 坐标，不经过重复镜像；
- [ ] 删除/回收当前选中单位后清空 selection；
- [ ] 新 GameView 中 handle 不存在时自动清空 selection。

### G12：固定撤销与结束部署

- [ ] deployment 且未 finished 时，“撤销”和“结束部署”始终可见；
- [ ] 它们位于顶部中央或右下固定操作条，不属于单位详情 DOM；
- [ ] undo 不可用时按钮 disabled，并显示 `UNDO_EMPTY` 说明；
- [ ] 结束部署前显示本回合 accepted plan 摘要和己方单位数；
- [ ] 确认层说明将执行对手历史动作和一次 pysim 战斗；
- [ ] FINISH pending 时全页面禁止 mutation，显示“对手执行中/战斗模拟中”；
- [ ] BLOCKED 后保留权威部署棋盘并自动展开失败详情；
- [ ] 选中单位、打开科技、打开审计抽屉都不能遮挡主按钮。

## 9. 战斗播放与下一回合复位

### G13：战斗播放器

- [ ] FINISH 成功进入 round_result 时自动播放当前 battle trace；
- [ ] 播放/暂停、回到开头、seek、退出播放保持可用；
- [ ] 角色颜色按 human/opponent 映射，不直接把 team 0 永久画蓝；
- [ ] HP、击杀、tower_down、skill、device、spawn 使用同一 view transform；
- [ ] battle 结果展示 winner、伤害、HP before/after、经验和 reward；
- [ ] 固定标注“pysim 模拟结果，非真实对局胜负”；
- [ ] 历史真实 label 只在审计对照区展示。

### G14：复位状态机

离开战斗/进入下一回合时：

1. 停止 timer；
2. 清空 `frames`、`events`、`playT`、`spawnDone` 和浮字；
3. 清空 `drag`、`pickBuy`、`pickRelease`；
4. selection 仅在新 GameView 仍有同 handle 且 phase 允许时保留，否则清空；
5. 从新 `GameView.players[].units` 读取等级、经验和 world 部署坐标；
6. 通过 world-to-view 转换重新绘制部署阵型。

- [ ] 不从 trace 最后一帧生成下一回合单位坐标；
- [ ] 不从 opening offer 重新生成初始阵型；
- [ ] 不读取未来 replay snapshot 回灌阵型；
- [ ] 上回合移动后的权威坐标在下一回合保持；
- [ ] battle 中临时移动、死亡和召唤物不污染持久部署坐标；
- [ ] `battle` 若作为“上回合结果”继续留在 GameView，UI 明确标记其 round，不触发重复播放。

**复位 Gate**：记录 FINISH 前 GamePlayer 的单位 `(handle, x, y)`，完成 battle 和
ACK 后，新 GameView 中相同 handle 的部署坐标与该记录一致（规则明确改变位置的机制除外，
且必须由 receipt/audit 说明）。Canvas 重绘结果与新 GameView 一致。

## 10. GameView 与前端公共契约

所有新增字段随 schema version 版本化。推荐由服务端提供以下等价信息；最终字段命名可以
服从现有 Python 风格，但语义不得缺失。

```json
{
  "schema_version": "game_view_v2",
  "board": {
    "coordinate_space": "world_v1",
    "bounds": {"x_min": -350, "x_max": 350, "y_min": -300, "y_max": 300},
    "midline_y": 0,
    "human_player": 1,
    "players": [
      {
        "player": 0,
        "role": "opponent",
        "deploy_zone": {"y_min": -300, "y_max": 0, "max_exclusive": true},
        "towers": [{"index": 0, "x": -140, "y": -170, "level": 0}]
      }
    ]
  }
}
```

### G15：契约要求

- [ ] map bounds、部署区和塔位置来自一个服务端/engine 真源；
- [ ] 每个 player view 同时含真实 `player` 和展示 `role`；
- [ ] 单位坐标明确为 world space；
- [ ] legal actions 提供 `can_undo`、`can_finish_deployment` 及不可用原因；
- [ ] unit detail/tech 卡需要的显示字段由 GameView 提供；
- [ ] replay option 提供 §5.1 的完整口径；
- [ ] serializer 不泄漏内部 entity ID、RNG、shard 绝对路径或可写 state dict；
- [ ] 前端遇到旧 schema 时显示“不兼容，请刷新/重建语料”，不静默猜字段。

## 11. 错误、并发与可恢复性

- [ ] 每个 mutation 使用 `expected_version`；
- [ ] command pending 期间所有 mutation 入口 disabled；
- [ ] stale version 自动 GET 最新 GameView，并清理过期的本地交互态；
- [ ] rejected receipt 使用游戏内提示，并可一键展开审计详情；
- [ ] network/HTTP 错误不伪装成策略 rejected；
- [ ] session 404 回到回放选择页并说明服务可能已重启；
- [ ] BLOCKED 保留棋盘、round、失败 action、reason、raw entry 和 scanner 预计信息；
- [ ] terminal 禁用购买/移动/结束部署，只保留结果和返回选择页；
- [ ] 页面 hash 恢复 session 后，以 GET 返回 phase 初始化界面，不复用刷新前本地状态。

## 12. 实施顺序

不得先完成皮肤再把坐标问题留到最后。建议按以下提交顺序施工，每步附最小测试：

1. `docs: freeze frontend step2 taskbook`（本文）；
2. `opening: fix and version side-aware formation coordinates`；
3. `transition: validate player deployment zones`；
4. `gameview: expose board and replay-prefix presentation contracts`；
5. `web: add role-aware world/view coordinate projection`；
6. `web: fix replay selection semantics and limited-start explanation`；
7. `web: rebuild game-first HUD and persistent deploy controls`；
8. `web: add opening/reinforcement card overlays and shop placement UX`；
9. `web: add unit/unlock/tech panels and collapsible audit drawer`；
10. `web: fix battle role mapping and authoritative round reset`；
11. `test: add two-side browser acceptance and visual evidence`；
12. `docs: append frontend step2 implementation summary`。

若 Q1 最终选择“严格只改前端”，第 2～4 步必须在本文实施总结中标为外部 blocker，且不得
宣称 P0 坐标 Gate 已完成。

## 13. 预计涉及的文件

以下是未来实施范围，不表示本轮已修改：

| 层 | 预计文件 | 任务 |
|---|---|---|
| 前端 | `web/static/game.html` | 页面结构、Canvas 投影、交互状态机、播放器、审计抽屉 |
| 可选静态拆分 | `web/static/game.css`、`web/static/game.js` | 无构建拆分；若不拆则继续保留在 HTML |
| GameView/session | `web/game_service.py` | board/role/legal/display contract、复位所需权威数据 |
| 回放库 | `web/game_library.py` | 回合口径、blocker 分类、start mode、路径兼容 |
| API | `web/game_api.py` | schema version 和稳定错误响应（若契约需要） |
| 开局 | `pysim/transition/opening.py` | side-aware formation 构造 |
| 部署 | `pysim/transition/deploy.py`、`errors.py` | 权威半场校验和 reason code |
| 数据工具 | `tools/build_opening_catalog.py`、`tools/build_game_library.py` | schema/manifest 重建 |
| 数据 | `data/game/opening_catalog.json`、`data/samples/replay_game/` | 新版本 committed fixture |
| 测试 | `tests/test_game_api.py` 及前端浏览器测试 | P0、session 和端到端验收 |

不得修改与 Step 2 无关的战斗数值、装备机制、benchmark oracle 或 replay 原始语料。

## 14. 测试任务

### 14.1 坐标与 opening 单元测试

- player 0 package 所有普通单位满足 `y < 0`；
- player 1 package 是同编队 Y 镜像并满足 `y > 0`；
- 两边 X、单位类型、等级和数量保持一致；
- 两方核心塔与单位在各自半场；
- world-to-view 与 view-to-world 对 player 0/1 都满足 round trip；
- 人类为任一 player 时，其单位投影到画面下方。

### 14.2 部署规则测试

- player 0 在负 Y 买/移动 accepted，正 Y rejected；
- player 1 在正 Y accepted，负 Y rejected；
- y=0 rejected；
- rejected 的 digest、supply、units、version 全部不变；
- 对手历史跨区 action 进入可审计 BLOCKED；
- 合法 historical plan 不因前端视图镜像改变。

### 14.3 Replay library/API 测试

- `[0..9]` 返回源范围 R0–R9、record count 10；
- runtime blocker 在 R5 时 playable through 为 4；
- strict blocker 在 R4、runtime blocker 在 R5 时两者分别返回；
- start mode 与服务端阈值一致；
- Windows `\` 和 POSIX `/` shard fixture 都能安全加载；
- scanner 预计 blocker 与 session 实际 BLOCKED 一致。

### 14.4 前端交互测试

- 单击/双击卡牌按 Q2 裁决进入正确购买状态；
- 幽灵只在合法区显示可放置状态；
- 购买 payload 坐标经过逆变换；
- unit selected、dragging、tech open 时结束部署仍存在；
- mouseup 只发一个 MoveUnit；
- stale/rejected 后本地状态与 GameView 对齐；
- opening/reinforcement pending 防重复点击；
- 审计抽屉折叠/展开不丢数据。

### 14.5 战斗与复位测试

- player 0/1 作为人类时 battle team 均映射到正确颜色和上下半场；
- tower_down/skill/spawn 事件位置经过相同转换；
- FINISH 前记录部署坐标，ACK 后相同 handle 的 GamePlayer 坐标一致；
- trace 末帧与部署坐标不同时，下一回合采用部署坐标；
- 播放 timer 和临时 frame state 被清理，不重复播放上回合；
- 经验/等级使用结算后的 GameView 值。

### 14.6 浏览器与视觉验收

至少验收：

- 1920×1080（参考截图原比例）；
- 1366×768（常见最低桌面尺寸）；
- Chromium 系浏览器；若项目已有其他目标浏览器，同步覆盖。

每个尺寸保存以下证据：回放选择、开局四卡、部署主屏、解锁 modal、单位/科技详情、增援
四卡、战斗播放、下一回合复位、审计抽屉展开和 BLOCKED。可以截图或录屏，但不得只凭
静态截图替代交互验收。

### 14.7 回归命令

未来实施完成后至少运行：

```bash
pytest tests/test_game_api.py -q
pytest tests -q
```

若增加浏览器测试，必须在实施总结中补充可直接复制的命令，并说明是否需要单独启动
8300 服务。测试不得依赖开发者手工预置一个未记录的内存 session。

## 15. Definition of Done

只有以下项目全部完成，Step 2 才算完成：

- [ ] 本文 Q1～Q4 已由用户裁决，或明确按推荐默认方案实施；
- [ ] 回放长度、普通前缀、严格前缀和 blocker 在 UI 中不再混淆；
- [ ] scanner 与 runtime 前缀/阻塞回合一致；
- [ ] opening catalog 坐标约定已版本化且双方初始 state 分处各自半场；
- [ ] 人类接管 player 0 或 player 1 时都固定显示在下半场；
- [ ] BuyUnit/MoveUnit 的所属半场由服务端权威校验；
- [ ] 单位、塔、技能事件和 battle trace 使用同一坐标/角色映射；
- [ ] 购买流程不再要求用户点击视觉上的对方半场；
- [ ] 结束部署在整个 deployment phase 始终可见；
- [ ] 开局、增援、解锁、单位详情和科技达到本文截图参考的信息层级；
- [ ] 审计抽屉保留 v1 全部审计能力；
- [ ] 下一回合单位按 GamePlayer/GameView 复位到上回合部署阵型；
- [ ] 两种人类 side 的自动化测试和浏览器验收都通过；
- [ ] 1366×768 与 1920×1080 不存在关键控件遮挡或横向溢出；
- [ ] `/`、`/bench`、旧 API 和全仓测试无回归；
- [ ] 实施总结已续写到 §18，实际行为、测试结果和任务书偏差均已记录。

## 16. 非目标与硬约束

Step 2 不做：

- 装备卡、未建模塔技能、未映射战场技能等新规则；
- 改善 pysim 战斗准确率或 benchmark 数值；
- 2v2、联网、多用户、账号或数据库；
- 用户上传回放；
- React/Vue 迁移或前端构建系统；
- 3D 战场、原游戏模型复刻或像素级临摹；
- 从截图裁切并发布原游戏素材；
- 把未来 replay snapshot 写回连续环境；
- 为了流程顺畅而 silent skip unsupported action。

硬约束：

1. 服务器 state 是真值；
2. world/view 坐标转换集中且可逆；
3. human/opponent 角色与 player 0/1 编号不能混用；
4. 未支持机制继续明确阻塞；
5. 所有价格和规则来自 GameView/transition；
6. 游戏化布局不能牺牲审计可见性；
7. 本任务完成声明必须有自动化测试和浏览器证据。

## 17. Step 0.1 review 原文留档

以下逐字保留用户最初写入本文的反馈，便于实施后逐条回看，不把后续解释覆盖原问题。

```text
后续前端step2的任务书 写在md中

如果进行实现 实现的总结也续写在本md文档中

# 对step0.1的review


## 选择开局

这里一开始进入8300/game下

显示没有可连续>=5回合的选项

这显然是不对的， 肯定是判定机制有问题

有不少显示“共10回合” 的 怎么会没有连续5回合？
肯定是哪里搞错了

## 初始单位

这里初始单位显示我方的单位 落在了对方半场？？
（虽然是可拖动的）

拖动后显示move_unit 这个正常

## 购买单位

购买单位之后要点击单位再点击对方半场才能放置单位
很神奇

要不改为双击单位就可以购买？


## 如何结束部署？

移动单位后很难回到结束部署

移动单位后右侧显示（升级 旋转 回收）
没有结束部署选项

## 结束部署后位置错误

结束部署后 对方和我方渲染在了一个场地上 这方面建议再确认一下

（红方单位问什么会渲染在了蓝方的指挥塔附近？）

## 进入下一回合之后单位复位

单位要复位。

先修那么多吧

## 更像

我在 data/screenshots/ 放了游戏截图
分析这些截图
使得前端更像游戏
```

后续补充裁决：复位数据使用 GamePlayer，等价于上回合部署结束时的阵型，详见 §1 和
§9。

## 18. Step 2 实施总结（实施后填写）

> 当前尚未实施。施工完成后在本节续写，不要提前勾选 Definition of Done。

### 18.1 用户裁决

- Q1：待回答；
- Q2：待回答；
- Q3：待回答；
- Q4：待回答。

已经在上面回答了

### 18.2 实际改动

待填写：按前端、GameView/session、transition、数据/fixture、测试分层列出。

### 18.3 验证结果

待填写：测试命令、通过数、浏览器尺寸、截图/录屏位置。

### 18.4 与任务书的偏差

待填写：每一项偏差的原因、风险、用户裁决和后续任务；无偏差时明确写“无”。

### 18.5 未完成项

待填写：仍未完成的复选框和 blocker；不得用“基本完成”替代明确状态。
