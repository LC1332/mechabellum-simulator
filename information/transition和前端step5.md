# Transition 与前端 Step 5：BLOCKED 机制清点、任务书与 QA

> 审计日期：2026-08-28  
> 代码基线：commit `e83c26b`  
> 范围：`pysim`、`pysim/transition`、`/game`、capability scanner、Normal 1v1 回放。  
> 本文先冻结现状和待裁决规则；本轮不实现机制。用户 QA 结果应直接追加在第 10 节，
> 后续实现总结、验证数字和遗留项继续写在本文末尾。

## 0. 结论先行

目前 `/game` 中需要区分三种“阻塞”，它们不是一回事：

1. **运行时硬阻塞**：历史对手动作无法 canonicalize，或执行时 receipt 被拒，session
   原子回滚并进入 `SESSION_BLOCKED`。这是 Step 5 首先要消除的墙。
2. **strict-effect 截断**：动作可以执行，但战斗效果未实现或未被真实游戏 oracle 验证。
   它只应缩短严格效果前缀，不应阻止普通 session 运行。
3. **普通玩家非法动作**：例如钱不够、单位本回合不可移动。它只返回 rejected receipt，
   state/version 不变，不应把整个 session 标成 `BLOCKED`。

当前 Normal 1v1 语料中的硬机制墙高度集中：

- `900001` 对建筑回收：`3256` 次；
- `ID=0 + SkillIndex` 无法从当前技能槽还原真实技能：`2195` 次；
- `1500001` 移动信标：`1571` 次。

三者合计 `7022/8486 = 82.8%` 的 `UNSUPPORTED_ACTION_FIELD`。因此 Step 5 不宜先广泛
补低频伤害技能，而应先闭合**建筑回收、技能槽重建、移动信标**。

此外有两个会扭曲审计结论的结构问题：

- strict scanner 对普通非装备增援卡返回的数据缺少 `effect_complete`，所以即使四张卡
  都不是装备，也会返回 `APPROXIMATE_REINFORCEMENT_EFFECT`；这是 scanner 元数据错误，
  不能解释为这些卡真的都未实现。
- 非装备增援卡目前又存在反方向问题：runtime 只要认识费用，通常就直接视为可执行；
  63 张单位强化卡和 13 张专家/补给卡没有逐卡六段支持度，可能出现“实际只实现一半，
  但普通前缀没有阻塞也没有 warning”的盲区。

## 1. “会造成阻塞”的准确含义

### 1.1 `/game` 真正进入 `SESSION_BLOCKED` 的条件

`web/game_service.py::_run_opponent_plan()` 对历史对手采用严格执行：除升级经验补齐外，
任意历史动作 receipt 被拒都会回滚整个对手 plan，并进入 `SESSION_BLOCKED`。包括：

- raw/字段/规则未支持；
- 技能 ID 或目标无法解析；
- 资金不足、购买额度超限；
- 单位不存在、科技前置不满足；
- 单位本回合不可移动、位置非法；
- 历史 plan 缺少 `FinishDeploy` 或没有走到 `PRE_BATTLE`；
- 正规化引用未解析、对手回合缺失。

人类玩家提交相同非法动作时，只显示明确拒绝，不会永久阻塞 session。也就是说，
`BLOCKED` 的产品语义是“历史对手在当前反事实时间线中已无法忠实继续”，而不是泛指
按钮不可用。

### 1.2 runtime playable 与 strict effect

当前 capability 有两条轴：

```text
runtime_playable_through_round
  = 动作、经济与持久状态能否继续执行

strict_effect_through_round
  = 在 runtime 可执行的基础上，战斗/结算效果也闭合且 confidence=verified
```

已知装备 transition 完整但战斗效果缺失时，应允许普通 session 继续并显示 warning；
未知装备 ID、未知动作字段、不能进入持久 state 的动作才是硬 blocker。

## 2. 语料审计口径

本次使用本机 `local_data/humen_rounds.json`：

- 全量：1106 局、20282 个 player-round；
- `/game` 目标域：1005 局 `gameMode=Normal && matchMode=VS_1_1`、17074 个
  player-round；
- 每个回合用当前 `Normalizer(Economy(GameData))` 现场去 Undo/Cancel，再调用当前
  `capability.classify_norm_entry()`；
- 下表统计的是**正规化后仍存活的动作出现次数**，不是原始点击数，也不是“首次 blocker
  的 option 数”。同一 option 可能在更早的 blocker 处已经停止；因此这些数字用于排机制
  工作量，不可直接相加成可玩率。

当前本机这份 `humen_rounds.json` 不含完整 `reinforce_offers`，且没有 Step 4 当时生成的
`local_data/replay_game`。所以本文不拿它重算 option 前缀；Step 4 记录的“2010 options、
enabled 4→29”保留为上次构建结果，Step 5 实施时必须用完整 `.grbr` 重新建库后复核。

## 3. 当前运行时硬 blocker 清单

### 3.1 未支持或无法解析的技能/装置

Normal 1v1 中共有 `8486` 个 `UNSUPPORTED_ACTION_FIELD`：

| 优先级 | ID / 通道 | 当前解释 | 正规化动作数 | 当前缺口 |
|---|---|---|---:|---|
| P0 | `900001` + `ConstructionIndex` | 战地回收用于建筑 | 3256 | 单位回收已支持；建筑回收完全未建模 |

卖建筑非常常见 墙会回收50
炮会回收100 

这个应该帮我修复 预计难度不大

| P0 | `ID=0` 未解析 | 只能看到 `SkillIndex`，live 槽表中找不到该槽 | 2195 | codec/技能槽重建失败，不能判断实际机制 |

SkillIndex = 0 应该就是快速补给
立即获得200补给，下回合获得的补给减少300

| P0 | `1500001` | 蓝图 3 研究后得到的移动信标 | 1571 | 三个 Positions 的路径/单位选择语义未实现 |

移动信标的机制是这样的
移动信标会指定第一个位置下一个半径为 40米 的圆圈
覆盖到的单位会参与移动
（注意如果对于多模组单位 会出现一部分被覆盖一部分不被覆盖的情况

这些单位会先走向 第二坐标 （对应中心圆圈的对应位置）
再走向第三坐标（对应中心圆圈的对应位置）

单位在走的时候 如果射程内有敌人也会进行攻击
（这里你可以简化为停下来攻击）直到射程内没有敌人再继续走


| P0 | `400002` | 蓝图 1 研究后得到的黏油弹 | 400 | 形状、减速、持续与叠加未实现 |

黏油弹和火焰弹是一样的

会覆盖从第一个点为中心半径30m
（用一个圆型横扫）
到第二个点为中心半径30m
的范围

如果落下的时候有护盾，则护盾覆盖的地面上不会有黏油

黏油中的单位移动速度降低55%

如果黏油被点燃则转化为火焰 （当回合之后消失）
地面上的黏油 如果没有被点燃，黏油会在战场上持续2回合（也就是下一次战斗的时候还有）
（区别是鬼鳐的科技黏油弹或者火神的黏油弹是当回合就会消失的）

| P0 | `30001` contraption | 未知装置 | 287 | 已知费用 100，但身份和战斗效果未知 |

我没有看到 30001的装备 

13030001 是激光瞄具



| P1 | `200001` | 电磁冲击 | 281 | EMP/status/护盾交互未实现 |

电磁冲击大约半径60

如果命中护盾 对护盾造成20000伤害

被命中的科技暂时失效 移动速度降低40% 持续25s

如果地面单位被护盾包裹 则不受到电磁冲击影响

| P1 | `1500002` | 增援卡移动信标 | 166 | 与 `1500001` 的差异和 waypoint 规则未知 |

增援卡移动信标和蓝图的移动信标是一个功能

| P1 | `300006` | 离子轰炸 | 103 | 移动光束轨迹、tick、友伤/护盾未实现 |

离子轰炸也是从A点到B点，以半径为20的圆扫过 持续性伤害的

这个可能要标定一下速度和dps。

| P1 | `600002` | 烟雾弹 | 77 | 直线区域、射程降低、持续一回合未实现 |

烟雾弹的范围和黏油弹是一样的
落下的时候也会被护盾阻挡

| P1 | `300005` | 闪电风暴 | 51 | 随机/固定落雷、tick、减速未实现 |

这个你也想办法实现一下 看看有没有办法反向

| P1 | `500002` | 酸液弹 | 37 | 百分比掉血、易伤、区域持续未实现 |

酸液的覆盖范围和黏油弹也是一样的

| P1 | `200002` | 巨型电磁冲击 | 31 | EMP 大范围版本未实现 |

这个相当于是更大的电磁冲击
仅仅修改小电磁冲击的半径就能实现

| P1 | `200003` | 光子投射 | 31 | 减伤与 status 免疫未实现 |

光子效果都是降低伤害30% 免疫电磁、印染、酸液 和退化光束（退化光束是恶灵的科技）

这里可以理解为给覆盖范围内友军单位20s的光子效果


上述合计正好为 `8486`。其中 `900001` 不是“整个技能未支持”：单位目标已经正规化为
typed `SELL_UNIT`，只有建筑目标仍被精确阻塞。

全 1106 局混合模式语料里另有 24 次 `900004`，但它们不在本阶段的 Normal 1v1
目标域；先保留为待识别的非目标机制，不计入上表和 Step 5 完成 Gate。

### 3.2 科技规则数据缺失

Normal 1v1 有 `501` 个 `MISSING_RULE_DATA`，均来自 `UpgradeTechnology` 的 tech ID 不在
`GameData.techs`。这批并非都缺战斗实现：不少 ID 已出现在 `engine.py` 特判和
`information/科技的购买价格.md` 中，只是 transition 的“科技定义、所属兵种、价格、
前置”没有统一登记，导致购买动作在战斗前被拒。

| 次数 | 单位 | Tech ID | 已知名称 |
|---:|---|---:|---|
| 106 | 猎犬 | `4228` | 消防装置 |
| 96 | 堡垒 | `1001` | 保护屏障 |
| 82 | 爬虫 | `3510` | 松散队列 |
| 30 | 犀牛 | `2305` | 残骸利用 |
| 24 | 犀牛 | `2805` | 最后一击 |
| 22 | 魔眼 | `4430` | 飞行模式 |
| 18 | 恶灵 | `4418` | 地面巡航 |
| 16 | 剑齿虎 | `4721` | 见价格调查表，需核名 |
| 15 | 骇客 | `1014` | 保护屏障 |
| 14 | 钢球 | `2408` | 重装锁定 |
| 13 | 骇客 | `1714` | 强化控制 |
| 11 | 台风 | `5222` | 维修阵列 |
| 11 | 深渊 | `2329` | 残骸利用 |
| 11 | 沙虫 | `3823` | 突袭 |
| 7 | 沙虫 | `3723` | 沙暴 |
| 6 | 雷霆 | `4027` | 连锁 |
| 5 | 深渊 | `4329` | 纵扫 |
| 5 | 台风 | `4722` | 防空标记 |
| 4 | 犀牛 | `2505` | 动力装甲 |
| 2 | 爬虫 | `2710` | 酸性爆炸 |
| 2 | 磁暴 | `493101` | 应急装甲 |
| 1 | 台风 | `5122` | 战地重组 |
| 1 | 试验级丧钟 | `48400102` | 特殊单位科技，名称/价格未冻结 |

处理上应拆成两步：先把已有高置信价格表的 22 个普通 ID 接入统一 tech registry；
试验级丧钟等特殊单位科技继续保持 unsupported，直到 ID、价格和效果有证据。

### 3.3 数据/codec blocker，不应误算成 pysim 战斗机制

| blocker | 当前情况 | 正确归类 |
|---|---|---|
| `ChooseAdvanceTeam` raw marker | raw classifier 会报 unsupported；`/game` 实际由 opening catalog 在 round 0 单独处理 | codec/phase 边界，不是战斗机制 |
| `UNSUPPORTED_OPENING` | team ID 不在 29 组 opening catalog，或源记录缺 round 0 | 数据覆盖 |
| `MISSING_REINFORCEMENT_OFFERS` | shard 没有完整四选一候选 | 数据覆盖 |
| `MALFORMED_REPLAY_REFERENCE` | norm 缺失、回合缺失、引用未解析 | 数据质量 |
| 对手动作 receipt 被拒 | 可能是资金、单位、CD、额度等前序偏差级联 | runtime/scanner 一致性 |
| 对手 plan 无 `FinishDeploy` | 无法走到一场完整战斗 | 回放/normalizer 完整性 |

全 1106 局语料中曾统计到 60 个 `unknown_index`；本次过滤到 Normal 1v1 后为 0。非 1v1
还存在 side/team 与坐标系不一致导致的买入半场误判，但 2v2 和特殊模式不属于本阶段目标，
不能为修它而放宽 Normal 1v1 合法性。

## 4. 不会阻塞普通 session、但会截断 strict effect 的机制

### 4.1 装备

当前 25 个装备 ID 的 transition/state 链路都已建立；Normal 1v1 中正规化后有 2589 次
`UseEquipment`。它们不会阻塞普通 runtime，但没有任何一个达到 `effect_complete`：

- 7 个已有静态 battle spec，但缺真实游戏 oracle，confidence 仍为 provisional：
  `13030001` 激光瞄具、`13030002` 重型装甲、`13030003` 改良火控、
  `13030004` 强化模块、`13030005` 速攻模块、`13030006` 超重型装甲、
  `13030007` 增幅核心；
- 其余 18 个没有完整 battle effect；护盾、恢复、免疫、生产线、信标、酸液、寄生、
  低血触发、统御核心等仍缺；
- 部署模块 `13040001` 的“每回合可移动”已在 Step 4 进入 movement rules，但 registry
  仍把它列在 cross-round unmodeled，说明 registry 证据文字与当前实现需要同步；
- 强化模块的升级折扣已进入 transition，静态攻防也已进入 battle，但仍需验证替换、出售、
  升级和多回合持久语义。

### 4.2 已执行但仍 provisional 的技能/装置

以下机制普通 runtime 可执行，battle path 也存在，但数值或空间规则没有真实 oracle，
不能标成 verified：

- 导弹打击 `300001`、空投护盾 `800001`、燃烧弹 `100002`；
- 轨道轰炸 `300003`、核弹 `300004`、轨道标枪 `300007`；
- 地底威胁 `1200001`、犀牛来袭 `1200002`、呼叫机群 `1200003`、
  呼叫战舰 `1200004`、天降火神 `1200005`；
- 飞弹装置 `10001`、护盾装置 `20001`。

其中燃烧弹已把 DPS 改为 270，但仍用圆形区域近似真实直线火墙；多发轰炸的 sunflower
落点、若干 splash/radius/CD 仍是 `cal`。这类机制应该显示 fidelity warning，不应制造
runtime `BLOCKED`。

### 4.3 建筑与 settlement 的“半效果”

建筑 cid 1–4 可以从快照进入战斗，但：

- 没有 typed、稳定的 `ConstructionState`；
- `ObjectOutcome` 不会把建筑剩余耐久/死亡写回下一回合；
- 建筑回收未实现；
- 建筑、塔、装置是否计分/给经验的口径未独立确认。

这会造成两种表现：建筑回收直接硬阻塞；不发生回收时 session 虽能继续，但后续回合的
建筑状态可能偏离真实游戏。

## 5. 当前支持度扫描器的两个缺陷

### 5.1 strict 对普通增援卡的假阳性

`capability.offer_fidelity()` 对非装备卡只返回：

```python
{"transition_complete": True, "battle_fidelity": "exact"}
```

没有 `confidence` 和 `effect_complete`。`scan_offers(strict_all_supported=True)` 又用
`not fid.get("effect_complete")` 判定失败，因此四张普通单位强化卡也会被 strict 截断。

最小复现：`[30101, 30102, 30104, 30105]` 四张堡垒强化卡在 runtime scan 返回 `None`，
strict scan 返回 `APPROXIMATE_REINFORCEMENT_EFFECT`。Step 5 必须先修这个结构问题，
否则 strict prefix 没有机制解释价值。

### 5.2 runtime 对非装备增援卡的假阴性

当前共有 478 张增援条目：357 张单位获得卡、63 张单位强化卡、24 张装备、21 张
舰长技能/战术、13 张专家/补给卡。除装备外，runtime 支持判断主要检查“费用存在、赠送
单位能解析”，随后：

- 单位强化卡/专家卡把 ID 加进 `officers`；
- 舰长技能卡把 ID 加进技能槽；
- 单位获得卡生成单位。

但这并不证明每张卡的特殊效果完整。engine 能读取 gamedata 中的静态 officer mods，
经济和部署特殊项只实现了一部分；行为型强化、召唤/经验/替换/叠加等没有逐卡 registry。
因此当前普通前缀可能高估支持度。

Step 5 需要把“增援卡类别”改成“逐 item ID + 逐 effect”的六段登记，至少输出：

```text
decode / legality / economy / persistent_state / battle / settlement
confidence / evidence / effect_complete
```

## 6. 其他可能让历史对手在运行时阻塞的规则偏差

这些未必会被 build-time scanner 提前发现，但会在 `_run_opponent_plan()` 执行时被拒：

1. **经济残差**：`supply_exact_rate` 仍只有 36.6%。`Income200r` 没有解释所有隐藏资金流，
   历史对手可能出现 `INSUFFICIENT_SUPPLY`；当前只有升级经验允许 override，资金不允许。
2. **技能槽重建**：normalizer 的 live 槽视图无法完整纳入专家回合赠技、跨回合 slot 状态
   和部分同回合拾取，形成大量 `ID=0` blocker。
3. **建筑持久状态**：前一场战斗没有回写建筑死亡，下一回合 ConstructionIndex 可能指向
   transition 认为仍存在或根本没有登记的对象。
4. **科技元数据分裂**：engine、价格调查表与 `GameData.techs` 不是同一个定义源；购买端
   先拒绝，battle 端已有的效果也无法到达。
5. **scanner/runtime 判定面不一致**：scanner 主要做类型、字段、购买额度和 offer 检查，
   runtime 还会校验实时资金、库存、槽 active/CD、目标、移动权、前置和 phase。

Step 5 不能通过 silent skip 或回灌下一回合快照消除这些 blocker。若产品需要 override，
必须逐类命名、记录金额/状态差异，并与真实规则支持率分开统计。

## 7. Step 5 实施任务书

### T0：先修能力口径与报告

- [ ] 新增可复现的 `tools/step5_blocker_report.py`，分别输出全量、Normal 1v1、首次
  runtime blocker、全部机制 occurrence、strict-only 和 runtime-reject 分布；
- [ ] blocker detail 必须含 `mechanism/id/name/raw_index/round/side`，不能只显示
  `passthrough {'raw_type': 'ReleaseCommanderSkill'}`；
- [ ] `offer_fidelity()` 对所有类别返回完整字段，修复非装备卡 strict 假阳性；
- [ ] 建立逐增援 item registry，修复非装备卡 runtime 假阴性；
- [ ] registry、scanner、GameView、battle warning 使用同一支持度对象；
- [ ] 重新从完整 `.grbr` 生成 norm、shard、manifest，并冻结 Step 5 基线。

### T1：高频 codec 与 transition 墙

- [ ] 建立 typed `ConstructionState` 与稳定 construction entity/ref；
- [ ] 实现 `900001` 建筑回收，并确认 `900004` 是否同一机制/变体；
- [ ] 重建技能槽生命周期：snapshot 槽、专家定时赠技、蓝图下回合赠技、同回合卡牌拾取、
  多份同 ID、active/CD、Cancel/Undo 全部共享一套 slot allocator；
- [ ] 消除可以通过已知 slot 事实解析的 `ID=0`；仍无法解析的记录保留精确 blocker 和证据；
- [ ] 为 `1500001/1500002` 建 typed waypoint/move-beacon action，不能把三 Positions
  压成单点 strike；
- [ ] 将已有高置信价格表的普通缺失 tech ID 接入统一 tech registry；特殊单位科技单列。

### T2：共享 status / area / world-object 框架

- [ ] 用一个 `TimedAreaEffect` 表达直线、圆形、移动区域和持续时间；
- [ ] 用一个 `StatusKind`/damage pipeline 表达 EMP、减速、引燃、酸液、烟雾、光子减伤
  与免疫；
- [ ] 先实现 `400002` 黏油弹，再实现 EMP `200001/200002`、光子 `200003`、
  闪电 `300005`、离子 `300006`、酸液 `500002`、烟雾 `600002`；
- [ ] 查明并实现 contraption `30001`；
- [ ] 每个 ID 独立登记 target shape、费用、CD、友伤、护盾、空地、持续、叠加和证据。

### T3：装备与增援逐卡闭合

- [ ] 为 7 个静态装备采集真实游戏 A/B oracle，验证叠加顺序后才升级 verified；
- [ ] 按“护盾/恢复/免疫 → 生产/召唤/地形 → 跨回合触发”实现其余 18 个装备；
- [ ] 同步 registry 中部署模块等已实现的跨层证据，删除过期 `unmodeled` 说明；
- [ ] 63 张单位强化卡和 13 张专家/补给卡逐 ID 审计静态、经济、部署、定时和行为效果；
- [ ] 单位获得卡验证数量、等级、出生位置、出售价值、是否占购买额度和移动权限。

### T4：runtime/scanner 一致与前端 QA

- [ ] 对每个历史对手 rejected reason 建 scanner 预测或明确标记 `runtime_only_check`；
- [ ] session blocker 页面显示机制名称、ID、目标、receipt、规则证据和“可否用近似继续”；
- [ ] strict warning 与 runtime blocker 使用不同颜色和文案，装备近似不得显示为
  `SESSION_BLOCKED`；
- [ ] 对经济/经验等 override 分别版本化；默认不新增 silent override；
- [ ] 用完整库验证首次 scanner blocker 与实际 session stop round 一致；
- [ ] 报告 Step 5 前后 enabled options、runtime prefix、strict prefix 和 blocker 消除量，
  不只报告测试通过数。

## 8. Step 5 Definition of Done

- `900001` 建筑回收、可解释的 `ID=0`、`1500001/1500002` 和普通缺失科技不再造成
  generic `UNSUPPORTED_ACTION_FIELD/MISSING_RULE_DATA`；
- 未完成技能仍按具体 ID 精确阻塞，不能错误映射或静默丢弃；
- strict scanner 不再因为字段缺失把所有普通增援卡判为 approximate；
- 所有 478 张增援条目至少有逐 ID transition 状态，不能只按类别推断；
- 普通 runtime、strict effect、fidelity warning、玩家非法动作四种状态在 API/UI 中可区分；
- 完整 `.grbr` → norm → shard → manifest 可一条命令重建，blocker 报告可复现；
- capability 预计的首次 runtime blocker 与实际 `/game` 停止回合一致；
- 无 silent skip、无下一快照回灌、无 rejected mutation、无未知 ID 被当成 0 元/无效果接受；
- 新实现机制有真实或用户冻结规则证据；provisional 不冒充 verified；
- 实施总结、测试、前缀变化、残余 blocker 和 QA 裁决追加回本文。

## 9. 本阶段非目标

- 不为提高覆盖率而支持 2v2、特殊模式或非标准地图；
- 不把所有装备和技能一次性标为 supported；
- 不在机制任务里同时重写 target selection、移动、弹道等 engine 热循环；
- 不把 build-time strict prefix 当作真实游戏胜负准确率；
- 不用当前 pysim 输出反向生成 oracle 证明自己正确；
- 不为历史回放通过而自动补钱、自动生成目标、跳过动作或读取未来快照。

## 10. 交给用户的 QA 裁决单

请尽量按编号直接回答。若某项不确定，可提供一局 `.grbr`、游戏内截图或说明“暂按
provisional”；实现会保留 blocker，不会猜测后宣称 exact。

### QA-1：战地回收建筑（最高优先级）

1. `900001` 对 `ConstructionIndex` 是否就是回收己方核心建筑/指令中心建筑？
2. 能回收哪些对象：开局建筑 cid 1–4、能量塔、临时飞弹/护盾装置，还是仅建筑？
3. 回收返还多少补给：固定值、建造价格、当前耐久比例，还是不返还？
4. 回收后对象是永久从跨回合状态删除吗？是否影响本回合战斗计分？
5. 混合模式语料中的 `900004` 与 `900001` 是相同回收的另一版本，还是完全不同技能？
   此项只做身份记录，本阶段不要求支持非 Normal 1v1。

用户裁决：

> 待填写。

### QA-2：技能槽与 ID=0

1. `SkillIndex` 是否在整局内稳定，旧槽使用后保留位置，新卡只追加到新槽？
2. 同一个技能有两份时，是否一定是两个独立 slot、独立 active/CD？
3. 专家在 `activeRound` 发放的技能，是回合收入前、增援选择前还是部署开始时入槽？
4. 蓝图研究得到的技能是否全部在下一回合入槽？战地回收/黏油弹/移动信标规则是否一致？
5. 若回放记录 `ID=0` 且当回合快照没有对应 slot，是否允许使用“本回合刚选中的技能卡”
   补建 slot，还是必须阻塞？

用户裁决：

> 待填写。

### QA-3：移动信标 `1500001` 与 `1500002`

1. 两个 ID 分别是什么来源、是否为同一效果的研究版/卡牌版？
2. 回放中一次释放常有三个 Positions；它们表示“起点、途经点、终点”，还是三个单位/
   三个目标区域？
3. 技能是部署阶段立即改单位位置，还是给单位本场战斗指定移动路径？
4. 如何选择受影响单位；为什么记录中通常没有 `UnitIndex`？
5. 是否能跨中线、进入侧翼区；是否触发 10 秒传送，是否与快速传送/部署模块叠加？
6. 路径持续一场战斗还是跨回合；能否被 Undo/Cancel；CD 是多少？

用户裁决：

> 待填写。

### QA-4：黏油弹 `400002`

1. 落点是直线、圆形还是沿两个/多个 Positions 定义的区域？
2. 对移动速度降低多少，是否还有引燃/伤害/射程效果，持续多久？
3. 影响敌我双方吗；影响空军吗；护盾内陆军是否免疫？
4. 多次黏油区域如何叠加；与燃烧弹/酸液/烟雾如何交互？
5. 技能 CD 和每回合可释放次数是多少？

用户裁决：

> 待填写。

### QA-5：装置 `30001`

1. `30001` 的游戏内名称和实际效果是什么？
2. 当前调查只确认费用 100；它是可攻击/可摧毁的装置、护盾、磁铁、地雷还是其他对象？
3. 作用范围、生命、持续、每回合上限和是否跨回合分别是什么？
4. 先进护盾装置 `10007` 或先进飞弹装置 `10008` 是否影响它？

用户裁决：

> 待填写。

### QA-6：EMP、光子、闪电、离子、酸液、烟雾的共用规则

请优先确认共用规则，而不是只给 tooltip 数值：

1. 圆形/直线区域如何由回放 Positions 定义；宽度、半径、移动方向和 tick 频率；
2. 是否友伤，是否影响空军；
3. 护盾是吸收技能伤害、阻挡 status，还是两者都做；护盾只保护陆军的规则是否适用于全部；
4. EMP 的“科技暂时失效”具体停用哪些属性/行为，持续时间是否可刷新/叠加；
5. 酸液 `3%/s + 受到 250% 伤害` 的百分比基于 max HP 还是当前 HP，是否作用于建筑/护盾；
6. “持续 1 回合”在单场 battle 中等于整场 120 秒，还是有固定秒数；
7. 光子减伤、烟雾射程下降、闪电减速与同类科技/装备是加法、乘法还是取最大。

用户裁决：

> 待填写。

### QA-7：缺失科技的购买规则

1. 第 3.2 节 22 个普通 tech ID 是否都是玩家可正常购买的科技，而不是默认科技/临时行为 ID？
2. 是否同意以 `information/科技的购买价格.md` 的价格和名称作为 transition v1 口径？
3. 这些科技是否仍遵循“基础价 + 当前已购科技数 ×200”的阶梯价，以及专家科技折扣？
4. 试验级丧钟 `48400102` 的名称、价格、前置和效果是什么？

用户裁决：

> 待填写。

### QA-8：strict-effect 的产品口径

1. 一回合四张增援候选中，只要有一张未 verified，strict prefix 就停止；还是只有玩家/
   历史对手实际选择未 verified 卡时才停止？
2. 普通 runtime 是否继续保持“未支持候选禁用，但可以选其他卡或跳过”？
3. 7 个已有静态实现但未做真实 oracle 的装备，应显示“已模拟·待校准”，还是与完全未实现
   装备统一显示“pysim 未计算效果”？

建议口径：strict 分成 `strict_all_offers` 与 `strict_chosen_path` 两个指标；普通 runtime
继续允许绕过未支持候选。UI 分开显示“已模拟待校准”和“未模拟”。

用户裁决：

> 待填写。

### QA-9：历史对手资金不足时是否允许 override

当前经济 exact rate 只有 36.6%。历史对手因模型遗漏而 `INSUFFICIENT_SUPPLY` 时，有三种
产品口径：

1. 保持硬 blocker，最严格但可玩前缀短；
2. 像升级经验一样，只补到刚好可执行，并在 audit/trajectory 标记
   `OPPONENT_SUPPLY_OVERRIDE`；
3. replay 模式对对手整回合使用快照推导的 injected income，sandbox/RL 仍用真实模型。

建议先选 3；它最适合“审计历史对手策略”，同时不会把补钱规则泄漏到自由 RL 环境。

用户裁决：

> 待填写。

## 11. 后续实施总结

> Step 5 实现后追加：commit、schema/engine/registry version、代码改动、自动测试、浏览器
> 验收、完整库重建命令、前缀变化、各 blocker 消除量、oracle 结果、偏差与剩余 QA。
