# pysim 尚未实现的战斗机制清单

> 生成方式:`tools/build_fidelity_report.py` 读取
> `local_data/battlefield_registry.json`(mechanism_registry_v1)
与回放语料的机制出现频次自动渲染。任何机制实现后重跑该工具更新。
支持度四轴:`transition_complete`(转移层)/ `battle_fidelity`
(exact|approximate|unsupported)/ `confidence` / `effect_complete`。

## 指挥官技能(13 项)

已 exact 实现:`100002 燃烧弹`, `300001 导弹打击`, `300003 轨道轰炸`, `300004 核弹`, `300007 轨道标枪`, `800001 800001`, `1000001 再部署`, `1100001 强化训练`, `1200001 空降兵召唤`, `1200002 犀牛召唤`, `1200003 爬虫召唤`, `1200004 战舰召唤`, `1200005 火神召唤`

## 装备(25 项)

**未实现战斗效果(battle_fidelity=unsupported)**:

| id | 名称 | 转移层 | 战斗保真 | 置信度 | 备注 |
|---|---|---|---|---|---|
| 1305003 | 1305003 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 1305005 | 1305005 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 1305009 | 1305009 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 1306001 | 1306001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate); 生产线 periodic summons unmodeled (E4) |
| 1306002 | 1306002 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate); 生产线 periodic summons unmodeled (E4) |
| 1306003 | 1306003 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate); 生产线 periodic summons unmodeled (E4) |
| 1306004 | 1306004 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate); 深渊信标 unmodeled (E4) |
| 1307001 | 1307001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 1307002 | 1307002 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 1308001 | 1308001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 1309001 | 1309001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 13010001 | 13010001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 13020001 | 13020001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 13030009 | 13030009 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 13030010 | 13030010 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate); 统御核心 round income +50 / death-wipe unmodeled (E5) |
| 13040001 | 13040001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate); 部署模块 per-round redeploy rights unmodeled (E5) |
| 13100001 | 13100001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |
| 13120001 | 13120001 | 是 | 缺失 | unsupported | step3:transition chain (charge/stock/bind/persist); battle effect not implemented (battle_approximate) |

已 exact 实现:`13030001 13030001`, `13030002 13030002`, `13030003 13030003`, `13030004 13030004`, `13030005 13030005`, `13030006 13030006`, `13030007 13030007`

## 装置(2 项)

已 exact 实现:`10001 飞弹炮塔`, `20001 护盾装置`

## 能量塔技能(5 项)

已 exact 实现:`1 快速补给`, `3 批量征召`, `4 精英征召`, `5 强化瞄准`, `6 高速移动`

## 蓝图(7 项)

已 exact 实现:`1 黏油弹研究`, `2 战地回收研究`, `3 移动信标研究`, `4 攻击专家I`, `5 防御专家I`, `401 攻击专家II`, `501 防御专家II`

## 专家(5 项)

已 exact 实现:`10004 额外部署位`, `10007 军医`, `10008 教练`, `10009 军械官`, `20003 润滑专家`

## RL Phase 1 的处理口径(2026-08-28 用户裁决)

- 未实现的指挥官技能/装备按 **执行了但没有效果** 处理:
  teacher forcing 与 arena 中 receipt 记 accepted + fidelity flag
  (NOOP_REASON_CODES),回放不中断,数据覆盖最大化;
- approximate 机制照常执行,样本打 fidelity 标记,Silver 分层单独报表;
- gold 主指标排除 unsupported 机制为主的样本(fidelity 分桶可见)。

## 已知的行为级残差(语料审计)

- 建筑回收(ReleaseCommanderSkill + ConstructionIndex):真实游戏有
  退款,pysim 无效果。快照锚定的推导收入会吸收该退款,计划继续;
- techMap 字段为回合后语义:回合内科技购买被跳过(已由快照包含),
  费用由推导收入吸收(896/896 次验证);
- 约 2% 的玩家-回合存在未解释移动(step4 审计),对应 walk 在
  first-failure 截断,进入 Silver/诊断。

## 语料出现频次(new corpus 1106 局)

**指挥官技能**(槽出现次数, id→槽): `战地回收`×12710(slot0), `导弹打击`×2299(slot1), `强化训练`×2239(slot1), `移动信标`×1676(slot1), `黏油弹`×1367(slot2), `800001`×1355(slot1), `爬虫召唤`×1174(slot1), `犀牛召唤`×1013(slot1), `再部署`×884(slot1), `电磁冲击 EMP`×756(slot1), `燃烧弹`×662(slot1), `空降兵召唤`×649(slot2)

**装备(场上绑定)**: `13030009`×2131, `13030001`×1230, `13030003`×866, `13030002`×730, `13030005`×608, `1305003`×370, `13010001`×284, `1308001`×278, `13040001`×264, `1309001`×253, `13020001`×219, `1307001`×213, `13030007`×169, `13030006`×157, `1306003`×135

**装置**: `护盾装置`×5062, `飞弹炮塔`×4907, `未知装置`×309

**蓝图研究**: `(未观测)`×1782, `精英征召`×1770, `强化瞄准`×1745, `批量征召`×690, `快速补给`×660, `401`×624, `501`×620, `1002`×1
