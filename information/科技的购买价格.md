# 兵种默认成本与科技基础价确认表

> 静态快照日期：2026-08-10。单位默认解锁/一级购买费优先来自人工确认表；科技基础价来自中文 Wiki 快照。
> 局内科技结算价 = 下表基础价 + 200 × 同兵种此前已生效科技数。这个 +200 不是科技基础价推导规则。

## 逐兵种总表

| ID | 兵种 | 默认解锁 | 一级购买 | 每级升级 | 科技数 | 来源 | 置信度 | 回放部署/研究 |
|---:|---|---:|---:|---:|---:|---|---|---:|
| 1 | 堡垒 (fortress) | 200 | 400 | 200 | 10 | human_verify_unlock_fee+wiki | high | 4562/451 |
| 2 | 长弓 (marksman) | 0 | 100 | 50 | 8 | human_verify_unlock_fee+wiki | high | 32249/697 |
| 3 | 火神 (vulcan) | 200 | 400 | 200 | 7 | human_verify_unlock_fee+wiki | high | 6164/482 |
| 4 | 熔点 (melting point) | 200 | 400 | 200 | 6 | human_verify_unlock_fee+wiki | high | 2528/412 |
| 5 | 犀牛 (rhino) | 50 | 200 | 100 | 9 | human_verify_unlock_fee+wiki | high | 4570/342 |
| 6 | 兵蜂 (wasp) | 50 | 200 | 100 | 10 | human_verify_unlock_fee+wiki | high | 8344/502 |
| 7 | 野马 (mustang) | 0 | 200 | 100 | 6 | human_verify_unlock_fee+wiki | high | 19976/1052 |
| 8 | 钢球 (steel ball) | 0 | 200 | 100 | 7 | human_verify_unlock_fee+wiki | high | 10210/381 |
| 9 | 尖牙 (fang) | 0 | 100 | 50 | 6 | human_verify_unlock_fee+wiki | high | 53153/266 |
| 10 | 爬虫 (crawler) | 0 | 100 | 50 | 6 | human_verify_unlock_fee+wiki | high | 150204/496 |
| 11 | 霸主 (overlord) | 200 | 500 | 250 | 9 | human_verify_unlock_fee+wiki | high | 1212/178 |
| 12 | 暴雨 (stormcaller) | 50 | 200 | 100 | 6 | human_verify_unlock_fee+wiki | high | 5708/203 |
| 13 | 铁锤 (sledgehammer) | 0 | 200 | 100 | 7 | human_verify_unlock_fee+wiki | high | 17334/180 |
| 14 | 骇客 (hacker) | 50 | 200 | 100 | 5 | human_verify_unlock_fee+wiki | high | 802/113 |
| 15 | 弧光 (arclight) | 0 | 100 | 50 | 7 | human_verify_unlock_fee+wiki | high | 33802/811 |
| 16 | 凤凰 (phoenix) | 50 | 200 | 100 | 8 | human_verify_unlock_fee+wiki | high | 14512/537 |
| 17 | 战争工厂 (war factory) | 350 | 800 | 400 | 9 | human_verify_unlock_fee+wiki | high | 1462/208 |
| 18 | 恶灵 (wraith) | 50 | 300 | 150 | 7 | human_verify_unlock_fee+wiki | high | 5204/428 |
| 19 | 狂蝎 (scorpion) | 50 | 300 | 150 | 7 | human_verify_unlock_fee+wiki | high | 7384/498 |
| 20 | 火獾 (fire badger) | 0 | 200 | 100 | 7 | human_verify_unlock_fee+wiki | high | 21072/654 |
| 21 | 剑齿虎 (sabertooth) | 0 | 200 | 100 | 6 | human_verify_unlock_fee+wiki | high | 13331/373 |
| 22 | 台风 (typhoon) | 50 | 300 | 150 | 7 | human_verify_unlock_fee+wiki | high | 4147/249 |
| 23 | 沙虫 (sandworm) | 200 | 400 | 200 | 8 | human_verify_unlock_fee+wiki | high | 1640/166 |
| 24 | 狼蛛 (tarantula) | 0 | 200 | 100 | 8 | human_verify_unlock_fee+wiki | high | 21180/633 |
| 25 | 鬼鳐 (phantom ray) | 50 | 200 | 100 | 8 | human_verify_unlock_fee+wiki | high | 13279/609 |
| 26 | 先知 (farseer) | 50 | 300 | 150 | 7 | human_verify_unlock_fee+wiki | high | 2994/287 |
| 27 | 雷霆 (raiden) | 200 | 400 | 200 | 6 | human_verify_unlock_fee+wiki | high | 2612/238 |
| 28 | 猎犬 (hound) | 0 | 100 | 50 | 6 | human_verify_unlock_fee+wiki | high | 32388/560 |
| 29 | 深渊 (abyss) | 350 | 800 | 400 | 7 | human_verify_unlock_fee+wiki | high | 486/132 |
| 30 | 魔眼 (void eye) | 0 | 100 | 50 | 7 | human_verify_unlock_fee+wiki | high | 21026/472 |
| 31 | 磁暴 (vortex) | 0 | 100 | 50 | 8 | human_verify_unlock_fee+wiki | high | 15985/309 |
| 2001 | 丧钟 (death knell) | 350 | 800 | 400 | 6 | wiki_2026-08-10 | high | 0/0 |
| 2002 | 泰山 (mountain) | 350 | 800 | 400 | 8 | wiki_2026-08-10 | high | 766/112 |
| 4001 | 实验丧钟/特殊单位 (experimental death knell (special)) | 未定 | 未定 | 未定 | 0 | replay_only | unknown | 64/0 |

## 科技明细

### 1：堡垒 (fortress)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 701 | 双发 (Doubleshot) | 100 | 18 | 615 | wiki_2026-08-10 | high |
| 1001 | 保护屏障 (Barrier) | 500 | 123 | 2273 | wiki_2026-08-10 | high |
| 1105 | 防空弹幕 (Anti air barrage) | 200 | 119 | 2167 | wiki_2026-08-10 | high |
| 1201 | 尖牙制造 (Fang production) | 300 | 4 | 161 | wiki_2026-08-10 | high |
| 3001 | 装甲强化 (Armor enhancement) | 150 | 1 | 50 | wiki_2026-08-10 | high |
| 10201 | 射程强化 (Range enhancement) | 300 | 92 | 2114 | wiki_2026-08-10 | high |
| 10301 | 发射器过载 (Launcher overload) | 150 | 44 | 1265 | wiki_2026-08-10 | high |
| 10401 | 实心弹 (Solid shot) | 200 | 21 | 338 | wiki_2026-08-10 | high |
| 10801 | 精英射手 (Elite marksman) | 150 | 25 | 481 | wiki_2026-08-10 | high |
| 110201 | 火箭拳 (Rocket punch) | 300 | 4 | 72 | wiki_2026-08-10 | high |

### 2：长弓 (marksman)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 702 | 双发 (Doubleshot) | 250 | 106 | 1969 | wiki_2026-08-10 | high |
| 1202 | 射击小队 (Shooting squad) | 300 | 2 | 57 | wiki_2026-08-10 | high |
| 1802 | 电磁弹 (Electromagnetic shot) | 250 | 97 | 1948 | wiki_2026-08-10 | high |
| 3202 | 防空专精 (Aerial Specialisation) | 250 | 233 | 2220 | wiki_2026-08-10 | high |
| 10102 | 突击模式 (Assault mode) | 150 | 15 | 278 | wiki_2026-08-10 | high |
| 10202 | 射程强化 (Range enhancement) | 300 | 221 | 2335 | wiki_2026-08-10 | high |
| 10402 | 快速弹夹 (Quick reload) | 150 | 3 | 95 | wiki_2026-08-10 | high |
| 10802 | 精英射手 (Elite marksman) | 400 | 20 | 598 | wiki_2026-08-10 | high |

### 3：火神 (vulcan)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 1103 | 燃烧弹 (Incendiary bomb) | 300 | 15 | 507 | wiki_2026-08-10 | high |
| 1203 | 最佳搭档 (Best partner) | 300 | 91 | 1845 | wiki_2026-08-10 | high |
| 3003 | 装甲强化 (Armor enhancement) | 150 | 12 | 304 | wiki_2026-08-10 | high |
| 10203 | 射程强化 (Range enhancement) | 300 | 229 | 2376 | wiki_2026-08-10 | high |
| 10603 | 高温火焰 (Scorching fire) | 300 | 69 | 2188 | wiki_2026-08-10 | high |
| 11010 | 黏油弹 (Sticky oil bomb) | 200 | 28 | 688 | wiki_2026-08-10 | high |
| 180203 | 引燃 (Ignite) | 250 | 38 | 1625 | wiki_2026-08-10 | high |

### 4：熔点 (melting point)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 304 | 能量汲取 (Energy absorption) | 200 | 39 | 1769 | wiki_2026-08-10 | high |
| 1106 | 电磁弹幕 (Electromagnetic barrage) | 300 | 77 | 2332 | wiki_2026-08-10 | high |
| 1107 | 能量散射 (Energy diffraction) | 150 | 53 | 2249 | wiki_2026-08-10 | high |
| 1204 | 爬虫制造 (Crawler Production) | 300 | 7 | 769 | wiki_2026-08-10 | high |
| 3004 | 装甲强化 (Armor enhancement) | 100 | 0 | 33 | wiki_2026-08-10 | high |
| 10204 | 射程强化 (Range enhancement) | 300 | 236 | 2384 | wiki_2026-08-10 | high |

### 5：犀牛 (rhino)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 905 | 战地维修 (Field maintenance) | 200 | 3 | 251 | wiki_2026-08-10 | high |
| 1109 | 旋风斩 (Whirlwind) | 150 | 97 | 2138 | wiki_2026-08-10 | high |
| 2305 | 残骸利用 (Wreckage recycling) | 100 | 31 | 806 | wiki_2026-08-10 | high |
| 2505 | 动力装甲 (Power armor) | 300 | 4 | 291 | wiki_2026-08-10 | high |
| 2805 | 最后一击 (Final blitz) | 250 | 31 | 1321 | wiki_2026-08-10 | high |
| 3005 | 装甲强化 (Armor enhancement) | 200 | 112 | 2073 | wiki_2026-08-10 | high |
| 10505 | 机械狂暴 (Mechanical rage) | 100 | 27 | 813 | wiki_2026-08-10 | high |
| 180305 | 光子涂层 (Photon coating) | 300 | 19 | 1066 | wiki_2026-08-10 | high |
| 180805 | 战斗进化 (Combat Evolvement) | 150 | 18 | 727 | wiki_2026-08-10 | high |

### 6：兵蜂 (wasp)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 206 | 能量护盾 (Energy shield) | 300 | 97 | 2152 | wiki_2026-08-10 | high |
| 406 | 高爆弹药 (High explosive ammo) | 100 | 6 | 107 | wiki_2026-08-10 | high |
| 506 | 对地专精 (Ground specialization) | 200 | 30 | 594 | wiki_2026-08-10 | high |
| 1606 | 高速引擎 (Jump drive) | 100 | 10 | 168 | wiki_2026-08-10 | high |
| 1806 | 电磁弹 (Electromagnetic shot) | 100 | 50 | 1582 | wiki_2026-08-10 | high |
| 3206 | 防空专精 (Aerial specialization) | 200 | 130 | 2252 | wiki_2026-08-10 | high |
| 10206 | 射程强化 (Range enhancement) | 300 | 165 | 2353 | wiki_2026-08-10 | high |
| 10606 | 穿甲弹 (Armor piercing bullets) | 100 | 1 | 19 | wiki_2026-08-10 | high |
| 10806 | 精英射手 (Elite marksman) | 400 | 13 | 274 | wiki_2026-08-10 | high |
| 180206 | 引燃 (Ignite) | 100 | 0 | 15 | wiki_2026-08-10 | high |

### 7：野马 (mustang)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 407 | 高爆弹药 (High explosive ammo) | 50 | 100 | 1290 | wiki_2026-08-10 | high |
| 3207 | 防空专精 (Aerial specialization) | 300 | 111 | 2184 | wiki_2026-08-10 | high |
| 3307 | 导弹拦截 (Missile interceptor) | 200 | 52 | 1290 | wiki_2026-08-10 | high |
| 4607 | 斩杀弹 (Execution Rounds) | 200 | 281 | 2032 | wiki_2026-08-10 | high |
| 10207 | 射程强化 (Range enhancement) | 300 | 495 | 2379 | wiki_2026-08-10 | high |
| 10607 | 穿甲弹 (Armor piercing bullets) | 300 | 13 | 348 | wiki_2026-08-10 | high |

### 8：钢球 (steel ball)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 308 | 能量汲取 (Energy absorption) | 200 | 10 | 698 | wiki_2026-08-10 | high |
| 608 | 伤害分摊 (Damage sharing) | 250 | 7 | 202 | wiki_2026-08-10 | high |
| 1308 | 机械分裂 (Mechanical division) | 300 | 80 | 2048 | wiki_2026-08-10 | high |
| 2408 | 重装锁定 (Fortified target lock) | 200 | 22 | 1493 | wiki_2026-08-10 | high |
| 3008 | 装甲强化 (Armor enhancement) | 300 | 9 | 831 | wiki_2026-08-10 | high |
| 10208 | 射程强化 (Range enhancement) | 300 | 92 | 2242 | wiki_2026-08-10 | high |
| 180808 | 滚动充能 (Kinetic Charge) | 150 | 161 | 2015 | wiki_2026-08-10 | high |

### 9：尖牙 (fang)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 209 | 随身护盾 (Portable shield) | 500 | 105 | 2332 | wiki_2026-08-10 | high |
| 3109 | 榴弹发射器 (Grenade Launcher) | 150 | 28 | 1522 | wiki_2026-08-10 | high |
| 10209 | 射程强化 (Range enhancement) | 300 | 69 | 2381 | wiki_2026-08-10 | high |
| 10509 | 机械狂暴 (Mechanical rage) | 250 | 28 | 1265 | wiki_2026-08-10 | high |
| 10609 | 穿甲弹 (Armor piercing bullets) | 100 | 6 | 498 | wiki_2026-08-10 | high |
| 180209 | 引燃 (Ignite) | 150 | 30 | 1537 | wiki_2026-08-10 | high |

### 10：爬虫 (crawler)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 2610 | 潜地行动 (Subterranean blitz) | 350 | 282 | 2345 | wiki_2026-08-10 | high |
| 2710 | 酸性爆炸 (Acidic explosion) | 100 | 4 | 525 | wiki_2026-08-10 | high |
| 3510 | 松散队列 (Loose formation) | 250 | 89 | 1827 | wiki_2026-08-10 | high |
| 10510 | 机械狂暴 (Mechanical rage) | 100 | 63 | 1318 | wiki_2026-08-10 | high |
| 10710 | 冲击钻头 (Impact Drill) | 150 | 23 | 1451 | wiki_2026-08-10 | high |
| 180110 | 复制 (Replicate) | 250 | 35 | 2070 | wiki_2026-08-10 | high |

### 11：霸主 (overlord)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 411 | 高爆弹药 (High explosive ammo) | 200 | 10 | 490 | wiki_2026-08-10 | high |
| 911 | 战地维修 (Field maintenance) | 150 | 1 | 144 | wiki_2026-08-10 | high |
| 1108 | 舰炮 (Overlord artillery) | 300 | 6 | 644 | wiki_2026-08-10 | high |
| 1211 | 母舰 (Mothership) | 250 | 31 | 2071 | wiki_2026-08-10 | high |
| 1611 | 高速引擎 (Jump drive) | 300 | 3 | 329 | wiki_2026-08-10 | high |
| 3011 | 装甲强化 (Armor enhancement) | 150 | 0 | 71 | wiki_2026-08-10 | high |
| 10211 | 射程强化 (Range enhancement) | 300 | 41 | 1978 | wiki_2026-08-10 | high |
| 10311 | 发射器过载 (Launcher overload) | 300 | 47 | 1867 | wiki_2026-08-10 | high |
| 180311 | 光子投射 (Photon emission) | 300 | 39 | 1941 | wiki_2026-08-10 | high |

### 12：暴雨 (stormcaller)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 412 | 高爆弹药 (High explosive ammo) | 150 | 38 | 968 | wiki_2026-08-10 | high |
| 812 | 燃烧弹 (Incendiary bomb) | 350 | 16 | 891 | wiki_2026-08-10 | high |
| 1812 | 电磁爆炸 (Electromagnetic explosion) | 300 | 30 | 1571 | wiki_2026-08-10 | high |
| 10212 | 射程强化 (Range enhancement) | 300 | 39 | 2142 | wiki_2026-08-10 | high |
| 10312 | 发射器过载 (Launcher overload) | 250 | 20 | 1657 | wiki_2026-08-10 | high |
| 10912 | 破甲弹 (High-explosive Anti-tank Shell) | 150 | 60 | 2259 | wiki_2026-08-10 | high |

### 13：铁锤 (sledgehammer)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 613 | 伤害分摊 (Damage sharing) | 200 | 11 | 1246 | wiki_2026-08-10 | high |
| 913 | 战地维修 (Field maintenance) | 200 | 35 | 1595 | wiki_2026-08-10 | high |
| 1813 | 电磁弹 (Electromagnetic shot) | 350 | 5 | 547 | wiki_2026-08-10 | high |
| 3013 | 装甲强化 (Armor enhancement) | 250 | 54 | 2100 | wiki_2026-08-10 | high |
| 10213 | 射程强化 (Range enhancement) | 300 | 25 | 1563 | wiki_2026-08-10 | high |
| 10513 | 机械狂暴 (Mechanical rage) | 250 | 14 | 1628 | wiki_2026-08-10 | high |
| 10613 | 穿甲弹 (Armor piercing bullets) | 150 | 36 | 855 | wiki_2026-08-10 | high |

### 14：骇客 (hacker)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 1014 | 保护屏障 (Barrier) | 400 | 19 | 2326 | wiki_2026-08-10 | high |
| 1714 | 强化控制 (Enhanced control) | 300 | 17 | 2260 | wiki_2026-08-10 | high |
| 1814 | 电磁干扰 (Electromagnetic interference) | 100 | 10 | 2049 | wiki_2026-08-10 | high |
| 10214 | 射程强化 (Range enhancement) | 300 | 63 | 2379 | wiki_2026-08-10 | high |
| 11014 | 多重控制 (Multi control) | 250 | 4 | 502 | wiki_2026-08-10 | high |

### 15：弧光 (arclight)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 1815 | 电磁弹 (Electromagnetic shot) | 400 | 23 | 735 | wiki_2026-08-10 | high |
| 3015 | 装甲强化 (Armor enhancement) | 100 | 20 | 420 | wiki_2026-08-10 | high |
| 3115 | 防空弹药 (Anti aircraft ammunition) | 300 | 13 | 317 | wiki_2026-08-10 | high |
| 4515 | 震荡波 (Shockwave) | 250 | 155 | 2011 | wiki_2026-08-10 | high |
| 10215 | 射程强化 (Range enhancement) | 300 | 407 | 2369 | wiki_2026-08-10 | high |
| 10815 | 精英射手 (Elite marksman) | 400 | 55 | 1706 | wiki_2026-08-10 | high |
| 10915 | 蓄能攻击 (Charged shot) | 100 | 138 | 1939 | wiki_2026-08-10 | high |

### 16：凤凰 (phoenix)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 216 | 能量护盾 (Energy shield) | 200 | 5 | 546 | wiki_2026-08-10 | high |
| 1616 | 高速引擎 (Jump drive) | 100 | 55 | 778 | wiki_2026-08-10 | high |
| 1816 | 电磁弹 (Electromagnetic shot) | 200 | 78 | 1949 | wiki_2026-08-10 | high |
| 2916 | 量子重组 (Quantum reassembly) | 150 | 86 | 1801 | wiki_2026-08-10 | high |
| 10216 | 射程强化 (Range enhancement) | 300 | 235 | 2380 | wiki_2026-08-10 | high |
| 10316 | 发射器过载 (Launcher overload) | 200 | 50 | 1134 | wiki_2026-08-10 | high |
| 10816 | 精英射手 (Elite marksman) | 400 | 10 | 344 | wiki_2026-08-10 | high |
| 10916 | 蓄能攻击 (Charged shot) | 200 | 18 | 598 | wiki_2026-08-10 | high |

### 17：战争工厂 (war factory)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 417 | 高爆弹药 (High explosive ammo) | 350 | 35 | 1786 | wiki_2026-08-10 | high |
| 3017 | 装甲强化 (Armor enhancement) | 350 | 0 | 476 | wiki_2026-08-10 | high |
| 3317 | 导弹拦截 (Missile interceptor) | 350 | 17 | 2177 | wiki_2026-08-10 | high |
| 10217 | 射程强化 (Range enhancement) | 500 | 58 | 2360 | wiki_2026-08-10 | high |
| 10317 | 发射器过载 (Launcher overload) | 300 | 4 | 1471 | wiki_2026-08-10 | high |
| 12017 | 凤凰制造 (Phoenix Production) | 500 | 26 | 1492 | wiki_2026-08-10 | high |
| 12117 | 钢球制造 (Steel Ball Production) | 450 | 43 | 1930 | wiki_2026-08-10 | high |
| 12217 | 铁锤制造 (Sledgehammer Production) | 400 | 25 | 1831 | wiki_2026-08-10 | high |
| 180317 | 光子涂层 (Photon coating) | 250 | 0 | 765 | wiki_2026-08-10 | high |

### 18：恶灵 (wraith)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 418 | 高爆弹药 (High explosive ammo) | 150 | 8 | 322 | wiki_2026-08-10 | high |
| 918 | 战地维修 (Field maintenance) | 200 | 7 | 473 | wiki_2026-08-10 | high |
| 3018 | 装甲强化 (Armor enhancement) | 200 | 14 | 627 | wiki_2026-08-10 | high |
| 4418 | 地面巡航 (Land Cruiser) | 300 | 24 | 1596 | wiki_2026-08-10 | high |
| 10218 | 射程强化 (Range enhancement) | 300 | 147 | 2361 | wiki_2026-08-10 | high |
| 110181 | 浮游炮阵 (Floating artillery array) | 400 | 73 | 2110 | wiki_2026-08-10 | high |
| 180418 | 退化光束 (Degeneration beam) | 200 | 155 | 2047 | wiki_2026-08-10 | high |

### 19：狂蝎 (scorpion)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 719 | 双发 (Doubleshot) | 100 | 118 | 2237 | wiki_2026-08-10 | high |
| 919 | 战地维修 (Field maintenance) | 150 | 24 | 1036 | wiki_2026-08-10 | high |
| 3019 | 装甲强化 (Armor enhancement) | 100 | 13 | 474 | wiki_2026-08-10 | high |
| 10019 | 攻城模式 (Siege mode) | 300 | 7 | 406 | wiki_2026-08-10 | high |
| 10219 | 射程强化 (Range enhancement) | 300 | 262 | 2311 | wiki_2026-08-10 | high |
| 10419 | 收束射击 (Focused Fire) | 150 | 40 | 1505 | wiki_2026-08-10 | high |
| 180519 | 酸性攻击 (Acid attack) | 250 | 34 | 1546 | wiki_2026-08-10 | high |

### 20：火獾 (fire badger)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 820 | 液态火 (Napalm) | 300 | 14 | 591 | wiki_2026-08-10 | high |
| 920 | 战地维修 (Field maintenance) | 150 | 2 | 276 | wiki_2026-08-10 | high |
| 10220 | 射程强化 (Range enhancement) | 300 | 262 | 2325 | wiki_2026-08-10 | high |
| 10620 | 高温火焰 (Scorching fire) | 300 | 136 | 1891 | wiki_2026-08-10 | high |
| 11020 | 逆火 (Backfire) | 200 | 98 | 1868 | wiki_2026-08-10 | high |
| 180220 | 引燃 (Ignite) | 100 | 16 | 399 | wiki_2026-08-10 | high |
| 180620 | 引燃 (Ignite) | 100 | 126 | 2182 | wiki_2026-08-10 | high |

### 21：剑齿虎 (sabertooth)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 721 | 双发 (Doubleshot) | 150 | 109 | 2175 | wiki_2026-08-10 | high |
| 3321 | 导弹拦截 (Missile interceptor) | 100 | 41 | 1448 | wiki_2026-08-10 | high |
| 4721 | 野战工事 (Field Fortifications) | 200 | 21 | 1037 | wiki_2026-08-10 | high |
| 10221 | 射程强化 (Range enhancement) | 300 | 106 | 2287 | wiki_2026-08-10 | high |
| 10321 | 战地维修 (Field Maintenance) | 200 | 82 | 1831 | wiki_2026-08-10 | high |
| 110211 | 副炮 (Secondary Armament) | 200 | 14 | 715 | wiki_2026-08-10 | high |

### 22：台风 (typhoon)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 2922 | 反应装甲 (Reactive Armor) | 200 | 45 | 2085 | wiki_2026-08-10 | medium |
| 4722 | 防空标记 (Anti-Air Mark) | 300 | 7 | 668 | wiki_2026-08-10 | medium |
| 5122 | 战地重组 (Battlefield Reassembly) | 300 | 1 | 536 | wiki_2026-08-10 | medium |
| 5222 | 维修阵列 (Maintenance Array) | 200 | 15 | 940 | wiki_2026-08-10 | medium |
| 5322 | 残骸引爆 (Wreckage Detonation) | 200 | 76 | 2092 | wiki_2026-08-10 | medium |
| 10222 | 射程强化 (Range Enhancement) | 300 | 81 | 2317 | wiki_2026-08-10 | medium |
| 1102022 | 野战工事 (Field Fortifications) | 200 | 24 | 891 | wiki_2026-08-10 | medium |

### 23：沙虫 (sandworm)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 923 | 潜地维修 (Burrow maintenance) | 150 | 24 | 1662 | wiki_2026-08-10 | high |
| 3023 | 装甲强化 (Armor enhancement) | 250 | 25 | 1489 | wiki_2026-08-10 | high |
| 3123 | 对空 (Anti aerial) | 100 | 4 | 314 | wiki_2026-08-10 | high |
| 3623 | 复制 (Replicate) | 250 | 52 | 2148 | wiki_2026-08-10 | high |
| 3723 | 沙暴 (Sandstorm) | 200 | 13 | 645 | wiki_2026-08-10 | high |
| 3823 | 突袭 (Strike) | 150 | 17 | 1193 | wiki_2026-08-10 | high |
| 10523 | 机械狂暴 (Mechanical rage) | 150 | 18 | 798 | wiki_2026-08-10 | high |
| 13023 | 机械分裂 (Mechanical division) | 200 | 13 | 1287 | wiki_2026-08-10 | high |

### 24：狼蛛 (tarantula)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 424 | 高爆弹药 (High explosive ammo) | 300 | 178 | 2020 | wiki_2026-08-10 | high |
| 924 | 战地维修 (Field maintenance) | 150 | 21 | 768 | wiki_2026-08-10 | high |
| 3024 | 装甲强化 (Armor enhancement) | 100 | 47 | 1077 | wiki_2026-08-10 | high |
| 3124 | 防空弹药 (Anti aircraft ammunition) | 150 | 3 | 270 | wiki_2026-08-10 | high |
| 10224 | 射程强化 (Range enhancement) | 300 | 270 | 2363 | wiki_2026-08-10 | high |
| 10524 | 机械狂暴 (Mechanical rage) | 400 | 71 | 1906 | wiki_2026-08-10 | high |
| 10624 | 穿甲弹 (Armor piercing bullets) | 150 | 6 | 244 | wiki_2026-08-10 | high |
| 11024 | 蜘蛛雷 (Spider mine) | 200 | 37 | 853 | wiki_2026-08-10 | high |

### 25：鬼鳐 (phantom ray)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 225 | 能量护盾 (Energy shield) | 400 | 56 | 1086 | wiki_2026-08-10 | high |
| 425 | 高爆弹药 (High explosive ammo) | 150 | 55 | 466 | wiki_2026-08-10 | high |
| 725 | 全弹发射 (Burst mode) | 200 | 38 | 1301 | wiki_2026-08-10 | high |
| 3025 | 装甲强化 (Armor enhancement) | 250 | 74 | 1903 | wiki_2026-08-10 | high |
| 3225 | 对地锁定 (Ground Targeting) | 200 | 103 | 1367 | wiki_2026-08-10 | high |
| 3925 | 隐形 (Stealth cloak) | 100 | 195 | 1282 | wiki_2026-08-10 | high |
| 10225 | 射程强化 (Range enhancement) | 300 | 38 | 1163 | wiki_2026-08-10 | high |
| 11025 | 黏油弹 (Sticky oil bomb) | 100 | 50 | 964 | wiki_2026-08-10 | high |

### 26：先知 (farseer)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 726 | 全弹发射 (Full Barrage) | 150 | 2 | 425 | wiki_2026-08-10 | high |
| 1826 | 电磁爆炸 (Electromagnetic explosion) | 150 | 7 | 534 | wiki_2026-08-10 | high |
| 3226 | 防空专精 (Aerial specialization) | 200 | 69 | 2196 | wiki_2026-08-10 | high |
| 3326 | 导弹拦截 (Missile interceptor) | 200 | 47 | 1790 | wiki_2026-08-10 | high |
| 10226 | 射程强化 (Range enhancement) | 300 | 18 | 1160 | wiki_2026-08-10 | high |
| 180326 | 光子投射 (Photon emission) | 400 | 111 | 2228 | wiki_2026-08-10 | high |
| 180526 | 搜索雷达 (Scanning radar) | 200 | 33 | 1189 | wiki_2026-08-10 | high |

### 27：雷霆 (raiden)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 227 | 能量护盾 (Energy shield) | 200 | 2 | 270 | wiki_2026-08-10 | high |
| 1827 | 电磁弹 (Electromagnetic Shot) | 300 | 44 | 2283 | wiki_2026-08-10 | high |
| 4027 | 连锁 (Chain) | 200 | 8 | 892 | wiki_2026-08-10 | high |
| 4127 | 电离 (Ionization) | 100 | 24 | 1830 | wiki_2026-08-10 | high |
| 10227 | 射程强化 (Range enhancement) | 300 | 127 | 2382 | wiki_2026-08-10 | high |
| 110271 | 分叉 (Fork) | 250 | 33 | 1879 | wiki_2026-08-10 | high |

### 28：猎犬 (hound)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 3028 | 机械狂暴 (Mechanical Rage) | 300 | 58 | 1529 | wiki_2026-08-10 | high |
| 4228 | 消防装置 (Fire Extinguisher) | 200 | 124 | 2208 | wiki_2026-08-10 | high |
| 10228 | 射程强化 (Range Enhancement) | 300 | 177 | 2356 | wiki_2026-08-10 | high |
| 10528 | 消防装置 (Fire Extinguisher) | 200 | 37 | 1227 | wiki_2026-08-10 | high |
| 11028 | 燃烧弹 (Incendiary Bomb) | 250 | 121 | 1236 | wiki_2026-08-10 | high |
| 180828 | 枪膛增压 (Chamber Compression) | 300 | 43 | 954 | wiki_2026-08-10 | high |

### 29：深渊 (abyss)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 2329 | 残骸利用 (Wreckage Recycling) | 200 | 17 | 2165 | wiki_2026-08-10 | high |
| 4329 | 纵扫 (Vertical Sweep) | 350 | 11 | 2233 | wiki_2026-08-10 | high |
| 10229 | 射程强化 (Range Enhancement) | 500 | 35 | 2378 | wiki_2026-08-10 | high |
| 11029 | 裂解 (Disintegration) | 350 | 20 | 2263 | wiki_2026-08-10 | high |
| 12029 | 暗黑伙伴 (Dark companion) | 300 | 20 | 2265 | wiki_2026-08-10 | high |
| 110291 | 蜂群导弹 (Swarm missiles) | 500 | 27 | 2372 | wiki_2026-08-10 | high |
| 180329 | 光子涂层 (Photon Coating) | 300 | 2 | 612 | wiki_2026-08-10 | high |

### 30：魔眼 (void eye)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 230 | 能量护盾 (Energy Shield) | 250 | 22 | 736 | wiki_2026-08-10 | high |
| 330 | 能量汲取 (Energy Absorption) | 50 | 19 | 237 | wiki_2026-08-10 | high |
| 4430 | 飞行模式 (Aerial Mode) | 200 | 35 | 1033 | wiki_2026-08-10 | high |
| 10230 | 射程强化 (Range Enhancement) | 300 | 128 | 2259 | wiki_2026-08-10 | high |
| 10930 | 蓄能攻击 (Charged Shot) | 100 | 108 | 1687 | wiki_2026-08-10 | high |
| 180430 | 电磁装甲 (Electromagnetic Armor) | 300 | 126 | 1853 | wiki_2026-08-10 | high |
| 180530 | 压制射击 (Suppression Shots) | 100 | 34 | 1721 | wiki_2026-08-10 | high |

### 31：磁暴 (vortex)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 631 | 并网 (Grid Integration) | 200 | 61 | 1773 | wiki_2026-08-10 | high |
| 931 | 战地维修 (Field Maintenance) | 150 | 11 | 886 | wiki_2026-08-10 | high |
| 4531 | 储能护盾 (Accumulator Shield) | 300 | 3 | 214 | wiki_2026-08-10 | high |
| 10231 | 射程强化 (Range Enhancement) | 300 | 90 | 2308 | wiki_2026-08-10 | high |
| 123101 | 移动电站 (Mobile Power Station) | 250 | 28 | 939 | wiki_2026-08-10 | high |
| 180931 | 电磁云 (Electromagnetic Cloud) | 400 | 81 | 1765 | wiki_2026-08-10 | high |
| 493101 | 应急装甲 (Emergency Armor) | 150 | 3 | 259 | wiki_2026-08-10 | high |
| 503101 | 电磁双生 (Electromagnetic Twin) | 350 | 32 | 1386 | wiki_2026-08-10 | high |

### 2001：丧钟 (death knell)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 1102001 | 能量散射 (Energy Diffraction) | 500 | 0 | 2384 | wiki_2026-08-10 | high |
| 1022001 | 射程强化 (Range Enhancement) | 500 | 0 | 2384 | wiki_2026-08-10 | high |
| 1202001 | 钢球制造 (Steel Ball Production) | 450 | 0 | 2384 | wiki_2026-08-10 | high |
| 102001 | 保护屏障 (Barrier) | 700 | 0 | 2384 | wiki_2026-08-10 | high |
| 32001 | 能量汲取 (Energy Absorption) | 400 | 0 | 2384 | wiki_2026-08-10 | high |
| 11020012 | 电磁轰炸 (Electromagnetic Barrage) | 500 | 0 | 2384 | wiki_2026-08-10 | high |

### 2002：泰山 (mountain)

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| 72002 | 饱和打击 (Saturation Bombardment) | 500 | 3 | 1874 | wiki_2026-08-10 | high |
| 302002 | 巨山装甲 (Mountain Plating) | 400 | 9 | 1831 | wiki_2026-08-10 | high |
| 312002 | 防空弹药 (Anti-Aircraft Ammunition) | 300 | 6 | 1963 | wiki_2026-08-10 | high |
| 1022002 | 射程强化 (Range Enhancement) | 500 | 51 | 2309 | wiki_2026-08-10 | high |
| 1032002 | 增程炮弹 (Extended Range Ammo) | 400 | 29 | 2118 | wiki_2026-08-10 | high |
| 11020021 | 炮射火箭 (Gun-launched Missile) | 400 | 4 | 1231 | wiki_2026-08-10 | high |
| 11020022 | 烟雾弹 (Smoke Bomb) | 350 | 3 | 1148 | wiki_2026-08-10 | high |
| 18032002 | 光子循环 (Photon Loop) | 400 | 7 | 1816 | wiki_2026-08-10 | high |

### 4001：实验丧钟/特殊单位 (experimental death knell (special))

| 科技 ID | 科技 | 基础价 | 回放研究次数 | 配置次数 | 来源 | 置信度 |
|---:|---|---:|---:|---:|---|---|
| — | 特殊单位：不套用普通丧钟价格模型 | 未定 | — | — | replay_only | unknown |

## 覆盖检查

- 检查回放：1106 局；版本：{'2119': 169, '2203': 114, '2207': 823}。
- 特殊 ID：4001 保留为实验丧钟/特殊单位；未知默认解锁和科技费用保持未定。
- 配置中出现但未进入棋盘或经济动作、未纳入标准表的 ID：[51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 101, 153, 167, 168, 1001, 1002]。
- 实际出现但不在目录的 ID：[]。
- 普通单位缺少科技目录映射：[]。
- 特殊单位预期保留的未定科技：[{'unit_id': 4001, 'tech_id': 10400101, 'research_actions': 2}, {'unit_id': 4001, 'tech_id': 11400101, 'research_actions': 5}, {'unit_id': 4001, 'tech_id': 1400101, 'research_actions': 5}, {'unit_id': 4001, 'tech_id': 1400104, 'research_actions': 2}, {'unit_id': 4001, 'tech_id': 11400103, 'research_actions': 1}, {'unit_id': 4001, 'tech_id': 18400101, 'research_actions': 1}, {'unit_id': 4001, 'tech_id': 1400103, 'research_actions': 1}, {'unit_id': 4001, 'tech_id': 11400102, 'research_actions': 1}, {'unit_id': 4001, 'tech_id': 48400102, 'research_actions': 1}]。

所有正常单位的升级费均为一级购买费用的一半；解析器和本表都只读取同一份静态成本源。
