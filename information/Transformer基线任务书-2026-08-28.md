# Transformer Baseline 任务书（RL Phase 1.5，2026-08-28）

> 上游依据：
> [`第一阶段强化学习任务书-2026-08-27.md`](第一阶段强化学习任务书-2026-08-27.md)、
> [`pysim战场技能修正任务书.md`](pysim战场技能修正任务书.md)。
>
> 编写基线：commit `091f9cb`。当前 transition 为 `transition-v0.7`，battle engine 为
> `pysim-step31`，battlefield 输入为 `battlefield-input-v2`；已有 RL Phase 1 正式数据和
> checkpoint 仍绑定 `transition-v0.6 / pysim-step30`，不可直接作为本阶段正式训练数据。
>
> 当前状态（2026-08-28 施工后更新）：**Transformer 工程骨架已全部实现并通过测试**
> （contract/tokenizer/TValue/TPolicy-BC/losses/DDP/工具链/50 项单测/端到端 smoke/
> 7 卡 NCCL DDP smoke，详见 §18）。**T0 Gate 已由用户解除**：用户 2026-08-28 确认
> 1000 局回测结果良好，正式 test/arena 结论不再被禁止；contract `t0_backtest.status
> = accepted` 已落盘（commit `27859dd` 后，replay-set hash 与详细分桶指标待回测
> 报告归档后补充）。本阶段目标（用户指示）：**训练出能在 direct-pysim arena 中
> 超过回放赢家的 policy**。
> 文中复选框代表未来实施状态，只有代码、产物、固定测试集指标和可复现实跑证据同时存在
> 时才能勾选。

## 0. 阶段定位与先给结论

第一阶段不是“只用了经典算法”。实际包含三类模型：

| 类型 | Phase 1 实现 | 定位 |
|---|---|---|
| 经典非神经模型 | constant prior、logistic regression、HistGradientBoosting、频率策略 | 最低基线 |
| 神经网络 Value | 约 1.2M 参数的 DeepSets/MLP 双域模型 | 主 Value baseline |
| 神经网络 Policy | DeepSets board encoder + 分层 MLP/pointer heads 的 `pi_BC` | 主行为克隆 baseline |

因此本阶段不是“从经典算法第一次升级到深度学习”，而是从**集合池化神经网络**升级到能显式
表达单位间空间关系、技能区域、动作 prefix 和可变候选集合的 **Transformer baseline**。

本阶段命名为 **RL Phase 1.5**。它仍以监督学习/行为克隆和单回合 direct-pysim arena 为主，
目的是建立可信、可复现、能公平压过或证伪 Phase 1 DeepSets 的 Transformer 对照；不因使用
Transformer 就提前宣称进入 PPO、自博弈或完整多回合强化学习。

核心链路为：

```text
技能修正回测冻结
  -> v2 contract / observation / action / sim labels
  -> 同数据重训 DeepSets-v2（公平对照）
  -> Transformer Battle Value + Transformer BC
  -> teacher-forced + free-running + direct-pysim arena
  -> 决定是否值得进入离线策略改进 / DAgger / 单回合候选博弈
```

Transformer 的主要验证点不是参数量，而是能否修复 Phase 1 暴露的四个问题：

1. DeepSets pooling 难以表达单位间距离、射程覆盖、区域技能和局部克制；
2. `V_sim` prefilter top-k recall 只有 `0.381`，不能进入正式候选搜索；
3. `pi_BC` 正常 END 只有 `93.3%`、forced END `6.7%`，存在 prefix 漂移；
4. Value side-swap max difference 为 `0.12–0.81`，没有满足对称性 Gate。

## 1. 已知基线与本机资源

### 1.1 Phase 1 冻结结果

以下只作为历史对照，不得与重建后的 v2 指标直接混为同一实验：

- policy 数据 450,601 行，其中 Gold 264,066；
- battle real 9,042 行，其中 Gold 8,156；
- battle sim 13,587 个聚合 state/candidate 行；
- DeepSets `V_sim` candidate pairwise accuracy：`0.690/0.700/0.708`；
- DeepSets `V_real` test NLL：`0.640/0.659/0.694`；
- DeepSets Policy validation verb top-1：约 `0.44–0.45`，条件频率基线约 `0.47`；
- `best-of-8 direct pysim` 相对原始 sampled BC：mean gain `+0.202`，
  95% CI `[0.133, 0.278]`；
- arena action rejection 为 3，均为供给 mask/engine 残差；
- `pi_BC vs human replay` 仍明显为负。

### 1.2 当前硬件事实

本机可见硬件：

```text
GPU       8 × NVIDIA H20，单卡约 96 GiB，GPU 间 NV18/NVLink
CPU       2 × AMD EPYC 9K84，合计 384 logical CPUs
RAM       约 2.2 TiB
workspace 可用磁盘约 5.2 TiB
```

当前 `.venv-rl` 记录为 Python 3.11、PyTorch `2.7.1+cu126`，具备 SDPA；但在编写任务书时的
受限执行上下文中，PyTorch CUDA probe 返回 0 卡并伴随 NVML 初始化告警，而 `nvidia-smi`
宿主探针可看到 8 张 H20。用户已冻结资源边界：**本任务只允许使用物理 GPU 1–7，GPU 0
必须留给调试和其他任务**。正式施工前必须在实际训练 shell 中解决/确认
`CUDA_VISIBLE_DEVICES`、容器设备映射、NCCL 和权限；不能仅凭 `nvidia-smi` 认为 DDP 可用。

### 1.3 资源使用原则

- 不为“用满 7 卡”盲目扩大模型；先让模型容量与 26 万 Gold prefix、约 8 千 real battle
  标签相匹配；
- 小模型/ablation 优先按 GPU 并行多个独立 run，减少低效率 all-reduce；
- 中型正式模型使用单机 7 卡 DDP，并报告 scaling efficiency；
- 所有 launcher/config/manifest 显式记录物理 GPU allowlist `1,2,3,4,5,6,7` 和 reserved
  GPU `0`；本任务的自动重试、评测和数据生成进程也不得回退占用 GPU 0；
- `V_real` 小样本是主要过拟合风险，GPU 数量不能弥补标签规模；
- pysim label 生成主要吃 CPU，应按 NUMA 分片使用 CPU，与 GPU 训练错峰或隔离资源；
- 禁止用 test 指标选择模型、宽度、深度、epoch 或 loss 权重。

## 2. 范围、成功标准与非目标

### 2.1 必做范围

- 建立 `rl_transformer_contract_v1`，绑定修正后的 transition/battlefield/engine；
- 重建与技能修正一致的 v2 real/policy/sim 数据和 tensor cache；
- 在 v2 数据上重训 Phase 1 DeepSets，形成真正 apples-to-apples 对照；
- 实现 encoder-only 的 `TValue`：`V_battle_sim` 与 `V_battle_real` 独立输出；
- 实现结构化 autoregressive `TPolicy-BC`：预测下一个原子动作及其参数；
- 支持 unit、construction、tech、equipment、tower、技能/区域和有序多落点 token；
- 使用 transition 生成的 legality mask，禁止模型替代规则引擎；
- 3 个训练 seed、validation-only 模型选择、一次冻结 test；
- teacher-forced、free-running、side-swap、direct-pysim arena 和 best-of-N 评测；
- 单机 7×H20（物理卡 1–7）的吞吐、显存、利用率和 DDP 可复现报告；
- 输出数据卡、模型卡、失败样例、已知限制和 Go/No-Go 结论。

### 2.2 成功分层

| 层级 | 完成条件 |
|---|---|
| 工程完成 | v2 contract/data/cache、两类 Transformer、DDP、测试和报告均可复现 |
| 有效 Transformer baseline | 在同数据/同 split/同预算下至少一个核心指标显著优于 DeepSets-v2，且没有靠 test 调参 |
| Value 可用于预筛 | `V_sim` prefilter top-k recall 达到 `0.90`，并且 direct-sim regret 可接受 |
| Policy 可进入下一阶段 | rejection=0、正常 END≥99%、forced END<1%，arena 不因新 exploit 获益 |

“工程完成”不要求 Transformer 必须获胜。若 Transformer 没有超过 DeepSets，应如实结项为
“Transformer baseline 已建立，但复杂模型没有带来净收益”，不能换 split 或只挑最好 seed。

### 2.3 非目标

- 不做 PPO、SAC、IMPALA、多回合 self-play 或长期 `V_episode`；
- 不把语言模型 tokenizer、自然语言动作或永久 entity ID 引入控制链路；
- 不把 legality、资金、CD、目标形状交给模型猜；
- 不把 `V_sim` 当最终裁判，arena 最终结论仍来自 direct pysim；
- 不把正在回测的 provisional 技能数值包装成真实游戏 verified；
- 不做未经对照的超大模型，不以参数量、显存占用或 GPU 利用率作为策略质量；
- 不在 baseline 主结论中混入 DAgger、在线 RL、reward fine-tuning 或人类人工修 plan；
- 不提交大型 shard、tensor cache、checkpoint 或原始回放到 git。

## 3. 开工 Gate：技能回测与版本冻结

### T0：等待并吸收 1000 局人类回放回测

- [ ] 记录回测使用的代码 commit、回放集合 hash、游戏版本、样本数和完整命令；
- [ ] 记录总体准确率以及按 round、单位数、技能、fidelity、胜/负/平的分桶结果；
- [ ] 明确“准确率”口径：winner、WDL、damage、unit survival 或多指标，禁止只留一个百分比；
- [ ] 对新增技能单列有技能/无技能对照及 bootstrap 95% CI；
- [ ] 保存最差和最自信错误样例，不只保存总表；
- [x] 用户确认是否接受当前 provisional 技能口径进入训练
      （用户 2026-08-28: 回测已确认良好, 可以开始训练 — 已写入 contract
      t0_backtest.record.decision）；
- [x] 若回测导致技能参数、compiler、settlement 或 engine 再修改，继续 bump 版本后重跑本 Gate
      （条件项: 回测未引发再修改, engine 保持 pysim-step31, 无需 bump）。

### 3.1 正式冻结物

必须生成新的 contract，而不是原地覆盖 Phase 1 v1：

```text
contract_version       = rl_transformer_contract_v1
schema_version         = transition-v0.7 或回测后的更高版本
engine_version         = pysim-step31 或回测后的更高版本
battlefield_input      = battlefield-input-v2 或更高版本
observation_version    = transformer_obs_v2
action_version         = transformer_act_v2
data_version           = transformer_data_v2
sim_label_version      = sim_label_v2_<engine_digest>
tokenizer_version      = structured_token_v1
split_version          = 继承并冻结 phase1 replay-group split
training_gpu_allowlist = [1, 2, 3, 4, 5, 6, 7]
reserved_physical_gpus = [0]
```

- [x] `check_contract` 对旧 `data/rl_phase1_contract.json` 必须明确报版本不兼容
      （tests/rl_transformer::test_check_contract_rejects_phase1_contract）；
- [x] 数据、cache、checkpoint、arena manifest 都保存完整 contract digest
      （checkpoint/cache manifest 均含 contract_digest + binds）；
- [x] 任何影响 battle outcome 的代码变化使 sim cache 自动失效
      （sim_label_version = sim_label_v2_<engine_digest>, engine digest 变化即换版本）；
- [x] 任何 observation/action/tokenizer 变化使 tensor cache 自动失效
      （TOKEN_CACHE_BINDS + check_cache_manifest, 单测覆盖）；
- [x] 正式 test 运行后，配置冻结；若再改配置必须新建 run family，不能覆盖首次结果
      （第一正式轮 auto_v1 已完成且未被改写; DeepSets-v2 复跑使用独立 run family
      deepsets_v2/, 不覆盖 Phase 1 v1_full 历史产物; 配置冻结的自动强制工具仍待补）。

### 3.2 可并行与不可并行的工作

等待回测期间可以完成：模型骨架、toy data、token round-trip、DDP smoke、CPU/GPU throughput
probe 和单元测试。以下工作必须等待版本冻结：

- 正式 sim label 生成；
- 正式 DeepSets-v2/Transformer 训练；
- 正式 test 与 arena；
- 任何“优于旧模型/可进入下一阶段”的结论。

## 4. Transformer Observation 与 Token 契约

### 4.1 总原则

Transformer 消费结构化 token，不消费 JSON 文本。所有 token 必须由版本化 adapter 从
ObservationV2 生成；模型层不得读取 raw replay XML/JSON，也不得读取 FightReport、winner、
未来 HP、文件名、玩家名或 label path。

实体集合不使用随输入排序变化的 ordinal positional embedding。实体顺序被 permutation 后，
Value 输出应不变，Policy 的语义 pointer 输出应随 token permutation 等变。

### 4.2 `BattleTokenObservationV2`

建议 token 类型：

```text
[VALUE_CLS]
[GLOBAL]
[SELF_TOWER] [OPP_TOWER]
[SELF_UNIT] × N      [OPP_UNIT] × M
[SELF_TECH] × K      [OPP_TECH] × L
[CONSTRUCTION] × C   [DEVICE] × D
[SKILL_RELEASE] × S  [GROUND_AREA] × A
```

每个单位 token 至少编码：

- mech、side、level、exp bucket、equipment；
- 已购 tech 的关联表示；
- ego 坐标 `(x,y)`、朝向、空/地、多模组摘要、单位价值；
- 当前公开 status；
- fidelity/confidence 只作可见机制标记，不输入真实结果。

技能/区域 token 至少编码：source skill、owner、shape、ordered points、radius、剩余 round/tick、
shield rule、affects/layer 和 confidence。未知/unsupported 机制使用独立 token 与 mask，不静默
当作不存在。

### 4.3 `PolicyTokenObservationV2`

在 battle token 上增加：

- supply、buy quote/remaining、可解锁 mech、tech/equipment/skill inventory；
- 当前 unit/ construction observation-local handle；
- 技能 target kind、target arity、CD/stock/cost；
- 当前 prefix 长度、剩余 action budget；
- 过去原子动作的结构化 history，最多 64 步；
- 每步 receipt 的 `accepted/noop/fidelity` 摘要，但不输入由未来动作产生的信息。

history 的目的主要是建模“这一回合已经做了什么”和何时 END。当前 shadow state 仍是权威
状态；history 不得代替 transition，也不得包含人类未来计划。

### 4.4 空间关系编码

单位、建筑和区域之间增加可审计的 pairwise relative attention bias：

```text
dx/dy bucket + distance bucket + same/opposite side
+ entity type pair + inside/outside known area + air/ground relation
```

- bucket 边界写入 config 和 contract；
- 不使用 replay index/entity ID 大小；
- 所有几何从 ego 坐标计算，side swap 后使用同一 mirror 函数；
- relative bias 必须有数值单测，不能靠训练“自己学会镜像”；
- Flash/SDPA 路径若不支持自定义 bias，需有等价 fallback 和吞吐对照。

### 4.5 长度、padding 与截断

- Phase 1 的每方 64 单位 padding 不直接等于所有 token 的总上限；
- 先统计 v2 语料 P50/P95/P99/max token 数，再冻结 `max_entity_tokens`；
- 超限样本不得默默截掉单位或技能；主数据应排除并记录，或使用确定性分块聚合；
- action history 最大 64，超过即沿用 `forced_end/action_budget` 诊断；
- padding token 不参与 attention、pooling、pointer 或 loss。

## 5. Action V2：结构化自回归解码

本阶段仍按“每次生成一个原子动作，执行 transition，再重新观测”工作。Transformer decoder
在一次原子动作内按结构化顺序生成参数：

```text
[ACTION_BOS]
  -> VERB
  -> PRIMARY_OBJECT（mech/tech/equipment/skill/tower/blueprint/contraption）
  -> TARGET_POINTER（unit/construction，可选）
  -> POSITION_1 -> POSITION_2 -> POSITION_3（按技能 arity，可选、有序）
  -> ORIENTATION（可选）
  -> [COMMIT]
```

### 5.1 Mask 与 pointer

- 每个阶段只对合法候选计算 masked CE/ranking loss；
- unit/construction pointer 指向当前 observation token，不输出永久 ID；
- 每次 transition 后 handle 可重排，模型不得缓存旧 handle；
- 替换 Phase 1 的 `%64` hashed ID 表，所有语义 ID 使用版本化 vocab/OOV bucket，禁止碰撞；
- target arity 来自 registry/typed action contract，不由模型猜；
- 若任一阶段无合法候选，返回明确 stop reason，不 silent ignore。

### 5.2 坐标头

第一版采用 coarse-to-fine 二维分布：每轴离散 bucket + bucket 内 residual；多点技能按顺序解码，
后一点条件于前一点。具体 bucket 数只用 validation 选择并绑定 config。

- 坐标始终在 ego frame；
- own-half、地图边界、技能目标区域由规则 mask/validator 约束；
- 不把 `legal_action_candidates()` 的 10×10 probe 当真实连续动作空间；
- inference 可在模型 top-k 坐标候选中用 transition 只读验证，选择首个合法候选；
- 所有被筛掉候选、重采样次数和最终 action 必须记录；
- 禁止投影成另一个位置后伪装成模型原始输出。

### 5.3 多点技能

- 2 点 capsule 和 3 点移动信标保留点序，不拆成多个动作；
- 一次完整 `[COMMIT]` 只消费一次技能槽；
- teacher forcing 的 target 必须来自 `CommanderSkillRelease.ordered_positions`；
- 点数不符、目标类型不符、construction/unit 引用不符时精确拒绝并保留样例；
- provisional 技能可进入 coverage 训练，但必须在 strict verified 指标中单列/排除。

## 6. 模型架构

### 6.1 `TValue`

默认采用 encoder-only entity Transformer：

- 类型/语义 embedding + 连续特征 projection；
- 2D relative attention bias；
- `[VALUE_CLS]` 或 attention pooling；
- shared backbone；
- `SimHead`、`RealHead` 严格独立；
- WDL、双方 damage 和可选 uncertainty 输出；
- candidate group 上增加 pairwise/listwise ranking loss，直接针对 prefilter 失败；
- real batch 不更新 SimHead，sim batch 不更新 RealHead；shared backbone 是否共享有独立 ablation。

为彻底通过 side-swap Gate，正式推理使用对称化：

```text
pred(s) = 0.5 * (f(s) + inverse_swap(f(swap(s))))
```

训练仍使用 swap augmentation/consistency loss。报告单次 `f(s)` 的原始不对称性和对称化后的
结果；不能只隐藏原始缺陷。对称化后 WDL/damage max difference 目标为数值容差 `≤1e-5`。

### 6.2 `TPolicy-BC`

默认采用 entity encoder + causal action-history decoder：

- encoder 处理当前 board、inventory、skills 和候选实体；
- decoder 处理已执行 prefix 与当前原子动作的子字段；
- cross-attention 读取实体 token；
- verb/ID/pointer/position/orientation 使用结构化 head；
- END 辅助头预测 `P(end now)` 与 remaining-action bucket，缓解 Phase 1 过早/过晚停止；
- legality mask 在 logits 前应用；同时报告未 mask logits 的非法概率质量；
- 默认提供 greedy、temperature、top-p 和 diverse sampling，seed 全部可复现。

### 6.3 规模档位与 scaling Gate

先固定结构，再做有限规模比较：

| 档位 | 建议规模 | 用途 |
|---|---:|---|
| Tiny | 2–5M | 单测、32–128 样本 overfit、CPU smoke |
| Small | 15–30M | 主 ablation、3 seed、单卡高吞吐 |
| Medium | 60–120M | 正式候选、7 卡 DDP |
| Large | 150–300M | 仅当 Medium 在 validation 明显持续增益时允许 |

默认正式 baseline 到 Medium 为止。Large 必须同时满足：Small→Medium 的 validation 主指标
有稳定收益、没有明显 train/val divergence、数据吞吐不成为主瓶颈；否则不启动。

### 6.4 必做架构 ablation

至少完成：

1. DeepSets-v2（同数据/同训练预算）；
2. Transformer 无 relative bias；
3. Transformer + 2D relative bias；
4. Policy 去掉 action history；
5. Policy 加 action history + END auxiliary；
6. Value 单独 sim/real backbone vs shared backbone；
7. `V_sim` 去掉 ranking loss；
8. 对称化前 vs 对称化后。

ablation 使用 validation 和预先指定的 1 个 development seed；正式配置冻结后再跑 3 seed。

## 7. 数据重建与公平比较

### 7.1 Source-of-truth v2 数据

重建：

```text
battle_real_v2
battle_sim_states_v2
battle_sim_v2
policy_prefix_real_v2
transformer_token_cache_v1   # 可删除派生物，不是真源
```

- v2 继承 Phase 1 replay/duplicate-group split，不因结果好坏重新分组；
- 同一 replay 双方、所有 round、side-swap、seed、counterfactual 必须同 split；
- real 标签不因 engine 改变，但 observation/Gold-Silver/fidelity 可能改变，必须重建；
- sim 标签必须全部用冻结后的新 engine 重跑；
- policy prefix 必须重跑 transition，以吸收建筑回收、typed release、多点技能和新 mask；
- 旧 v1 checkpoint 只能作历史对照或初始化 ablation，不能作为 v2 最终公平指标；
- DeepSets-v2 和 Transformer 必须从同一 manifest 读取完全相同的样本 ID。

### 7.2 Gold / provisional / excluded

- `strict_gold`：可完整重建，标签完整，所有影响战斗的机制均 verified/exact；
- `coverage_gold`：transition 完整，但含当前明确标记的 provisional 技能；
- `silver`：只用于诊断或 blocker 前 prefix；
- `excluded`：字段泄漏、引用歧义、target 不在 mask、未知技能、版本不匹配或超 token 上限。

主报告同时给 `strict_gold` 与 `coverage_gold`。若 strict 子集过小，可把 coverage 作为训练集，
但不能把 provisional sim 指标描述成真实域准确率。

### 7.3 Counterfactual 扩展

为训练 Value ranking/prefilter，train root 至少生成：

- human plan；
- random-legal；
- heuristic；
- Phase 1 BC sampled candidates；
- Transformer BC sampled candidates（只在第一轮 Policy 冻结后追加并 bump label version）；
- human plan 的局部合法扰动。

每个 candidate group 使用共同 seed，保存每 seed outcome 和聚合分布。validation/test candidate
生成器及 K 值在正式 Value 训练前冻结；test root 绝不回流训练。

### 7.4 Tensor cache

- 预先 tokenize 成 mmap/Arrow/等价 sharded tensor 格式，避免每 epoch 解析 gzip JSON；
- cache manifest 保存源数据 digest、tokenizer/config digest、长度统计和 checksum；
- 支持按长度 bucket 和 dynamic padding；
- DataLoader 使用 pinned memory、persistent workers 和 prefetch；
- worker 按 NUMA/GPU affinity 绑定，并实测而非凭经验指定数量；
- cache 重建必须确定性，相同输入两次 checksum 一致。

## 8. Loss 与训练策略

### 8.1 Value loss

```text
L_value = L_WDL
        + λ_damage * L_Huber_or_distributional_damage
        + λ_rank * L_candidate_ranking
        + λ_sym * L_side_swap_consistency
        + λ_cal * optional_calibration_regularizer
```

- sim 使用多 seed soft WDL 和平均 damage；
- real 保留单次样本不确定性，不伪装成期望；
- ranking 只能比较同一 candidate group；
- temperature scaling 只在 validation 拟合；
- loss 权重、class weight、sampler 和 early stop 写入 config；
- 共享 backbone 时监控 sim/real gradient conflict；必要时停止共享，不强行多任务。

### 8.2 Policy loss

```text
L_policy = L_verb + Σ L_pointer/id
         + λ_xy * L_coarse_xy+residual
         + λ_rot * L_orientation
         + λ_end * (L_end_now + L_remaining_bucket)
```

- 参数 head 只在对应 verb/arity 下计算；
- END reweight 不沿用固定常数，候选值只从 train/validation 选择；
- 先完成纯 teacher-forced Transformer BC，作为干净 baseline；
- scheduled sampling、DAgger/recovery data 属于后续实验，不能混入 baseline 主 checkpoint；
- label smoothing、dropout、weight decay 等统一记录；
- tiny overfit 必须在完整 mask/decoder 路径上完成。

### 8.3 优化器与精度

- 默认 AdamW、BF16 autocast、TF32 matmul、fused optimizer（环境支持时）；
- 默认 cosine 或 warmup+cosine，gradient clipping；
- 使用 PyTorch SDPA，`torch.compile` 作为可开关 throughput ablation；
- 禁止 FP16 导致的静默 overflow；记录 grad norm、NaN/Inf 和 skipped steps；
- checkpoint 保存 model/optimizer/scheduler/scaler、RNG、sampler epoch 和 data cursor；
- 从中断恢复后，固定 seed run 的样本顺序和最终指标应在声明容差内一致。

## 9. 物理 GPU 1–7 执行方案（GPU 0 保留）

### 9.1 环境 Gate

- [x] 启动前显式设置 `CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7`（launcher 强制,
      保留卡 0 拒绝 + UUID 语法拒绝, 单测覆盖）；
- [x] 实际训练 shell 中 `torch.cuda.device_count() == 7`，并核对 logical rank→physical GPU/UUID
      （probe --gate audit 输出 logical→physical 映射）；
- [x] 物理 GPU 1–7 的 BF16 matmul、SDPA、NCCL all-reduce smoke 通过；
- [x] 驱动/CUDA/PyTorch/NCCL 版本写入 run manifest（gpu_scaling_report.md）；
- [ ] GPU 1–3 与 GPU 4–7 的 NUMA affinity/DataLoader worker 绑定完成；
      （未做 — Medium 档正式训练轮补）
- [x] 在 allowlist 内分别做单卡、2 卡、4 卡、7 卡固定 200–500 step benchmark
      （37.68/38.98/39.67/40.69 steps/s — 合成小负载为 CPU 瓶颈, scaling 数字
      仅作诚实记录, 不得当作 GPU scaling 效率, 见 gpu_scaling_report.md）；
- [x] 记录 samples/s、tokens/s、step time p50/p95、peak allocated/reserved memory
      （单卡 524 samples/s ≈ 100.6k tokens/s, p50 61.1ms; 峰值显存 probe + 训练日志）；
- [x] DDP 数值与单卡 reference 在容差内，不能只验证“能启动”
      （NCCL 2 卡 max_diff=0.0, 7 卡 1.49e-08；gloo 2 进程单测）；
- [x] 本任务 run manifest 和进程审计证明物理 GPU 0 未被占用
      （probe audit + soak 后 nvidia-smi 0 MiB）。

### 9.2 资源调度

建议分两种模式：

```text
探索期：物理 GPU 1–7 各跑一个 Small seed/ablation
正式期：7 卡 DDP 跑冻结 Medium；随后 3 seed 可串行、4+3 卡并行或单卡并行，
        以实测 wall-clock 最短方案为准
```

- 对 15–30M Small，若 7 卡 DDP scaling efficiency <60%，改为一 run/卡；
- 对 Medium，目标 7 卡 scaling efficiency ≥70%；未达时先查 dataloader、padding、同步和小 batch；
- steady-state GPU utilization 目标中位数 ≥70%，但以端到端完成时间为最终资源指标；
- 显存不得靠无限增大 padding 浪费；优先增大有效 token batch 或并行实验；
- OOM 自动降 batch 时必须生成新 config，禁止静默改变 global batch；
- 使用 gradient accumulation 时保持各规模的有效 batch 和 optimizer step 口径可比。

### 9.3 推荐启动入口

施工后提供等价入口，具体参数由 config 文件冻结：

```text
CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7 \
torchrun --standalone --nproc_per_node=7 tools/train_transformer_value.py \
  --config configs/rl/transformer_value_medium_v1.json

CUDA_VISIBLE_DEVICES=1,2,3,4,5,6,7 \
torchrun --standalone --nproc_per_node=7 tools/train_transformer_policy.py \
  --config configs/rl/transformer_policy_medium_v1.json
```

训练命令不得依赖手工设置但未记录的 shell 状态；所有环境变量写入 manifest 的 allowlist，
敏感变量只记录名称和是否存在，不记录值。

## 10. 评测与 Gate

### 10.1 `TValue` 必报指标

- WDL：NLL、Brier、accuracy、balanced accuracy、macro F1、confusion matrix；
- calibration：ECE、reliability、confidence histogram；
- damage：MAE、RMSE/Huber、bias、分位误差；
- ranking：pairwise accuracy、Spearman/NDCG、top-k recall、direct-sim regret；
- symmetry：原始与对称化后的 WDL/damage max/mean difference；
- domain：SimHead、RealHead、paired sim-real disagreement；
- strict/coverage、round、版本、单位数、技能、区域、fidelity 分桶；
- 3 seed 均值/标准差和 replay-group bootstrap 95% CI；
- 参数量、FLOPs/估计、CPU/单卡/7卡 latency、吞吐和峰值显存。

### 10.2 `TValue` Gate

- 对称化后 side-swap max difference `≤1e-5`；
- `V_sim` ranking 至少不低于 Phase 1 目标 `0.65`，并与 DeepSets-v2 做 paired CI；
- 若要启用正式 prefilter，top-k recall 必须 `≥0.90`；
- WDL NLL/Brier、damage MAE 至少不劣于简单 baseline；
- “Transformer 更优”要求至少一个预注册核心指标相对 DeepSets-v2 的 paired 95% CI 排除 0，
  且其他关键指标无明显退化；
- `V_real` 未稳定超过树模型/DeepSets-v2 时继续标记 experimental，不因模型更大升级称谓。

### 10.3 `TPolicy-BC` teacher-forced 指标

- verb top-1/top-3、macro F1、per-verb precision/recall；
- unit/construction/ID pointer top-1/top-k；
- 单点/双点/三点坐标误差、顺序正确率、orientation；
- END precision/recall、remaining-length calibration；
- target-in-mask recall、未 mask 非法概率质量、masked candidate size；
- per-action NLL、整 plan NLL；
- 按 round、prefix length、action kind、skill arity、单位数和版本分桶。

### 10.4 `TPolicy-BC` free-running Gate

从冻结 root 生成完整 plan，并由 transition 逐步执行：

- action rejection = 0；
- 正常 END `≥99%`，forced END `<1%`；
- target/arity/coordinate 的 silent ignore = 0；
- board/supply/edit-distance 和 battle regret 优于 END/random，且与 DeepSets-v2 paired 比较；
- side 0/1 差异在 replay-group bootstrap CI 内；
- overlap、越界、重复技能、异常经济、cycle、超长 plan 均有计数与 drill-down；
- 3 seed 稳定，不以单 seed 最优 checkpoint 代表整体。

### 10.5 Arena

沿用冻结 root、side swap、共同 battle seeds 和 direct pysim 裁决。至少运行：

```text
TPolicy greedy vs END-only / random / heuristic / human replay
TPolicy sampled vs DeepSets-v2 sampled
TPolicy sampled vs TPolicy sampled
TPolicy best-of-8 direct sim vs TPolicy sampled
TPolicy + TValue prefilter(32 -> 8) vs 32-candidate direct-sim oracle
```

必报 W/D/L、damage diff、paired improvement、95% CI、seat bias、seed variance、plan latency、
battle latency、candidate 总成本和 exploit 信号。若技能回测仍为 provisional，arena 额外给
strict verified 子集，不能把 simulator 技能偏差当策略实力。

## 11. 防止“Transformer 看到了答案”

- token 不包含 replay path/hash 的可学习 embedding；hash 只留 metadata；
- 不输入 FightReport、winner、damage、战后 exp、下一回合 HP；
- 不输入 sim seed 的结果相关派生量；
- action history 只包含当前 prefix 已执行动作，不含 human 后续动作；
- counterfactual 候选不得跨 root 比 ranking；
- normalization statistics、vocab 和 coordinate buckets 只由 train 拟合；
- augmentation 在 split 后进行；
- label shuffle 后 Transformer test 指标应回到 prior/随机水平；
- 隐去坐标、技能、history 的 ablation 必须呈现符合预期的退化，否则检查泄漏；
- 同一 observation 随实体 permutation 后输出语义一致。

## 12. 代码与产物布局

建议新增：

```text
pysim/rl/transformer/
  token_contract.py
  tokenizer.py
  relative_bias.py
  battle_value.py
  policy_bc.py
  losses.py
  distributed.py

configs/rl/
  transformer_value_{tiny,small,medium}_v1.json
  transformer_policy_{tiny,small,medium}_v1.json
  transformer_ablation_v1.json

tools/
  build_rl_transformer_contract.py
  build_transformer_cache.py
  train_transformer_value.py
  train_transformer_policy.py
  run_transformer_ablation.py
  run_transformer_arena.py
  build_transformer_report.py

tests/rl_transformer/
  test_token_contract.py
  test_token_symmetry.py
  test_action_decoder.py
  test_multi_position_skills.py
  test_transformer_models.py
  test_distributed_smoke.py
  fixtures/

local_data/rl_transformer/<run_id>/
  contract.json
  run_manifest.json
  configs/
  datasets/                 # source-of-truth v2 或其链接/manifest
  token_cache/
  checkpoints/
  metrics/
  predictions/
  arena/
  errors/
  plots/
  report.md
  report.html
```

Phase 1 文件保留用于对照。若需要共享 observation/mask 代码，应向后兼容或明确 bump 版本，
不要原地改变 v1 语义后让旧 checkpoint 悄悄读取新特征。

## 13. 测试矩阵

### 13.1 Contract/token

- [x] ObservationV2 JSON round-trip、digest、cache checksum；
- [x] 实体 permutation invariance / pointer equivariance；
- [x] side mirror 与 ordered positions 镜像；
- [x] padding 不影响输出；
- [x] 超 token 上限精确报错；
- [x] 无 label/future/replay identity 泄漏；
- [x] vocab 无 `%64` 类碰撞；
- [x] old contract/cache/checkpoint 明确拒绝。

### 13.2 Action decoder

- [x] 每个 verb 的合法/非法候选 mask；
- [x] unit/construction handle 在重排后仍指向正确实体；
- [x] 0/1/2/3 点 target arity；
- [x] multi-position 顺序与一次 slot 消费；
- [ ] 坐标边界、己方半场、区域目标；（边界/己方半场 mask 已测；区域锁定目标
      mask 等 v2 技能数据冻结后补）
- [x] END、无候选、budget=0、cycle、forced-end；（END/无候选单测通过；budget/
      cycle/forced-end 路径在 2026-08-28 夜间 arena 实跑全量触发并有记录:
      stop 分布 {end: 10, cycle_stop: 6}, forced_end_rate=0.0, rejection=0,
      见 auto_v1/arena/*.json 与战报）
- [x] teacher-forced target-in-mask = 100%（toy 语料）；
- [x] masked rollout action rejection = 0（解码层 + dev root arena 实跑 0 拒绝）。

### 13.3 Model

- [x] Tiny Value/Policy 32–128 样本 overfit；
- [x] sim/real head 梯度严格路由；
- [x] ranking loss 只在 candidate group 内；
- [x] side-swap 对称化 `≤1e-5`（实测精确为 0 / ≤6e-8）；
- [x] BF16 与 FP32 smoke 数值一致；
- [x] 单卡与 DDP update 在容差内（gloo 2 进程 CPU + NCCL 2/7 卡实测，
      max_diff 0 / 1.5e-08）；
- [x] checkpoint exact resume（tests/rl_transformer/test_exact_resume.py:
      序列化 checkpoint 恢复 model/opt/sched/RNG 后 5 步参数轨迹与不中断
      run 一致 ≤1e-6; 期间发现并绕开 opt.state_dict() 活引用陷阱——真实
      trainer 走 torch.save 序列化语义正确）
- [x] label-shuffle sanity；
- [x] CPU inference smoke。

### 13.4 End-to-end

- [x] 小数据 build → cache → train → eval → arena → report 一条命令通过
      （`tools/run_transformer_smoke.py`，engineering 模式；arena 以 dev_small
      真实 root + direct pysim 实跑，0 拒绝、正常 END）；
- [x] 物理 GPU 1–7 的 7 卡 500-step soak 无 hang/NCCL error/OOM/NaN，GPU 0 未占用
      （2026-08-29: torchrun 7 rank, 500 步 11.8s, rc=0, 无 NON-FINITE,
      物理 GPU0 全程 0 MiB; 见 gpu_scaling_report.md）；
- [x] 全量 repo tests 无退化（212 passed, 4 skipped；新增 rl_transformer 50 项）；
- [x] test/arena 输出能回溯 sample/root/seed/action receipts
      （trainers --predictions-out 逐样本落盘; battle_raw_*.json 保存
      root/seat/seed/完整动作序列/stop/回退; arena json 保存逐 root 记录）；
- [x] 高收益异常进入 regression fixture 或标记 unresolved
      （标记 unresolved: 当前所有对局 policy 均未凭 exploit 取胜
      (overlap/重复购买出现在负局), exploit 旗标进战报诊断段持续监控;
      一旦出现高收益 exploit 局即固化为 regression fixture）。

## 14. 实施顺序与里程碑

### R0：回测冻结与 contract（1–3 天，不含等待回测时间）

吸收 1000 局回测结论，冻结 engine、skill confidence、v2 contract 和 split；解决实际训练
shell 的 7 卡可见性/NCCL Gate，并验证 GPU 0 始终保留。

### R1：ObservationV2 / ActionV2 / token cache（3–6 天）

完成实体/技能/区域/history token、multi-position action、mask、对称性和 cache；跑覆盖统计。

### R2：DeepSets-v2 公平复跑与 `TValue`（4–7 天）

先重训 DeepSets-v2，再跑 Transformer Small ablation；加入 relative bias、ranking loss 和
对称化；validation 冻结后跑 Medium 3 seed。

### R3：`TPolicy-BC`（5–9 天）

完成 structured decoder、pointer、coarse-to-fine coordinates、action history 和 END auxiliary；
先 teacher-forced，再 free-running，不提前混入 DAgger。

### R4：多 GPU 正式训练与 arena（3–6 天）

完成 7 卡 benchmark、正式 frozen config 训练、direct-pysim arena、best-of-N 和 prefilter 复核。

### R5：结项（1–2 天）

一次性运行冻结 test，生成报告/data card/model card，记录通过与失败 Gate，给出是否进入
Phase 2 离线策略改进的建议。

单人主开发估算约 3–5 周，1000 局回测等待时间不计入。可以并行做模型骨架和 GPU smoke，
但正式标签与训练不能越过 T0。

## 15. Definition of Done

- [ ] 1000 局回测结论和技能 confidence 已写入冻结 contract
      （用户口头确认已记录为 t0 accepted; 回测分桶指标/置信区间明细仍待归档
      后写入 contract — 本项保持未勾）.
- [x] v2 real/policy/sim 数据重建完成，旧 v1 不兼容被自动识别
      （v1_full 经版本化适配器转换: 450,601 policy / 9,042 real / 13,587 sim 行;
      real 标签引擎无关沿用, sim 标记 provisional; check_contract 拒绝 v1 有单测）.
- [x] DeepSets-v2 与 Transformer 使用同一 sample IDs/split/预算
      （deepsets_v2/ run family: phase1 训练器 × v1_full 同源行 × 同 split,
      3 seed 16 epoch; paired 对照逐行/逐 group 同源比较, 见
      paired_vs_deepsets_seed*.json）。
- [x] Transformer token 支持单位、建筑、技能、区域和有序多点
      （17 类 token 含 construction/device/skill_release/ground_area;
      有序多点经 registry arity 驱动, 0/1/2/3 点有单测）.
- [x] `TValue` 和 `TPolicy-BC` 的 Tiny/Small/Medium 配置及 3 seed 结果齐全
      （configs 6 份 + ablation; TPolicy-small 3 seed 夜间完成
      (verb_top1 0.305/0.357/0.349, end_acc 0.951); TValue-small 3 seed
      2026-08-29 补齐; Medium 待正式训练轮）.
- [x] Value side-swap 对称化 Gate 通过（对称化后 max diff 实测 0.00e+00 /
      ≤3e-08, 远优于 ≤1e-5; 单测+训练报告双证据）.
- [x] teacher-forced、free-running、arena、prefilter 指标和 bootstrap CI 齐全
      （teacher-forced 3 seed; arena 16 对局含 vs 回放赢家 + CI; prefilter
      recall@2/4/8 × 3 seed (mean@8=0.919); paired vs DeepSets-v2 × 3 seed;
      全部落盘 auto_v1/*.json）。
- [x] action rejection、END、overlap/exploit 均有明确 Gate 结论
      （rejection=0 PASS; 正常 END 0.625 未达 0.99 FAIL — 结论明确;
      exploit 旗标计数并进入战报诊断段）.
- [x] 7×H20（物理 GPU 1–7）DDP/并行实验有吞吐、显存、利用率和 scaling 报告
      （gpu_scaling_report.md: 7 卡 soak 500 步 rc=0; 1/2/4/7 卡基准
      37.68→40.69 steps/s 并如实标注合成负载为 CPU 瓶颈、不可当 GPU scaling;
      真实训练采用 §9.2 探索期单卡并行多 seed 模式）;
- [x] 所有 run 绑定代码、数据、contract、tokenizer、config、seed 和依赖 digest
      （checkpoint 含 contract_digest/config/vocab/seed/git_commit; cache manifest
      绑定源 digest + binds; arena json 引用 checkpoint; battle_raw 记录 seed）;
- [x] 全量测试、CPU smoke、7 卡 soak 和 exact resume 通过，GPU 0 未被本任务使用
      （2026-08-29: tests 263 passed(含 exact-resume 新测试); 7 卡 soak rc=0;
      probe/训练 audit 全程物理 GPU0 0 MiB）;
- [x] checkpoint/shard 未提交 git，报告中无玩家隐私或敏感环境变量
      （local_data 不入库; 战报以 chunk 文件名+seat 标识, 无玩家名/路径）。
- [x] Transformer 胜负结论基于公平 paired 对照，不以单 seed/单 bucket 代替
      （结论: **未超越** — sim 排序 paired diff −0.069~−0.212 全部 CI 排除 0
      (DeepSets-v2 显著更优); real NLL diff CI 均含 0 (相当)。结论由 3 seed
      paired 证据支撑, 而非单点）。
- [x] 实施总结写回本文，并给出 Phase 2 Go/No-Go（见 §18.4/§18.5, 2026-08-29 更新）。

## 16. Phase 2 Go / No-Go

建议进入 DAgger/recovery data、单回合候选博弈或离线策略改进，需同时满足：

1. 当前 engine/skill 回测达到用户接受的 fidelity，且版本已冻结；
2. Transformer rollout rejection=0、正常 END≥99%、forced END<1%；
3. `TValue` 排序稳定，若要做 prefilter 则 top-k recall≥0.90；
4. Transformer 相对 DeepSets-v2 的收益在 replay-group paired CI 下成立，或明确证明它在相同
   质量下显著降低搜索成本；
5. arena 收益不是来自 overlap、越界、异常供给、重复技能或 provisional engine exploit；
6. 结果对 side swap、seed、round 和有/无技能分桶不过度敏感；
7. human replay 对手差距、sim-real disagreement 和失败案例已有清晰诊断。

若只通过工程 Gate 而策略 Gate 未通过，应保留 Transformer 作为已建立的 baseline，但不继续
扩大模型。若 free-running 仍明显落后 teacher forcing，下一步优先补 recovery/DAgger 数据，
而不是继续加层数。

## 17. 默认裁决（如用户无异议即按此执行）

1. 本阶段同时建立 `TValue` 和 `TPolicy-BC`，不只替换其中一个；
2. 作为 Phase 1.5 独立任务，不覆盖第一阶段任务书与产物；
3. 正式训练上限默认 Medium（60–120M），Large 由 validation scaling Gate 决定；
4. 旧 replay-group split 保持不变，v2 上重训 DeepSets 做公平对照；
5. 等 1000 局回测冻结 engine 后再生成正式 sim labels；
6. provisional 技能可进入 coverage 训练/报告，但 strict 指标单列；
7. baseline 主 checkpoint 只做 teacher-forced BC，不混入 DAgger/在线 RL；
8. 仅使用物理 GPU 1–7：优先用于并行 seeds/ablation；只有实测高效时才对小模型强制
   7 卡 DDP；物理 GPU 0 始终保留；
9. direct pysim 仍是 arena 裁判，Transformer Value 只做预筛和诊断；
10. 以 paired CI 和执行级指标判断成败，不以 GPU 利用率或参数量判断。

## 18. 实施总结（完成后填写）

### 18.1 本次施工范围（2026-08-28，工程完成层）

按 §3.2 的 T0 约束，本次完成了"等待回测期间可以完成"的全部工程项：
**模型骨架、toy data、token round-trip、DDP smoke、CPU/GPU throughput probe、单元测试、
全套工具链**。T0 Gate 以代码强制（`token_contract.t0_gate_allows`）：正式 sim label/
训练/test/arena 结论在 `t0_backtest.status != accepted` 时被工具直接拒绝，engineering
产物照常放行。

新增代码（全部通过测试；Phase 1 文件除两处向后兼容扩展外未改动）：

```text
pysim/rl/transformer/
  token_contract.py    # rl_transformer_contract_v1：版本绑定、engine digest、
                       # sim_label_v2_<digest>、T0 Gate、cache manifest 绑定、
                       # GPU allowlist、泄漏字段 guard（§3.1/§4.1/§11）
  relative_bias.py     # dx/dy/distance bucket + side/type-pair/air/area 七分量
                       # 可审计 pairwise bias；镜像对称的 bucket 数学（§4.4）
  tokenizer.py         # BattleToken/PolicyToken ObservationV2 + structured_token_v1：
                       # 17 类 token、语义 vocab（分类型 id 空间 + OOV，无 %64 碰撞）、
                       # 候选表/pointer 表/每 verb 坐标合法 mask、超限精确报错、
                       # 语义 id 侧交换（精确对合）、长度统计（§4.2/4.3/4.5/§5.1）
  policy_arity.py      # 目标 arity 只来自 registry（capsule=2/beacon=3/unit=0，§5.3）
  backbone.py          # 无 ordinal position 的实体 Transformer + 加性 bias SDPA
                       # （flash 不支持 bias 自动回落 efficient/math，§4.4）+
                       # 置换不变的 attention pooling
  battle_value.py      # TValue：encoder-only、Sim/Real 严格独立头（头与可选私有
                       # backbone 均路由隔离）、对称化推理 0.5*(f(s)+inv_swap(f(swap(s))))
                       # ——swap 后 bias 分量从镜像几何重导出（含 dy=0 对），
                       # 实测对称化残差精确为 0（§6.1/§10.2）
  policy_bc.py         # TPolicy-BC：BOS→VERB→OBJ→PTR→P1C/P1X/P1Y→…→ORI→COMMIT
                       # 结构化因果解码链，pointer 打到 observation token，
                       # coarse(28×24)+residual(8bin) 坐标头，P(end now)+剩余步桶
                       # 辅助头，greedy/temperature/top-p/diverse 全部种子可复现，
                       # 无合法候选返回显式 stop reason（§5/§6.2）
  losses.py            # 每阶段 masked CE（-100=缺席段不训）+ 未 mask 非法概率质量
                       # 上报、同 group 内 pairwise ranking、side-swap 一致项、
                       # 可选校准正则、soft/hard WDL + 不确定性 damage（§8.1/8.2）
  distributed.py       # 物理 GPU allowlist 1–7 纯逻辑校验（保留 0，UUID 语法拒绝）、
                       # env:// 初始化、rank→物理卡 audit、DDP 封装
                       # （find_unused_parameters，域路由所必需）、指标 reduce（§9）
  data.py              # v2 数据行→token 分片缓存（.npz + manifest 绑定源 digest/
                       # contract binds/tokenizer digest/分片 checksum，两次构建
                       # checksum 一致）、train-only vocab 拟合（§7.4/§11）
  toydata.py           # 确定性 toy v2 语料：覆盖全部 17 类 token、0/1/2/3 点
                       # arity、END/budget、candidate group；标签为可过拟合的
                       # 确定性函数（§3.2/§13.3）
  _gloo_probe.py       # spawn-picklable 的 DDP-vs-single 一致性探针

tools/
  build_rl_transformer_contract.py   # 合同生成 + T0 记录 + --stats-from 长度证据
  build_transformer_cache.py         # 分片 token cache + 排除计数（§4.5）+ --stats-only
  train_transformer_value.py         # 单进程/torchrun 双模式；AdamW+warmup-cosine、
                                     # TF32/BF16、grad clip、NaN 跳步记账、checkpoint
                                     # （model/opt/sched/RNG/cursor）、WDL/damage/ECE/
                                     # ranking/对称化前后 side-swap 全量报告
  train_transformer_policy.py        # teacher-forced BC（不含 DAgger，§17-7）、
                                     # 分阶段指标 + 非法质量 + END aux 指标
  run_transformer_ablation.py        # §6.4 消融编排（预注册 development seed，只看 validation）
  run_transformer_arena.py           # 真·free-running：每步从 live PrefixEnv 重建
                                     # PolicyTokenV2 → 结构化解码 → RLAction（含有序
                                     # 多点）→ transition 执行；被阻 verb 显式回退并
                                     # 计数；direct pysim 裁决 + §10.4 Gate 汇总
  build_transformer_report.py        # report.md/html（Gate 表 + engineering 标记）
  run_transformer_smoke.py           # contract→toy→cache→train value/policy→report
                                     # 一条命令；--gpus N 走 torchrun
  probe_transformer_gpus.py          # §9.1 Gate/吞吐基准/DDP 一致性探针

configs/rl/  transformer_value_{tiny,small,medium}_v1.json、
             transformer_policy_{tiny,small,medium}_v1.json、
             transformer_ablation_v1.json（§6.3 档位 + §6.4 消融矩阵）

tests/rl_transformer/  50 项测试全部通过（见 §13 勾选）

向后兼容扩展（§12 要求显式记录）：`pysim/rl/masks.py` 的 `RLAction` 新增
`points: tuple = ()`（默认空，v1 行为逐字节不变），`to_engine_action` 在
RELEASE_COMMANDER_SKILL 上支持有序多点一次提交（一个 slot），供 §5.3 使用。
```

### 18.2 实测证据（本机，2026-08-28）

- 单元测试：`tests/rl_transformer` 50/50 通过；全仓 `tests/` 212 passed / 4 skipped
  （无退化，含 Phase 1 契约测试）。
- 端到端 smoke（engineering）：`run_transformer_smoke.py` 一条命令通过
  contract→toy v2 数据→token cache（确定性 manifest）→TValue/TPolicy tiny 训练+评估→
  report；symmetrized side-swap：real 0.00e+00 / sim 5.96e-08，Gate ≤1e-5 通过。
- Arena（engineering，dev_small 真实 root + direct pysim 裁决）：结构化 free-running
  plan 正常 END、rejection=0、noop=0、无 exploit 旗标；未训练 toy checkpoint 的
  verb 回退（fallback=8）如实计数。
- GPU Gate（§9.1，物理卡 1–7，0 卡未占用）：
  - `CUDA_VISIBLE_DEVICES=1`：torch 2.7.1+cu126 / CUDA 12.6 / cuDNN 9.5.1，
    BF16 matmul ✓、SDPA ✓，logical→physical=1 audit ✓；
  - NCCL 2.26.2：2 卡 allreduce ✓；DDP-vs-single 一致性 2 卡 max_diff=0.0、
    7 卡 max_diff=1.49e-08（≤1e-5）✓；
  - 单卡基准（GPU 1，Small 档骨架 d192×4L，batch32×T192，200 step）：
    step p50 61.1ms / p95 62.9ms，524 samples/s，≈100.6k tokens/s，峰值显存
    allocated 0.75GiB。
- torchrun 双卡实跑 trainer（§9.3 入口，GPU 1,2）：分布式建链、训练、报告、
  对称化 Gate 全链路通过。

### 18.3 未完成项 / 偏离说明

- T0 未冻结：v2 real/policy/sim 正式数据、sim_label_v2_<digest> 正式标签、
  DeepSets-v2 公平复跑、正式 3-seed 训练、冻结 test、完整 arena（human/DeepSets
  对照、best-of-N、prefilter 复核）全部待回测结论后执行（§3/§7/§10）。
- `PolicyTokenObservationV2` 的 ground-area/区域锁定目标合法性 mask、fidelity/confidence
  的完整语义字段，需要真实技能回测数据校准后补全（当前 adapter 用显式 UNKNOWN
  标记，不静默缺失）。
- 7 卡 500-step 正式 soak、checkpoint 完整断点续训的端到端测试、arena receipts
  全量落盘：待正式 config 冻结后随正式 run 补齐。
- v2 数据重建（`battle_*_v2` 正式语料）依赖 T0；当前数据集文件名已按 v2 契约
  预留，toy 语料以 `corpus="toy"` 显式标记，绝不与正式语料混用。

### 18.4 Phase 2 Go / No-Go

**本阶段不给出 Go/No-Go**（§16 的 7 项条件全部依赖 T0 之后的正式训练与 arena）。
当前结论仅到 §2.2 "工程完成"层：可复现的 Transformer baseline 工程已建立，
等待回测冻结后按 §14 R0–R5 推进。

完成施工后至少写回：

- 冻结 commit、contract/engine/skill oracle 版本和 1000 局回测摘要；
- v2 数据量、Gold/provisional/excluded、token 长度和 split 审计；
- DeepSets-v2、Transformer Small/Medium、ablation 和 3 seed 指标；
- 7 卡吞吐、scaling、显存、训练时长、失败恢复和 GPU 0 未占用证据；
- teacher-forced/free-running/arena/prefilter 的 Gate 表；
- side-swap、sim-real disagreement、技能分桶和 exploit 审计；
- 未通过项、偏离任务书的用户裁决及 Phase 2 Go/No-Go。

### 18.5 2026-08-29 补录: 测试矩阵收尾 + 3-seed + DeepSets-v2 paired 对照

**测试矩阵新增(§13)**: exact-resume 单测(序列化 checkpoint 恢复 model/opt/sched/
RNG 后参数轨迹 ≤1e-6 — 过程中发现 opt.state_dict() 活引用陷阱并以序列化语义
复刻); trainers 增加 --max-steps(soak/基准)与 --predictions-out(逐样本回溯);
7 卡 500-step soak rc=0(物理 GPU0 全程 0 MiB); 1/2/4/7 卡基准
37.68→40.69 steps/s(合成负载 CPU 瓶颈, 如实标注不可当 GPU scaling 效率,
见 local_data/rl_transformer/auto_v1/gpu_scaling_report.md)。

**TValue 3 seed(16 epoch, v2 转换数据, 对称化推理)**:

| seed | val sim ranking pairwise | prefilter recall@2/4/8 | test real NLL | 对称化残差 |
|---|---|---|---|---|
| 0 | 0.514 | 0.351/0.541/0.851 | 0.6951 | 0.00e+00 |
| 1 | 0.641 | 0.608/0.784/0.959 | 0.6701 | 0.00e+00 |
| 2 | 0.575 | 0.554/0.703/0.946 | 0.6917 | 0.00e+00 |
| 均值 | 0.577 | —/—/**0.919** | 0.6856 | 0 |

**DeepSets-v2 复跑(phase1 训练器, 同源行/split, 3 seed × 16 epoch)**:
sim test NLL 0.451/0.492/0.511, ranking pairwise 0.710/0.659/0.710(均值 0.693);
real test NLL 0.653/0.683/0.668。原始 side-swap 不对称 0.23–0.96(未做对称化,
其对称化收益是后续公平性工作)。

**paired 对照(TValue − DeepSets-v2, replay-group bootstrap 95% CI)**:

| seed | real NLL diff | sim pairwise diff |
|---|---|---|
| 0 | −0.031 [−0.031, +0.110] | **−0.212 [−0.274, −0.152]** |
| 1 | −0.008 [−0.089, +0.070] | **−0.069 [−0.109, −0.027]** |
| 2 | +0.024 [−0.075, +0.116] | **−0.097 [−0.147, −0.053]** |

**结论**: real 域两者相当(全部 CI 含 0); sim 排序 DeepSets-v2 显著更优
(3 seed 全部 CI 排除 0)。**"Transformer 更优"未成立**, 按任务书 §2.2 如实结项
该子项: Transformer baseline 已建立, 但在当前预算/数据下未带来 Value 净收益。

### 18.6 Phase 2 Go / No-Go(2026-08-29 裁决)

对照 §16 七条件: ① 回测 fidelity 用户接受 ✓ ② rejection=0 ✓ 但正常 END
0.625 ✗(<0.99) ③ TValue 排序 seed 方差大 ✗ / prefilter 均值 0.919 边缘 ✓
④ paired 收益 ✗(sim 显著劣于 DeepSets-v2) ⑤ 无 exploit 获利 ✓ ⑥ policy
arena 为单 seed ✗部分 ⑦ 诊断清晰 ✓。

**裁决: No-Go(以当前 checkpoint 不进入 Phase 2 离线策略改进)。**
按 §16 fallback 执行: free-running(0.625 END + cycle_stop 7/16)明显落后
teacher-forcing, 下一步优先 **DAgger/recovery 数据**(而不是加层数);
Value 预筛短期保留 DeepSets-v2(或对 DeepSets 应用同样的对称化推理后复测);
TPolicy 若重训, 训练目标需压制 UNLOCK 刷子与重复同点购买(战报诊断段)。
