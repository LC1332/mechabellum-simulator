# PySim 伤害标定与逐兵种武器修正 oracle 执行任务书（2026-08-30）

> 上游任务书：[`pysim爬虫动力学与伤害标定修正任务书-2026-08-29.md`](pysim爬虫动力学与伤害标定修正任务书-2026-08-29.md)（下称"0829 任务书"）。  
> 用户 review：[`pysim_0829修正.md`](pysim_0829修正.md)。  
> 编写基线：0829 任务书的 R0（数据对齐）、R2（footprint/crawler flow/retarget 原语）、R4 解锁规则链、R1 runner 骨架已于 2026-08-29 会话落地（§1 清单）；oracle 取证、科技包、逐兵种武器修正、烘焙冻结未开始 —— 本任务书的主线。  
> 执行环境：Windows oracle 机器。游戏本体 `E:\SteamLibrary\steamapps\common\Mechabellum`，oracle 工程 `C:\Users\chengli\Documents\mech`，工具 `C:\Users\chengli\Documents\mech\RouteC\tools`。工具链关键事实已于 08-29 会话核实（§2，勿再重复探索）。  
> 当前状态：代码原语全部默认 OFF，八库 1793/2349 与 human 7/8 control 已复现零漂移；本任务书执行时第一步是 oracle 环境冻结（O0），任何数值修改都后置于取证。

## 0. 结论先行

上一会话把"能离线做的"做完了；本轮解决"必须上 oracle 的"。策略分三层：

1. **聚合遥测先行**：现有 `oracle_dll.dll` 已能在活体游戏内跑 `SimpleSimulator` 并返回逐单位 `dmgReal/dmgMax/kills`（§2.3 已核实样本）。这足以直接回答 0829 的 D1 单体标定、K1–K5 科技 A/B、骇客等级扫描、朝向总输出、以及新回放 8 pair 的复跑 —— 这些是本轮主线，**不需要改 DLL**。
2. **逐帧遥测独立立项**：任务书 §3.3 的逐帧契约（位置/朝向/首发时刻/换靶事件）当前 DLL 不产出。作为 O9 的设计评审项处理：先给设计（hook 点、采样率、输出格式、崩溃隔离），评审通过才动 `oracle_dll.c`；不允许因为没有逐帧数据就停掉第 1 层。
3. **修正后置于证据**：每个引擎改动（科技包、武器周期、转向门控）都必须先有 control/treatment 的 oracle dmgReal 差值，再实现，再 A/B，最后统一烘焙。禁止沿用"胜率偏高就调低"。

本轮主线固定为：

```text
O0 环境冻结 + O2 craft 能力核实
  -> O3 s30 标定库与字段归一化
  -> O4/O5 单体 120s 与朝向矩阵 (P0 高估兵种优先)
  -> O6 K1-K5 科技 A/B -> 引擎科技包实现
  -> O7 骇客扫描 / O8 footprint 接触边界 / O10 P1 低估复核
  -> O11 爬虫动力学 oracle 化 / O13 新回放过程量对齐
  -> O14 回归 + 烘焙 + engine freeze + RL/Transformer 移交
```

任何默认规则必须先有 oracle 证据；观察性胜率表只用于安排实验优先级。

## 1. 当前基线（2026-08-29 会话已完成，勿重做）

### 1.1 代码与数据

| 类别 | 产物 | 验证 |
|---|---|---|
| 回放对齐 | `tools/build_crawler_damage_cases.py` → `data/crawler_damage_oracle/crawler-damage-replay-v1/{manifest,alignment}.json` | 10 pair = 8 ok + 2 空场 + 0 ambiguous；hash 与 0829 任务书一致 |
| 轮次语义 | fight_round = pair.round（同轮 `units_fight` 为花名册）；report（match.round = N+1）的单位集合**不是**花名册，仅用于 exp 差值归因；缺失行 = 阵亡或被出售（survived=null） | exp 链 1744→3546→5236→… 逐场验证 |
| footprint | `pysim/battlefield/geometry.py`（footprint-spec-v1）+ 引擎 `footprint_box/footprint_reg` flag；剑齿虎(21) 20×20 provisional spec（内圈 20 × 爬虫直径 4 自洽；未烘焙） | 不可穿透/无重叠/确定性/性能测试过 |
| crawler flow | 引擎 `crawler_flow/flow_band/flow_w/flow_chirality`：近战爬虫仅在目标邻域带且未进射程时叠加周向切向流 | C2/C4 (96 模块) treatment 接触环 = 20（与 review 吻合）；768 模块性能门禁过 |
| retarget | 引擎 `crawler_retarget/retarget_hyst`：移动换靶滞回 + 事件审计 `(t,row,old,new,reason∈{first_lock,dead,out_of_range,closer_unblocked})` | 测试过；事件入 `b.retarget_events` |
| 标定 ledger | `pysim/calibration.py`（CalibrationRow）+ 引擎 `calib_ledger` 纯探针（fire_events/target_since/walked；强制 eq_ledger 记账） | 探针零影响断言；ledger 与 card_damage 对账断言 |
| 解锁规则 | `rules.py`：`unlock_limit_quote`（每方每回合 1）、`UNIT_EXPERT_OFFICERS`（六兵种专家）、`expert_auto_unlock_mechs`；`PlayerState.manual_unlocks_this_round`（旧档默认 0）；deploy/opening(GIFT)/settlement(advance_round)/env 四点接入；`UNLOCK_LIMIT_REACHED` 错误码 | 14 测试（U1–U10 对映）；transition 156 全绿 |
| runner | `benchmarks/{crawler_common,run_crawler_dynamics,run_damage_calibration,run_chaff_calibration}.py`（control/treatment 双臂） | 双臂冒烟通过 |
| 溯源 | `data/calib/step33/step33_provenance.json` | — |

### 1.2 复现基线（回归 OFF 臂的锚点）

| 基线 | 数值 |
|---|---|
| 新回放 control（`--skills`） | 7/8 winner；winner AliveMechCount \|err\| mean 41.5；`伤害标定 r14` 判错 |
| 八库 | 1793/2349 = 76.3%（s24 271/320、s25 140/186、s26 284/450、s27 124/140、s28 803/1004、s29p 39/57、s29cal 20/42、s29c 112/150）——逐库与 bench_ver 一致 |
| 非 RL 测试 | 232 passed / 5 skipped |
| 版本 | `ENGINE_VERSION=pysim-step31`、`SCHEMA_VERSION=transition-v0.7`（冻结纪律：结项一次性 bump） |

注意：爬虫卡 = 24 模块（mech_count）。0829 任务书里的"24/96/384/768 爬虫"按**模块数**理解 = 1/4/16/32 张卡；规划场景时勿再混淆（上一会话测试已踩过）。

## 2. Oracle 工具链核实事实（2026-08-29 会话，直接引用）

### 2.1 通道与协议

- **驱动器** `C:\Users\chengli\Documents\mech\RouteC\tools\oracle_b.py`：注入 `tools/inject/oracle_dll.dll` 进活体 `Mechabellum.exe`（CreateRemoteThread+LoadLibraryA，主菜单就绪即用；游戏崩溃自动重启+重注入已授权，step22 Q-A）。
- **作业协议**（文件队列，无 RPC）：驱动器写 `RouteC/data/oracle_work/in/{job}.json` = `{"seeds":[...], "sround": N}` 并预放 `in/{job}.{seed}.grbr`；DLL 逐种子跑完写 `out/{job}.{seed}.json`。job 名约束 `[A-Za-z0-9_-]{1,40}`。残留旧结果会被秒回 —— `submit_job` 已先删残留（勿绕开它）。
- **投递入口**：`oracle_b.submit_job(job, {seed: grbr_path}, timeout=180~240, sround=sround)`；`sround` = 构造局 XML 最后一个 `MatchSnapshotData/round`（从该回合快照出兵 + maxRound 截断）。完整用法范例：`step29_run.py::cmd_run`（craft → 解析 sround → submit → pysim 对照 → 落 `data/exp/<lib>/<name>.json`）。
- **DLL 执行链**（`tools/inject/oracle_dll.c` 头注）：`MatchUtility.LoadReplay` → `Replay.LoadBattleRecord` → `GetMatchSetting(mapID)` → `BattleSetting` 7 参 ctor（`MatchType.ReplaySimulate=1`）→ `SimpleSimulator.Run(setting)` → `GetFightResult()` + `BattleController.Current.GetBattleStatisticManager()` 逐单位统计。每个作业 SEH 隔离。
- **构造器** `craft_replay.craft(sc)`，场景 schema（step24 模板）：
  ```python
  {"template": <step24 meta.tpl>, "seed": 20220822,
   "p0": {"units": [{id,level,x,y}], "clear_techs": True, "techs": {mech: [tid]}},
   "p1": {...同构...},
   "clear_officers": True,
   "constructions": {"0": [], "1": []}, "use_construction": False,
   "towers": {"0": [0,0], "1": [0,0]}, "skills": <可选>}
  ```

### 2.2 oracle 结果 payload（已核实字段）

来自 `data/exp/s29cal/cal_clr_m_1.json` 的 oracle 记录：

```text
ok, err, job, seed, winner(0/1), score[2], alive[2], deadScore,
reportCount, bsmEnabled,
units[] = {team, uid, rectype, mechid, dmgMax, dmgReal, kills}
样本: {team:0, uid:21, rectype:0, mechid:0, dmgMax:557361, dmgReal:7911, kills:9}
```

- `dmgReal` = 该单位实际造成的伤害（伤害标定的主指标）；`kills` = 击杀数；`score/alive/deadScore` = FightReport 口径。
- **未证实语义**（O0 必答）：`dmgMax` 与 `dmgReal` 的精确定义（样本比值 ≈70，疑似含过杀/理论齐射累计）；`uid` 与回放 unit index 的对应；`mechid` 样本恒 0 的原因；`rectype` 取值域。

### 2.3 能力边界（本轮策略依据）

| 需求 | 现通道 | 结论 |
|---|---|---|
| 单位级 120s 实际伤害/击杀 | ✅ dmgReal/kills | 直接可用（O4/O6/O7/O10 主线） |
| 科技 control/treatment 差值 | ✅ 同上 | 直接可用 |
| 胜负/存活/score | ✅ | 直接可用 |
| 首发时刻、逐帧位置、朝向、换靶事件、同时攻击数 | ❌ 仅聚合 | 逐帧遥测独立立项（O9）；聚合近似见各 case 备注 |
| 装备注入（超重型装甲 13030006）、TestCommand buff、开局军官 | ❓ craft 未核实 | O2 首日核实，含绕行方案 |
| 朝向 isRotate 构造 | ❓ 未核实 | O2 |

## 3. 目标、成功标准与非目标

### 3.1 必做目标

- 冻结 oracle 环境（游戏 build、DLL hash、数据表 hash、seed 复跑确定性）并回答 §2.2 的 payload 语义问题；
- 核实 craft_replay 的装备/buff/军官/朝向注入能力，冻结标定场景的构造配方；
- 建立 s30 标定场景库 + oracle↔PySim 同字段归一化对拍管线；
- 完成 P0 七兵种（雷霆27/深渊29/战争工厂17/暴雨12/沙虫23/台风22/堡垒1）单体 120s 标定与朝向实验，产出逐兵种 diff 与修正；
- 完成 K1–K5 科技 A/B（猎犬燃烧弹 11028、鬼鳐全弹 725、鬼鳐高爆 425、狂蝎双发 719、台风残骸引爆 5322）并在引擎实现（独立 flag）；
- 骇客 lv1–9 阈值扫描与冻结；
- footprint/爬虫射程接触边界的 outcome 级取证；逐帧遥测设计评审；
- P1 低估兵种（磁暴31/骇客14/泰山2002 至少三项）机制复核；
- 爬虫动力学 C1–C8 的 oracle 化与两臂对拍；1500 模块性能 gate；
- 解锁时序 Q11/Q12/Q13 的 oracle 受控局；
- 新回放 8 pair 的逐单位过程量对齐（PySim CalibrationRow vs 回放 exp 链/oracle 复跑）；
- 回归、默认烘焙、ENGINE_VERSION/SCHEMA bump、RL/Transformer 移交。

### 3.2 成功分层

| 层级 | 通过条件 |
|---|---|
| 环境可信 | manifest 含游戏/DLL/数据 hash；同 seed 复跑 dmgReal 逐单位一致（确定性）或给出方差口径 |
| 标定可信 | 每个待测兵种/科技有 control+treatment 双臂 oracle dmgReal；PySim 侧同字段 diff；稳态兵种相对误差 ≤5%、复杂/清杂 ≤10%，超出逐事件解释 |
| 科技可信 | 每项科技弹数/周期/单体与清杂收益分别可解释；引擎实现有独立 flag 且单关只影响相关 case |
| 动力学可信 | C1–C8 oracle 列就位；内圈容量/绕背/穿透 gate 在冻结容差内 |
| 解锁可信 | Q11/Q12/Q13 时序冻结；现有 14 测试 + 新增 oracle 对映测试全绿 |
| 集成可信 | OFF 臂 1793/2349 与 7/8 不变；ON 臂 flips 可回连；非 RL 测试全绿；schema v0.8 adapter 验证 |
| 可冻结训练 | engine freeze 后才通知 RL/Transformer 重建 sim labels |

### 3.3 明确非目标

- 不改 `pysim/rl/**`、`tests/rl/**`、RL/Transformer 训练代码（mask 同步以移交说明交付）；
- 不在逐帧遥测评审通过前盲改 `oracle_dll.c`；
- 不用观察性胜率差直接改数值；不用单一 `atk_mul` 硬拟合总伤害；
- 不把 provisional footprint/射程参数烘焙为默认；
- 堡垒防空弹幕不做对地标定（0829 已定）；2v2/特殊地图/部署碰撞不做；
- 装备 ID 不扩展（runtime 只回归）。

## 4. 实施任务

### O0：oracle 环境冻结与 payload 语义（首日，前置一切）

- [ ] `oracle_b.py status` 确认游戏进程/模块/工作区；记录游戏 exe `GameAssembly.dll` 版本戳与文件 hash、`oracle_dll.dll` hash、gamedata.json hash；
- [ ] 创建 `crawler-damage-oracle-v2` manifest（接续 v1 对齐表，新增本节环境字段与命令行实录，不写隐私绝对路径）；
- [ ] payload 语义四问：`dmgMax` 定义（对照已知理论齐射伤害推导）、`uid`↔回放 unit index、`mechid` 恒 0 原因、`rectype` 取值域；用 1–2 个手工可算场景（如 3 级暴雨对无科技剑齿虎）验证；
- [ ] 确定性检查：同 grbr 同 seed 复跑 ≥3 次，逐单位 dmgReal/kills 必须一致；不一致则本库全部实验改为 ≥20 seeds 均值±sd 口径并在 manifest 声明；
- [ ] 复跑 step29 任一已有 exp 记录 1 条，确认与历史 oracle 结果一致（通道未漂移）。

### O1：解锁 census（无 oracle 依赖，可并行）

- [ ] 对 1106 局语料（`local_data/humen_rounds.json` 或 norm 流）统计：每玩家每回合 folded `UnlockUnit` 成功次数分布；>1 的回合全部列出并人工归类（自动解锁来源/计数误差）；
- [ ] 自动解锁来源 census：兵种专家 6 种、单位获得卡、单位强化卡各自出现时 `unlocked_units` 快照的时序差分（round N 有专家/领卡 → round N..N+1 unlocked 集合新增）；
- [ ] 输出 `data/crawler_damage_oracle/crawler-damage-replay-v1/unlock_census.json`：每来源 → (观察数, 时序结论, confidence)；Q11 的 corpus 侧证据先于 oracle 受控局。

### O2：craft_replay 能力核实与标定配方冻结（首日）

- [ ] 装备注入：`伤害标定.grbr` 的靶带 `equipment: 13030006`。核实 craft 是否支持 per-unit equipment；不支持则给出绕行（提高靶等级/数量、缩短窗口、或直接以两份真实回放为 D1 靶源，见 O13）；
- [ ] TestCommand buff（先进防御）：同上核实；不能注入则以"靶有效血池 = 表值×等级×装备×维修折算"显式建模并记录口径；
- [ ] isRotate（朝向）：D2 的前提。核实 craft 的 units 是否透传 isRotate；
- [ ] 军官/开局包：Q11 受控局的前提（O12）；核实 `clear_officers` 与自定义 officers 注入；
- [ ] 科技注入冒烟：猎犬 11028 / 鬼鳐 725 / 425 / 狂蝎 719 / 台风 5322 各构一张单卡局，确认游戏侧接受该 tech id（`techs` 映射进 XML 且 oracle ok）；
- [ ] 把以上结论写进 manifest 的 `craft_capability` 节；任何"不支持"都触发对应任务的绕行方案，不静默降级。

### O3：s30 标定场景库与字段归一化

- [ ] 新库 `data/step30_scenarios.json`（group `CAL2`=单体标定 / `CH`=清杂 / `CD`=爬虫动力学 / `K`=科技 / `HK`=骇客），沿用 step24 模板与 `_craft_scenario` 通道；
- [ ] oracle 侧归一化器 `tools/step30_norm.py`（新增，放 RouteC 不入库；入库的是其输出 schema）：`{case, seed, units:[{uid→pysim card 映射, dmgReal, dmgMax, kills}], winner, score, alive}`；
- [ ] PySim 侧同 schema：扩展 `benchmarks/run_damage_calibration.py` 增加 `--oracle-diff <dir>`，逐 case 输出 `abs_err/rel_err` 表（CalibrationRow.actual_damage vs oracle dmgReal 求和口径）；
- [ ] 明确求和口径：多武器/多 member 兵种的 dmgReal 聚合到"卡"级与"成员"级两列，禁止只对总数；
- [ ] gate：混合场景无逐 source 归因时自动拒出数值（沿用 0829 §7.1）。

### O4：D1 单体 120s 标定矩阵（P0 高估兵种）

对 0829 §5/T6 P0 表逐兵种（27/29/17/12/23/22/1）：

- [ ] 场景：单兵种卡（等级 1 与 9 两档）vs 维修剑齿虎靶（O2 冻结的配方），固定距离/位置/朝向，120s 窗口（sround 截断）；
- [ ] seeds：确定性成立则 3 seeds 复核，否则 20 seeds 报均值±sd；
- [ ] 产出：oracle dmgReal（卡级/成员级）、kills、TTK（钢球/熔点类直接取 end_t）；PySim 对照（`run_damage_calibration.py`）+ diff 表；
- [ ] 每兵种修正顺序固定：静态表字段 → 空间/转向首发损失 → 稳态武器周期与弹数 → 弹道/命中/溅射 → 换靶/过杀 → 120s 总量；diff>10% 的兵种逐事件解释（用 calib_ledger 的 volleys/first_fire/impacts 定位差异层）；
- [ ] 犀牛/沙虫的近距反杀干扰：用"靶不反击"变体（若 craft 可造无武器靶；否则以 0829 §5/T5 隔离方案）单列一节。

### O5：D2/D3 朝向与换靶实验（重炮转向）

- [ ] 前置：O2 isRotate 核实通过；
- [ ] 0°/90°/180° 三朝向 × P0 重炮兵种（暴雨/台风/堡垒/雷霆优先），oracle dmgReal 与（若可得）end_t 差分 → 推转向时间常数与"转向是否门控攻击"的证据；
- [ ] PySim 侧：`heavy_facing_set=<ids>` flag（引擎已有 facing/facing_set 支路，仅逐兵种白名单接入 + 本实验标定），A/B 只切该 flag；
- [ ] D3 换靶：构造"靶 A 射程内、靶 B 更近且侧向"的 case，oracle 总输出差分近似换靶代价（逐帧不可得时的聚合近似，标注 approximate）；
- [ ] Q6 结论（转向门控整个攻击 or 可边转边前摇）写入 manifest；未决则 facing 保持 OFF。

### O6：K1–K5 科技 A/B 与引擎科技包

每项科技固定流程：control（无科技）/ treatment（带科技）同场景 → oracle dmgReal/kills 差分 → 引擎实现（独立 flag）→ PySim A/B 复核。

- [ ] **K1 猎犬燃烧弹 11028**：静止大型靶 / 移动穿越靶 / 密集爬虫三场景；单体与清杂收益分开；与火神燃烧场并排确认显著更小更短；引擎 flag `hound_incendiary_v2`（不复用火神参数，半径/持续/tick 从 oracle 差分反推）；
- [ ] **K2 鬼鳐全弹发射 725**：用户冻结语义 = 攻击时间 +150%（总 cycle 2.5×）、2→10 枚。oracle：target 数 1/2/10 三档弹分配 + 120s dmgReal；数据表侧同时反查 `attackIntervalChangeRate/attackDuration` 作用点（Q7）；引擎 flag `wraith_full_salvo_v2`；核对现有 `barrage_split` 是否把整轮伤害除以 10（若是，修）；
- [ ] **K3 鬼鳐高爆 425**：固定密度爬虫阵列，kills(t) 差分（10/30/60/120s 采样用不同截断 sround 跑多局近似）；`crawler_flow OFF/ON` 双臂标定（0829 §5/T8：不得在堆叠密度上标 AoE）；引擎 flag `wraith_he_v2`；
- [ ] **K4 狂蝎双发 719**：无科技/双发同场 A/B，120s dmgReal 倍率 + 两发是否同靶（target=1 vs 2 场景差分推断）；引擎 flag `scorpion_double_v2`；
- [ ] **K5 台风残骸引爆 5322**：首杀前基础输出与首杀后连锁清杂分开报告；防同 tick 重复引爆；引擎 flag `typhoon_wreck_v2`；
- [ ] 从 `cards[].technologies` 自动枚举其余"攻击力/攻击间隔/弹数/蓄力"类对地纯 DPS 科技，能归因的进 s30-K 组，不能的标 `mixed_unattributable`。

### O7：T9 骇客等级阈值

- [ ] 骇客 lv1–9 vs 维修剑齿虎（O2 配方），oracle 观察 winner/units（转化在 payload 的表现 = O0 语义问题之一：转化单位 team 翻转是否反映在 units[].team）；
- [ ] 二分 + 边界补点，冻结"刚好 ≤120s 解决"的最低等级与相邻失败等级；
- [ ] 记录 control beam 前摇/进度/阈值口径（数据表反查 + 等级扫描拐点）；骇客现有引擎行为（hack_cur/hack_rate 500/hack_gate）对照修正。

### O8：Q1/Q3 footprint 与爬虫射程接触边界

outcome 级取证（无逐帧时的聚合方法）：

- [ ] Q3 射程：爬虫卡 vs 靶，按初始距离 d 扫描（d=5..40 步长 5），oracle"首次出现伤害的 d"与"伤害开始时间随 d 的斜率"反推射程常数；与 review 的 ~10 比较；
- [ ] Q1 footprint：靶两侧等距放置等量爬虫（左右两列），对称性破缺程度 + dmgReal 时间曲线拐点近似矩形 vs 圆的表面距离差；结论只用于修正 provisional spec 的 half_width（仍不烘焙）；
- [ ] footprint 参数进 dump/digest/evidence/trace（0829 T2 未完项：trace 已有，补 dump/digest）。

### O9：逐帧遥测设计评审 + Q2 内圈容量

- [ ] 设计文档（不开工）：`oracle_dll.c` 增加 per-frame 采样的 hook 点选择（SimpleSimulator 内部 tick 入口 vs BattleStatisticManager 事件流）、采样率（≥10Hz）、输出（out/*.jsonl，字段对齐 0829 §3.3 契约）、GC/性能/崩溃隔离方案、与现有 SEH 作业协议的兼容；
- [ ] 评审通过后才实现；实现后先过"3 seeds 逐帧 digest 一致"再采数；
- [ ] Q2 内圈容量的三层近似（按成本递增）：① oracle 聚合：爬虫数 M=16..32 扫描，靶承受 dmgReal 的边际增益拐点 ≈ 内圈饱和点；② PySim crawler_flow 臂的接触环统计（已有，20±2 容差）；③ 逐帧或游戏内观测冻结最终常数；
- [ ] 1500 模块（63 张卡）120s 性能 gate：treatment ≤ control 2×，超限提交 profile。

### O10：P1 低估兵种复核（至少 磁暴/骇客/泰山）

- [ ] 磁暴 31：链式/电磁弧目标数与跳跃伤害 —— 构造 1/2/4/8 目标梯度局，oracle dmgReal 增益曲线 vs 引擎链模型；
- [ ] 骇客 14：并入 O7；
- [ ] 泰山 2002：双武器独立目标核实（单靶 vs 双靶 dmgReal 差分）；
- [ ] 其余（霸主 11 双弹已由 pc_set=2 部分建模、犀牛 5、兵蜂/野马/火獾 6/7/20 基础射程/接敌）按 O4 同法，时间允许才做。

### O11：爬虫动力学 C1–C8 oracle 化

- [ ] C1–C8 场景（`benchmarks/run_crawler_dynamics.py` 的 case 定义 → s30-CD 组）逐个过 oracle，采 winner/alive/end_t/dmgReal；
- [ ] PySim 双臂（control/treatment）对拍表：每 case 报 oracle vs 两臂的 winner/alive/end_t 偏差；动力学指标（接触环/绕背）暂以 PySim 内测量为准（标注 approximate，待 O9）；
- [ ] C6（两大型目标换靶）与 C7（周界空位补入）的 oracle 差分重点核对 `crawler_retarget` 语义；
- [ ] 旧 `chaff_xsep/chaff_nosep/sep_tan` 与新 flag 的替代关系说明写入 provenance（新机制 ON 时旧实验开关必须显式失效或告警）。

### O12：解锁时序受控局（Q11/Q12/Q13）

- [ ] 前置：O2 军官注入核实 + O1 census 证据；
- [ ] Q11：持剑齿虎专家 20036 的构造局，观察 round 1..4 的 `unlocked_units` 快照差分（立即解锁 vs activeRound=3 解锁）；六专家各一局；
- [ ] Q12：每 kind（单位获得卡/单位强化卡/经济卡/装备卡）各取 1–2 张卡的受控局，locked→unlocked 差分冻结纳入表；
- [ ] Q13：同回合双主动解锁的构造局（若动作流可注入），确认游戏侧拒绝形态；不可注入则维持"核心统一拒绝 + census 无 >1 反例"结论；
- [ ] 结果回写 `UNIT_EXPERT_OFFICERS` 时序（advance_round 的 activeRound 门）与 `reinforcement_auto_unlock_mechs` 覆盖表；不一致即改引擎并补测试。

### O13：新回放 8 pair 过程量对齐

- [ ] 用 v1 对齐表的 8 case：PySim（现有 `--skills` control + calib_ledger）逐 case 报 per-card damage/kills/volleys vs 回放 exp 差值（exp 链即真实伤害归因通道）+ report alive；
- [ ] oracle 复跑选项：两份 .grbr 本身是真实回放 —— 以 sround=各 fight round 复跑 oracle，得到同起点确定性结果，与真实 report 对齐后作为 8 case 的 oracle 基线（多 seed 变体报方差）；
- [ ] `伤害标定 r14`（当前唯一判错 pair）专项：逐单位 diff 定位 miss 层（伤害量/接敌时间/清杂效率）；
- [ ] 产出逐 case diff 表进 `data/crawler_damage_oracle/crawler-damage-replay-v1/pairs_process_diff.json`。

### O14：回归、烘焙与冻结（收尾）

- [ ] 单项 flag A/B：每个新 flag（footprint_box/crawler_flow/crawler_retarget/heavy_facing_set/hound_incendiary_v2/wraith_full_salvo_v2/wraith_he_v2/scorpion_double_v2/typhoon_wreck_v2）过"单关只影响相关 case"gate；
- [ ] 组合臂（全部 ON）跑八库 + 1106 局 paired A/B：报 good/bad/net flips、survivor/end-time 变化、paired bootstrap CI；旧库下降逐 case 审计（补偿误差 vs 新回归）；
- [ ] OFF 臂 bit-exact：1793/2349 与 7/8 不变（已有锚点，重跑确认）；
- [ ] 装备 runtime 12 微型场景 + 19 专项回归；
- [ ] transition schema v0.7→v0.8 bump + `manual_unlocks_this_round` 旧档 adapter 显式测试（本会话字段已就位，只差 bump 与迁移测试）；
- [ ] `ENGINE_VERSION` 一次性 bump；`data/calib/step34/step34_provenance.json` 汇总本轮全部证据链；
- [ ] RL/Transformer 移交：schema/字段语义/`unlock_limit_quote` 入口/auto-unlock 时序/golden 预期 + `RL_UNLOCK_MASK_STALE` 解除声明；旧 sim labels 作废通知；
- [ ] 正式 human test 参数冻结后只跑一次；之后改动新建 run family。

## 5. Oracle 实验矩阵（s30 库规划）

| 组 | Case 前缀 | 内容 | seeds | 主指标 |
|---|---|---|---|---|
| CAL2 | `d1_<mech>_lv<1|9>` | P0×P1 兵种 vs 维修剑齿虎 | 3（或 20） | dmgReal 卡/成员级、TTK |
| CAL2 | `d2_<mech>_r<0|90|180>` | 朝向矩阵（重炮优先） | 3 | dmgReal 差分→转向常数 |
| K | `k1..k5_<tech>_<场景>` | 科技 control/treatment | 3/20 | dmgReal、kills、（K3）kills(t) |
| HK | `hk_lv<1..9>` | 骇客等级扫描 | 3 | winner/转化表现/拐点 |
| CD | `c1..c8_*` | 爬虫动力学 | 3 | winner/alive/end_t |
| CH | `ch_<mech>_d<24|96|384|768>` | 清杂密度梯度 | 3 | kills(t)、clear_t |
| FP | `fp_rng_d*` / `fp_sym_*` | Q3 射程 / Q1 footprint | 3 | 首伤距离/对称性破缺 |
| UN | `un_q11_<expert>` 等 | 解锁时序受控局 | 1 | unlocked 差分时序 |

规模估算：~120–160 case × 2–3 seeds ≈ 300–450 oracle 作业（step29 经验单作业平均 <60s，串行约 5–8 小时，可分批断点续跑）。

## 6. 测试与 Gate

### 6.1 数据/环境 Gate

- [ ] manifest 含游戏/DLL/数据/scenario hash 与命令实录；隐私路径不入库；
- [ ] payload 语义四问有答案并经手工可算场景验证；
- [ ] 确定性：同 seed 复跑逐单位一致（或声明方差口径且全部实验 ≥20 seeds）；
- [ ] craft 能力表就位；每个"不支持"有对应绕行且被对应任务引用。

### 6.2 标定/科技 Gate

- [ ] 每兵种/科技：oracle 与 PySim 双侧同 schema；绝对+相对误差都报；稳态 ≤5%/复杂 ≤10% 或逐事件解释；
- [ ] 多弹齐射同报弹数/单弹/整轮；击杀靶用 TTK/截断曲线；
- [ ] 科技单项关掉只影响相关 case；未取证字段保持 provisional。

### 6.3 动力学 Gate（接续 0829 §7.2）

- [ ] C1–C8 oracle 列就位；两臂对拍表产出；
- [ ] 1500 模块性能 ≤ control 2×；
- [ ] 内圈容量三层近似结论一致后冻结容差。

### 6.4 解锁 Gate（接续 0829 §7.5）

- [ ] Q11/Q12/Q13 冻结；census 无未解释 >1 回合；
- [ ] v0.8 adapter 测试；RL 移交文档发出。

### 6.5 回归 Gate

- [ ] OFF 臂：八库 1793/2349、新回放 7/8、非 RL 全绿、装备 runtime 不回归；
- [ ] ON 臂：flips 可回连；正式 A/B 报告含 CI；
- [ ] engine version 最后一次性 bump。

## 7. 推荐实施顺序与提交边界

### R0'：环境冻结 + 能力核实（0.5–1 天）
O0、O2、O1（并行）。产出 v2 manifest 与 craft 能力表。不改任何引擎数值。

### R1'：s30 库与对拍管线（1 天）
O3。先跑 2–3 个冒烟 case 全链路（craft→oracle→norm→PySim diff）。

### R2'：单体标定 + 科技 A/B（4–6 天）
O4、O5、O6。每兵种/科技独立提交：场景+oracle 记录入库，引擎修正另提交（带 flag），不混。

### R3'：特殊兵种与机制取证（3–5 天）
O7、O8、O10。骇客/磁暴/泰山机制结论优先。

### R4'：动力学与解锁 oracle 化（2–4 天）
O11、O12、O9（设计文档先行，实现另行评审）。

### R5'：过程量对齐 + 冻结（2–3 天）
O13、O14。

单人预估 **2.5–3.5 周**。oracle 不可用的时段做 O1/O13-PySim 侧/O9 文档/O14 的 OFF 臂回归。

## 8. 交付物

- `crawler-damage-oracle-v2` manifest（环境 hash、payload 语义、craft 能力表、命令实录）；
- `data/step30_scenarios.json` + `data/exp/s30/` oracle 记录 + 归一化 diff 表；
- 逐兵种（P0 全部、P1 ≥3）oracle/PySim diff 与修正提交；
- K1–K5 引擎实现（9 个新 flag）+ A/B 记录；
- 骇客阈值冻结表；Q1/Q3 接触边界结论；Q2 内圈容量三层近似；
- C1–C8 oracle 对拍表；解锁时序冻结表 + census；
- 8 pair 过程量 diff（含 r14 专项归因）；
- schema v0.8 + adapter 测试；step34 provenance；engine freeze 与 RL/Transformer 移交通知；
- 结项报告（含与本任务书的偏差与用户裁决记录）。

## 9. 开工前必须回答的问题（在 0829 Q1–Q13 基础上新增）

| 编号 | 问题 | 解决方式 | 未决时处理 |
|---|---|---|---|
| Q14 | `dmgMax`/`dmgReal` 精确定义与比值含义？ | O0 手工可算场景 | 只用 dmgReal，dmgMax 弃用 |
| Q15 | `uid` 与回放 unit index / pysim card 的映射规则？ | O0 双侧对齐 | 按 team+顺序映射并标 approximate |
| Q16 | oracle 通道对同 seed 是否逐单位确定？ | O0 复跑 ×3 | 全库 ≥20 seeds 口径 |
| Q17 | craft 能否注入 equipment/TestCommand/officers/isRotate？ | O2 冒烟 | 各任务启用绕行方案 |
| Q18 | 转化（骇客）在 units[].team 的表现？ | O7 扫描拐点 | 阈值只用 winner/拐点 |

Q1–Q13 沿用 0829 任务书 §10，其中 Q2/Q3/Q4 以 O8/O9 的聚合近似先给 provisional 值，逐帧通道评审通过后再冻结。

## 10. 推荐复现命令

```bash
# 上一会话基线锚点（OFF 臂不变性）
python tools/build_crawler_damage_cases.py --validate
python -m pysim.replay_check --rounds local_data/crawler_damage_replay_v1.json --skills
python benchmarks/run.py --lib all

# oracle 通道（RouteC）
python tools/oracle_b.py status
python tools/oracle_b.py run <构造.grbr> --seed 11 --tag smoke30

# 本轮 runner（已就绪）
python benchmarks/run_crawler_dynamics.py --arm control,treatment
python benchmarks/run_damage_calibration.py --mechs 12,22 --orient 0,90,180
python benchmarks/run_chaff_calibration.py --attackers 3,22 --densities 96,384

pytest -q tests/test_crawler_dynamics.py tests/test_damage_calibration.py \
  tests/transition/test_unlock_rules.py
```

新增工具（`tools/step30_norm.py` 等）的 `--help` 实测输出冻结后回填 manifest；任务书不预虚构脚本参数。
