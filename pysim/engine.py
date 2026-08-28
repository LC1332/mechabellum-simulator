# Python battle kernel (port of engine/Battle.cs) with multi-module cards.
# Design: numpy SoA arrays, vectorized targeting/movement/separation,
# card-level exp (all modules of a card share exp & level-ups, like the game).
# Divergences from C# v1 (documented):
#   - multi-module deployment via deploy.formation_positions
#   - card-level exp; upgrade thresholds use the killer's own exp table
#     (C# used the victim's table for thresholds - bug)
#   - full retarget pass every 5 ticks; per-tick validation of current target
#   - damage within a tick lands simultaneously (C# is sequential)
# step11 experiment switches (Battle.opts; validated defaults as of step6b:
#   init_cd="prep", splash on; see opts docs):
#   init_cd:    "sum" first-attack delay = initCD+prepare | "max" |
#               "replace" (initCD only) | "prep" (prepare only, default) | "none"
#   weapons:    0 (default) | 1 -> damage x weaponCountPerSkill (step6b-P3
#               probe; blanket x2 REJECTED by r1-2 A/B: 58.4% vs 60.4%)
#   splash:     default on (step6b); splash=0 restores legacy useSelfSplash gate
#   form_scale: float multiplier on formation rectangle (default 1.0)
#   facing:     False | True  rotation-gated attacks (attackAngle cone +
#               rotateSpeed turn rate, initial heading toward the enemy side)
import math
import json
import os
import numpy as np

from .gamedata import SkillDef
from .deploy import (formation_positions, MAP_X, MAP_Y,
                     TOWER_MECH, TOWER_HP_BASE, TOWER_RADIUS, TOWER_STRENGTH_LIFE,
                     PARALYSE_DURATION, PARALYSE_DMG, PARALYSE_SPEED, PARALYSE_AMPLIFY,
                     DEVICE_BARRIER, DEVICE_MISSILE,
                     BLD_WALL, BLD_AA, BLD_RF, BLD_MAGNET,
                     MAGNET_TRIGGER, MAGNET_SLOW_R, MAGNET_SELF_T,
                     building_module_offsets)
# battlefield E2 equipment table (single source; battlefield.effects imports
# nothing from the engine so this cannot cycle)
from .battlefield.effects.equipment import EQUIPMENT_BATTLE_SPECS as _EQ_SPECS
# step32 动态装备 runtime table (任务书 T1/T2): composite runtime specs +
# unified StatusKind bits; equipment_static_spec keeps the E2 static stage
# semantics (legacy 7 ids byte-identical) while runtime-only static blocks
# (次级增幅核心/汲取模块) ride the same stage.
from .battlefield.effects.equipment import (
    EQUIPMENT_RUNTIME_SPECS as _EQ_RUNTIME,
    STATUS_BITS as _STATUS_BITS,
    equipment_static_spec as _eq_static_spec)

DT = 0.01                 # 100 Hz
FIGHT_TIME = 120.0
MAX_TICKS = int(FIGHT_TIME / DT)
RETARGET_TICKS = 10        # full targeting pass interval
TRACE_TICKS = 10          # 0.1s frames

# step23 纪律14 校准层: data/calib.json = {mechs: {id: {dmg_mult, atk_dur_mult,
# prep, bullet_spd, splash, range_add}}}, 每项溯源 data/calib/{mech}/*.json
# (实验编号+场景); 表提取真值到位后逐项替换并记录差值。opts.calib=0 关闭。
_CALIB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "calib.json")
_CALIB_CACHE = None


def load_calib():
    global _CALIB_CACHE
    if _CALIB_CACHE is None:
        try:
            _CALIB_CACHE = json.load(open(_CALIB_PATH, encoding="utf8"))
        except Exception:
            _CALIB_CACHE = {}
    return _CALIB_CACHE


def _parse_id_set(val):
    """step32 equipment flag parser: "" / None -> empty; "1305003;1308001"
    (or comma form) -> {1305003, 1308001}; also accepts a real set/tuple."""
    if not val:
        return set()
    if isinstance(val, (set, frozenset)):
        return {int(x) for x in val}
    if isinstance(val, (tuple, list)):
        return {int(x) for x in val}
    out = set()
    for tok in str(val).replace(";", ",").split(","):
        tok = tok.strip()
        if tok:
            out.add(int(float(tok)))
    return out

# step9 flank (sneak) teleport: units deployed in the enemy half materialize
# over FLANK_DELAY seconds (quick-teleport officer 10009 halves it). During
# teleport the unit IS targetable, cannot attack or move, and its HP grows
# linearly 0 -> maxHP; damage beyond the grown HP kills it (user Q5).

# states
IDLE, PREPARE, ATTACK, COOL = 0, 1, 2, 3

# projectile buffer growth
_PROJ_CAP0 = 256


class Battle:
    def __init__(self, gd):
        self.gd = gd
        # step6b-validated defaults: splash on (all splashRange>0 units),
        # first-attack delay = prepareTime only (init_cd=prep won the r1-2
        # A/B: 60.4% vs 54.8% sum, on top of the splash fix)
        # step15-validated defaults (r1-3 A/B, base sneak=card 64.3%):
        #   weapons=1  -> damage x weaponCountPerSkill per attack (Vulcan
        #                 twin barrels tooltip; +0.8pp - the old step6b
        #                 rejection predates officers/barrage/towers)
        # step16: aa_bias universal discount REMOVED (user: base AA targeting
        #   is plain nearest - Mustang without 防空专精 shoots ground/air
        #   fairly). The discount is tech-gated now: 防空专精 3202 (air
        #   targets score 30m closer, +90% air dmg) / 对地锁定 3225 bake
        #   per-row offsets. opts.aa_bias stays as an ablation residual
        #   (default 0).
        # dual_target=1: 台风(22)/霸主(11) twin pods each pick their own
        #   target (user: they DO attack two targets simultaneously; the
        #   other wcnt=2 units fire both barrels at one target).
        # step18 (user-identified game truths; see 任务 T1/T7/T10/T11):
        #   air_sep=0: air<->ground pairs LEAVE the separation set (fliers
        #     and walkers never push each other; default flipped from the
        #     step13 ablation residual 1) - USER-ASSERTED game truth
        #   sep_foe: enemy unit-unit pairs in the separation set. step18 A/B
        #     full-938: foe-push OFF costs -0.8pp (58.4 vs 59.2) and lets
        #     opposing swarms interpenetrate (暴雨 sim exp share explodes to
        #     0.70 vs real 0.15, T14 report). The game truth is likely HARD
        #     COLLISION BLOCKING between enemies without displacement - the
        #     soft push is our best available proxy, so it stays ON (1).
        #     User's recollection ("似乎不会推动对方 你再看看") flagged
        #     uncertain; flip to 0 for the literal-no-push ablation.
        #   sep_chaff: crawler(10)/fang(9) collision reduction mode -
        #     "off" default: all three variants LOST accuracy in the step18
        #     full-938 A/B (pair -0.8 / half -1.0 / out -2.2); the over-push
        #     the user saw needs the enemy blocking model, not chaff removal
        #   tower_exp=1: tower kills grant flat 100 exp (user Q2: level-1
        #     tower ~ one crawler squad; higher tower levels unknown Q-D)
        #   split_shots=1: a volley's shots onto the same target queue as
        #     separate damage events (双发 two near-tick hits; per-hit armor
        #     / barrier soak each shot - user T10)
        #   burn_regen=1: regen is suppressed while standing in an enemy
        #     burning field (引燃压制回血, user T10)
        #   thunder3=1: 雷霆(27) intrinsically attacks 3 distinct targets
        #     (user T11; rides the dual_target volley-conservation code)
        # step19 (experiment-validated defaults; each has an ablation switch):
        #   barrage_split=1: T11 - a projectileCount>1 barrage divides ONE
        #     volley over its shells (dmg/N each; 0 restores the legacy
        #     full-volley-per-shell overkill, 2 = per-barrel dmg/w_count)
        #   stop_m: T12 - units stop stop_m meters INSIDE max range
        #     (stop_dist = range - stop_m; firing range unchanged; 0 = old)
        #   walk_f: T12 - walk threshold factor on stop_dist (was hardcoded
        #     0.9)
        #   tech_* =1: step19 behavior techs (data via tools/step19_mkgd.py
        #     merge of tech.json + container.json buffDatas; see §T13):
        #     tech_emp 电磁弹 on-hit disable (1.7-5.3s per-unit, speed -40%)
        #     tech_cloak 隐形 3925 untargetable until first shot
        #     tech_backfire 逆火 180620 +70 range 20s on damaged
        #     tech_rolling 滚动充能 180808 +1 range / 7m moved (max 100)
        #     tech_scorch 焦土 11020 <50% HP charge & self-destruct
        #     tech_killboom 残骸引爆 5322 victim explodes (115, 12m, allies)
        #     tech_split 机械分裂 1308 steel-ball death -> 5 crawler ghosts
        #     tech_incend 燃烧弹 11028 periodic ground fire (dps cal 352)
        #     tech_whirl 旋风斩 1109 >=2 foes in 25m -> 1.4x AoE(35m)
        #     tech_aabar 防空弹幕 1105 16x900 AA missiles / 10s
        #     tech_deadline 斩杀弹 4607 instakill below level HP line
        #     tech_ignite 引燃 family 6%/s burn 2s + regen lock
        #     tech_aegis 应急装甲 503101 first <50% HP -> 4s invuln
        self.opts = {"init_cd": "prep", "form_scale": 1.0, "facing": False,
                     "weapons": 1, "aa_bias": 0, "dual_target": 1,
                     "air_sep": 0, "sep_foe": 1, "sep_chaff": "off",
                     "tower_exp": 1, "tower_exp_val": 100.0,
                     "split_shots": 1, "burn_regen": 1, "thunder3": 1,
                     "barrage_split": 1, "stop_m": 0, "walk_f": 0.9,
                     "regen_gate": 1,
                     "tech_emp": 1, "tech_cloak": 1, "tech_backfire": 1,
                     "tech_rolling": 1, "tech_scorch": 1, "tech_killboom": 1,
                     "tech_split": 1, "tech_incend": 1, "tech_whirl": 1,
                     "tech_aabar": 1, "tech_deadline": 1, "tech_ignite": 1,
                     "tech_aegis": 1,
                     # step20 T6 behavior families (data via
                     # tools/step20_mkgd.py merge of tech.json subtables):
                     #   tech_intercept 导弹拦截 3307/3317/3321/3326/3371 -
                     #     shoot down enemy projectiles in [25,150]m; per-shot
                     #     success declines (0.05-0.08) to a floor (0.3-0.4),
                     #     recovers when idle; weaponCount parallel attempts
                     #   tech_share 伤害分摊/并网 608/613/660/657/61002/631 -
                     #     linked same-team bearers split incoming damage
                     #     (631 并网: 35% shared within 105m, up to 4 linked;
                     #     floatRate=0 families: even split, desc radius)
                     #   tech_summon supportUnitTechnologies 49 techs -
                     #     periodic summons (unitID/createDuration/
                     #     createCountPerTime), pre-allocated dead rows
                     #     activated on schedule (ghost-pool pattern)
                     "tech_intercept": 1, "tech_share": 1, "tech_summon": 1,
                     # step20 T7: EMP disable 面 = 全部科技数据通道 (表义);
                     # 0 = 旧行为 (只压 tech_dmg/armor/regen)
                     "emp_full": 1,
                     # step12: magnet slow fraction (user Q2: -30%..-50%,
                     # "similar to sticky oil"; 0.4 default, sweep 0.3/0.5)
                     "bld_slow": 0.4,
                     # step25 (comments 六条机制修正, 全部独立 A/B 可关):
                     #   tech_eshield/tech_antishield: 能量护盾家族 (盾=自身
                     #     maxHP) + 护盾穿透 (x1.3 同时打盾和本体) —— P1 静态
                     #     破案实锤 (u129/u235/u118 oracle 承伤 = 2x 血池精确)
                     #   rain_kite: C1 只有暴雨 (12) 近距回退; arc_sep: 停位
                     #     远程兵分离半径倍率 (弧形站位, 1.0=关; U组 A/B:
                     #     1.0=162/1.2=165/1.3=165/1.5=166/1.8=162, 定版 1.5)
                     #   wraith_guns: step15 恶灵4炮各瞄各的 (arc15+guns=168
                     #     = 80.8% U组最优, 恶灵 r 0.55→0.67)
                     #   hack_cur/hack_par/hack_pin/hack_emp: C3 骇客 阈值=
                     #     剩余血量 / 撤瘫痪(默认) / 本体定身 / 1814 科技失效
                     #   sweep_tier (+sweep_t1/t2, sweep_m1/m2/m3): C2 深渊
                     #     扫掠按受害者体积三档倍伤
                     #   swing_pin (+swing_min): C5 慢重挥击 (atk_dur>=2s)
                     #     弹着定死发射时刻落点 + 挥击锁靶
                     "tech_eshield": 1, "tech_antishield": 1,
                     "rain_kite": 1, "arc_sep": 1.5, "wraith_guns": 1,
                     "hack_cur": 1, "hack_par": 0, "hack_pin": 1, "hack_emp": 1,
                     "sweep_tier": 1, "swing_pin": 1, "swing_min": 2.0,
                     # step26 P1 单兵种修正线索包 (全部默认关, A/B 后定版):
                     #   sw_emerge: 沙虫出场/重新钻出的攻击锁定窗口秒数
                     #     (用户: 钻出地面和攻击前摇动画都比较久; Q-D 无科技
                     #     沙虫也延迟); sw_burrow: 潜地沙虫 (攻击范围内无敌)
                     #     不推动我方兵种 (推力全由自身承担)
                     #   vulcan_guns: 火神武器数 1 (单发全额) vs 表值 2 (现默认)
                     #   beam_cd: 激光蓄能档同时缩短攻击间隔 (充能越高打得
                     #     越快, cool_t = base/max(mult, beam_cd_min))
                     #   barrage_same: 齐射全部 pc 发打同一目标 (Q-E 先知
                     #     双弹同靶; =1 默认集合 {26}, 可给 "26,25")
                     #   sep_fac/sep_sweeps: 分离推力系数/扫描轮数 (野马
                     #     无碰撞堆积假说; stiff_sep=旧 3 轮全额)
                     #   occl: 前排友军遮挡后排射界 (恶灵混编反 向; 简化线段
                     #     模型, occl_gap/occl_w/occl_min_rng 调档, 每 0.5s 重算)
                     "sw_emerge": 0, "sw_burrow": 0, "vulcan_guns": 0,
                     "beam_cd": 0, "beam_cd_min": 0.25,
                     "sep_fac": 0, "sep_sweeps": 0, "occl": 0,
                     "occl_gap": 1.2, "occl_w": 1.0, "occl_min_rng": 40,
                     # step27 定版: 暴雨/先知齐射全弹同靶 (12,26) —— s27
                     # 无科技对拍 先知 r 0.445→1.00 k 15/15 精确, 暴雨 k
                     # 37→62; =0 恢复散射
                     "barrage_same": "12,26",
                     # step27 (review 定调: 无科技基础兵种数据与机制; 全部
                     # 默认关, 三库 A/B 后定版; 溯源 data/calib/step27/):
                     #   timeout_judge="score": 超时判定改按 oracle FightResult
                     #     口径 —— 先比存活价值 score (Σ 模块价×血量比),
                     #     再比存活模块数, 平 = -1 (旧: 人数→血量比)
                     #   aggro_ang: 角度+距离仇恨 (用户口径: 射程外目标不
                     #     锁定, 仇恨随时刷新到"从角度和距离判断上更近"的
                     #     单位): 新索敌/移动目标评分 eff = d*(1+w*(1-cosθ)),
                     #     θ = 行进方向(静止时指向敌质心)与目标方位夹角;
                     #     0=关 (纯距离)。注意 opts.aggro 是 step7 的远程
                     #     停走半径 (别撞名)。锁定语义本身 (射程内锁到死/
                     #     出射程) 引擎 keep 条件已满足。
                     #   beam_pair: 激光 ramp 按"目标对"重置 (熔点吸取每个
                     #     新目标都从低档升温; Q-B: 武器热量 beam_t 保留,
                     #     ramp 归零 —— 作用在不同量上)
                     #   dual_set: 双弹各瞄各的内置集合 (默认 "22,11"; 霸主
                     #     双弹同靶候选 = 去掉 11, review t3307_4)
                     #   tech_addhp: additionalDamageTechDatas 4127 电离
                     #     (雷霆 -70% 攻击 + 额外造成目标当前生命 50%)
                     #   tech_swsummon: moveAbilitySummonTechDatas 3623 复制
                     #     (沙虫每次钻出召唤幼虫 1001; 默认沙虫无幼虫, Q-D)
                     #   (定版 2026-08-23, A/B 见 data/calib/step27/
                     #   step27_provenance.json: s24 262 / s25 140 /
                     #   s26 283 / s27 113, 全门禁通过)
                      "timeout_judge": "score", "aggro_ang": 0, "beam_pair": 0,
                      "dual_set": "22", "tech_addhp": 0,
                      # step28b: 3623 复制实装 (每次钻出召 1 幼虫 lv3;
                      # s28 +1, 四库无该科技全平 —— 免费采纳)
                      "tech_swsummon": 1,
                     # 定版默认 (final_nomustang 臂):
                     #   hack_rate 500 (骇客前后摇长, 转化减速; s27 野马
                     #     vs骇客 10 局翻 5; s24 u150 转化局为已知代价)
                     #   barrage_same "12,26" (暴雨/先知齐射全弹同靶;
                     #     s27 先知 r 0.45→1.00 k 15/15 精确)
                     #   atk_mul "21:1.25,3:1.5" (剑齿虎/火神节奏校准;
                     #     s26 +8; 野马 buff 7:0.55 使 s24 -3 未采纳)
                      # step28b: 野马 7:0.55 追加 (三库 r 0.26-0.60 持续过弱;
                      # s26 283→286 单臂 / s24 -2 在 ratchet 261 内, 组合定版
                      # s26 288)
                      "hack_rate": 500, "atk_mul": "21:1.25,3:1.5,7:0.55",
                     # step28 定版 (2026-08-24, A/B 见 data/calib/step28/
                     # step28_provenance.json): hack_gate 光束状态门控
                     # (s27 nt_14v09 5/5) + cycle_set 武器周期守恒白名单
                     # (杂兵/重炮; 机枪病理修复, s27 nt_07v11 5/5) +
                     # pc_set 霸主 2 发 (oracle dmgMax 对账 2杀/轮) +
                     # chaff_cover 爬虫掩护语义。sw_dive/facing/lock_delay/
                     # splash_decay/volley_splash_set 负分或门禁冲突 →
                     # 默认关, opts 保留待后续 (volley_splash_set: Q-C
                     # "一个落点"在 split_shots 下已由同点双爆体现, 单溅射
                     # 口径 s26 -1 未采纳)。
                      "hack_gate": 1, "cycle_set": "7,9,10,11,17",
                      "pc_set": "11:2", "chaff_cover": 1,
                      # step28b 定版 (2026-08-24 续接会话, 溯源 data/calib/
                      # step28/step28b_provenance.json): swing_fix 齐射守恒
                      # (四库 +2/0/+2/0, s28 +5 —— swing 早退分支此前不应用
                      # pc_map 且不乘 w_count, 霸主 pc2/暴雨双武器齐射失效)
                      # + tech_swsummon 3623 沙虫复制实装 (s28 +1, 四库平)
                      # + summon_row_cap 48 (表值满额不再截断, s28 平)。
                      "swing_fix": 1,
                     # step28 其余 opts (review s27 全量 + 用户 Q 区答复;
                     # 溯源 data/calib/step28/): 全部默认关 = A/B 负或
                     # 门禁冲突, 保留待后续:
                     #   lock_delay: 索敌到首发的全局锁定延迟秒数 (Q-B)
                     #   facing_set: 前向攻击扇形白名单 (Q-A) —— 全负否决
                     #   sw_dive: 沙虫潜地不可锁定 (nt_21v23 语义; s27 +5
                     #     但 s25 -4 卡门禁, 待 summon 动力学修复)
                     #   chaff_nosep: 爬虫(10) 碰撞对全关 (用户备选口径)
                     #   volley_splash_set: 齐射单溅射 (Q-C "一个落点";
                     #     split_shots 已体现同点双爆, s26 -1 未采纳)
                     #   calib_over: {mid: {...}} 追加覆写 calib.json
                     #   splash_decay: 溅射线性衰减 (s26 +5/s24 -3)
                     #   bld_term=2: 机动单位全灭即终局 (Q-E 定版; 由
                     #     B/CAL/BP 组的 runner 注入, 非全局默认)
                     "lock_delay": 0, "facing_set": "", "sw_dive": 0,
                     "chaff_nosep": 0, "calib_over": None,
                     "volley_splash_set": "", "splash_decay": 0,
                     # step28b summon_row_cap: tech_summon 每 (卡,科技)
                     # 预分配行上限 (24 = step20 旧值截断表值满额; 48 定版,
                     # oracle st764 94 存活 ≈ 3 堡垒 x 32 满额实锤)
                     "summon_row_cap": 48,
                     # ---- step29 opts (review 全量 + 用户 Q 区答复; 全部默认关,
                     #   A/B 后定版; 溯源 data/calib/step29/) ----
                     #   no_stack_set: 该集合兵种的 multi 齐射不向同一目标叠加
                     #     —— 每目标至多 1 发, 射程内目标不足时多发打空
                     #     (st293/st354 用户定版: 雷霆 3 目标各 1 倍, 独目标
                     #     不被 *3, 推翻 step18 thunder3 守恒口径)
                     #   inc_stack: countIncrease (双发族) 的多发全部打同一
                     #     锁定目标 (st957: 剑齿虎双发只锁 1 单位+上弹+12%)
                     #   scatter5: 能量散射 (extraWeapon→laserSkillDatas 替换)
                     #     = n 条射线, 每条升温速率和上限 ×frac (st297 用户
                     #     口径: 5 条 x17%, 优先锁不同目标, 不足时同目标叠加)
                     #   summon_max_batch: 周期型制造科技总批次上限 (st315/
                     #     st298: 母舰 32s×5×3次 / 尖牙制造 36s×8×3次)
                     #   scorch_spd: 焦土冲锋速度倍率 (st346: 火獾带剩余血量
                     #     高速飞向目标并自爆, 飞行期间可被打)
                     #   prep_over: 逐兵种 prepareTime 覆写 "mid:秒" (熔点
                     #     攻击频率标定 st963; 与 fb_prep 同通道)
                     #   mine_summon: 蜘蛛雷 11024 (extraWeapon→supportSkill
                     #     召唤自爆雷 2发/15s max99; 11024 完全未建模的补装)
                     # step29 定版 (2026-08-24, A/B 见 data/calib/step29/
                     # step29_provenance.json): no_stack 雷霆齐射不叠加
                     # (st293/st354 Q-E 用户定版) + inc_stack 双发全弹同靶
                     # (st957) + summon_max_batch 3 (st315/st298 Q-C) +
                     # scatter5 熔点能量散射 5x17% (st297)。s28 801→803,
                     # s24 271/s25 140/s27 124 持平。
                     "no_stack_set": "27", "inc_stack": 1, "scatter5": 1,
                     "summon_max_batch": 3, "scorch_spd": 0.0,
                     "prep_over": "", "mine_summon": 0,
                     #   chaff_xsep: 只关爬虫(10)↔其他单位碰撞 (保留爬虫
                     #     互撞; Q-B(a) 用户定版)
                     #   sep_tan: 友军分离方向切向滑开占比 (寻路绕行代理;
                     #     cb_vulcan_dps 爬虫堆积 / C23_02 先知顶狼蛛)
                     "chaff_xsep": 0, "sep_tan": 0.0,
                     #   wc_set: 逐兵种 weaponCount 覆写 "mid:n" (泰山 2 案)
                     "wc_set": "",
                     "bld_term": 0,
                     # ---- step32 动态装备 runtime flags (任务书 T0/T1;
                     # 逐机制 feature flag, A/B 一次只切一个 ID) ----
                     #   eq_runtime: master 开关 (静态 E2 四数值不受它管,
                     #     A/B 的 OFF 臂由 runner 直接清 equipmentId)
                     #   eq_off / eq_only: 分号分隔的装备 ID 集合 —— eq_off
                     #     停用这些 ID 的 runtime 行为, eq_only 只保留集合内
                     #     的 (单机制隔离)
                     #   eq_ledger: DamageReceipt 记账 (0=关, 1=auto 场上有
                     #     runtime 装备才记, 2=强制)
                     "eq_runtime": 1, "eq_off": "", "eq_only": "",
                     "eq_ledger": 1}
        self.tower_mods = {}        # team -> {"range": +m (ranged only), "speed": +m}
        self._pending = []          # (team, mech_id, level, x, y, is_rotate)
        self._towers = []           # (team, x, y, strengthen)
        # step8-B battlefield skills (pysim/skills.py): devices join the SoA
        # arrays as pseudo-mechs, summons become normal cards, strikes are
        # scheduled damage events (all corpus releases are pre-fight -> t=0)
        self._devices = []          # (team, kind, x, y, params-dict)
        self._strikes = []          # (team, x, y, damage, splash, t)
        self._burns = []            # (team, x, y, dps, radius) step15 fire fields
        # step5 战场技能 (任务书 §4/§6/§7): ground areas, moving beams, seeded
        # storms, beacon releases and scheduled bursts. All consumed at
        # finalize/step; nothing here is numpy row state.
        self._areas = []            # ground areas (oil/smoke/acid) dicts
        self._ions = []             # moving-circle beams dicts
        self._storms = []           # seeded lightning schedules dicts
        self._beacons = []          # raw beacon releases [(team, pts, radius)]
        self._bursts = []           # scheduled (t, kind, team, params-dict)
        self._area_results = []     # (ref, ignited) per reportable area
        # step12 battlefield constructions: player defenses from the replay
        # snapshot (walls / AA cannon / rapid-fire cannon / magnetic barricade).
        # Each placement expands into `count` module rows sharing a group id.
        self._buildings = []        # (team, cid, x, y, group-index)
        self.cards = []             # per card: dict(mech, team, level, exp)
        # step14 officers: global per-player modifiers (pysim/gamedata.py
        # OfficerDef); baked into cards at finalize via per-row factor arrays
        self.officer_ids = {0: (), 1: ()}
        self.time = 0.0
        self.end_tick = 0
        self.kills = []             # {t, killer, victim, kmech, vmech, kteam}
        self.total_damage = 0.0
        # step32 T2: actual-damage ledger. Each settled event appends one
        # DamageReceipt dict (see _apply_damage); filled only when a runtime
        # equipment is on the field (opts.eq_ledger, 0=off/1=auto/2=force).
        self.damage_receipts = []
        self._receipt_seq = 0
        # step32 T3: auditable status channel events
        # (status_apply/blocked/clear/expire as dicts).
        self.status_events = []
        # step22 T4: per-card credited damage (card_idx -> float), 与
        # total_damage 同口径 (超杀截断); killerless (splash share/strike
        # 二次事件) 不记账
        self.card_damage = {}
        self.total_kills = 0
        self.total_attacks = 0
        self.trace_enabled = False
        self.trace = []
        self._finalized = False

    # ---------- setup ----------
    def add_card(self, team, mech_id, level, x, y, is_rotate=False, techs=None,
                 spawn_at=0.0, exp=0, equipment_id=0):
        # techs=None -> card default technologies; list -> full effective set
        # (empty list disables techs entirely - debug switch)
        # spawn_at: teleport duration in seconds (step9 flank deploy); 0 = none
        # exp: snapshot exp carried into the fight (step14 opts.exp_seed;
        # 0 = old behavior, cards start the fight at 0 exp)
        # equipment_id: bound equipment (battlefield E2); 0 = none. Static
        # modifiers bake in _bake_card_mods AFTER the tech/officer stage
        # (equipment_stage_v1; battlefield/effects/equipment.py is the table)
        self._pending.append((team, mech_id, level, x, y, is_rotate, techs,
                              float(spawn_at), int(exp), int(equipment_id)))

    def add_tower(self, team, x, y, strengthen=0):
        """step8: a destructible crystal; its destruction paralyses the
        owner team (dmg x0.1 / speed x0.2 / damage taken x1.5) for
        9-2*strengthen seconds."""
        self._towers.append((team, x, y, int(strengthen)))

    def add_skill_event(self, team, ev):
        """step8-B battlefield skill event (pysim/skills.py format):
          {"kind": "turret",  x, y, damage/range/cooling/...}  -> 飞弹 device
          {"kind": "barrier", x, y, hp, radius}                -> 护盾装置/空投护盾
          {"kind": "strike",  x, y, damage, splash, t?}        -> 导弹打击
          {"kind": "summon",  x, y, mech, count, level}        -> 呼叫机群/地底威胁
        step4 P1 strike extensions: "ff" (friendly fire — 轨道轰炸/核弹 hit
        BOTH teams' units, QA#6) and "bypass" (轨道标枪 ignores barrier
        absorption). Must be called before finalize()."""
        kind = ev.get("kind")
        x, y = float(ev.get("x", 0.0)), float(ev.get("y", 0.0))
        if kind == "summon":
            self.add_card(team, int(ev["mech"]), int(ev.get("level", 1)), x, y)
            if not hasattr(self, "_summon_marks"):
                self._summon_marks = []
            self._summon_marks.append((team, int(ev["mech"]), x, y))
        elif kind in ("turret", "barrier"):
            self._devices.append((team, kind, x, y, dict(ev)))
        elif kind == "strike":
            self._strikes.append((team, x, y, float(ev.get("damage", 0.0)),
                                  float(ev.get("splash", 0.0)),
                                  float(ev.get("t", 0.0)),
                                  bool(ev.get("ff", False)),
                                  bool(ev.get("bypass", False))))
        elif kind == "burn":
            # step15 燃烧弹: burning ground patch, dps while enemies stand in
            # it (radius from cal; patch burns the whole fight)
            self._burns.append((team, x, y, float(ev.get("dps", 0.0)),
                                float(ev.get("radius", 10.0))))
        elif kind in ("oil", "smoke", "acid"):
            # step5 任务书 §6: swept-capsule ground areas. The ordered pair
            # (A, B) stays ONE area — never two independent circles.
            pts = ev.get("points") or []
            if len(pts) >= 2:
                ax, ay = float(pts[0][0]), float(pts[0][1])
                bx, by = float(pts[1][0]), float(pts[1][1])
            else:
                ax, ay, bx, by = x, y, x, y
            self._areas.append({
                "kind": kind, "team": team, "ref": str(ev.get("ref", "")),
                "ax": ax, "ay": ay, "bx": bx, "by": by,
                "radius": float(ev.get("radius", 30.0)),
                "slow_mult": float(ev.get("slow_mult", 0.45)),
                "range_mult": float(ev.get("range_mult", 0.65)),
                "pct_dps": float(ev.get("pct_dps", 0.03)),
                "vuln_mult": float(ev.get("vuln_mult", 2.5)),
                "shield_block": bool(ev.get("shield_block")),
                "dead": False, "ignited": False,
                "report": kind == "oil",   # only oil persists cross-round
            })
        elif kind == "emp":
            # step5 任务书 §7 T7: instant EMP detonation (scheduled by t)
            self._bursts.append((float(ev.get("t", 0.0)), "emp", team, {
                "x": x, "y": y, "ref": str(ev.get("ref", "")),
                "radius": float(ev.get("radius", 60.0)),
                "shield_damage": float(ev.get("shield_damage", 20000.0)),
                "duration": float(ev.get("duration", 25.0)),
                "slow_mult": float(ev.get("slow_mult", 0.60))}))
        elif kind == "photon":
            # step5 任务书 §7 T8: friendly photon field, applied at t
            pts = ev.get("points") or []
            if len(pts) >= 2:
                ax, ay = float(pts[0][0]), float(pts[0][1])
                bx, by = float(pts[1][0]), float(pts[1][1])
            else:
                ax, ay, bx, by = x, y, x, y
            self._bursts.append((float(ev.get("t", 0.0)), "photon", team, {
                "ax": ax, "ay": ay, "bx": bx, "by": by,
                "radius": float(ev.get("radius", 30.0)),
                "duration": float(ev.get("duration", 20.0)),
                "dmg_taken_mult": float(ev.get("dmg_taken_mult", 0.70))}))
        elif kind == "storm":
            # step5 任务书 §7 T11: seeded lightning storm inside circle
            self._storms.append({
                "team": team, "ref": str(ev.get("ref", "")),
                "cx": x, "cy": y,
                "radius": float(ev.get("radius", 130.0)),
                "duration": float(ev.get("duration", 12.0)),
                "interval": float(ev.get("interval", 0.8)),
                "damage": float(ev.get("damage", 800.0)),
                "splash": float(ev.get("splash", 8.0)),
                "slow_mult": float(ev.get("slow_mult", 0.60)),
                "slow_duration": float(ev.get("slow_duration", 1.0)),
                "next_t": float(ev.get("interval", 0.8)),
                "rng": None})       # seeded at finalize (battle seed)
        elif kind == "ion":
            # step5 任务书 §7 T10: moving-circle beam A->B (no ground trail)
            pts = ev.get("points") or []
            if len(pts) >= 2:
                ax, ay = float(pts[0][0]), float(pts[0][1])
                bx, by = float(pts[1][0]), float(pts[1][1])
            else:
                ax, ay, bx, by = x, y, x, y
            self._ions.append({
                "team": team, "ref": str(ev.get("ref", "")),
                "ax": ax, "ay": ay, "bx": bx, "by": by,
                "radius": float(ev.get("radius", 20.0)),
                "speed": float(ev.get("speed", 25.0)),
                "dps": float(ev.get("dps", 600.0)), "done": False})
        elif kind == "beacon":
            # step5 任务书 §7 T12: move beacon. THREE ordered points: A =
            # selection centre (r=40), B/C = waypoint centres. Member-level
            # selection happens at finalize (formation rows must exist).
            pts = [(float(p[0]), float(p[1])) for p in (ev.get("points") or ())]
            if len(pts) >= 3:
                self._beacons.append((team, pts,
                                      float(ev.get("radius", 40.0))))

    def add_building(self, team, cid, x, y, index=None):
        """step12: one player-defense construction placement from the replay
        snapshot (cid 1-4). index = snapshot ConstructionSnapshotData.Index
        (globally unique per player, never reused) - used to align the
        survival metric against the next round's snapshot."""
        bdef = self.gd.buildings.get(int(cid))
        if bdef is None:
            return
        if index is None:
            index = len(self._buildings)
        self._buildings.append((team, int(cid), float(x), float(y), int(index)))

    def finalize(self):
        gd = self.gd
        pos_list = []   # (team, mech, level, x, y, card_idx, spawn_at)
        for team, mech_id, level, x, y, is_rotate, techs, spawn_at, exp0, eq0 \
                in self._pending:
            m = gd.mechs.get(mch_id := mech_id)
            if m is None or m.main_skill_id == 0:
                continue
            c = len(self.cards)
            if techs is None:
                card = gd.cards.get(mech_id)
                # step19: card default_technologies are SHOP SLOTS across all
                # families (step16 semantics: only bought techs resolve;
                # mdefull seeds MAIN-table defaults only). The step19 family
                # merge made sub-table defaults suddenly live in direct-engine
                # use (ghost crawlers from 钢球's default 1308 broke the
                # tests) - filter to the main table, matching mdefull.
                techs = [t for t in (card.default_technologies if card else ())
                         if (td := gd.techs.get(int(t))) is not None
                         and td.family == "technologyDatas"]
            self.cards.append({"mech": mech_id, "team": team, "level": max(1, level),
                               "exp": int(exp0) if self.opts.get("exp_seed", 0) else 0,
                               "techs": list(techs), "_pos": (x, y),
                               "equipment": int(eq0)})
            for px, py in formation_positions(gd, mech_id, x, y, is_rotate,
                                              scale=self.opts["form_scale"]):
                pos_list.append((team, mech_id, max(1, level), px, py, c, spawn_at))
        # step32 T1: per-card runtime equipment resolution (feature-flag
        # gated). Static E2 modifiers are NOT gated here (legacy semantics);
        # the A/B runner zeroes equipmentId for OFF arms instead.
        self._eq_runtime = self._eq_runtime_map()
        for team, tx, ty, stg in self._towers:
            pos_list.append((team, TOWER_MECH, 1, tx, ty, -1, 0.0))
        # step19 tech_split (机械分裂 1308): pre-allocate DEAD ghost crawler
        # rows for every unit of a card carrying the tech; a 钢球 death
        # revives 5 ghosts at its position (fixed SoA arrays cannot grow
        # mid-fight, so ghosts sit at card_pos until activated)
        # step20 T6: generalized to the whole deadSummonTechnologies family
        # (1308 机械分裂 / 13055+13058 超级机械分裂 / 1301003 重组野马 /
        #  13023), unitID/unitCount/unitLevel from the merged extra.
        self._ghost_pool = {}     # card_idx -> [(nspawn, [row indices])]
        if self.opts.get("tech_split", 1):
            for c, card in enumerate(self.cards):
                for tid in card.get("techs") or ():
                    td = gd.techs.get(int(tid))
                    if td is None or td.family != "deadSummonTechnologies":
                        continue
                    ex = td.extra or {}
                    nspawn = int((ex.get("unitCount") or [5])[0])
                    gid = int(ex.get("unitID", 10)) or 10
                    glv = max(1, int(ex.get("unitLevel", 0) or 0))
                    unit_count = sum(1 for p in pos_list if p[5] == c)
                    ent = self._ghost_pool.setdefault(c, [])
                    pool = []
                    for _ in range(unit_count * nspawn):
                        px, py = card.get("_pos", (0.0, 0.0))
                        pos_list.append((card["team"], gid, glv, px, py, -1, 0.0))
                        pool.append(len(pos_list) - 1)
                    ent.append((nspawn, pool))
        # step20 T6 tech_summon: supportUnitTechnologies periodic summons -
        # pre-allocate DEAD rows per (card, tech); step() activates
        # createCountPerTime rows every createDuration seconds at the
        # summoner's CURRENT position while it is alive (hard cap 24 rows
        # per tech to bound SoA cost; maxCount>0 additionally caps live ones)
        self._summon_pool = []    # dicts(card, tech, rows, nxt, spawned)
        if self.opts.get("tech_summon", 1):
            for c, card in enumerate(self.cards):
                for tid in card.get("techs") or ():
                    td = gd.techs.get(int(tid))
                    if td is None:
                        continue
                    su = (td.extra or {}).get("summon")
                    if not su:
                        continue
                    per = float(su["createDuration"])
                    one_shot = per > FIGHT_TIME
                    if per <= 0 or one_shot:
                        per = 1e9     # one-shot (最佳搭档 etc.)
                    nbatch = max(1, int(FIGHT_TIME // max(per, 0.1))) \
                        if not one_shot else 1
                    # step29 summon_max_batch: 周期型制造科技总批次上限
                    # (st315/st298 用户定版: 母舰 32s×5×最多3次, 尖牙制造/
                    # 爬虫制造 36s×8×3次 —— descParams 第3参数; Q-C 周期型
                    # 通用, 模拟中通常只涉及 1-2 次)
                    _smb = int(self.opts.get("summon_max_batch", 0) or 0)
                    if _smb > 0 and not one_shot:
                        nbatch = min(nbatch, _smb)
                    nrow = min(int(self.opts.get("summon_row_cap", 24) or 24),
                               nbatch * int(su["createCountPerTime"]))
                    rows = []
                    for _ in range(nrow):
                        px, py = card.get("_pos", (0.0, 0.0))
                        pos_list.append((card["team"], int(su["unitID"]),
                                         max(1, int(su["unitLevel"])),
                                         px, py, -1, 0.0))
                        rows.append(len(pos_list) - 1)
                    if rows:
                        self._summon_pool.append({
                            "card": c, "tech": int(tid), "rows": list(rows),
                            "nxt": float(su["startTime"]), "alive": [],
                            "cnt": int(su["createCountPerTime"]),
                            "period": per, "maxc": int(su.get("maxCount") or 0),
                            "pos": su.get("positions") or []})
        # step29 蜘蛛雷 (mine_summon): extraWeaponTechnologies → supportSkill
        # 召唤自爆雷 (11024 狼蛛: skill 24002 = 2 发/15s maxCount 99; 雷行
        # mech 1002 走向最近敌, 接敌自爆 2500 伤 12m 溅射; 走位 = 召唤点
        # 向敌, 每 15s 在持有者身前 ±20,40 处布雷)
        self._mine_pool = []
        if self.opts.get("mine_summon", 0):
            for c, card in enumerate(self.cards):
                for tid in card.get("techs") or ():
                    td = gd.techs.get(int(tid))
                    if td is None or td.family != "extraWeaponTechnologies":
                        continue
                    _skid = int((td.extra or {}).get("skillID") or 0)
                    _sk = gd.skills.get(_skid) if _skid else None
                    if _sk is None or _sk.type != "supportSkillDatas" \
                            or _sk.create_duration <= 0 or _sk.unit_id <= 0:
                        continue
                    per = float(_sk.create_duration)
                    nrow = min(96, max(1, int(FIGHT_TIME // per))
                               * max(1, int(_sk.create_count_per_time)))
                    rows = []
                    for _ in range(nrow):
                        px, py = card.get("_pos", (0.0, 0.0))
                        pos_list.append((card["team"], int(_sk.unit_id),
                                         1, px, py, -1, 0.0))
                        rows.append(len(pos_list) - 1)
                    self._mine_pool.append({
                        "card": c, "tech": int(tid), "rows": list(rows),
                        "nxt": float(_sk.start_time or 1),
                        "cnt": max(1, int(_sk.create_count_per_time)),
                        "period": per, "maxc": int(_sk.max_count or 0),
                        "pos": _sk.positions or [],
                        "dmg": None, "splash": None})
                    if self.trace_enabled:
                        self.trace.append("I|mine_pool|%d|%d|%d" % (
                            int(card["team"]), int(tid), nrow))
        # step27 3623 复制 (moveAbilitySummonTechDatas): 沙虫每次钻出 (=
        # 索敌瞬间, 无钻地循环下的代理口径) 召唤 createCountPerTime 只
        # 幼虫 (1001, 表级 unitLevel); 预分配每沙虫单位 _SW_CAP 行 (行满
        # 后不再召唤); spawn 队列在 step() 里处理。默认沙虫无幼虫 (Q-D)。
        self._swsummon_pool = {}   # card -> dict(rows, live, cnt, delay, offs)
        self._swsummon_q = []      # (worm row, due time, card idx)
        if self.opts.get("tech_swsummon", 0):
            _SW_CAP = 8
            for c, card in enumerate(self.cards):
                if card["mech"] != 23:
                    continue
                tdef = None
                for tid in card.get("techs") or ():
                    td = gd.techs.get(int(tid))
                    if td is not None and td.family == "moveAbilitySummonTechDatas":
                        tdef = td
                        break
                if tdef is None:
                    continue
                ex = tdef.extra or {}
                unit_count = sum(1 for p in pos_list if p[5] == c)
                gid = int(ex.get("unitID", 1001)) or 1001
                glv = max(1, int(ex.get("unitLevel", 1) or 1))
                pool = []
                for _ in range(unit_count * _SW_CAP):
                    px, py = card.get("_pos", (0.0, 0.0))
                    pos_list.append((card["team"], gid, glv, px, py, -1, 0.0))
                    pool.append(len(pos_list) - 1)
                self._swsummon_pool[c] = {
                    "rows": pool, "live": [],
                    "cnt": int(ex.get("createCountPerTime", 1) or 1),
                    "delay": float(ex.get("productTime", 1.0) or 1.0),
                    "offs": ex.get("positions") or [[0.0, 35.0]]}
        # step32 T6/T12: 装备生产线 —— 与 tech_summon 同一套 ghost-pool 模式
        # (预分配死行 + 定时激活), schedule 来自 runtime spec (首批
        # t=period, 每批 count 个, 共 batches 批; 召唤行 card_idx=-1 不属于
        # 任何卡, 死亡经验走无主通道)。三类生产线只是数据项, 无逐 ID 分支。
        self._eq_pool = []
        if self._eq_runtime:
            for c, spec in self._eq_runtime.items():
                su = spec.summon
                if su is None:
                    continue
                rows = []
                px, py = self.cards[c].get("_pos", (0.0, 0.0))
                for _ in range(int(su.count) * int(su.batches)):
                    pos_list.append((self.cards[c]["team"], int(su.mech_id),
                                     1, px, py, -1, 0.0))
                    rows.append(len(pos_list) - 1)
                if rows:
                    self._eq_pool.append({
                        "card": c, "rows": list(rows),
                        "nxt": float(su.resolved_first_at()),
                        "cnt": int(su.count), "period": float(su.period),
                        "batches": int(su.batches), "done": 0})
        # step32 T4/T10: 装备跟随屏障 —— 每张 carrier 卡一个 DEVICE_BARRIER
        # 装置 (多模组共享一个, oracle Q3 待证), 覆盖/吸收/EMP 打盾全部走
        # 现有 barrier 装置管线; step() 里每 tick 跟随 carrier 首个存活成员。
        self._eq_bar_devs = []      # (device index k, card_idx)
        if self._eq_runtime:
            for c, spec in self._eq_runtime.items():
                b = spec.barrier
                if b is None:
                    continue
                rows = [p for p in pos_list if p[5] == c]
                if not rows:
                    continue
                self._devices.append((self.cards[c]["team"], "barrier",
                                      float(rows[0][3]), float(rows[0][4]),
                                      {"hp": float(b.hp),
                                       "radius": float(b.radius),
                                       "eq_card": c}))
                self._eq_bar_devs.append((len(self._devices) - 1, c))
        # step12: construction placements expand into module rows (wall = 5
        # modules sharing one group, magnet = 10); rows land BEFORE devices so
        # the `i >= n - ndev` device indexing in the closures stays valid
        bld_rows = []      # mech-row -> (team, cid, group, module k, bdef)
        for team, cid, bx, by, gidx in self._buildings:
            bdef = gd.buildings.get(cid)
            if bdef is None:
                continue
            for k, (ox, oy) in enumerate(building_module_offsets(bdef)):
                px = max(-MAP_X, min(MAP_X, bx + ox))
                py = max(-MAP_Y, min(MAP_Y, by + oy))
                pos_list.append((team, -10 - cid, 1, px, py, -1, 0.0))
                bld_rows.append((team, cid, gidx, k, bdef))
        for team, kind, x, y, prm in self._devices:
            mid = DEVICE_MISSILE if kind == "turret" else DEVICE_BARRIER
            pos_list.append((team, mid, 1, x, y, -1, 0.0))
        n = len(pos_list)
        self.n = n
        ndev = len(self._devices)
        f32 = np.float32
        self.x = np.array([p[3] for p in pos_list], dtype=f32)
        self.y = np.array([p[4] for p in pos_list], dtype=f32)
        self.team = np.array([p[0] for p in pos_list], dtype=np.int8)
        self.mech_id = np.array([p[1] for p in pos_list], dtype=np.int32)
        self.card_idx = np.array([p[5] for p in pos_list], dtype=np.int32)
        self.level = np.array([p[2] for p in pos_list], dtype=np.int32)
        self.uid = np.arange(1, n + 1, dtype=np.int64)
        self.dead = np.zeros(n, dtype=bool)
        self.is_tower = self.mech_id == TOWER_MECH
        self.is_device = (self.mech_id == DEVICE_BARRIER) | (self.mech_id == DEVICE_MISSILE)
        self.spawn_at = np.array([p[6] for p in pos_list])
        # step12 building rows (towers, then buildings, then devices);
        # building rows are contiguous in pos_list, so the index scan pairs
        # 1:1 with bld_rows order. mech_id = -10 - cid covers -11..-14.
        self.is_bld = self.mech_id <= BLD_WALL
        bld_idx = np.where(self.is_bld)[0]
        assert len(bld_idx) == len(bld_rows), "building row mismatch"
        self.bld_cid = np.zeros(n, dtype=np.int32)
        self.bld_group = np.full(n, -1, dtype=np.int32)
        self.bld_mod = np.zeros(n, dtype=np.int32)
        # magazine + magnet state (0 hidden, 1 popped)
        self.bld_shot_cnt = np.zeros(n, dtype=np.int32)
        self.bld_shots_cap = np.zeros(n, dtype=np.int32)
        self.bld_reload_t = np.zeros(n)
        self.bld_state = np.zeros(n, dtype=np.int8)
        self.bld_pop_at = np.full(n, -1.0)
        for u, (team, cid, gidx, k, bdef) in zip(bld_idx, bld_rows):
            self.bld_cid[u] = cid
            self.bld_group[u] = gidx
            self.bld_mod[u] = k
            self.bld_shots_cap[u] = bdef.reload_shots
            self.bld_reload_t[u] = bdef.reload_time
        self.bld_defs = {int(u): bld_rows[i][4] for i, u in enumerate(bld_idx)}
        # tower strengthen level (0-4) mapped onto unit order
        self.tower_str = np.zeros(n, dtype=np.int32)
        if self._towers:
            tl = [t[3] for t in self._towers]
            k = 0
            for i, p in enumerate(pos_list):
                if p[1] == TOWER_MECH:
                    self.tower_str[i] = tl[k]
                    k += 1

        # step8-B device rows (last ndev entries of pos_list)
        dev_prm = [None] * ndev
        for k in range(ndev):
            dev_prm[k] = self._devices[k][4]

        def unit_life(i):
            if self.is_tower[i]:
                return self.tower_hp() + TOWER_STRENGTH_LIFE[min(4, self.tower_str[i])]
            if self.is_bld[i]:
                bdef = self.bld_defs.get(int(i))
                if bdef is not None:
                    # opts.bld_wall_life overrides the decoded per-module HP
                    if bdef.cid == 1 and self.opts.get("bld_wall_life"):
                        return float(self.opts["bld_wall_life"])
                    return bdef.life
                return 1000.0
            if i >= n - ndev:
                return float(dev_prm[i - (n - ndev)].get("hp", 1.0))
            return gd.mechs[int(self.mech_id[i])].life

        def unit_dmg(i):
            if self.is_tower[i]:
                return 0.0
            if self.is_bld[i]:
                bdef = self.bld_defs.get(int(i))
                return bdef.damage if bdef is not None else 0.0
            if i >= n - ndev and not self.is_tower[i]:
                return float(dev_prm[i - (n - ndev)].get("damage", 0.0))
            return gd.mechs[int(self.mech_id[i])].damage

        def unit_radius(i):
            if self.is_tower[i]:
                return TOWER_RADIUS
            if self.is_bld[i]:
                bdef = self.bld_defs.get(int(i))
                return bdef.radius if bdef is not None else 4.0
            if i >= n - ndev:
                return float(dev_prm[i - (n - ndev)].get("radius", 5.0))
            return gd.mechs[int(self.mech_id[i])].radius

        # step23 纪律14 校准层: per-mech 覆写 (data/calib.json, 溯源
        # data/calib/{mech}/*.json; opts.calib=0 关闭)。life/dmg 在此,
        # 攻速/弹速/溅射/射程在技能参数段。
        calib = load_calib().get("mechs", {}) if self.opts.get("calib", 1) else {}
        # step28 calib_over: A/B 扫描用追加覆写 (不落盘 calib.json)
        if self.opts.get("calib_over"):
            calib = dict(calib)
            for k, v in self.opts["calib_over"].items():
                cur = dict(calib.get(str(k)) or {})
                cur.update(v or {})
                calib[str(k)] = cur
        self.calib_life = np.ones(n)
        self.calib_dmg = np.ones(n)
        ids_n = self.mech_id[:n]
        for mid_s, ov in calib.items():
            try:
                m_ = ids_n == int(mid_s)
            except (TypeError, ValueError):
                continue
            if m_.any():
                self.calib_life[m_] = float(ov.get("life_mult", 1.0))
                self.calib_dmg[m_] = float(ov.get("dmg_mult", 1.0))
        life = np.array([unit_life(i) for i in range(n)], dtype=f32)
        dmg = np.array([unit_dmg(i) for i in range(n)], dtype=f32)
        self.max_hp = life * self.level * self.calib_life
        self.hp = self.max_hp.copy()
        self.base_dmg = dmg * self.level * self.calib_dmg
        self.radius = np.array([unit_radius(i) for i in range(n)])
        self.move_speed = np.array([0.0 if (self.is_tower[i] or self.is_bld[i] or i >= n - ndev) else
                                    gd.mechs[int(self.mech_id[i])].move_speed for i in range(n)])
        self.is_fly = np.array([False if (self.is_tower[i] or self.is_bld[i] or i >= n - ndev) else
                                gd.mechs[int(self.mech_id[i])].is_fly for i in range(n)], dtype=bool)
        can_air = np.array([False if (self.is_tower[i] or self.is_bld[i] or i >= n - ndev) else
                            gd.mechs[int(self.mech_id[i])].can_attack_air for i in range(n)], dtype=bool)
        # cannons hit ground only (user Q3: 炮无法攻击空军); walls and magnets
        # never attack. Buildings are ground objects, so any unit with
        # can_attack_ground may target them (walls/cannons are aggro for air
        # units too).
        can_gnd = np.array([False if (self.is_tower[i] or self.is_bld[i] or i >= n - ndev) else
                            gd.mechs[int(self.mech_id[i])].can_attack_ground for i in range(n)], dtype=bool)
        for u in self.bld_defs:
            if self.bld_cid[u] in (2, 3):
                can_gnd[u] = True
        # turret device (飞弹) fires at both layers; barriers never attack
        for k in range(ndev):
            i = n - ndev + k
            if self._devices[k][1] == "turret":
                can_air[i] = True
                can_gnd[i] = True
        # hittable[i, j]: unit i may attack unit j
        self.hittable = (self.team[:, None] != self.team[None, :]) & np.where(
            self.is_fly[None, :], can_air[:, None], can_gnd[:, None])
        # step24 转化重建用 (骇客): 保留层攻击能力数组
        self._can_air_rows = can_air.copy()
        self._can_gnd_rows = can_gnd.copy()
        # step32 T4: 装备屏障的行索引 (device rows 是 pos_list 末段)
        self._eq_follow_bars = [(self.n - ndev + k, c)
                                for k, c in self._eq_bar_devs]

        # per-unit skill params (vectorized state machine)
        skill_idx = []
        rng, prep, init_cd, atk_dur, atk_pt, cool = [], [], [], [], [], []
        bullet_spd, splash_rng, use_splash, dmg_rate = [], [], [], []
        melee, w_cnt = [], []
        for mid in self.mech_id:
            m = gd.mechs.get(int(mid)) if mid != TOWER_MECH else None
            s = gd.skills.get(m.main_skill_id) if m else None
            if s is None:
                skill_idx.append(-1)
                rng.append(0.0); prep.append(0.0); init_cd.append(0.0); atk_dur.append(1.0)
                atk_pt.append(0.0); cool.append(0.0); bullet_spd.append(0.0)
                splash_rng.append(0.0); use_splash.append(False); dmg_rate.append(1.0)
                melee.append(False); w_cnt.append(1.0)
            else:
                skill_idx.append(id(s))
                rng.append(s.range); prep.append(s.prepare_time); init_cd.append(s.initial_cool_down)
                atk_dur.append(max(0.01, s.attack_duration)); atk_pt.append(s.attack_point)
                cool.append(s.cooling_time); bullet_spd.append(s.bullet_speed)
                splash_rng.append(s.splash_range); use_splash.append(s.use_self_splash)
                dmg_rate.append(s.damage_rate)
                melee.append(bool(s.is_melee)); w_cnt.append(float(s.weapon_count))
        self.skill_ref = skill_idx
        self.skill_of = {id(s): s for s in gd.skills.values()}
        # step24 骇客控制光束 (controllBeamSkillDatas): 光束锁定目标 = 瘫痪
        # (不能移动/攻击), 不造成直伤; 对塔/建筑走普通直伤 (u053: oracle 骇客
        # dmgReal 恰 = 一座 L0 塔血 3400, ko=1 = 塔; 对兵击杀计数但伤害远低于
        # 目标血量 = 转化/移除口径, 以瘫痪模型近似)。opts.hacker_beam=0 关。
        self.is_hacker = np.zeros(n, dtype=bool)
        for i, mid in enumerate(self.mech_id):
            if mid <= 0 or i >= n - ndev:
                continue
            m = gd.mechs.get(int(mid))
            s = gd.skills.get(m.main_skill_id) if m else None
            if s is not None and s.type == "controllBeamSkillDatas":
                self.is_hacker[i] = True
        self.hacked = np.zeros(n, dtype=bool)
        # step8-B: turret devices get a synthetic SkillDef (no gd.skills
        # entry); barrier rows keep skill_ref -1 (never fire)
        for k in range(ndev):
            team, kind, x, y, prm = self._devices[k]
            if kind != "turret":
                continue
            i = n - ndev + k
            sk = SkillDef()
            sk.type = "deviceTurret"
            sk.name = "飞弹"
            sk.range = float(prm.get("range", 100.0))
            sk.prepare_time = float(prm.get("prepare", 0.5))
            sk.cooling_time = float(prm.get("cooling", 5.0))
            sk.attack_duration = max(0.01, float(prm.get("attack_duration", 1.0)))
            sk.attack_point = sk.attack_duration
            sk.bullet_speed = float(prm.get("bullet_speed", 80.0))
            sk.splash_range = float(prm.get("splash", 0.0))
            sk.damage = []
            sk.damage_rate = 1.0
            self.skill_of[id(sk)] = sk
            skill_idx[i] = id(sk)
            rng[i] = sk.range
            prep[i] = sk.prepare_time
            init_cd[i] = 0.0
            atk_dur[i] = sk.attack_duration
            atk_pt[i] = sk.attack_point
            cool[i] = sk.cooling_time
            bullet_spd[i] = sk.bullet_speed
            splash_rng[i] = sk.splash_range
        # step12: cannons fire through the normal unit attack state machine
        # using their construction skills (3002001 AA / 3003001 RF); walls and
        # magnets stay skill-less (never attack)
        for u, bdef in self.bld_defs.items():
            if bdef.cid not in (2, 3):
                continue
            s = gd.skills.get(bdef.skill_id)
            if s is None:
                continue
            skill_idx[u] = id(s)
            rng[u] = s.range
            prep[u] = s.prepare_time
            init_cd[u] = s.initial_cool_down
            atk_dur[u] = max(0.01, s.attack_duration)
            atk_pt[u] = s.attack_point
            cool[u] = s.cooling_time
            bullet_spd[u] = s.bullet_speed
            splash_rng[u] = s.splash_range
            dmg_rate[u] = s.damage_rate
        self.is_melee = np.array(melee, dtype=bool)
        # step15: multi-gun units (distinct weapon skillIDs, e.g. Wraith 4
        # cannons / Raiden 3 / War Factory 4 mounts). opts.wraith_guns=1 lets
        # each gun pick its own nearest target (wiki Wraith: "always targets
        # up to 4 different units"); identical-weapon units stay wcnt-based.
        self.gun_cnt = np.zeros(n, dtype=np.int32)
        if self.opts.get("wraith_guns"):
            for i, mid in enumerate(self.mech_id):
                if mid == TOWER_MECH or mid < 0 or i >= n - ndev:
                    continue
                s = gd.skills.get(gd.mechs[int(mid)].main_skill_id) \
                    if gd.mechs.get(int(mid)) else None
                if s is None or not getattr(s, "weapon_ids", None):
                    continue
                self.gun_cnt[i] = len(set(s.weapon_ids))
        # weapons multiplier (step6b-P3): weaponCountPerSkill = damage
        # instances per attack (tooltip-verified: Normal mode = weapon count,
        # e.g. Vulcan's twin barrels -> 2). opts weapons=0 keeps 1/attack.
        self.w_count = np.array(w_cnt)
        if not self.opts.get("weapons"):
            self.w_count = np.ones(n)
        # step26 P1 火神武器数 A/B (用户: 看看是 2 把武器还是 1 把): 表值
        # weaponCountPerSkill=2 → 默认 dmg×2; vulcan_guns=1 改单武器单发全额
        # (=1 集合 {3}; "3,21" 指定兵种集合 —— s26 剑齿虎 r4.82 同款假说)
        _vg = self.opts.get("vulcan_guns", 0)
        if _vg == 1 or _vg == "1":
            _vset = {3}
        elif isinstance(_vg, str) and _vg not in ("", "0"):
            _vset = {int(x) for x in _vg.split(",") if x.strip()}
        else:
            _vset = set()
        if _vset:
            for _vm in _vset:
                self.w_count[self.mech_id == _vm] = 1
        # step29 wc_set: 逐兵种 weaponCount 覆写 "mid:n" (st299/calib:
        # 泰山 2002 表值 weaponCountPerSkill=1 但 oracle 对大目标 DPS
        # ≈ 2-3 把; 用户 Q "泰山 1 把还是 2 把武器" 对账)
        self._wc_mul = np.ones(n)   # swing 分支同步乘武器数
        _wcs = str(self.opts.get("wc_set", "") or "")
        if _wcs:
            for kv in _wcs.replace(";", ",").split(","):
                if ":" in kv:
                    kk, vv = kv.split(":", 1)
                    try:
                        self.w_count[self.mech_id[:n] == int(float(kk))] = \
                            float(vv)
                        self._wc_mul[self.mech_id[:n] == int(float(kk))] = \
                            float(vv)
                    except ValueError:
                        pass
        self.range = np.array(rng)
        self.stop_dist = np.where(self.range > 0, self.range, 5.0)
        self.prep_t = np.array(prep)
        # step22 T5-F2: 火獾(20) 基础射速校准 —— 武器 1000000 表未提取
        # (attackInterval 缺失), oracle 构造局实测基础吞吐为引擎默认
        # (prep 1.0s/发, ~0.8 发/s) 的 ~2 倍; 用 prep 覆写逼近 (~1.5 发/s)
        fbprep = float(self.opts.get("fb_prep", 0.55))
        if fbprep >= 0:
            self.prep_t[self.mech_id[:n] == 20] = fbprep
        # step29 prep_over: 逐兵种 prepareTime 覆写 "mid:秒" (熔点攻击频率
        # 标定 st963: 攻击前摇/频率明显过高, 与 fb_prep 同通道)
        _po = str(self.opts.get("prep_over", "") or "")
        if _po:
            for kv in _po.replace(";", ",").split(","):
                if ":" in kv:
                    kk, vv = kv.split(":", 1)
                    try:
                        self.prep_t[self.mech_id[:n] == int(float(kk))] = float(vv)
                    except ValueError:
                        pass
        self.init_cd = np.array(init_cd)
        self.atk_dur = np.array(atk_dur)
        self.hit_at = np.minimum(np.array(atk_pt), self.atk_dur)
        self.cool_t = np.array(cool)
        self._cool0 = self.cool_t.copy()
        self._bld_reloading = np.zeros(n, dtype=bool)
        self.bullet_spd = np.array(bullet_spd)
        # splash (step6b): any unit with splashRange>0 deals splash damage.
        # The old gate (useSelfSplash) kept ALL 41 splash units off - the
        # dominant reason chaff (crawler/fang swarms) died one-by-one.
        # opts splash=0 restores the legacy gate for ablation.
        splash_vals = np.array(splash_rng)
        if self.opts.get("splash", True):
            self.splash = splash_vals
        else:
            self.splash = np.where(use_splash, splash_vals, 0.0)
        # step23 纪律14 校准层 (技能参数段; calib/ids_n 在生命段已建):
        # prep 覆写 / atk_dur 倍率 / bullet_spd / splash 覆写 / range 平移
        if calib:
            for mid_s, ov in calib.items():
                try:
                    m_ = (ids_n == int(mid_s))
                except (TypeError, ValueError):
                    continue
                if not m_.any():
                    continue
                if ov.get("prep") is not None:
                    self.prep_t[m_] = float(ov["prep"])
                if ov.get("atk_dur_mult") is not None:
                    self.atk_dur[m_] *= float(ov["atk_dur_mult"])
                if ov.get("bullet_spd") is not None:
                    self.bullet_spd[m_] = float(ov["bullet_spd"])
                if ov.get("splash") is not None:
                    self.splash[m_] = float(ov["splash"])
                if ov.get("range_add"):
                    self.range[m_] += float(ov["range_add"])
                    self.stop_dist[m_] = self.range[m_]
        self.dmg_rate = np.array(dmg_rate)
        # tech modifier outputs (baked per card in _bake_card_mods)
        self.tech_dmg = np.ones(n)
        self.min_rng = np.zeros(n)
        # step16 sub-table tech outputs (per-row, baked alongside):
        #   air_dmg/gnd_dmg: layer damage rate (防空专精 +90% vs air etc.)
        #   aa_off/gnd_off:  targeting score offsets in meters (防空专精 30 /
        #                    对地锁定 60 - the air discount is TECH-gated,
        #                    base targeting stays plain nearest)
        #   armor:           flat per-hit damage reduction (装甲强化)
        #   regen:           maxHP fraction per second (战地维修)
        #   lifesteal:       heal fraction of damage dealt (能量汲取)
        #   multi_n:         extra targets per attack (双发 countIncrease +
        #                    intrinsic dual-pod 台风/霸主)
        #   sec_dmg/sec_rng: on-hit secondary splash (震荡波)
        self.air_dmg = np.zeros(n)
        self.gnd_dmg = np.zeros(n)
        self.aa_off = np.zeros(n)
        self.gnd_off = np.zeros(n)
        self.armor = np.zeros(n)
        self.regen = np.zeros(n)
        self.lifesteal = np.zeros(n)
        self.multi_n = np.zeros(n, dtype=np.int32)
        self.inc_multi = np.zeros(n, dtype=bool)   # step29 双发 countIncrease 源
        # step29 能量散射 (st297): scatter_n>0 的行 = n 条独立射线, 每条
        # 升温速率和上限 ×scatter_frac; 每条射线自己的 (目标, 升温步数)
        # (beam_ramp5/beam_tgt5 在 state 段初始化, 此处给 bake 用标量)
        self.scatter_n = np.zeros(n, dtype=np.int32)
        self.scatter_frac = np.ones(n)
        self.sec_dmg = np.zeros(n)
        self.add_hp = np.zeros(n)          # step27 4127 电离
        self.sec_rng = np.zeros(n)

        # facing model (opts.facing): heading starts toward the enemy side;
        # attacks require the target inside the attackAngle cone, otherwise
        # the unit turns toward it at rotateSpeed.
        self.half_cone = np.array([
            math.pi if (mid == TOWER_MECH or mid < 0 or
                        not (a := gd.mechs[int(mid)].attack_angle) or a >= 360)
            else math.radians(a) * 0.5
            for mid in self.mech_id])
        self.rot_spd = np.array([
            0.0 if mid < 0 else math.radians(gd.mechs[int(mid)].rotate_speed)
            for mid in self.mech_id])
        # team 0 starts on the y<0 half pointing +y; team 1 mirrored
        self.head = np.where(self.team == 0, math.pi / 2, -math.pi / 2)

        # step14: per-row officer factors (rates sum like techs by default;
        # opts off_stack="mul" multiplies instead). Targets: All / Air /
        # Ground / Melee / Ranged / Custom (unitIds list); None = economy
        # officer, no combat effect.
        off = {"life": np.zeros(n), "dmg": np.zeros(n), "speed": np.zeros(n),
               "rng": np.zeros(n), "splash": np.zeros(n), "intV": np.zeros(n),
               "intR": np.zeros(n)}
        if self.opts.get("officers", 1):
            for team in (0, 1):
                members = (~self.dead) & (self.team == team) & (~self.is_tower) \
                    & (~self.is_bld) & (~self.is_device)
                for oid in self.officer_ids.get(team) or ():
                    od = gd.officers.get(int(oid))
                    if od is None or not od.mods:
                        continue
                    if od.target == "Air":
                        m2 = members & self.is_fly
                    elif od.target == "Ground":
                        m2 = members & (~self.is_fly)
                    elif od.target == "Melee":
                        m2 = members & self.is_melee
                    elif od.target == "Ranged":
                        m2 = members & (~self.is_melee)
                    elif od.target == "Custom":
                        m2 = members & np.isin(self.mech_id, list(od.unit_ids))
                    else:   # All / size classes (no size data yet -> all)
                        m2 = members
                    if not np.any(m2):
                        continue
                    for k, key in (("life", "life"), ("dmg", "dmg"),
                                   ("speed", "speed"), ("rngV", "rng"),
                                   ("splash", "splash"), ("intV", "intV"),
                                   ("intR", "intR")):
                        if od.mods.get(k):
                            off[key][m2] += od.mods[k]
        self._off = off
        self._off_stack = self.opts.get("off_stack", "sum")
        # step19 behavior-tech per-row state (flags baked in
        # _bake_card_mods; dynamic values live here) - MUST exist before
        # the bake loop below
        n = self.n
        self.emp_until = np.full(n, -1.0)      # victim EMP expiry (电磁弹)
        self.emp_dur = np.zeros(n)             # attacker on-hit EMP duration
        self.cloaked = np.zeros(n, dtype=bool) # 隐形 3925
        # step24 骇客转化: 光束累计 hack 进度 (速率 hack_rate/s, 多骇客叠加),
        # 进度 ≥ 受害者 maxHP → 转化 (换阵营继续作战); 期间被瘫痪。
        # 速率定标: u150 8/9 火獾(4222HP) ~50s 内 2 骇客转化 ≈ 340/s·骇客;
        # u066 剑齿虎(14886HP) ~44s 未完成 ✓ 同速率外推。
        self.hack_progress = np.zeros(n)
        self.beaming = np.zeros(n, dtype=bool)   # step25 C3 骇客 channel 中
        self.row_damage = np.zeros(n)          # per-row credited damage
        self.bf_until = np.full(n, -1.0)       # 逆火 range-buff expiry
        self.bf_val = np.zeros(n)              # 逆火 +range (70)
        self.rolling = np.zeros(n, dtype=bool) # 滚动充能
        self.moved = np.zeros(n)               # cumulative meters walked
        self.scorch = np.zeros(n, dtype=bool)  # 焦土 card flag
        self.scorch_on = np.zeros(n, dtype=bool)  # charging (post-trigger)
        self.killboom = np.zeros(n, dtype=bool)   # 残骸引爆 attacker flag
        self.whirl = np.zeros(n, dtype=bool)      # 旋风斩
        self.aabar = np.zeros(n, dtype=bool)      # 防空弹幕
        self.deadline_v = np.zeros(n)             # 斩杀弹 HP line
        self.ignite_atk = np.zeros(n)             # 引燃 %/s (on-hit pct burn)
        self.burn_pct_until = np.full(n, -1.0)    # victim pct-burn expiry
        self.burn_pct_rate = np.zeros(n)          # victim pct-burn rate
        self.aegis = np.zeros(n, dtype=bool)      # 应急装甲 available
        self.aegis_until = np.full(n, -1.0)
        self.fire_atk = [None] * n              # fireIntensify (dps, rad, life)
        self.range_base = self.range.copy()     # dynamic range buffs apply on top
        self._tech_eff = self.tech_dmg.copy()   # EMP-suppressed tech dmg mult
        self._damaged = np.zeros(n, dtype=bool) # 战地维修 regen gate (受伤后)
        self._incend = []                       # (card_idx, next_t, period)
        self._aabar_t = np.full(n, -1.0)        # 防空弹幕 next fire time
        # step20 T6 导弹拦截 per-row params (baked in _bake_card_mods);
        # _icept_ready/_icept_p are dynamic (next allowed attempt / current
        # success probability, declines per shot, recovers while idle)
        self.icept_min = np.zeros(n)            # 0 = no intercept tech
        self.icept_max = np.zeros(n)
        self.icept_int = np.zeros(n)
        self.icept_dec = np.zeros(n)
        self.icept_flr = np.zeros(n)
        self.icept_rise = np.zeros(n)
        self.icept_rint = np.zeros(n)
        self.icept_w = np.zeros(n, dtype=np.int32)
        # step21 T2 v2: attackNum = damage dealt to a missile's HP per
        # successful intercept hit (instant-kill model until 2026-08-22,
        # user-specified HP model now); proj_hp = this unit's projectile HP
        # (skill maxLife × 1+proj_life techs; 0 = not interceptable)
        self.icept_atk = np.zeros(n)
        self.proj_hp = np.zeros(n)
        self._icept_ready = np.zeros(n)         # next attempt time
        self._icept_p = np.ones(n)              # current success prob
        # step20 T6 伤害分摊/并网 per-row params
        self.share_rate = np.zeros(n)           # 0 = none
        self.share_rad = np.zeros(n)
        self.share_maxc = np.zeros(n, dtype=np.int32)
        # step25 P1 能量护盾 (energyShieldTechnologies 201-266): 盾值 = 自身
        # maxHP (表 description 定版), 伤害先扣盾后扣血; 护盾穿透
        # (antiEnergyShield 1901-1913) = 对有盾单位伤害 xmult 且同时打盾和
        # 本体。u129/u235/u118 实锤: oracle 胜方对盾侧承伤 = 2x 血池精确。
        self.shield = np.zeros(n)
        self.anti_shield = np.zeros(n)          # 0 = no tech; else dmg mult
        # step25 C5 狂蝎前后摇: 慢重挥击 (atk_dur>=swing_min) 弹着定死在
        # 发射时刻落点, 不追踪 (oracle u160: 狂蝎 31950 dmgMax 只实现 789
        # —— 杂兵在前摇/飞行期间走出弹着点)
        self.swing_pin = np.zeros(n, dtype=bool)
        # step20 T7 emp_full: tech-only deltas of EMP-able channels
        # (range / attack interval / move speed / splash). EMP 的
        # disableTechnology = Technology.Deactive→RemoveData (dump.cs
        # Technology/ActivableItem 链定版), 生效面 = 科技贡献的全部数据通道,
        # 不止 dmg/armor/regen。Officer 修改器不是 Technology, 不受影响。
        self.rng_td = np.zeros(n)
        self.dur_td = np.zeros(n)
        self.spd_td = np.zeros(n)
        self.spl_td = np.zeros(n)
        self._emp_on = np.zeros(n, dtype=bool)  # edge detection mask
        # 机械分裂 ghosts + T6 召唤 rows start dead at their card position
        for rows in getattr(self, "_ghost_pool", {}).values():
            for _n, pool in rows:
                self.dead[pool] = True
                self.hp[pool] = 0.0
        for ent in getattr(self, "_summon_pool", []):
            self.dead[ent["rows"]] = True
            self.hp[ent["rows"]] = 0.0
        # step27 3623 幼虫行同样出生即死 (step() 激活)
        for ent in getattr(self, "_swsummon_pool", {}).values():
            self.dead[ent["rows"]] = True
            self.hp[ent["rows"]] = 0.0
        # step32 生产线召唤行出生即死 (step() 定时激活)
        for ent in getattr(self, "_eq_pool", []):
            self.dead[ent["rows"]] = True
            self.hp[ent["rows"]] = 0.0
        for c in range(len(self.cards)):
            self._bake_card_mods(c, preserve_hp=False)
        # step9: teleporting units start at 0 HP and grow linearly to maxHP
        # over spawn_at seconds (baking above resets hp, so order matters)
        self.hp[self.spawn_at > 0] = 0.0

        # round-level tower buffs (step7-A: 强化瞄准/高速移动), baked once.
        # Speed never applies to static buildings; range does apply to the
        # cannons (canBeEffectedByTowerBuff rows), matching the game.
        for team, tm in (self.tower_mods or {}).items():
            mask = self.team == team
            if tm.get("speed"):
                self.move_speed[mask & (~self.is_bld)] += tm["speed"]
            if tm.get("range"):
                # ranged units only (melee units gain nothing from +range)
                rmask = mask & (~self.is_melee) & (self.range > 0)
                self.range[rmask] += tm["range"]
                self.stop_dist[rmask] = np.where(
                    self.range[rmask] > 0, self.range[rmask], 5.0)
        # static per-round tower buffs fold into range_base (backfire /
        # rolling charge re-derive self.range from range_base each pass)
        self.range_base = self.range.copy()


        # state
        self.state = np.full(n, IDLE, dtype=np.int8)
        self.state_t = np.zeros(n)
        self.first_attack = np.ones(n, dtype=bool)
        self.dmg_applied = np.zeros(n, dtype=bool)
        self.beam_t = np.zeros(n)
        self.target = np.full(n, -1, dtype=np.int32)
        self.mv_target = np.full(n, -1, dtype=np.int32)
        self._spawning = np.zeros(n, dtype=bool)
        self._spawn_done = self.spawn_at <= 0    # units without delay start done
        # step26 P1: 沙虫 (23) 钻地掩码 + 先知同靶集合
        self.is_sw = self.mech_id == 23
        self._sw_had_t = np.zeros(n, dtype=bool)
        self._sw_emerge_until = np.zeros(n)
        self._sw_had2 = np.zeros(n, dtype=bool)   # step27 3623 钻出口径
        # step27 熔点 ramp 按"目标对"重置: beam_tgt = 当前吸取对象,
        # beam_ramp = 该对象上的累计升温步数 (Q-B: 武器热量 beam_t 保留)
        self.beam_tgt = np.full(n, -1, dtype=np.int64)
        self.beam_ramp = np.zeros(n)
        # step29 能量散射每射线的 (目标, 升温步数) + 蜘蛛雷行掩码
        # (scatter_n/scatter_frac 标量在 bake 前初始化, 此处补运行态)
        self.beam_ramp5 = np.zeros((n, 8))
        self.beam_tgt5 = np.full((n, 8), -1, dtype=np.int64)
        self.is_mine = self.mech_id == 1002
        # step27 超时 score 判定: 每模块价值 = 卡价×等级/模数 (塔/装置/
        # 建筑不计; oracle Score ≈ Σ 模块价×血量比, u054/u216/b241 对拍)
        self._score_val = np.zeros(n)
        for i in range(n):
            mid = int(self.mech_id[i])
            if mid <= 0 or self.is_tower[i] or self.is_device[i] \
                    or self.is_bld[i]:
                continue
            card_ = gd.cards.get(mid)
            if card_ is not None and card_.mech_count > 0:
                self._score_val[i] = card_.base_money / card_.mech_count \
                    * self.level[i]
        # step27 4127 电离: 每击额外造成目标当前生命值比例伤害
        # (数组本体在 armor 段初始化, 此处仅留说明)
        _bs = self.opts.get("barrage_same", 0)
        if _bs == 1 or _bs == "1":
            self._barrage_same_set = {26}
        elif isinstance(_bs, str) and _bs not in ("", "0"):
            self._barrage_same_set = {int(x) for x in _bs.split(",") if x.strip()}
        else:
            self._barrage_same_set = set()
        # step28 cycle_set: 换靶保循环白名单 ("11,17" 之类; 详见 _full_target_pass)
        _cs = str(self.opts.get("cycle_set", "") or "")
        self._cycle_set = {int(x) for x in _cs.replace(";", ",").split(",")
                           if x.strip()}

        # paralysis factors (step8): rebuilt-free multipliers, flipped on
        # tower-down events and restored on expiry
        self._dmg_fac = np.ones(n)
        self._spd_fac = np.ones(n)
        self._amp_fac = np.ones(n)
        # step12 magnet slow factor (multiplies _spd_fac; buildings are
        # immune to both paralysis and the slow field - they never move)
        self._magnet_fac = np.ones(n)
        # step5 任务书 §4/T4-T5: battlefield area/status channels
        self.photon_until = np.full(n, -1.0)   # 光子投射 expiry per row
        self._storm_slow_until = np.full(n, -1.0)
        self._area_fac = np.ones(n)            # oil/storm slow product
        self._acid_on = np.zeros(n, dtype=bool)
        self._smoke_on = np.zeros(n, dtype=bool)   # edge-triggered range scaling
        self._wp_active = np.zeros(n, dtype=bool)  # move-beacon carriers
        self._wp_stage = np.zeros(n, dtype=np.int8)
        self._wp_x0 = np.zeros(n); self._wp_y0 = np.zeros(n)
        self._wp_x1 = np.zeros(n); self._wp_y1 = np.zeros(n)
        self._photon_taken = 1.0
        self._acid_vuln = 1.0
        self.paralyse_until = {0: -1.0, 1: -1.0}
        self.towers_down = {0: 0, 1: 0}
        self.bld_groups_down = {0: 0, 1: 0}
        # step32 T3/T8: equipment status-immunity channels. Permanent mask
        # (抗干扰) + timed mask with its own expiry (光子涂层 30s window,
        # riding the existing photon damage-taken channel).
        self._eq_immune_perm = np.zeros(n, dtype=np.int64)
        self._eq_immune_temp = np.zeros(n, dtype=np.int64)
        self._eq_immune_until = np.full(n, -1.0)
        if self._eq_runtime:
            for _rc, _rspec in self._eq_runtime.items():
                _rm = np.where((self.card_idx == _rc) & (~self.dead))[0]
                if not len(_rm):
                    continue
                for _tm in _rspec.timed:
                    if _tm.kind != "photon":
                        continue
                    self.photon_until[_rm] = np.maximum(
                        self.photon_until[_rm], float(_tm.duration))
                    self._eq_immune_temp[_rm] |= _tm.immunity_mask
                    self._eq_immune_until[_rm] = np.maximum(
                        self._eq_immune_until[_rm], float(_tm.duration))
                    if _tm.dmg_taken_mult != 1.0:
                        self._photon_taken = float(_tm.dmg_taken_mult)
                    if self.trace_enabled:
                        for _u in _rm:
                            self.trace.append(
                                "E|0.00|status_apply|%d|photon_eq|%.0f"
                                % (int(self.uid[_u]), float(_tm.duration)))
                if _rspec.immunity:
                    self._eq_immune_perm[_rm] |= _rspec.immunity_mask
                    if self.trace_enabled:
                        for _u in _rm:
                            self.trace.append(
                                "E|0.00|status_apply|%d|eq_immune|%s"
                                % (int(self.uid[_u]),
                                   "+".join(_rspec.immunity)))

        # projectiles
        self._p_cap = _PROJ_CAP0
        # step19 T4d: --seed reseeds the barrage RNG (the engine's only
        # stochastic element); default 1401 keeps the deterministic world.
        import numpy as _np
        self._barrage_rng = _np.random.RandomState(int(self.opts.get("seed", 1401)))
        self.p_x = np.zeros(_PROJ_CAP0); self.p_y = np.zeros(_PROJ_CAP0)
        self.p_tx = np.zeros(_PROJ_CAP0); self.p_ty = np.zeros(_PROJ_CAP0)
        self.p_speed = np.zeros(_PROJ_CAP0); self.p_dmg = np.zeros(_PROJ_CAP0)
        self.p_splash = np.zeros(_PROJ_CAP0)
        # step21 T2: per-projectile HP (0 = plain shot, not interceptable)
        self.p_hp = np.zeros(_PROJ_CAP0)
        self.p_src = np.zeros(_PROJ_CAP0, dtype=np.int32)
        self.p_tgt = np.zeros(_PROJ_CAP0, dtype=np.int32)
        # step23 T3: p_home=0 → 定点弹 (落点发射时固定, 不追踪; 齐射散射弹真实语义)
        self.p_home = np.ones(_PROJ_CAP0)
        self.p_n = 0

        # step8-B: strike queue (sorted by t; every corpus release is
        # pre-fight, so t=0 -> lands on the first tick) + trace v3 events
        self._strikes.sort(key=lambda s: s[5])
        self._strike_k = 0
        if self.trace_enabled:
            for team, kind, x, y, prm in self._devices:
                self.trace.append("E|0.00|skill|%d|%s|%.0f,%.0f" % (team, kind, x, y))
            for team, cid, x, y, gidx in self._buildings:
                self.trace.append("E|0.00|bld|%d|%d|%d|%.0f,%.0f" % (team, cid, gidx, x, y))
            for team, x, y, dmg, splash, t, _ff, _byp in self._strikes:
                self.trace.append("E|%.2f|skill|%d|strike|%.0f,%.0f" % (t, team, x, y))
            for team, mech, sx, sy in getattr(self, "_summon_marks", []):
                self.trace.append("E|0.00|skill|%d|summon|%.0f,%.0f" % (team, sx, sy))
            # step3: pre-fight burn patches (燃烧弹 100002) get their trace
            # line like every other skill channel (trace-only, no sim change)
            for team, x, y, dps, radius in self._burns:
                self.trace.append("E|0.00|skill|%d|burn|%.0f,%.0f" % (team, x, y))
        # step5 battlefield skills: shield-clip ground areas, seed storms,
        # select beacon members (all deterministic so the digest holds)
        self._step5_finalize()
        if self.trace_enabled:
            for a in self._areas:
                if not a["dead"]:
                    self.trace.append("E|0.00|area_create|%d|%s|%.0f,%.0f->%.0f,%.0f|r%.0f"
                                      % (a["team"], a["kind"], a["ax"], a["ay"],
                                         a["bx"], a["by"], a["radius"]))
            for s in self._storms:
                self.trace.append("E|0.00|storm|%d|%.0f,%.0f|r%.0f"
                                  % (s["team"], s["cx"], s["cy"], s["radius"]))
            for i in self._ions:
                self.trace.append("E|0.00|ion|%d|%.0f,%.0f->%.0f,%.0f|r%.0f"
                                  % (i["team"], i["ax"], i["ay"], i["bx"],
                                     i["by"], i["radius"]))

        self._finalized = True
        return self

    def tower_hp(self):
        return float(self.opts.get("tower_hp", TOWER_HP_BASE))

    # ---------- helpers ----------
    # ---------- step32 equipment runtime (任务书 T1/T3) ----------
    def _eq_runtime_map(self):
        """card_idx -> EquipmentRuntimeSpec for cards whose equipment has an
        active runtime spec (opts.eq_runtime master + eq_off/eq_only per-ID
        feature flags; 一次只切一个机制的 A/B 由这两个集合表达)."""
        if not self.opts.get("eq_runtime", 1):
            return {}
        off = _parse_id_set(self.opts.get("eq_off", ""))
        only = _parse_id_set(self.opts.get("eq_only", ""))
        out = {}
        for c, card in enumerate(self.cards):
            eid = int(card.get("equipment") or 0)
            if not eid or eid in off or (only and eid not in only):
                continue
            rt = _EQ_RUNTIME.get(eid)
            if rt is not None:
                out[c] = rt
        return out

    def _status_immune(self, row, kind):
        """任务书 T3: unified status-immunity check at the apply entry point.
        Permanent mask (抗干扰) always active; the timed mask (光子涂层) only
        inside its window. Fast path: zero masks -> False (no-equipment
        battles never pay for this)."""
        bit = _STATUS_BITS.get(kind)
        if bit is None:
            return False
        row = int(row)
        if self._eq_immune_perm[row] & bit:
            return True
        return bool(self._eq_immune_temp[row] & bit) \
            and self._eq_immune_until[row] > self.time

    def _status_block_note(self, row, kind, source="equipment"):
        """Trace helper: auditable status_blocked event (no-op unless a
        runtime equipment is on the field)."""
        if self._eq_runtime:
            self.status_events.append({
                "t": round(self.time, 2), "victim": int(self.uid[row]),
                "kind": kind, "action": "status_blocked", "source": source})

    def _bake_card_mods(self, c, preserve_hp=False):
        # apply this card's technologies to all member units at the card's
        # current level; recomputed from defs, so idempotent (also used on
        # level-up). Only cheap array writes - no tick-path cost.
        card = self.cards[c]
        members = np.where((self.card_idx == c) & ~self.dead)[0]
        if len(members) == 0:
            return
        gd = self.gd
        m = gd.mechs.get(card["mech"])
        s = gd.skills.get(m.main_skill_id) if m else None
        level = card["level"]
        # step27 家族门控: 表外新家族 (电离/复制) 在 opts 关闭时不进数值
        # 通道 (保基线复现; A/B 定版后翻默认)
        _techs = list(card.get("techs") or ())
        if _techs:
            _drop_fam = set()
            if not self.opts.get("tech_addhp", 0):
                _drop_fam.add("additionalDamageTechDatas")
            if not self.opts.get("tech_swsummon", 0):
                _drop_fam.add("moveAbilitySummonTechDatas")
            if _drop_fam:
                _techs = [t for t in _techs
                          if (gd.techs.get(int(t)) is None
                              or gd.techs[int(t)].family not in _drop_fam)]
        agg = gd.sum_tech_mods(_techs, level)
        # step14 officer factors for these rows (zero when disabled)
        off = getattr(self, "_off", None)
        mul = getattr(self, "_off_stack", "sum") == "mul"
        if off is not None:
            o_life = off["life"][members]
            o_dmg = off["dmg"][members]
            o_spd = off["speed"][members]
            o_rng = off["rng"][members]
            o_spl = off["splash"][members]
            o_intv = off["intV"][members]
            o_intr = off["intR"][members]
        else:
            zeros = np.zeros(len(members))
            o_life = o_dmg = o_spd = o_rng = o_spl = o_intv = o_intr = zeros
        if m is not None:
            life_rate = agg["life_rate"] + (o_life if not mul else 0.0)
            if mul:
                life_rate = (1.0 + agg["life_rate"]) * (1.0 + o_life) - 1.0
            new_max = m.life * level * (1.0 + life_rate) * self.calib_life[members]
            dmg_rate = o_dmg if not mul else ((1.0 + o_dmg) - 1.0)
            new_dmg = m.damage * level * (1.0 + dmg_rate) \
                * self.calib_dmg[members]
            new_speed = m.move_speed + agg["speed"] + o_spd
            # battlefield E2 equipment stage (equipment_stage_v1): hp/dmg
            # MULTIPLY the post-tech+officer value, speed adds flat. Zero
            # for equipment_id 0, so non-equipment battles are unchanged.
            # step32: runtime spec static blocks (次级增幅核心/汲取模块)
            # resolve through the same accessor - legacy 7 ids unchanged.
            eq = _eq_static_spec(card.get("equipment") or 0)
            if eq is not None:
                new_max = new_max * (1.0 + eq.hp_mult)
                new_dmg = new_dmg * (1.0 + eq.dmg_mult)
                new_speed = new_speed + eq.speed_add
            if preserve_hp and np.any(self.max_hp[members] > 0):
                frac = self.hp[members] / np.maximum(self.max_hp[members], 1e-9)
                self.hp[members] = np.maximum(1.0, new_max * frac)
            else:
                self.hp[members] = new_max
            self.max_hp[members] = new_max
            self.base_dmg[members] = new_dmg
            self.move_speed[members] = new_speed
        self.tech_dmg[members] = 1.0 + agg["dmg_rate"]
        # step16 sub-table outputs
        self.air_dmg[members] = agg["air_rate"]
        self.gnd_dmg[members] = agg["gnd_rate"]
        self.aa_off[members] = agg["aa_off"]
        self.gnd_off[members] = agg["gnd_off"]
        self.armor[members] = agg["armor"]
        self.regen[members] = agg["regen"]
        self.lifesteal[members] = agg["lifesteal"]
        self.multi_n[members] = int(round(agg["multi"]))
        # step29 inc_multi: multi 来自 countIncrease 科技 (双发族) ——
        # inc_stack 口径下全部炮弹打同一锁定目标 (st957: 剑齿虎双发
        # "只锁定 1 个单位, 上弹 +12%"; thunder3/dual_set 的 intrinsic
        # multi 不属于此类)
        self.inc_multi[members] = agg["multi"] > 0.5
        # step32 T4/T5: equipment runtime effects applied AFTER the tech
        # aggregate above (which owns regen/lifesteal) so the equipment
        # overrides survive; re-derived on every bake (level-up re-bakes
        # included, mirroring the tech eshield full-reset). shield = final
        # baked maxHP (base for 便携式护盾); regen REPLACES tech 战地维修
        # (任务书: 纳米维修包覆盖战地维修); lifesteal ADDS (汲取模块).
        _rt = self._eq_runtime.get(c) if self._eq_runtime else None
        if _rt is not None:
            if _rt.shield_self == "max_hp":
                self.shield[members] = np.maximum(self.shield[members],
                                                  self.max_hp[members])
            if _rt.regen_frac is not None:
                self.regen[members] = float(_rt.regen_frac)
            if _rt.lifesteal_frac is not None:
                self.lifesteal[members] += float(_rt.lifesteal_frac)
        # step27 4127 电离 (additionalDamageTechDatas): 每击额外造成被击
        # 目标当前生命值 add_tgt_hp 比例伤害 (dmg_rate -0.7 走常规通道)
        self.add_hp[members] = 0.0
        if self.opts.get("tech_addhp", 0):
            for tid in card.get("techs") or ():
                td = gd.techs.get(int(tid))
                if td is not None and td.family == "additionalDamageTechDatas":
                    self.add_hp[members] = float(
                        (td.extra or {}).get("add_tgt_hp", 0.0))
        # 台风(22)/霸主(11): twin pods each track their own target (user
        # confirmation; other wcnt=2 units fire both barrels at one target)
        # step27 dual_set: 集合可配 (review t3307_4: 霸主两枚导弹不能同时
        # 锁两个目标 → 候选 "22" 去掉霸主双弹各瞄各的; A/B 定版)
        _ds = str(self.opts.get("dual_set", "22,11"))
        dset = {int(x) for x in _ds.split(",") if x.strip()}
        if self.opts.get("dual_target") and card["mech"] in dset:
            self.multi_n[members] = np.maximum(self.multi_n[members], 1)
        # step18 T11: 雷霆(27) intrinsically shoots 3 DISTINCT targets per
        # attack (user: 攻击不能叠加在同一单位上, 伤害计算 3 次; volley
        # conservation keeps the full 3x damage on a lone target)
        if self.opts.get("thunder3", 1) and card["mech"] == 27:
            self.multi_n[members] = np.maximum(self.multi_n[members], 2)
        self.sec_dmg[members] = agg["sec_dmg"]
        self.sec_rng[members] = agg["sec_rng"]
        # 防空弹药 (airAttackTechnologyDatas): the unit gains air attack
        if agg["grant_air"]:
            fly_cols = np.where(self.is_fly)[0]
            if len(fly_cols):
                self.hittable[members[:, None], fly_cols[None, :]] = \
                    self.team[members][:, None] != self.team[fly_cols][None, :]
        if s is not None:
            base_dur = max(0.01, s.attack_duration)
            # step27 atk_mul: 逐兵种攻击间隔倍率 ("21:1.25,3:1.5" 格式;
            # 无科技对照局对拍校准层, A/B 定版后固化进 calib.json)
            _am = self.opts.get("atk_mul")
            if _am:
                if isinstance(_am, str):
                    _amap = {}
                    for kv in _am.split(","):
                        if ":" in kv:
                            kk, vv = kv.split(":", 1)
                            _amap[int(kk)] = float(vv)
                    self._atk_mul_map = _amap
                else:
                    self._atk_mul_map = self._atk_mul_map or {}
                base_dur = base_dur * float(
                    (self._atk_mul_map or {}).get(int(card["mech"]), 1.0))
            int_rate = agg["interval_rate"] + o_intr
            int_val = agg["interval_val"] + o_intv
            if mul:
                int_rate = (1.0 + agg["interval_rate"]) * (1.0 + o_intr) - 1.0
                int_val = agg["interval_val"] + o_intv
            new_dur = np.maximum(0.01, base_dur * (1.0 + int_rate) + int_val)
            self.atk_dur[members] = new_dur
            self.hit_at[members] = np.minimum(s.attack_point * new_dur / base_dur, new_dur)
            new_rng = s.range * (1.0 + agg["range_rate"]) + agg["range_val"] + o_rng
            # equipment range stage: flat add AFTER tech/officer (激光瞄具
            # +20); EMP-full reverts only the TECH delta (rng_td), so
            # hardware range survives disable
            if eq is not None:
                new_rng = new_rng + eq.range_add
            self.range[members] = new_rng
            self.range_base[members] = new_rng
            self.stop_dist[members] = np.where(new_rng > 0, new_rng, 5.0)
            self.min_rng[members] = max(s.min_range, agg["min_range"])
            self.bullet_spd[members] = max(0.0, s.bullet_speed + agg["bullet"])
            # step21 T2: projectile HP = skill maxLife (level-picked) ×
            # (1 + projectileLifeChangeRate techs, 10912 重型导弹 2.0)
            ml = s.max_life or [0.0]
            self.proj_hp[members] = float(
                ml[min(level, len(ml)) - 1]) * (1.0 + agg["proj_life"])
            base_splash = s.splash_range if (s.use_self_splash or self.opts.get("splash", True)) else 0.0
            self.splash[members] = base_splash + agg["splash"] + agg["splash_add"] + o_spl
            # step20 T7: record the TECH-only deltas (agg without officer
            # parts) so EMP-full can revert these channels to base+officer
            if self.opts.get("emp_full", 1):
                self.rng_td[members] = s.range * agg["range_rate"] + agg["range_val"]
                self.dur_td[members] = new_dur - np.maximum(
                    0.01, base_dur * (1.0 + o_intr) + o_intv)
                self.spd_td[members] = agg["speed"]
                self.spl_td[members] = agg["splash"] + agg["splash_add"]
        # step19 T13 behavior-tech flags (static- table values; dynamics run
        # in step()). Unknown-family numeric fields were merged by
        # tools/step19_mkgd.py and flow through sum_tech_mods above.
        for tid in card.get("techs") or ():
            td = gd.techs.get(int(tid))
            if td is None:
                continue
            fam = td.family
            ex = td.extra
            buff = ex.get("buff") or {}
            if self.opts.get("tech_emp", 1) and buff.get("disableTechnology"):
                self.emp_dur[members] = float(buff.get("duration", 4.0))
            if self.opts.get("tech_backfire", 1) and tid == 180620:
                self.bf_val[members] = float(buff.get("attackRangeChangeValue", 70.0))
            if self.opts.get("tech_rolling", 1) and tid == 180808:
                self.rolling[members] = True
            if self.opts.get("tech_cloak", 1) and tid == 3925:
                self.cloaked[members] = True
            if self.opts.get("tech_scorch", 1) and tid == 11020:
                self.scorch[members] = True
            if self.opts.get("tech_killboom", 1) and fam == "killExplosionTechDatas":
                self.killboom[members] = True
            if self.opts.get("tech_whirl", 1) and tid == 1109:
                self.whirl[members] = True
            if self.opts.get("tech_aabar", 1) and tid == 1105:
                self.aabar[members] = True
                self._aabar_t[members] = -1.0
            if self.opts.get("tech_deadline", 1) and fam == "deadLineTechDatas":
                vals = ex.get("deadLineValue") or [320]
                self.deadline_v[members] = float(vals[min(level, len(vals)) - 1])
            if self.opts.get("tech_ignite", 1) and fam == "buffTechnologies" \
                    and "引燃" in (td.name or ""):
                # descParams {0}=6 %maxHP/s for {1}=2s; probability 1.0
                dps = float((td.desc_params or "6;2").split(";")[0]) / 100.0
                self.ignite_atk[members] = dps
            if self.opts.get("tech_aegis", 1) and fam == "stealthTechData":
                self.aegis[members] = True
            # step29 能量散射 (scatter5): extraWeapon→laserSkillDatas 替换
            # 主武器为 n 条 frac 射线 (st297 熔点 1107: descParams "30;5;17"
            # = 射程-30 / 5 条 / 每条 17%; 每条升温速率和上限都是单射线的
            # 17%, 优先锁不同目标, 目标 < n 时允许同目标多射线叠加)
            if self.opts.get("scatter5", 0) and fam == "extraWeaponTechnologies":
                _skid = int(ex.get("skillID") or 0)
                _skd = gd.skills.get(_skid) if _skid else None
                if _skd is not None and _skd.type == "laserSkillDatas" \
                        and _skd.damage_multiplier:
                    _pr = (td.desc_params or "").split(";")
                    try:
                        _nray = int(float(_pr[1])) if len(_pr) > 1 \
                            else len(_skd.weapon_ids) + 1
                    except ValueError:
                        _nray = len(_skd.weapon_ids) + 1
                    _frac = 0.01
                    if len(_pr) > 2:
                        try:
                            _frac = float(_pr[2]) / 100.0
                        except ValueError:
                            _frac = 0.01
                    _nray = max(1, min(8, _nray))
                    if 0.0 < _frac <= 1.0:
                        self.scatter_n[members] = _nray
                        self.scatter_frac[members] = _frac
            if fam == "fireIntensifyTechnologies":
                # 812/820: attack ignites ground at the target
                # descParams {0}=life {1}=radius {2}=dps
                pr = (td.desc_params or "15;5.5;270").split(";")
                for m in members:
                    self.fire_atk[int(m)] = (float(pr[2]), float(pr[1]), float(pr[0]))
            # step20 T6: 导弹拦截 (interceptMissileTechnologyDatas) + 伤害
            # 分摊/并网 (damageShareTechnologies) per-row params
            if self.opts.get("tech_intercept", 1) and "icept" in ex:
                ic = ex["icept"]
                self.icept_min[members] = ic["radMin"]
                self.icept_max[members] = ic["radMax"]
                self.icept_int[members] = max(0.05, ic["interval"])
                self.icept_dec[members] = ic["decline"]
                self.icept_flr[members] = ic["floor"]
                self.icept_rise[members] = ic["rise"]
                self.icept_rint[members] = max(0.05, ic["riseInt"])
                self.icept_w[members] = ic["weapons"]
                # v2 attackNum (step21_mkgd); huge fallback = old v1
                # instant-kill semantics for gamedata without the merge;
                # --opt icept_v1=1 forces the v1 semantics for A/B
                self.icept_atk[members] = 1e9 if self.opts.get("icept_v1") \
                    else float(ic.get("atk", 1e9))
            if self.opts.get("tech_share", 1) and "share" in ex:
                sh = ex["share"]
                self.share_rate[members] = sh["rate"]
                self.share_rad[members] = sh["radius"]
                self.share_maxc[members] = sh["maxCount"]
            # step25 P1 能量护盾 (opts.tech_eshield): 盾值 = 自身 maxHP
            # (tech.json description 定版: "护盾值等于自身的生命值"); 护盾
            # 穿透 (opts.tech_antishield): damageMultiplier ~1.3, 同时打
            # 盾和本体。重烘焙 (升级/转化) 时盾按满额重置。
            if self.opts.get("tech_eshield", 1) and fam == "energyShieldTechnologies":
                self.shield[members] = self.max_hp[members]
            if self.opts.get("tech_antishield", 1) \
                    and fam == "antiEnergyShieldTechnologies":
                self.anti_shield[members] = float(
                    (td.desc_params or "30%").rstrip("%")) / 100.0 + 1.0 \
                    if (td.desc_params or "").endswith("%") else 1.3
            # step25 C5: 慢重挥击单位 (atk_dur >= opts.swing_min, 默认 2s)
            # 弹着定死在挥击开始时的目标位置
            if self.opts.get("swing_pin", 1) and s is not None \
                    and s.attack_duration >= float(self.opts.get("swing_min", 2.0)):
                self.swing_pin[members] = True
        if self.opts.get("tech_incend", 1):
            for tid in card.get("techs") or ():
                if int(tid) == 11028:
                    self._incend.append([c, 0.0, 16.0])

    def alive_count(self, team):
        # step8-B: devices are battle-transient objects, not mechs - reports
        # count mechs only, so they never decide the fight outcome; same for
        # step12 buildings (static defenses, not part of the mech headcount)
        return int(np.count_nonzero((~self.dead) & (self.team == team)
                                    & (~self.is_tower) & (~self.is_device)
                                    & (~self.is_bld)))

    def _surface_matrix(self, idx):
        # surface distance matrix among idx (subset), inf on invalid pairs
        x = self.x[idx]; y = self.y[idx]; r = self.radius[idx]
        dx = x[:, None] - x[None, :]
        dy = y[:, None] - y[None, :]
        d = np.sqrt(dx * dx + dy * dy) - r[:, None] - r[None, :]
        np.fill_diagonal(d, np.inf)
        return d

    # ---------- targeting ----------
    def _full_target_pass(self):
        alive_idx = np.where(~self.dead)[0]
        if len(alive_idx) == 0:
            return
        # step19 dynamic range buffs: 逆火 (+70 for 20s after taking damage)
        # and 滚动充能 (+1 per 7m walked, max +100) rebuild self.range from
        # range_base every pass (firing/stop checks all read self.range)
        if np.any(self.bf_until > self.time) or np.any(self.rolling):
            add = np.zeros(self.n)
            act = (~self.dead) & (self.bf_until > self.time) & (self.bf_val != 0)
            add[act] += self.bf_val[act]
            if np.any(self.rolling):
                stacks = np.minimum(100.0, self.moved // 7.0)
                add += np.where(self.rolling, stacks, 0.0)
            self.range = self.range_base + add
            self.stop_dist = np.where(self.range > 0, self.range, 5.0)
        d = self._surface_matrix(alive_idx)
        h = self.hittable[np.ix_(alive_idx, alive_idx)]
        dm = np.where(h, d, np.inf)
        # step12: hidden magnets cannot be locked or attacked (and are not
        # movement targets); they pop below and become targetable next pass
        hid_local = np.where(self.is_bld[alive_idx]
                             & (self.mech_id[alive_idx] == BLD_MAGNET)
                             & (self.bld_state[alive_idx] == 0))[0]
        if len(hid_local):
            dm[:, hid_local] = np.inf
            # pop check: any enemy ground unit within MAGNET_TRIGGER (surface)
            foes = ((self.team[alive_idx][:, None] != self.team[alive_idx][hid_local][None, :])
                    & (~self.is_fly[alive_idx])[:, None]
                    & (~self.is_bld[alive_idx])[:, None])
            close = foes & (d[:, hid_local] <= MAGNET_TRIGGER)
            for pc in np.where(np.any(close, axis=0))[0]:
                mi = int(alive_idx[hid_local[pc]])
                self.bld_state[mi] = 1
                self.bld_pop_at[mi] = self.time
                if self.trace_enabled:
                    self.trace.append("E|%.2f|magnet_pop|%d|%d|%d" % (
                        self.time, int(self.team[mi]), int(self.bld_cid[mi]),
                        int(self.bld_group[mi])))
        # towers (crystals) join normal nearest-target selection (rule ① -
        # 83% of rounds have towers destroyed before annihilation, they sit
        # in the unit lanes); opts.tower_target="last" restores the v0 rule
        # "only when no enemy unit remains"; opts.tower_bias (meters) adds a
        # flat distance penalty to towers instead (0 = plain nearest)
        if np.any(tmask := self.is_tower[alive_idx]):
            mode = self.opts.get("tower_target", "nearest")
            if mode == "last":
                for team in (0, 1):
                    if not np.any((~self.dead) & (self.team != team) & (~self.is_tower)):
                        continue   # this team may attack towers freely
                    rows = np.where(self.team[alive_idx] == team)[0]
                    cols = np.where(tmask)[0]
                    if len(rows) and len(cols):
                        dm[np.ix_(rows, cols)] = np.inf
            elif (bias := float(self.opts.get("tower_bias", 0) or 0)) > 0:
                cols = np.where(tmask)[0]
                if len(cols):
                    dm[:, cols] += bias
        # step16 tech-gated targeting offsets: rows with 防空专精 score FLY
        # targets as if airTargetScoreOffset meters closer (30/40/60 by tier),
        # 对地锁定 does the same for GROUND columns. Base units target plain
        # nearest (user: Mustang without the tech shoots fairly; step15's
        # universal aa_bias is reverted to opts residual, default 0). Range
        # checks stay on the real distance.
        # siege blind zone: attacker cannot acquire targets closer than min_rng
        if np.any(self.min_rng[alive_idx] > 0):
            dm = np.where(d >= self.min_rng[alive_idx][:, None], dm, np.inf)
        aa_bias = float(self.opts.get("aa_bias", 0) or 0)
        off_air = self.aa_off[alive_idx] + aa_bias
        off_gnd = self.gnd_off[alive_idx]
        ok_sel = None
        if (np.any(off_air) or np.any(off_gnd)) and np.any(self.is_fly[alive_idx]):
            sel = dm.copy()
            flym = self.is_fly[alive_idx]
            sel[:, flym] -= off_air[:, None]
            sel[:, ~flym] -= off_gnd[:, None]
            nn_sel = np.argmin(sel, axis=1)
            real_nd_sel = dm[np.arange(len(alive_idx)), nn_sel]
            ok_sel = np.isfinite(real_nd_sel) & \
                (real_nd_sel <= self.range[alive_idx])
        # step27 aggro_ang: 射程外目标不锁定, 仇恨按"角度+距离"综合评分刷新
        # (用户 u216 口径) —— 只影响新索敌/移动目标评分, 锁定保持与射程
        # 判定仍用真实表面距离 dm。行进方向: 有位移用位移方向, 静止用
        # 指向敌方质心方向 (出击轴)。eff = d*(1 + w*(1-cosθ))。
        # (opts.aggro 是 step7 的远程停走半径, 两者不同名)
        dm_a = dm
        aggro_w = float(self.opts.get("aggro_ang", 0) or 0)
        if aggro_w > 0:
            ax_ = self.x[alive_idx]
            ay_ = self.y[alive_idx]
            tm_ = self.team[alive_idx]
            c0x, c0y = float(ax_[tm_ == 0].mean()), float(ay_[tm_ == 0].mean())
            c1x, c1y = float(ax_[tm_ == 1].mean()), float(ay_[tm_ == 1].mean())
            hx = np.where(tm_ == 0, c1x - ax_, c0x - ax_)
            hy = np.where(tm_ == 0, c1y - ay_, c0y - ay_)
            if hasattr(self, "vx"):
                mvx, mvy = self.vx[alive_idx], self.vy[alive_idx]
                mvn = np.hypot(mvx, mvy)
                moving = mvn > 1e-6
                hx = np.where(moving, mvx, hx)
                hy = np.where(moving, mvy, hy)
            hn = np.maximum(np.hypot(hx, hy), 1e-9)
            dirx = ax_[None, :] - ax_[:, None]
            diry = ay_[None, :] - ay_[:, None]
            tn = np.maximum(np.hypot(dirx, diry), 1e-9)
            cosm = np.clip((hx[:, None] * dirx + hy[:, None] * diry)
                           / (hn[:, None] * tn), -1.0, 1.0)
            np.fill_diagonal(cosm, 1.0)
            dm_a = dm * (1.0 + aggro_w * (1.0 - cosm))
            if ok_sel is not None:
                sel = dm_a.copy()
                sel[:, flym] -= off_air[:, None]
                sel[:, ~flym] -= off_gnd[:, None]
                nn_sel = np.argmin(sel, axis=1)
                real_nd_sel = dm[np.arange(len(alive_idx)), nn_sel]
                ok_sel = np.isfinite(real_nd_sel) & \
                    (real_nd_sel <= self.range[alive_idx])
        # movement target = nearest hittable enemy regardless of range -
        # computed BEFORE the step19 cloak mask below (enemies still advance
        # toward cloaked squads they cannot lock)
        mv_nn = np.argmin(dm_a, axis=1)
        mv_nd = dm_a[np.arange(len(alive_idx)), mv_nn]
        mv_local = np.where(np.isfinite(mv_nd), mv_nn, -1)
        # step19 lock mask: cloaked (隐形 3925) / 应急装甲-active columns
        # cannot be LOCKED; splash still hits them, movement still advances
        unt_col = self.cloaked[alive_idx] | (self.aegis_until[alive_idx] > self.time)
        # step28 sw_dive: 潜地沙虫不可被锁定 (review nt_21v23 用户定版) ——
        # 只有自己射程内出现可打敌人 (无对空科技=地面敌人, 有对空=含空中)
        # 才冒头; 冒头状态 = 该沙虫当前持有目标 (含 sw_emerge 钻出窗口,
        # 冒头阶段可被锁定可被攻击)。失去目标 = 重新潜地。
        if self.opts.get("sw_dive", 0) and getattr(self, "is_sw", None) is not None \
                and self.is_sw.any():
            bur = self.is_sw & (~self.dead) & (self.target < 0)
            unt_col = unt_col | bur[alive_idx]
        if np.any(unt_col):
            dm[:, unt_col] = np.inf
        nn = np.argmin(dm_a, axis=1)
        nd = dm[np.arange(len(alive_idx)), nn]
        has = np.isfinite(nd)
        nearest_local = np.where(has, nn, -1)
        if ok_sel is not None:
            # the discounted-nearest fly target wins when truly in range
            nearest_local = np.where(ok_sel, nn_sel, nearest_local)
        in_range = has & (nd <= self.range[alive_idx])
        if ok_sel is not None:
            in_range = in_range | ok_sel
        # C# keeps current target while valid (alive + in range);
        # opts.retarget="nearest" re-picks the nearest target every pass
        # (step14-P2 probe: mid-fight tower destruction is under-modeled,
        # dcc loser-2towers sim 7% vs report 30%)
        old = self.target[alive_idx]
        pos = np.full(self.n, -1, dtype=np.int64)
        pos[alive_idx] = np.arange(len(alive_idx))
        old_local = np.where(old >= 0, pos[np.maximum(old, 0)], -1)
        keep = old_local >= 0
        if np.any(unt_col):
            # a locked target going cloaked breaks the lock
            keep &= ~unt_col[np.maximum(old_local, 0)]
        if self.opts.get("retarget") == "nearest":
            keep[:] = False
        elif np.any(keep):
            gi = np.where(keep)[0]
            ot_g = old[gi]
            keep[gi] = (~self.dead[ot_g]) & (d[gi, old_local[gi]] <= self.range[alive_idx[gi]])
            # opts.aa_break: an AA-capable row drops its GROUND target once
            # a fly target is acquirable (pairs with aa_bias; alone the bias
            # only affects fresh acquisitions, so chaff soaks AA forever)
            if ok_sel is not None and self.opts.get("aa_break") and np.any(ok_sel):
                sub = gi[ok_sel[gi]]
                if len(sub):
                    old_fly = self.is_fly[alive_idx[old_local[sub]]]
                    keep[sub[~old_fly]] = False
        new_t_local = np.where(keep, old_local, np.where(in_range, nearest_local, -1))
        new_t = np.where(new_t_local >= 0, alive_idx[np.maximum(new_t_local, 0)], -1)
        # reset prepare when acquired target differs from old
        changed = (~keep) & (new_t >= 0) & (new_t != old)
        gi = alive_idx[changed]
        # step23 定版 (cycle_keep): 换靶不动武器循环 —— 状态/时钟/已发闩锁
        # 全保留, 只换目标。attackDuration 是武器冷却, 与目标存亡无关
        # (旧三连重置使 prep=0 远程单位在目标死亡瞬间再齐射: 暴雨 vs 爬虫墙
        # pysim 8s 机器枪扫完 384 vs oracle 30s 204 杀; 犀牛 0 挥击同源;
        # 而只保闩锁不保时钟又会把冷却拖长一整轮, 语料 -1.9pp)。
        # opts.cycle_keep=0 恢复旧行为; step28 cycle_set 白名单: 只对该
        # 集合兵种保循环 (s28 实锤: 霸主/工厂 清杂机枪病理 = 击杀→重置→
        # 瞬发; 全局开 cycle_keep 使 s24 -8 熔点翻车, 白名单化)。
        _ck = self.opts.get("cycle_keep", 0)
        if self.opts.get("cycle_set") and len(gi):
            in_set = np.isin(self.mech_id[gi], list(self._cycle_set))
        else:
            in_set = np.zeros(len(gi), dtype=bool)
        if _ck:
            in_set = np.ones(len(gi), dtype=bool)
        if len(gi):
            reset = gi[~in_set]
            self.state[reset] = PREPARE
            self.state_t[reset] = 0.0
            self.dmg_applied[reset] = False
            # 初次索敌 (IDLE) 仍需进 PREPARE 起振; 循环中的单位只换目标
            idle = gi[in_set & (self.state[gi] == IDLE)]
            if len(idle):
                self.state[idle] = PREPARE
                self.state_t[idle] = 0.0
        # step25 熔点残留: 激光 ramp (damageMultiplier 0.01→47x, 29档) 是
        # 武器热量不是目标锁 —— 换靶重置使混编高频换靶下伤害被砍到低档
        # (s25 锚批 r 0.67 flip 43/119 主因)。beam_keep=1 保热量。
        if not self.opts.get("beam_keep", 1):
            self.beam_t[gi] = 0.0
        self.target[alive_idx] = new_t
        # step24/25 骇客控制光束 (转化模型): 光束累计 hack 进度, 满阈值转化
        # (换阵营继续作战); 塔/建筑无 hack 条走普通直伤。u150 实锤: oracle
        # team0 出现 8 个从未部署的火獾 (伤害记到 p0 名下) = 转化。
        # step25 C3 (用户 comment 定版):
        #   ① hack_cur=1 (默认): 转化阈值 = 受害者当前剩余 HP (非 maxHP)
        #      —— "骇客控制的血量条>目标剩余血量则被视为控制"; 队友集火与
        #      光束协同加速转化。
        #   ② hack_par=0 (默认): 撤"目标瘫痪" —— 被光束单位可动可攻
        #      (u150 反证: 火獾边被骇边反杀 2 骇客); =1 恢复 step24 瘫痪。
        #   ③ hack_pin=1 (默认): 骇客本体 channel 定身 (用户 Q-D: 攻击时
        #      自己不能移动)。
        #   ④ hack_emp=1: 骇客带 1814 电磁干扰 → 被光束单位科技失效
        #      (复用 EMP 通道, 光束在 = 持续 disable)。
        if self.opts.get("hacker_beam", 1) and self.is_hacker.any():
            self.hacked[:] = False
            self.beaming[:] = False
            rate = float(self.opts.get("hack_rate", 2000.0))
            for i in np.where(self.is_hacker & (~self.dead))[0]:
                t = int(self.target[i])
                # step28 hack_gate: 光束只在 ATTACK/COOL 阶段运行 (PREPARE
                # 前摇期间不发波) —— review nt_14v09 "骇客前摇被明显估计少
                # 了": 旧口径持有目标即累计转化, prep/atk_dur 完全无效。
                if self.opts.get("hack_gate", 0) and self.state[i] == PREPARE:
                    continue
                if t >= 0 and not self.dead[t] and self.target[i] != i \
                        and not (self.is_tower[t] or self.is_bld[t]
                                 or self.is_device[t]):
                    # step32 T8: 抗干扰模块 blocks the whole hacker control
                    # package (beam control + conversion + 1814 disable)
                    if self._status_immune(int(t), "hacker"):
                        self._status_block_note(int(t), "hacker")
                        continue
                    self.hacked[t] = True
                    self.beaming[i] = True
                    self.hack_progress[t] += rate * RETARGET_TICKS * DT
                    thr = self.hp[t] if self.opts.get("hack_cur", 1) \
                        else self.max_hp[t]
                    if self.hack_progress[t] >= thr \
                            and self.team[t] != self.team[i]:
                        # 转化: 换阵营、满血、重置进度; 敌我矩阵按层能力重建
                        self.hack_progress[t] = 0.0
                        self.team[t] = self.team[i]
                        self.hp[t] = self.max_hp[t]
                        self.target[t] = -1
                        self.mv_target[t] = -1
                        self.state[t] = IDLE
                        self.emp_until[t] = -1.0
                        col_ok = np.where(self.is_fly,
                                          self._can_air_rows,
                                          self._can_gnd_rows)
                        self.hittable[:, t] = (self.team != self.team[t]) \
                            & col_ok
                        self.hittable[t, :] = (self.team[t] != self.team) \
                            & col_ok[t]
                    elif self.opts.get("hack_emp", 1):
                        # ④ 电磁干扰 1814: 光束在 = 科技持续失效
                        ci = int(self.card_idx[i])
                        if ci >= 0 and 1814 in (self.cards[ci].get("techs") or ()):
                            self.emp_until[t] = max(self.emp_until[t],
                                                    self.time + 0.11)
        # movement target = nearest hittable enemy regardless of range
        self.mv_target[alive_idx] = np.where(mv_local >= 0, alive_idx[np.maximum(mv_local, 0)], -1)
        # step7 queue rule (opts.queue): only the ally closest to a given
        # enemy advances toward it; the rest hold behind their own front
        # (report: symmetric moved-share, front ~45% / jammed mid ~32% /
        # clear-flank ~49%, most movers go 200m+ - units either lead a lane
        # or wait, nobody trickles past the line). Towers never block lanes.
        if self.opts.get("queue"):
            adv = np.zeros(self.n, dtype=bool)
            mvt = self.mv_target[alive_idx]
            col_of = {int(g): k for k, g in enumerate(alive_idx)}
            for t in (0, 1):
                sel = np.where((self.team[alive_idx] == t) & (~self.is_tower[alive_idx])
                               & (~self.is_bld[alive_idx]))[0]
                ut = np.where((self.team[alive_idx] == t) & (~self.is_bld[alive_idx])
                              & (mvt >= 0))[0]
                if len(sel) == 0 or len(ut) == 0:
                    continue
                cols = np.array([col_of[int(m)] for m in mvt[ut]])
                mind = d[np.ix_(sel, cols)].min(axis=0)   # closest ally per target
                lead = ut[d[ut, cols] <= mind + 1e-6]
                adv[alive_idx[lead]] = True
            self._adv_ok = adv
        # refresh separation candidate pairs (near-contact, with hysteresis
        # margin); unit-unit pairs push both sides, unit-tower pairs keep the
        # tower immovable (step7: towers are 20x20m buildings units must
        # walk around). opts.stiff_sep switches to full-overlap resolution
        # with multiple relaxation sweeps (hard collision approximation of
        # the game's RVO crowd jam: front line engages, rear cannot pass).
        # step18 sep_radius: chaff (爬虫10/尖牙9) rows may shrink here for
        # the sep_chaff="half" arm - separation only, targeting distances
        # keep the true radius.
        chaff_mode = str(self.opts.get("sep_chaff", "off"))
        sep_r = self.radius
        if chaff_mode == "half":
            chaff_rows = np.isin(self.mech_id[alive_idx], (9, 10))
            sep_r = self.radius.copy()
            sep_r[alive_idx[chaff_rows]] *= 0.5
        # step25 C1 停射弧形: 远程单位 (range>=60 非近战) 停位后分离半径
        # xarc_sep —— 多单位对同一目标自然展开成弧 (野马集团被 AoE 集中
        # 歼灭的主因候选); 1.0 = 关。作用于分离对构建 (sep_r 仅影响推开)。
        arc_sep = float(self.opts.get("arc_sep", 1.0) or 1.0)
        if arc_sep != 1.0:
            if chaff_mode != "half":
                sep_r = self.radius.copy()
            arc_rows = alive_idx[(~self.is_melee[alive_idx])
                                 & (self.range[alive_idx] >= 60)]
            sep_r[arc_rows] *= arc_sep
        rm = (sep_r[alive_idx][:, None] + sep_r[alive_idx][None, :]).astype(d.dtype) * 2.0
        iu = np.triu_indices_from(d, k=1)
        sel = d[iu] < rm[iu]
        # static objects never push each other apart (towers AND buildings);
        # unit-static pairs stay in the set - the unit gets pushed around
        imm = tmask | self.is_bld[alive_idx]
        sel &= ~(imm[iu[0]] & imm[iu[1]])
        # step18 T1a (default on): fliers and walkers never push each other
        # (user: 空中单位不会被地面单位推动 - ground pushes ground, air
        # pushes air). opts.air_sep=1 restores the old mixed set.
        if not self.opts.get("air_sep", 0):
            fly = self.is_fly[alive_idx]
            sel &= ~(fly[iu[0]] ^ fly[iu[1]])
        # step18 T1b (default on): enemy unit-unit pairs do not push (user:
        # 我方单位不会推动对方单位). Statics are exempt - an enemy tower or
        # wall still shoves its own lane geometry onto attackers.
        if not self.opts.get("sep_foe", 0):
            foe_pair = (self.team[alive_idx][iu[0]] != self.team[alive_idx][iu[1]]) \
                & (~imm[iu[0]]) & (~imm[iu[1]])
            sel &= ~foe_pair
        # step18 T1c: crawler/fang collision reduction arms
        if chaff_mode in ("out", "pair"):
            chaff = np.isin(self.mech_id[alive_idx], (9, 10))
            if chaff_mode == "out":
                sel &= ~(chaff[iu[0]] | chaff[iu[1]])
            else:   # "pair": only chaff*chaff pairs leave the set
                sel &= ~(chaff[iu[0]] & chaff[iu[1]])
        # step8-B: devices are intangible to movement (barriers are force
        # fields holding the units they protect, not walls to push out of)
        if np.any(dmask := self.is_device[alive_idx]):
            sel &= ~(dmask[iu[0]] | dmask[iu[1]])
        self._sep_i = alive_idx[iu[0][sel]]
        self._sep_j = alive_idx[iu[1][sel]]
        # sep_radius rows cached for _separate (half-arm symmetry)
        self._sep_r = sep_r

    def _validate_targets(self):
        t = self.target
        has = (t >= 0) & ~self.dead
        if not np.any(has):
            self.target[:] = np.where(self.dead, self.target, -1)
            return
        idx = np.where(has)[0]
        tt = t[idx]
        dx = self.x[tt] - self.x[idx]
        dy = self.y[tt] - self.y[idx]
        dist = np.sqrt(dx * dx + dy * dy) - self.radius[tt] - self.radius[idx]
        # step27 近战贴脸修复: 表面距离可为负 (体积重叠), 旧代码
        # `dist >= min_rng` 在 min_rng=0 时把一切贴脸目标判"太近"逐 tick
        # 清锁 → 近战单位永远 IDLE 打不还手 (s27 nt_05v10 犀牛对爬虫
        # 0 伤害实锤)。盲区语义按圆心距离判定, 且仅对 min_rng>0 的攻城
        # 单位生效 (与 _full_target_pass 的门控对齐)。opts.melee_blind=1
        # 恢复旧行为 (A/B 对照)。
        if self.opts.get("melee_blind", 0):
            ok = (~self.dead[tt]) & (dist <= self.range[idx]) \
                & (dist >= self.min_rng[idx])
        else:
            center = dist + self.radius[tt] + self.radius[idx]
            ok = (~self.dead[tt]) & (dist <= self.range[idx]) \
                & (((self.min_rng[idx] <= 0)) | (center >= self.min_rng[idx]))
        # step25 C5 挥击锁靶: 慢重挥击单位 (swing_pin) 前摇期间不弃锁
        # (出伤点严格在 attackPoint; 目标离开射程 = 发射时刻落空, 见
        # _fire_one 的 whiff 检查), 后摇照常。
        if self.opts.get("swing_pin", 1) and np.any(self.swing_pin[idx]):
            locked = self.swing_pin[idx] & (self.state[idx] == ATTACK) \
                & (~self.dmg_applied[idx])
            ok |= locked
        bad = idx[~ok]
        if len(bad):
            self.target[bad] = -1
            self.state[bad] = IDLE

    # ---------- combat ----------
    def _fire_one(self, i):
        self.total_attacks += 1
        s = self.skill_of.get(self.skill_ref[i])
        t = int(self.target[i])
        if s is None or t < 0:
            return
        # step29 蜘蛛雷不走正常开火 (爆炸技能 atk_dur≈0 会机枪化);
        # 接敌自爆统一在 step() 的 mine 块处理
        if self.is_mine[i]:
            return
        # step24 骇客控制光束: 对兵目标不结算伤害 (瘫痪在 _full_target_pass
        # 的 hacked 掩码里); 对塔/建筑走普通直伤 (oracle: 塔杀 3400 实锤)
        if self.opts.get("hacker_beam", 1) and self.is_hacker[i] \
                and not (self.is_tower[t] or self.is_bld[t] or self.is_device[t]):
            return
        # step19 旋风斩 1109: >=2 foes within 25m -> 1.4x attack-damage AoE
        # (35m around self) replaces the normal swing
        if self.whirl[i]:
            foes = np.where((~self.dead) & (self.team != self.team[i])
                            & self.hittable[i, :])[0]
            if len(foes):
                dist = np.sqrt((self.x[foes] - self.x[i]) ** 2
                               + (self.y[foes] - self.y[i]) ** 2) \
                    - self.radius[foes] - self.radius[i]
                if np.count_nonzero(dist <= 25.0) >= 2:
                    wd = self.base_dmg[i] * self.dmg_rate[i] * self._tech_eff[i] \
                        * self._dmg_fac[i] * 1.4
                    for f in foes[dist <= 35.0]:
                        self._deal_damage(i, int(f), self.x[int(f)], self.y[int(f)],
                                          wd, 0.0)
                    return
        # step19 隐形: attacking breaks cloak (reveals the unit)
        if self.cloaked[i]:
            self.cloaked[i] = False

        def layer_dmg(base, tgt):
            # step16 防空专精/对地专精: layer damage rate vs the target's layer
            return base * (1.0 + (self.air_dmg[i] if self.is_fly[tgt]
                                  else self.gnd_dmg[i]))

        dmg = self.base_dmg[i] * self.dmg_rate[i] * self._tech_eff[i] * self._dmg_fac[i]
        if s.damage:
            lv = min(int(self.level[i]), len(s.damage))
            dmg = s.damage[lv - 1] * self.level[i] * self.dmg_rate[i] * self._tech_eff[i] * self._dmg_fac[i]
        if s.type == "laserSkillDatas" and s.damage_multiplier:
            step = max(0.01, s.attack_duration)
            # step29 能量散射 (scatter5): n 条独立射线, 每条自己的 (目标,
            # 升温步数), 伤害 = 单射线档位 × frac —— 优先锁不同目标 (按
            # 距离排序), 射程内目标 < n 时后面的射线叠加到近目标上。
            # beam_pair 口径 (换目标重升温) 对每条射线独立生效。
            if self.scatter_n[i] > 0:
                nray = int(self.scatter_n[i])
                frac = float(self.scatter_frac[i])
                rays = [t]
                foes = np.where((~self.dead) & (self.team != self.team[i])
                                & self.hittable[i, :])[0]
                if len(foes):
                    dist = np.sqrt((self.x[foes] - self.x[i]) ** 2
                                   + (self.y[foes] - self.y[i]) ** 2) \
                        - self.radius[foes] - self.radius[i]
                    ok = (dist <= self.range[i]) & (foes != t)
                    if self.min_rng[i] > 0:
                        ok &= dist >= self.min_rng[i]
                    cand = sorted(zip(dist[ok].tolist(), foes[ok].tolist()))
                    rays += [int(f) for _, f in cand[:nray - 1]]
                while len(rays) < nray:
                    rays.append(t)
                mults = s.damage_multiplier
                for ri in range(nray):
                    f = int(rays[ri])
                    if self.beam_tgt5[i, ri] != f:
                        self.beam_tgt5[i, ri] = f
                        self.beam_ramp5[i, ri] = 0.0
                    idx = min(len(mults) - 1,
                              int(self.beam_ramp5[i, ri] / step))
                    self.beam_ramp5[i, ri] += step
                    df = layer_dmg(dmg, f) * mults[idx] * frac
                    self._deal_damage(i, f, self.x[f], self.y[f],
                                      df, self.splash[i])
                self.total_attacks += 1
                return
            # step27 beam_pair: ramp 按"目标对"累计 —— 换目标从低档重新
            # 升温 (review t1107_0 / Q-B: 武器热量 beam_t 保留, ramp 归零,
            # 两者作用在不同量上)。短暂失锁后重新索上同一目标不重置
            # (A/B: 连失锁也重置 → 熔点 r 0.67 过弱, s24 -4 / s25 -3)
            if self.opts.get("beam_pair", 0):
                if int(self.beam_tgt[i]) != t:
                    self.beam_tgt[i] = t
                    self.beam_ramp[i] = 0.0
                idx = min(len(s.damage_multiplier) - 1,
                          int(self.beam_ramp[i] / step))
                self.beam_ramp[i] += step
            else:
                idx = min(len(s.damage_multiplier) - 1, int(self.beam_t[i] / step))
            dmg *= s.damage_multiplier[idx]
            self.beam_t[i] += step
            # step26 P1 弧光蓄能 CD 假说 (用户: 蓄能会影响 CD): 充能档
            # 同时缩短攻击间隔 —— 充能越高打得越快 (cool_t 缩到
            # base/mult, 下限 beam_cd_min×base); A/B beam_cd=1。
            if self.opts.get("beam_cd", 0):
                mlt = max(0.05, float(s.damage_multiplier[idx]))
                floor_ = self._cool0[i] * float(self.opts.get("beam_cd_min", 0.25))
                self.cool_t[i] = max(floor_, self._cool0[i] / mlt)
        # step23 T4 深渊 sweepSkillDatas: 扫掠攻击 —— 向目标方向扫出长×宽
        # 矩形 (29001: 160×10, damageTimes=20), 域内每个敌人吃一发全额
        # (damageTimes 为扫掠 tick 数; 小单位一发即死, 大单位多 tick ——
        # 先按 1 发/受害者/次扫掠建模, A/B/C 对拍后校准)。opts.sweep_atk=0 关。
        # step25 C2 (用户 comment + Q-C 三档定版): "大型单位在游戏中看起来
        # 会被扫到成倍伤害" "剑齿虎和狼蛛也会吃亏" —— 受害者按体积三档
        # 吃多发 (小 1x / 中 2x / 大 3x, 阈值 sweep_t1/t2 按半径), 上限
        # damageTimes; 档位倍率 oracle 对拍可调 (sweep_m1/m2/m3)。
        if s.type == "sweepSkillDatas" and self.opts.get("sweep_atk", 1) \
                and s.sweep_length > 0:
            ang = math.atan2(self.y[t] - self.y[i], self.x[t] - self.x[i])
            ca, sa = math.cos(ang), math.sin(ang)
            foes = np.where((~self.dead) & (self.team != self.team[i])
                            & self.hittable[i, :])[0]
            dealt = 0.0
            if len(foes):
                rx = self.x[foes] - self.x[i]
                ry = self.y[foes] - self.y[i]
                along = rx * ca + ry * sa
                lateral = np.abs(-rx * sa + ry * ca)
                hitm = ((along >= -self.radius[foes])
                        & (along <= s.sweep_length + self.radius[foes])
                        & (lateral <= s.sweep_width / 2.0 + self.radius[foes]))
                if self.opts.get("sweep_tier", 1):
                    t1 = float(self.opts.get("sweep_t1", 8.0))
                    t2 = float(self.opts.get("sweep_t2", 16.0))
                    m1 = float(self.opts.get("sweep_m1", 1.0))
                    m2 = float(self.opts.get("sweep_m2", 2.0))
                    m3 = float(self.opts.get("sweep_m3", 3.0))
                    cap = max(1, int(s.damage_times or 20))
                    for f in foes[hitm]:
                        r_ = float(self.radius[f])
                        mult = m1 if r_ < t1 else (m2 if r_ < t2 else m3)
                        mult = min(mult, cap)
                        df = layer_dmg(dmg, int(f)) * mult
                        self._deal_damage(i, int(f), self.x[int(f)], self.y[int(f)],
                                          df, 0.0)
                        dealt += df
                else:
                    for f in foes[hitm]:
                        df = layer_dmg(dmg, int(f))
                        self._deal_damage(i, int(f), self.x[int(f)], self.y[int(f)],
                                          df, 0.0)
                        dealt += df
            # 深渊无 multi-gun/次溅/吸血尾段; 若后续 sweep 单位需要, 再并入
            if self.lifesteal[i] > 0 and dealt > 0 and not self.dead[i]:
                self.hp[i] = min(self.max_hp[i], self.hp[i] + dealt * self.lifesteal[i])
            return
        # step25 C5 前后摇 (用户 comment "狂蝎的前后摇都挺大的", 推广到全部
        # atk_dur>=swing_min 的慢重挥击单位): 弹着定死在发射时刻落点 ——
        # 不追踪, 飞行/前摇期间目标走出的位移 = 躲避; 前摇期间目标已离开
        # 射程 = 本轮落空 (whiff, 不发射)。齐射 (pc>1) 保持弹量: dmg/pc 每
        # 发 (barrage_split 口径), 首弹带原目标 (近失判定), 其余散射
        # random_targetRange 走真实溅射。opts.swing_pin=0 关。
        if self.opts.get("swing_pin", 1) and self.swing_pin[i] \
                and int(self.multi_n[i]) <= 0:
            d = math.hypot(self.x[t] - self.x[i], self.y[t] - self.y[i]) \
                - self.radius[t] - self.radius[i]
            if d > self.range[i] + 1e-3:
                return                       # 落空: 前摇期间目标脱离射程
            tx, ty = float(self.x[t]), float(self.y[t])
            df = layer_dmg(dmg, t)
            pc = int(getattr(s, "projectile_count", 1) or 1)
            # step28b swing_fix: 主路径的齐射守恒口径带入 swing 分支 ——
            # (a) pc_map 覆写 (pc_set 霸主 2 发原只作用于主路径, swing 分支
            #     仍 4 发散射, st176 实测 4x1185); (b) w_count>1 且 pc>1 的
            #     齐射单位 (霸主/暴雨 2 武器) 齐射总量 = df x w_count, 旧分支
            #     只打单武器量 (暴雨 96.5/发 = 386/4, 表值 2 武器应 772/轮)。
            #     pc=1 单位 (剑齿虎等) 不动 —— s26 剑齿虎 r 3.7 已过强。
            if self.opts.get("swing_fix", 0):
                if self.opts.get("pc_set") and not hasattr(self, "_pc_map"):
                    m_ = {}
                    for kv in str(self.opts["pc_set"]).split(","):
                        if ":" in kv:
                            k_, v_ = kv.split(":", 1)
                            m_[int(k_)] = int(float(v_))
                    self._pc_map = m_
                if getattr(self, "_pc_map", None):
                    pc = self._pc_map.get(int(self.mech_id[i]), pc)
                if pc > 1 and int(self.w_count[i]) > 1:
                    df = df * float(self.w_count[i])
                # step29 wc_set: swing 分支 (pc=1) 也乘武器数 (泰山对大
                # 目标 DPS 对账; _wc_mul 默认 1 不影响其他兵种)
                if self._wc_mul[i] > 1.0:
                    df = df * float(self._wc_mul[i])
            spl = self.splash[i]
            if pc > 1 and self.opts.get("barrage", 1) and spl > 0:
                shell = df / pc \
                    if int(self.opts.get("barrage_split", 1) or 0) == 1 else df
            else:
                shell = df
            if self.bullet_spd[i] > 0:
                self._spawn_projectile(i, t, shell, spl, tx=tx, ty=ty)
                for _ in range(pc - 1):
                    rng = getattr(self, "_barrage_rng", None)
                    if rng is None:
                        import numpy as _np
                        rng = self._barrage_rng = _np.random.RandomState(1401)
                    ang = rng.uniform(0.0, 2.0 * math.pi)
                    rad = rng.uniform(0.0, max(1.0, s.random_target_range))
                    self._spawn_projectile(i, -1, shell, spl,
                                           tx=tx + math.cos(ang) * rad,
                                           ty=ty + math.sin(ang) * rad)
                dealt = shell * pc
            else:
                self._deal_damage(i, -1, tx, ty, df, max(spl, 1.0))
                dealt = df
            if self.lifesteal[i] > 0 and not self.dead[i]:
                self.hp[i] = min(self.max_hp[i], self.hp[i] + dealt * self.lifesteal[i])
            return
        dealt = 0.0
        multi = int(self.multi_n[i])
        if multi > 0:
            # step16 multi-target volley: each barrel/shot tracks its own
            # target (台风/霸主 twin pods, 双发 countIncrease). Volley damage
            # is conserved: shots = max(w_count, 1+multi) spread over the
            # targets in range; a lone target soaks the whole volley (the
            # twin pods do not idle half the damage).
            shots = max(int(self.w_count[i]), 1 + multi)
            targets = [t]
            foes = np.where((~self.dead) & (self.team != self.team[i]))[0]
            if len(foes):
                dx = self.x[foes] - self.x[i]
                dy = self.y[foes] - self.y[i]
                dist = np.sqrt(dx * dx + dy * dy) - self.radius[foes] - self.radius[i]
                ok = (dist <= self.range[i]) & (foes != t) & self.hittable[i, foes]
                if self.min_rng[i] > 0:
                    ok &= dist >= self.min_rng[i]
                cand = sorted(zip(dist[ok].tolist(), foes[ok].tolist()))
                targets += [int(f) for _, f in cand[:multi]]
            # step29 no_stack_set: 该集合兵种的齐射不向同一目标叠加 —— 每个
            # 目标至多吃 1 发, 射程内目标不足时多发直接打空 (st293/st354
            # 用户定版 Q-E: 雷霆 3 目标各加载 1 倍攻击力, 2 目标时总输出 =
            # 2 倍, 独目标不被 *3; 推翻 step18 thunder3 的守恒口径)。
            if self.opts.get("no_stack_set") \
                    and not hasattr(self, "_no_stack_set"):
                self._no_stack_set = {int(x) for x in
                                      str(self.opts["no_stack_set"])
                                      .replace(";", ",").split(",")
                                      if x.strip()}
            if getattr(self, "_no_stack_set", None) \
                    and int(self.mech_id[i]) in self._no_stack_set:
                shots = min(shots, len(targets))
            # step29 inc_stack: countIncrease (双发) 的多发全部打同一锁定
            # 目标 (st957 用户实锤: 剑齿虎双发只锁定 1 个单位) —— 与
            # no_stack 相反, 台风 dual_set 的 twin pods 不在此类
            elif self.opts.get("inc_stack", 0) and self.inc_multi[i]:
                targets = [t]
            per_split = self.opts.get("split_shots", 1)
            if per_split:
                # step18 T10a: every shot queues its own damage event,
                # round-robin over the volley targets (双发 two near-tick
                # hits, 雷霆 3 shots on 3 units - armor/barrier soak each
                # hit; total damage identical to the summed event)
                for si in range(shots):
                    f = targets[si % len(targets)]
                    df = layer_dmg(dmg, f)
                    self._deal_damage(i, f, self.x[f], self.y[f], df, self.splash[i])
                    dealt += df
            else:
                per = dmg * shots / len(targets)
                for f in targets:
                    df = layer_dmg(per, f)
                    self._deal_damage(i, f, self.x[f], self.y[f], df, self.splash[i])
                    dealt += df
        else:
            base_one = layer_dmg(dmg, t)     # per-barrel damage vs this target
            # step18 T10a: twin barrels = two hits (own armor/barrier soak
            # each); barrage units (projectileCount>1) keep the summed shot
            # so the random-shell damage stays calibrated
            nsplit = int(self.w_count[i]) \
                if (self.opts.get("split_shots", 1) and self.w_count[i] > 1
                    and getattr(s, "projectile_count", 1) <= 1) else 1
            if nsplit > 1:
                # step28 volley_splash_set: 该集合兵种一次齐射只结算一发
                # 溅射 (Q-C 火神: 两把武器射向同一位置 = 单落点溅射一次;
                # 后发 splash=0, 直伤照常逐发)
                if self.opts.get("volley_splash_set") \
                        and not hasattr(self, "_vsplash_set"):
                    s_ = str(self.opts["volley_splash_set"])
                    self._vsplash_set = {int(x) for x in
                                         s_.replace(";", ",").split(",") if x.strip()}
                _single_spl = bool(getattr(self, "_vsplash_set", None)) \
                    and int(self.mech_id[i]) in self._vsplash_set
                for si in range(nsplit):
                    spl_si = self.splash[i] if (not _single_spl or si == 0) else 0.0
                    dealt += base_one
                    if self.bullet_spd[i] > 0:
                        self._spawn_projectile(i, t, base_one, spl_si)
                    else:
                        self._deal_damage(i, t, self.x[t], self.y[t],
                                          base_one, spl_si)
                dmg = base_one * nsplit
            else:
                dmg = base_one * self.w_count[i]
                # step14 unit-skill: multi-projectile barrage (鬼鳐/暴雨/先知/霸主 -
                # projectileCount>1 + randomTargetRange): extra shells land on random
                # points around the target and splash there (A/B r1-2 +0.8pp,
                # r1-3 +0.3pp net; opts.barrage=0 restores the single-shot behavior)
                # step19 T11 barrage_split: the volley may no longer multiply
                # by shell count - 1 = one volley shared over all shells (per
                # shell dmg/N), 2 = per-barrel shells (dmg/w_count each),
                # 0 = legacy full volley per shell (T14 overkill source)
                pc = int(getattr(s, "projectile_count", 1) or 1)
                # step28 pc_set: projectileCount 覆写 (霸主 Q-D: 用户口径
                # "应该只有2发" vs 表值 4, oracle 对拍裁决)
                if self.opts.get("pc_set") and not hasattr(self, "_pc_map"):
                    m_ = {}
                    for kv in str(self.opts["pc_set"]).split(","):
                        if ":" in kv:
                            k_, v_ = kv.split(":", 1)
                            m_[int(k_)] = int(float(v_))
                    self._pc_map = m_
                if getattr(self, "_pc_map", None):
                    pc = self._pc_map.get(int(self.mech_id[i]), pc)
                split = 0
                if pc > 1 and self.opts.get("barrage", 1) and self.splash[i] > 0:
                    split = int(self.opts.get("barrage_split", 1) or 0)
                if split == 1:
                    shell = dmg / pc
                elif split == 2:
                    shell = base_one
                else:
                    shell = dmg
                dmg = shell
                # step26 P1 先知同靶 (Q-E: "不会散布到两个目标, 有先知喜欢
                # 打尸体的说法"): 齐射 pc 发全部打当前目标 (每发 shell,
                # 追踪弹), 不散射 —— opts.barrage_same; 与 bb/bm 分支互斥
                # (same 优先)。
                _same_done = False
                if split and self._barrage_same_set \
                        and int(self.mech_id[i]) in self._barrage_same_set \
                        and pc > 1:
                    for _ in range(pc):
                        if self.bullet_spd[i] > 0:
                            self._spawn_projectile(i, t, shell, self.splash[i])
                        else:
                            self._deal_damage(i, t, self.x[t], self.y[t],
                                              shell, self.splash[i])
                    dealt += shell * pc
                    dmg = shell * pc
                    _same_done = True
                # step23 T3 齐射弹道化 (暴雨 r=2.20 头号病灶; oracle 暴雨 vs 爬虫墙
                # 256/384 杀 vs pysim 384/384 + 爬虫零伤害): 真实语义 = 全部
                # projectileCount 发导弹在发射时刻定死落点 (目标位置+均匀散布
                # randomTargetRange), 以 bulletSpeed 飞行 (暴雨 60m/s×180m≈3s),
                # 落地溅射 —— 移动目标可走出杀伤区, 慢弹追踪/瞬爆都高估吞吐。
                # barrage_ballistic=1: 首发直瞄落点+其余散射; =2: 全部散射;
                # 0 (默认关闭): 维持 step19 行为 (散射瞬爆+首发追踪)。
                bb = int(self.opts.get("barrage_ballistic", 0) or 0)
                # step23 T3 模型B 齐射内目标分散 (oracle 定标 t3_storm_cw:
                # 204杀×263.0 精确零浪费 → 真实齐射每发壳≈单目标命中,
                # 一轮 pc 发分散到 pc 个最近目标, 溅射对密集小目标不生效):
                # barrage_multi=1 = 多目标弹+保留溅射; =2 = 多目标弹+溅射置0
                # (单目标发); 与 barrage_ballistic 互斥 (bm 优先)。
                bm = int(self.opts.get("barrage_multi", 0) or 0)
                if split and bm and self.bullet_spd[i] > 0 and not _same_done:
                    foes = np.where((~self.dead) & (self.team != self.team[i])
                                    & self.hittable[i, :])[0]
                    spl = self.splash[i] if bm == 1 else 0.0
                    if len(foes):
                        dist = np.sqrt((self.x[foes] - self.x[i]) ** 2
                                       + (self.y[foes] - self.y[i]) ** 2) \
                            - self.radius[foes] - self.radius[i]
                        ok = (dist <= self.range[i]) & (foes != t)
                        if self.min_rng[i] > 0:
                            ok &= dist >= self.min_rng[i]
                        cand = sorted(zip(dist[ok], foes[ok]))
                        targets = [t] + [int(f) for _, f in cand[:pc - 1]]
                    else:
                        targets = [t]
                    for f in targets:
                        self._spawn_projectile(i, int(f), shell, spl)
                    dealt += shell * len(targets)
                elif split and bb and self.bullet_spd[i] > 0 and not _same_done:
                    rng = getattr(self, "_barrage_rng", None)
                    if rng is None:
                        import numpy as _np
                        rng = self._barrage_rng = _np.random.RandomState(1401)
                    # step23 T3 lead: 落点按目标速度×飞行时间前置 (慢弹 3s
                    # 飞行中爬虫走 48m, 不前置则整轮齐射落在空地上)
                    bx0, by0 = self.x[t], self.y[t]
                    if self.opts.get("barrage_lead", 1) and t >= 0 \
                            and hasattr(self, "vx"):
                        d_ = math.hypot(bx0 - self.x[i], by0 - self.y[i])
                        ticks_ = d_ / max(self.bullet_spd[i], 1e-6) / DT
                        bx0 += self.vx[t] * ticks_
                        by0 += self.vy[t] * ticks_
                    for si in range(pc):
                        px, py = bx0, by0
                        if si >= 1 or bb >= 2:
                            ang = rng.uniform(0.0, 2.0 * math.pi)
                            rad = rng.uniform(0.0, max(1.0, s.random_target_range))
                            px += math.cos(ang) * rad
                            py += math.sin(ang) * rad
                        self._spawn_projectile(i, -1, shell, self.splash[i], tx=px, ty=py)
                    dealt += shell * pc
                elif split and not _same_done:
                    for _ in range(pc - 1):
                        rng = getattr(self, "_barrage_rng", None)
                        if rng is None:
                            import numpy as _np
                            rng = self._barrage_rng = _np.random.RandomState(1401)
                        ang = rng.uniform(0.0, 2.0 * math.pi)
                        rad = rng.uniform(0.0, max(1.0, s.random_target_range))
                        px = self.x[t] + math.cos(ang) * rad
                        py = self.y[t] + math.sin(ang) * rad
                        self._deal_damage(i, -1, px, py, shell, self.splash[i])
                    dealt += shell * pc
                    if self.bullet_spd[i] > 0:
                        self._spawn_projectile(i, t, dmg, self.splash[i])
                    else:
                        self._deal_damage(i, t, self.x[t], self.y[t], dmg, self.splash[i])
                else:
                    dealt += dmg
                    if pc > 1 and self.opts.get("barrage", 1) \
                            and self.splash[i] > 0:
                        for _ in range(pc - 1):
                            rng = getattr(self, "_barrage_rng", None)
                            if rng is None:
                                import numpy as _np
                                rng = self._barrage_rng = _np.random.RandomState(1401)
                            ang = rng.uniform(0.0, 2.0 * math.pi)
                            rad = rng.uniform(0.0, max(1.0, s.random_target_range))
                            px = self.x[t] + math.cos(ang) * rad
                            py = self.y[t] + math.sin(ang) * rad
                            self._deal_damage(i, -1, px, py, dmg, self.splash[i])
                    if self.bullet_spd[i] > 0:
                        self._spawn_projectile(i, t, dmg, self.splash[i])
                    else:
                        self._deal_damage(i, t, self.x[t], self.y[t], dmg, self.splash[i])
        # step27 4127 电离: 本次攻击命中的每个直接目标额外吃"当前生命值 x
        # add_hp"伤害 (不溅射; 电离只有雷霆, 无 swing/sweep 提前 return 路径)
        if self.add_hp[i] > 0:
            hit_rows = [int(f) for f in targets] if multi > 0 else [t]
            for f in hit_rows:
                if f >= 0 and not self.dead[f]:
                    extra = self.add_hp[i] * self.hp[f]
                    self._deal_damage(i, f, self.x[f], self.y[f], extra, 0.0)
                    dealt += extra
        # step15 multi-gun: extra guns each pick their own nearest enemy in
        # range (Wraith 4 cannons on 4 distinct targets, splash per impact)
        if self.gun_cnt[i] > 1:
            foes = np.where((~self.dead) & (self.team != self.team[i]))[0]
            if len(foes):
                dx = self.x[foes] - self.x[i]
                dy = self.y[foes] - self.y[i]
                dist = np.sqrt(dx * dx + dy * dy) - self.radius[foes] - self.radius[i]
                ok = (dist <= self.range[i]) & (foes != t) & self.hittable[i, foes]
                if self.min_rng[i] > 0:
                    ok &= dist >= self.min_rng[i]
                cand = sorted(zip(dist[ok], foes[ok]))
                for _, f in cand[:int(self.gun_cnt[i]) - 1]:
                    df = layer_dmg(dmg, int(f)) if multi <= 0 else layer_dmg(dmg / max(1.0, self.w_count[i]), int(f))
                    self._deal_damage(i, int(f), self.x[f], self.y[f],
                                      df, self.splash[i])
        # step16 震荡波: on-hit secondary splash around the primary target
        if self.sec_dmg[i] > 0:
            self._deal_damage(i, -1, self.x[t], self.y[t],
                              self.sec_dmg[i], self.sec_rng[i])
        # step16 能量汲取: heal a fraction of the damage dealt this shot
        if self.lifesteal[i] > 0 and dealt > 0 and not self.dead[i]:
            self.hp[i] = min(self.max_hp[i],
                             self.hp[i] + dealt * self.lifesteal[i])

    def _spawn_projectile(self, src, tgt, dmg, splash, tx=None, ty=None):
        if self.p_n >= self._p_cap:
            grow = self._p_cap * 2
            for a in ("p_x", "p_y", "p_tx", "p_ty", "p_speed", "p_dmg",
                      "p_splash", "p_hp", "p_home"):
                cur = getattr(self, a)
                setattr(self, a, np.concatenate([cur, np.ones(grow - len(cur))
                                                 if a == "p_home" else np.zeros(grow - len(cur))]))
            for a in ("p_src", "p_tgt"):
                cur = getattr(self, a)
                setattr(self, a, np.concatenate([cur, np.zeros(grow - len(cur), dtype=np.int32)]))
            self._p_cap = grow
        k = self.p_n
        self.p_x[k] = self.x[src]; self.p_y[k] = self.y[src]
        if tx is not None:
            # step23 T3 定点弹: 落点=发射时定死 (齐射散射), tgt=-1 走溅射结算;
            # step25 C5 pinned 模式 (tgt>=0): 落点定死但保留原目标 —— 落地时
            # 原目标仍在落点半径+1 内 = 直伤 (带 on-hit rider), 已走出 = 落空
            self.p_tx[k] = tx; self.p_ty[k] = ty
            self.p_home[k] = 0.0
            self.p_tgt[k] = tgt if (tgt is not None and tgt >= 0) else -1
        else:
            self.p_tx[k] = self.x[tgt]; self.p_ty[k] = self.y[tgt]
            self.p_home[k] = 1.0; self.p_tgt[k] = tgt
        self.p_speed[k] = self.bullet_spd[src]; self.p_dmg[k] = dmg
        self.p_splash[k] = splash
        self.p_hp[k] = self.proj_hp[src]
        self.p_src[k] = src
        self.p_n = k + 1

    def _deal_damage(self, src, tgt, px, py, dmg, splash):
        # queue damage events; applied in batch at end of tick
        if splash > 0:
            enemies = np.where((~self.dead) & (self.team != self.team[src])
                               # hidden MAGNETS are untargetable by splash;
                               # walls/cannons soak splash normally
                               & ~(self.is_bld & (self.mech_id == BLD_MAGNET)
                                   & (self.bld_state == 0)))[0]
            if len(enemies):
                dx = self.x[enemies] - px
                dy = self.y[enemies] - py
                dist = np.sqrt(dx * dx + dy * dy)
                hitmask = dist - self.radius[enemies] <= splash
                # step28 splash_decay: 溅射伤害线性衰减 (爆心全额 → 半径边
                # 缘 0); 0 = 旧口径全额。s28 实锤: 战争工厂单发清密集爬虫
                # ~9 杀 vs oracle ~4.4 杀 (全额溅射使对群 DPS 虚高 ~2x)。
                # 建筑炮的溅射半径已由 calib v2 单独减半标定, 不叠加衰减。
                dec = float(self.opts.get("splash_decay", 0) or 0)
                if dec > 0 and src >= 0 and self.is_bld[src]:
                    dec = 0.0
                for dv, v in zip(dist[hitmask], enemies[hitmask]):
                    dm = dmg
                    if dec > 0:
                        d_rel = max(0.0, dv - self.radius[v]) \
                            / max(splash, 1e-9)
                        dm = dmg * max(0.0, 1.0 - dec * d_rel)
                    self._ev_victim.append(int(v)); self._ev_dmg.append(dm)
                    self._ev_killer.append(int(src))
        elif tgt >= 0 and not self.dead[tgt]:
            self._ev_victim.append(int(tgt)); self._ev_dmg.append(dmg)
            self._ev_killer.append(int(src))
            # step19 on-hit rider effects (direct hits only, not splash):
            # 电磁弹 disable, 引燃 pct-burn, fireIntensify ground fire
            # step5 T8: photon immunizes EMP + 引燃 (QA-4 user ruling)
            # step32 T3/T8: equipment immunity (光子涂层/抗干扰) blocks the
            # same way at the apply entry point; photon keeps precedence.
            if src >= 0:
                _ph = self.photon_until[tgt] > self.time
                _eq_emp = (not _ph) and self._status_immune(int(tgt), "emp")
                _eq_burn = (not _ph) and self._status_immune(int(tgt), "burn")
                if _eq_emp:
                    self._status_block_note(int(tgt), "emp")
                if _eq_burn:
                    self._status_block_note(int(tgt), "burn")
                if not _ph and not _eq_emp and self.emp_dur[src] > 0:
                    self.emp_until[tgt] = self.time + self.emp_dur[src]
                if not _ph and not _eq_burn and self.ignite_atk[src] > 0:
                    self.burn_pct_until[tgt] = self.time + 2.0
                    self.burn_pct_rate[tgt] = self.ignite_atk[src]
                fa = self.fire_atk[src]
                if fa is not None:
                    dps, rad, life = fa
                    self._burns.append([self.team[src], float(px), float(py),
                                        dps, rad, self.time + life])

    def _fire_strike(self, team, x, y, dmg, splash, t, ff=False, bypass=False):
        # step8-B: area strike at a fixed point, killerless (no exp);
        # units only - towers, devices and buildings are not strike targets.
        # step4 P1: ff=True hits BOTH teams (轨道轰炸/核弹 friendly fire,
        # QA#6); bypass=True marks the queued events so _apply_damage skips
        # barrier absorption (轨道标枪).
        foes = np.where((~self.dead)
                        & ((self.team != team) | bool(ff))
                        & (~self.is_tower) & (~self.is_device)
                        & (~self.is_bld))[0]
        if len(foes):
            dx = self.x[foes] - x
            dy = self.y[foes] - y
            hitmask = np.sqrt(dx * dx + dy * dy) - self.radius[foes] <= splash
            for v in foes[hitmask]:
                self._ev_victim.append(int(v)); self._ev_dmg.append(dmg)
                self._ev_killer.append(-1)
                if bypass:
                    if not hasattr(self, "_bypass_ev_idx"):
                        self._bypass_ev_idx = set()
                    self._bypass_ev_idx.add(len(self._ev_victim) - 1)
        if self.trace_enabled:
            self.trace.append("E|%.2f|skill|%d|strike_hit|%.0f,%.0f" % (self.time, team, x, y))

    def _update_projectiles(self):
        if self.p_n == 0:
            return
        m = self.p_n
        # step21 T2 导弹拦截 v2 (用户口径, 推翻 step20 的概率消散模型):
        # 可拦截导弹 (canBeIntercept ⟺ maxLife>0) 有 HP; 拦截命中扣
        # attackNum, 落地前打空 (hp≤0) 则弹道消散不掉伤害。命中率起始
        # judgmentProbability, 每次尝试连拦 decline (下限 lowerLimit), 空闲
        # 按 rise/riseInterval 回复 = "过热"。weaponCount = 每轮并行拦截数。
        # 战场技能 (strike) 不是弹道, 不可拦截 (表述一致)。
        intercepted = None
        if self.p_n and np.any(self.icept_max > 0):
            intercepted = np.zeros(m, dtype=bool)
            for i in np.where((self.icept_max > 0) & (~self.dead))[0]:
                if self._icept_ready[i] > self.time:
                    # idle recovery for units not attempting this tick
                    continue
                srcs = self.p_src[:m]
                # v1 ablation (--opt icept_v1=1): every enemy projectile is
                # interceptable and one hit destroys it (step20 model)
                if self.opts.get("icept_v1"):
                    foe = self.team[srcs] != self.team[i]
                else:
                    foe = (self.team[srcs] != self.team[i]) & (self.p_hp[:m] > 0)
                if not foe.any():
                    self._icept_p[i] = min(
                        1.0, self._icept_p[i] + self.icept_rise[i] * DT
                        / self.icept_rint[i])
                    continue
                dx = self.x[i] - self.p_x[:m]
                dy = self.y[i] - self.p_y[:m]
                d = np.sqrt(dx * dx + dy * dy)
                cand = np.where(foe & (~intercepted)
                                & (d >= self.icept_min[i])
                                & (d <= self.icept_max[i]))[0]
                if not len(cand):
                    self._icept_p[i] = min(
                        1.0, self._icept_p[i] + self.icept_rise[i] * DT
                        / self.icept_rint[i])
                    continue
                cand = cand[np.argsort(d[cand])]
                got = hits = 0
                for k in cand[:max(1, int(self.icept_w[i]))]:
                    if self._barrage_rng.random_sample() < self._icept_p[i]:
                        hits += 1
                        self.p_hp[k] -= self.icept_atk[i]
                        if self.p_hp[k] <= 0:
                            intercepted[k] = True
                            got += 1
                    self._icept_p[i] = max(self.icept_flr[i],
                                           self._icept_p[i] - self.icept_dec[i])
                self._icept_ready[i] = self.time + self.icept_int[i]
                if self.trace_enabled and hits:
                    self.trace.append("E|%.2f|icept|%d|%d|%d|%d" % (
                        self.time, int(self.team[i]), int(self.uid[i]),
                        got, hits))
        tgt = self.p_tgt[:m]
        # step23 T3: 只有追踪弹 (p_home>0) 跟随目标; 定点弹落点不变
        live = (~self.dead[tgt]) & (self.p_home[:m] > 0)
        self.p_tx[:m] = np.where(live, self.x[tgt], self.p_tx[:m])
        self.p_ty[:m] = np.where(live, self.y[tgt], self.p_ty[:m])
        dx = self.p_tx[:m] - self.p_x[:m]
        dy = self.p_ty[:m] - self.p_y[:m]
        dist = np.sqrt(dx * dx + dy * dy)
        step = self.p_speed[:m] * DT
        arrive = dist <= step
        if intercepted is not None:
            arrive = arrive | intercepted
        for k in np.where(arrive)[0]:
            if intercepted is not None and intercepted[k]:
                continue
            src = int(self.p_src[k]); tg = int(self.p_tgt[k])
            # step25 C5 pinned 弹 (p_home=0 且带原目标, 无溅射): 落地近失
            # 判定 —— 原目标仍在落点 (半径+1) 内 = 直伤, 已走出 = 落空
            if self.p_home[k] == 0.0 and tg >= 0 and self.p_splash[k] <= 0:
                if tg < self.n and not self.dead[tg] \
                        and math.hypot(self.x[tg] - self.p_tx[k],
                                       self.y[tg] - self.p_ty[k]) \
                        - self.radius[tg] <= 1.0:
                    self._deal_damage(src, tg, self.p_tx[k], self.p_ty[k],
                                      self.p_dmg[k], 0.0)
                continue
            self._deal_damage(src, tg, self.p_tx[k], self.p_ty[k], self.p_dmg[k], self.p_splash[k])
        # compact ALWAYS. The old guard ran this block only while some
        # projectile was still in flight - when every in-flight projectile
        # landed in the same tick the arrived one stayed in the buffer and
        # re-damaged its target every following tick (found via the user's
        # longbow-is-single-shot report: one landing dealt 2-6x its damage)
        keep = ~arrive
        self.p_n = int(np.count_nonzero(keep))
        if self.p_n:
            adv = keep & (dist > 0)
            self.p_x[:m] += np.where(adv, dx / np.maximum(dist, 1e-9) * step, 0.0)
            self.p_y[:m] += np.where(adv, dy / np.maximum(dist, 1e-9) * step, 0.0)
            for a in ("p_x", "p_y", "p_tx", "p_ty", "p_speed", "p_dmg", "p_splash", "p_hp", "p_home", "p_src", "p_tgt"):
                arr = getattr(self, a)
                arr[:self.p_n] = arr[:m][keep]

    def _apply_damage(self, tick):
        if not self._ev_victim:
            return
        v = np.array(self._ev_victim, dtype=np.int64)
        dm = np.array(self._ev_dmg) * self._amp_fac[v]
        # step32 T2: actual-damage ledger. When armed, dm0 keeps the
        # post-amp pre-mitigation raw per event and bar_take tracks the
        # barrier-redirect absorption; receipts are appended at the credit
        # stage below (shield/barrier/HP/overkill/prevented per event).
        _ledger = self.opts.get("eq_ledger", 1) == 2 or (
            self.opts.get("eq_ledger", 1) == 1
            and bool(self._eq_runtime))
        dm0 = dm.copy() if _ledger else None
        bar_take = np.zeros(len(v)) if _ledger else None
        # step5 任务书 §5 T5: photon (damage taken x0.70, ALL channels) and
        # acid vulnerability (x2.5 on ATTACK damage only — killerless DoT /
        # area ticks never re-amplify their own source). Photon dominates:
        # it immunizes the acid status entirely (QA-4).
        t_now0 = self.time
        _ph_on = self.photon_until[v] > t_now0
        _ac_on = (~_ph_on) & self._acid_on[v]
        if np.any(_ph_on) or np.any(_ac_on):
            killer0 = self._ev_killer
            for k in range(len(v)):
                if dm[k] <= 0 or self.dead[v[k]]:
                    continue
                if _ph_on[k]:
                    dm[k] *= self._photon_taken
                elif _ac_on[k] and killer0[k] >= 0:
                    dm[k] *= self._acid_vuln
        # step8-B: a live shield barrier absorbs damage dealt to covered
        # SAME-TEAM units (splash, projectiles and strikes all funnel through
        # this event queue); the overflow of the shield-breaking hit passes
        # through to the victim. Direct hits on the barrier itself are not
        # redirected.
        # step4 QA#6: barriers cover GROUND friendlies only — air units
        # (凤凰/兵蜂/深渊 ...) get no barrier protection.
        # step4 P1: bypass-flagged events (轨道标枪) skip the redirect.
        bypass_idx = getattr(self, "_bypass_ev_idx", None) or ()
        bars = np.where((self.mech_id == DEVICE_BARRIER) & (~self.dead)
                        & (self.hp > 0))[0] if np.any(self.is_device) else []
        if len(bars):
            for k in range(len(v)):
                vi = int(v[k])
                if self.mech_id[vi] == DEVICE_BARRIER:
                    continue
                if dm[k] <= 0 or k in bypass_idx:
                    continue
                if self.is_fly[vi]:
                    continue        # QA#6: no barrier cover for air units
                for bi in bars:
                    bi = int(bi)
                    if self.dead[bi] or self.hp[bi] <= 0 or self.team[vi] != self.team[bi]:
                        continue
                    d = math.hypot(self.x[vi] - self.x[bi], self.y[vi] - self.y[bi])
                    if d <= self.radius[bi]:
                        take = min(self.hp[bi], dm[k])
                        self.hp[bi] -= take
                        dm[k] -= take
                        if bar_take is not None:
                            bar_take[k] += take
                        if self.hp[bi] <= 0:
                            self._on_barrier_down(bi, int(self._ev_killer[k]) if k < len(self._ev_killer) else -1)
                        if dm[k] <= 0:
                            break
        # step20 T6 伤害分摊/并网: a hit on a linked bearer is split across
        # the link group (same team, same family tech, within share radius;
        # 631 并网 maxCount=4 caps the group incl. victim). rate=0 families
        # (608/613/660/657) split the damage EVENLY; 631 keeps (1-0.35) on
        # the victim and spreads 0.35 over the others. Only ORIGINAL events
        # expand (no chained re-share of shared damage).
        if np.any(self.share_rad > 0):
            n_ev0 = len(v)
            extra_v, extra_d = [], []
            for k in range(n_ev0):
                vi = int(v[k])
                if self.share_rad[vi] <= 0 or dm[k] <= 0 or self.dead[vi]:
                    continue
                linked = np.where((~self.dead) & (self.share_rad > 0)
                                  & (self.team == self.team[vi])
                                  & (self.share_rad == self.share_rad[vi]))[0]
                linked = linked[linked != vi]
                if len(linked):
                    d = np.hypot(self.x[linked] - self.x[vi],
                                 self.y[linked] - self.y[vi])
                    linked = linked[d <= self.share_rad[vi]]
                mc = int(self.share_maxc[vi])
                if mc > 0 and len(linked) > mc - 1:
                    d = np.hypot(self.x[linked] - self.x[vi],
                                 self.y[linked] - self.y[vi])
                    linked = linked[np.argsort(d)[:mc - 1]]
                if not len(linked):
                    continue
                rate = self.share_rate[vi]
                if rate <= 0:
                    each = dm[k] / (1.0 + len(linked))
                    dm[k] = each
                    for u in linked:
                        extra_v.append(int(u)); extra_d.append(each)
                else:
                    share = dm[k] * rate
                    dm[k] = dm[k] - share
                    each = share / len(linked)
                    for u in linked:
                        extra_v.append(int(u)); extra_d.append(each)
                if self.trace_enabled:
                    self.trace.append("E|%.2f|share|%d|%.0f|%d" % (
                        self.time, int(self.uid[vi]), dm[k], len(linked)))
            if extra_v:
                v = np.concatenate([v, np.array(extra_v, dtype=np.int64)])
                dm = np.concatenate([dm, np.array(extra_d)])
                if dm0 is not None:
                    dm0 = np.concatenate([dm0, np.array(extra_d)])
                    bar_take = np.concatenate(
                        [bar_take, np.zeros(len(extra_v))])
                # keep the event lists aligned (shared events carry no
                # killer; last_k/participants read them by index)
                self._ev_victim.extend(extra_v)
                self._ev_dmg.extend(extra_d)
                self._ev_killer.extend([-1] * len(extra_v))
        # step16 装甲强化: flat per-hit damage reduction, applied after the
        # barrier redirect (a barrier that eats the whole hit leaves nothing
        # for armor to shave; overflow passes through armor)
        # step19: EMP'd victims lose their tech-derived armor; 应急装甲
        # active victims ignore all damage (抵抗所有伤害)
        t_now = self.time
        aegis_on = self.aegis_until > t_now
        if np.any(aegis_on):
            dm = np.where(aegis_on[v], 0.0, dm)
        # step25 P1 能量护盾: 盾先于装甲吸收 (能量层, 与平板减伤无关);
        # 护盾穿透 (anti_shield): 对有盾单位伤害 xmult 且等量直伤本体
        # (同时打盾和本体)。stake = 本批盾吸收量, bextra = 穿透直伤本体量。
        stake = None
        bextra = None
        if self.opts.get("tech_eshield", 1) and len(v) \
                and np.any(self.shield[v] > 0):
            stake = np.zeros(len(v))
            bextra = np.zeros(len(v))
            any_anti = np.any(self.anti_shield > 0)
            for k in range(len(v)):
                vi = int(v[k])
                if self.dead[vi] or dm[k] <= 0 or self.shield[vi] <= 0:
                    continue
                d0 = float(dm[k])
                if any_anti:
                    ki = self._ev_killer[k]
                    if ki >= 0 and self.anti_shield[ki] > 0:
                        d0 *= self.anti_shield[ki]
                        bextra[k] = d0     # 本体直伤 (绕盾绕甲)
                take = min(self.shield[vi], d0)
                self.shield[vi] -= take
                stake[k] = take
                dm[k] = d0 - take
        if np.any(self.armor > 0):
            arm = self.armor * np.where(self.emp_until > t_now, 0.0, 1.0)
            dm = np.maximum(dm - arm[v], 0.0)
        # step18 T8a: 参与击杀 bookkeeping - any card landing real damage on
        # this victim joins its participant set (exp share on its death)
        if len(v):
            pd = getattr(self, "_part_dmg", None)
            if pd is None:
                pd = self._part_dmg = {}
            for k in range(len(v)):
                eff = dm[k] + (bextra[k] if bextra is not None else 0.0) \
                    + (stake[k] if stake is not None else 0.0)
                if eff > 0:
                    ki = self._ev_killer[k]
                    if ki >= 0 and self.card_idx[ki] >= 0:
                        ci = int(self.card_idx[ki])
                        if self.cards[ci]["team"] != self.team[v[k]]:
                            s = pd.get(int(v[k]))
                            if s is None:
                                pd[int(v[k])] = {ci}
                            else:
                                s.add(ci)
        # step19 逆火 180620: taking damage bumps range +70 for 20s;
        # 战地维修 gate: 受伤后 regen 才激活
        if len(v):
            eff_all = dm + (bextra if bextra is not None else 0.0) \
                + (stake if stake is not None else 0.0)
            if np.any(eff_all > 0):
                self._damaged[v[eff_all > 0]] = True
        else:
            if np.any(dm > 0):
                self._damaged[v[dm > 0]] = True
        if np.any(self.bf_val > 0):
            hit_rows = v[dm > 0]
            for vi in hit_rows:
                if self.bf_val[vi] > 0:
                    self.bf_until[vi] = t_now + 20.0
        np.add.at(self.hp, v, -(dm + (bextra if bextra is not None else 0.0)))
        # step23 记账修复: 同 tick 同 victim 多事件需按事件顺序结算 credit
        # (总 credit = min(Σdm, 事件前 HP), 与 oracle dmgReal 口径一致)。
        # 旧公式用整批扣血后的 HP 逐事件算 min(dm, hp+dm), 同批第 2+ 发的
        # 超杀段被重复扣减 → 漏记 (a_m6 实测 384 杀只记 74030/100992)。
        # step25 P1: 盾吸收 (stake) 与穿透直伤 (bextra) 同入 credit ——
        # oracle dmgReal 含盾伤 (u129 实锤 2x 血池)。
        if len(v):
            ev_sum, ev_idx = {}, {}
            for k in range(len(v)):
                vi = int(v[k])
                d_eff = float(dm[k]) \
                    + (bextra[k] if bextra is not None else 0.0) \
                    + (stake[k] if stake is not None else 0.0)
                ev_sum[vi] = ev_sum.get(vi, 0.0) + d_eff
                ev_idx.setdefault(vi, []).append(k)
            credited = np.zeros(len(v))
            for vi, ks in ev_idx.items():
                rem = self.hp[vi] + ev_sum[vi]     # 该 victim 本批事件前 HP
                for k in ks:
                    d_eff = float(dm[k]) \
                        + (bextra[k] if bextra is not None else 0.0) \
                        + (stake[k] if stake is not None else 0.0)
                    c = min(d_eff, max(rem, 0.0))
                    credited[k] = c
                    rem -= c
                    # step32 T2: DamageReceipt — same actual-damage figure
                    # feeds lifesteal/damage reports/trace (任务书: 不再用
                    # 发射前理论伤害). source_kind v1: attack vs environment
                    # (killerless DoT/area/strike); tags keep the auditable
                    # modifiers visible.
                    if _ledger:
                        _ki = int(self._ev_killer[k]) \
                            if k < len(self._ev_killer) else -1
                        _raw = float(dm0[k]) if dm0 is not None else d_eff
                        _sh = float(stake[k]) if stake is not None else 0.0
                        _bar = float(bar_take[k]) if bar_take is not None \
                            else 0.0
                        _bx = float(bextra[k]) if bextra is not None else 0.0
                        _tags = []
                        if _ki < 0:
                            _tags.append("killerless")
                        if _bx > 0:
                            _tags.append("anti_shield")
                        if k in bypass_idx:
                            _tags.append("bypass")
                        self._receipt_seq += 1
                        self.damage_receipts.append({
                            "ref": self._receipt_seq,
                            "t": round(self.time, 4),
                            "source_row": _ki,
                            "source_card": int(self.card_idx[_ki])
                            if _ki >= 0 else -1,
                            "source_kind": "attack" if _ki >= 0
                            else "environment",
                            "victim_row": int(v[k]),
                            "raw_damage": round(_raw, 3),
                            "shield_absorbed": round(_sh, 3),
                            "barrier_absorbed": round(_bar, 3),
                            "hp_damage": round(max(0.0, c - _sh - _bar), 3),
                            "overkill": round(max(0.0, d_eff - c), 3),
                            "prevented": round(max(0.0, _raw - d_eff), 3),
                            "killed": bool(rem <= 1e-9),
                            "tags": _tags})
            self.total_damage += float(np.sum(credited))
            # step22 T4: per-card 记账 (同 total_damage 口径逐事件累加);
            # step24 骇客转化: 同时记 per-row (转化单位的伤害按转化后阵营计)
            for k in range(len(v)):
                ki = self._ev_killer[k]
                if ki >= 0 and credited[k] > 0 and self.card_idx[ki] >= 0:
                    ci = int(self.card_idx[ki])
                    self.card_damage[ci] = self.card_damage.get(ci, 0.0) + float(credited[k])
                    self.row_damage[ki] += float(credited[k])
        else:
            self.total_damage += 0.0
        # step19 斩杀弹 4607: after the hit lands, a victim still standing
        # below the attacker's level HP line is destroyed outright
        if np.any(self.deadline_v > 0):
            for k in range(len(v)):
                ki = int(self._ev_killer[k]) if k < len(self._ev_killer) else -1
                vi = int(v[k])
                if 0 <= ki < self.n and self.deadline_v[ki] > 0 \
                        and not self.dead[vi] and 0 < self.hp[vi] <= self.deadline_v[ki]:
                    self.hp[vi] = 0.0
        # last killer per victim
        last_k = {}
        for vv, kk in zip(self._ev_victim, self._ev_killer):
            last_k[vv] = kk
        newly = np.where((~self.dead) & (self.hp <= 0))[0]
        self._process_deaths(newly, last_k)
        # step19 应急装甲 503101: first drop below 50% maxHP starts a 4s
        # invulnerable + untargetable window (one-shot per unit)
        if np.any(self.aegis):
            cand = np.where(self.aegis & (~self.dead)
                            & (self.hp < self.max_hp * 0.5)
                            & (self.hp > 0))[0]
            for vi in cand:
                self.aegis[vi] = False
                self.aegis_until[vi] = t_now + 4.0
        self._ev_victim.clear(); self._ev_dmg.clear(); self._ev_killer.clear()
        if hasattr(self, "_bypass_ev_idx"):
            self._bypass_ev_idx.clear()

    def _process_deaths(self, newly, last_k):
        t = self.time
        kb_queue = []
        for vi in newly:
            vi = int(vi)
            ki = last_k.get(vi, -1)
            self.dead[vi] = True
            self.total_kills += 1
            self.kills.append({
                "t": round(t, 2), "killer": int(self.uid[ki]) if ki >= 0 else 0,
                "victim": int(self.uid[vi]),
                "kmech": int(self.mech_id[ki]) if ki >= 0 else 0,
                "vmech": int(self.mech_id[vi]),
                "kteam": int(self.team[ki]) if ki >= 0 else -1,
            })
            if self.trace_enabled:
                km = int(self.mech_id[ki]) if ki >= 0 else 0
                self.trace.append("E|%.2f|kill|%d|%d|%d|%d" % (t, self.uid[ki] if ki >= 0 else 0, km,
                                                               self.uid[vi], int(self.mech_id[vi])))
            if self.is_tower[vi]:
                self._on_tower_down(vi)
                # step18 T7: tower kills grant flat exp (user Q2: a level-1
                # tower is worth ~ one crawler squad = value 100; higher
                # tower levels unknown Q-D, same flat value pending user).
                # opts.tower_exp=0 restores the no-exp behavior.
                if self.opts.get("tower_exp", 1) and ki >= 0 and not self.dead[ki] \
                        and self.card_idx[ki] >= 0:
                    self._grant_flat_exp(ki, float(self.opts.get("tower_exp_val", 100.0)))
            elif self.is_device[vi]:
                # devices die without exp/paralysis (mechless killer possible)
                if self.trace_enabled:
                    self.trace.append("E|%.2f|device_down|%d|%d" % (
                        t, int(self.team[vi]), int(self.mech_id[vi])))
            elif self.is_bld[vi]:
                self._on_bld_down(vi, ki)
            elif ki >= 0 and not self.dead[ki] and self.card_idx[ki] >= 0:
                self._grant_exp(ki, vi)
            elif ki < 0 or self.card_idx[ki] < 0:
                # step18 T8b: ownerless deaths (strikes / burns / devices)
                # split loot among all living enemy units
                self._grant_unowned_exp(vi)
            # step19 残骸引爆 5322: victims of a killboom attacker explode
            # (115 dmg / 12m, hits ALLIES too; no chain explosions)
            if 0 <= ki < self.n and self.killboom[ki] and not self.is_tower[vi] \
                    and not self.is_device[vi] and not self.is_bld[vi]:
                kb_queue.append((ki, float(self.x[vi]), float(self.y[vi])))
            # step23 犀牛 2805 最后一击: 死亡自爆 (oracle rh_t2805 定标: 爆伤=
            # 最大生命值, 半径 48m, 波及敌我双方 (rh_t2805 双方全灭平局, 爬虫
            # 仅打出 36677<38594, 犀牛被己方连锁爆炸收尾), 不波及空军 (用户
            # 口径, 同蜘蛛雷)。即时结算 (走事件队列会被 "全灭即终局" 吞掉),
            # credit=min(爆伤, 受害者剩余HP) 归属死亡犀牛; 连锁递归自然发生。
            # 无科技犀牛不自爆 (rh_ff: 友军爬虫死亡被长弓火力精确解释)。
            if self.opts.get("rhino_boom", 1) and self.mech_id[vi] == 5 \
                    and not self.is_tower[vi] and not self.is_bld[vi]:
                ci = int(self.card_idx[vi])
                if ci >= 0 and 2805 in (self.cards[ci].get("techs") or ()):
                    bx, by = float(self.x[vi]), float(self.y[vi])
                    boom = float(self.max_hp[vi])
                    hit = np.where((~self.dead) & (~self.is_fly)
                                   & (np.hypot(self.x - bx, self.y - by)
                                      - self.radius <= 48.0))[0]
                    if len(hit):
                        dm = np.full(len(hit), boom)
                        credited = np.minimum(dm, np.maximum(self.hp[hit], 0.0))
                        np.add.at(self.hp, hit, -dm)
                        self.total_damage += float(np.sum(credited))
                        self.card_damage[ci] = self.card_damage.get(ci, 0.0) \
                            + float(np.sum(credited))
                        lk2 = {int(h): vi for h in hit}
                        newly2 = np.where((~self.dead) & (self.hp <= 0))[0]
                        self._process_deaths(newly2, lk2)
            # step19 机械分裂 1308 → step20 T6 deadSummon family: a death
            # revives its unitCount ghosts at its position (pre-allocated
            # rows; nspawn per tech from the merged extra)
            if self.opts.get("tech_split", 1):
                ci = int(self.card_idx[vi])
                pool = getattr(self, "_ghost_pool", {}).get(ci)
                if pool:
                    for nspawn, rows in pool:
                        for _ in range(nspawn):
                            if not rows:
                                break
                            g = rows.pop(0)
                            self.dead[g] = False
                            self.hp[g] = self.max_hp[g]
                            self.x[g] = self.x[vi]
                            self.y[g] = self.y[vi]
                            self.target[g] = -1
                            self.state[g] = IDLE
                            self.state_t[g] = 0.0
                            self.first_attack[g] = True
                            self.mv_target[g] = -1
        # resolve queued 残骸引爆 explosions (one round; no chains)
        for ki, kx, ky in kb_queue:
            hit = np.where((~self.dead)
                           & (np.hypot(self.x - kx, self.y - ky)
                              - self.radius <= 12.0))[0]
            if len(hit):
                np.add.at(self.hp, hit, -115.0)
                lk2 = {int(h): ki for h in hit}
                newly2 = np.where((~self.dead) & (self.hp <= 0))[0]
                self._process_deaths(newly2, lk2)

    def _grant_flat_exp(self, ki, amount):
        # flat exp to the killer's card + the standard level-up loop
        # (shared by construction kills and step18 tower kills)
        card = self.cards[int(self.card_idx[ki])]
        card["exp"] += amount
        ke = self.gd.exps.get(int(self.mech_id[ki]))
        if ke is not None:
            while card["level"] < 9:
                # step22 T3: 内部等级=游戏等级+1 后, L→L+1 门槛 =
                # upgrade_at[L+1] = upgrade_at[内部等级] (旧 +1 索引会晚一档)
                need = ke.upgrade_at[card["level"]]
                if need <= 0 or card["exp"] < need:
                    break
                card["level"] += 1
                self._rescale_card(int(self.card_idx[ki]), card["level"])

    def _on_barrier_down(self, bi, killer=None):
        self.dead[bi] = True
        self.total_kills += 1
        self.kills.append({
            "t": round(self.time, 2), "killer": int(self.uid[killer]) if killer is not None and killer >= 0 else 0,
            "victim": int(self.uid[bi]),
            "kmech": int(self.mech_id[killer]) if killer is not None and killer >= 0 else 0,
            "vmech": int(self.mech_id[bi]),
            "kteam": int(self.team[killer]) if killer is not None and killer >= 0 else -1,
        })
        if self.trace_enabled:
            self.trace.append("E|%.2f|kill|%d|%d|%d|%d" % (
                self.time, self.uid[killer] if killer is not None and killer >= 0 else 0,
                int(self.mech_id[killer]) if killer is not None and killer >= 0 else 0,
                self.uid[bi], int(self.mech_id[bi])))
            self.trace.append("E|%.2f|device_down|%d|%d" % (
                self.time, int(self.team[bi]), int(self.mech_id[bi])))

    # ---------- paralysis (step8) ----------
    def _on_tower_down(self, vi):
        team = int(self.team[vi])
        self.towers_down[team] += 1
        dur = PARALYSE_DURATION[min(4, max(0, int(self.tower_str[vi])))]
        until = self.time + dur
        if self.trace_enabled:
            self.trace.append("E|%.2f|tower_down|%d|%d" % (self.time, team, self.towers_down[team]))
        if until > self.paralyse_until[team]:
            self.paralyse_until[team] = until
            # T9: paralysis never touches buildings (they keep firing at full
            # strength while the owner team is disabled)
            members = (~self.dead) & (self.team == team) & (~self.is_tower) \
                & (~self.is_bld)
            # step32 T8: 抗干扰模块 rows keep their factors at 1.0 (per-row
            # paralysis immunity; vectorized path stays when no mask is set)
            if np.any(self._eq_immune_perm) or np.any(self._eq_immune_temp):
                for u2 in np.where(members)[0]:
                    if self._status_immune(int(u2), "paralysis"):
                        self._status_block_note(int(u2), "paralysis")
                        continue
                    self._dmg_fac[u2] = PARALYSE_DMG
                    self._spd_fac[u2] = PARALYSE_SPEED
                    self._amp_fac[u2] = PARALYSE_AMPLIFY
            else:
                self._dmg_fac[members] = PARALYSE_DMG
                self._spd_fac[members] = PARALYSE_SPEED
                self._amp_fac[members] = PARALYSE_AMPLIFY
            if self.trace_enabled:
                self.trace.append("E|%.2f|paralyse|%d|%.2f" % (self.time, team, until))

    def _check_paralyse_expiry(self):
        for team in (0, 1):
            until = self.paralyse_until[team]
            if until >= 0 and self.time >= until:
                self.paralyse_until[team] = -1.0
                members = (~self.dead) & (self.team == team) & (~self.is_tower) \
                    & (~self.is_bld)
                self._dmg_fac[members] = 1.0
                self._spd_fac[members] = 1.0
                self._amp_fac[members] = 1.0

    # ---------- buildings (step12) ----------
    def _on_bld_down(self, vi, ki):
        t = self.time
        team = int(self.team[vi])
        cid = int(self.bld_cid[vi])
        grp = int(self.bld_group[vi])
        if self.trace_enabled:
            self.trace.append("E|%.2f|bld_down|%d|%d|%d|%d" % (t, team, cid, grp,
                                                               int(self.uid[vi])))
        # construction kills grant flat exp to the killer card (wiki "升级
        # 经验"; opts.bld_exp=0 disables for ablation)
        if self.opts.get("bld_exp", 1) and ki >= 0 and not self.dead[ki] \
                and self.card_idx[ki] >= 0:
            bdef = self.bld_defs.get(int(vi))
            self._grant_flat_exp(ki, bdef.exp if bdef is not None else 0)
        # a group is down when every module is dead (walls: 5 modules die
        # independently; snapshot presence next round is the replay truth)
        mates = (self.bld_group == grp) & (self.team == team) & (~self.dead)
        if not np.any(mates):
            self.bld_groups_down[team] += 1
            if self.trace_enabled:
                self.trace.append("E|%.2f|bld_group_down|%d|%d|%d" % (t, team, cid, grp))

    def _update_magnets(self):
        # magnet self-destruct + slow field, run on the retarget cadence
        mag = self.is_bld & (self.mech_id == BLD_MAGNET) & (~self.dead)
        if not np.any(mag):
            if hasattr(self, "_magnet_fac") and np.any(self._magnet_fac != 1.0):
                self._magnet_fac[:] = 1.0
            return
        # popped magnets self-destruct MAGNET_SELF_T seconds after popping
        due = mag & (self.bld_state == 1) & (self.time >= self.bld_pop_at + MAGNET_SELF_T)
        for mi in np.where(due)[0]:
            mi = int(mi)
            self.dead[mi] = True
            grp = int(self.bld_group[mi])
            if self.trace_enabled:
                self.trace.append("E|%.2f|magnet_down|%d|%d|%d|self" % (
                    self.time, int(self.team[mi]), int(self.bld_cid[mi]), grp))
            mates = (self.bld_group == grp) & (self.team == self.team[mi]) & (~self.dead)
            if not np.any(mates):
                self.bld_groups_down[int(self.team[mi])] += 1
                if self.trace_enabled:
                    self.trace.append("E|%.2f|bld_group_down|%d|%d|%d" % (
                        self.time, int(self.team[mi]), int(self.bld_cid[mi]), grp))
        # slow field: enemy ground units within MAGNET_SLOW_R of any popped
        # module move at (1 - slow); multiple fields do not stack
        slow = float(self.opts.get("bld_slow", 0.4))
        fac = np.ones(self.n)
        popped = np.where(mag & (self.bld_state == 1) & (~self.dead))[0]
        if len(popped):
            for team in (0, 1):
                pm = popped[self.team[popped] == team]
                if not len(pm):
                    continue
                foes = np.where((~self.dead) & (self.team != team)
                                & (~self.is_fly) & (~self.is_bld))[0]
                if not len(foes):
                    continue
                dx = self.x[foes][:, None] - self.x[pm][None, :]
                dy = self.y[foes][:, None] - self.y[pm][None, :]
                dist = np.sqrt(dx * dx + dy * dy) - self.radius[foes][:, None]
                hit = np.any(dist <= MAGNET_SLOW_R, axis=1)
                fac[foes[hit]] = 1.0 - slow
        self._magnet_fac = fac

    def _grant_exp(self, killer, victim):
        gd = self.gd
        ve = gd.exps.get(int(self.mech_id[victim]))
        ke = gd.exps.get(int(self.mech_id[killer]))
        if ve is None or ke is None:
            return
        lv = min(9, max(1, int(self.level[victim])))
        total = ve.loot_exp[lv]
        # step18 T8a (user rule): 击杀 = 50% exp to the killer + 50% split
        # evenly among ALL units that damaged this victim this fight
        # (参与击杀 includes the killer itself: killer gets 50% + 50%/n).
        # opts.exp_share="loot" restores the whole-loot-to-killer behavior.
        participants = self._dmg_participants(victim)
        if self.opts.get("exp_share", "5050") == "5050" and len(participants) >= 1:
            kcard = int(self.card_idx[killer])
            share = total * 0.5 / len(participants)
            for c in participants:
                self._add_card_exp_by_idx(c, share + (total * 0.5 if c == kcard else 0.0))
        else:
            self._add_card_exp(killer, total)

    def _add_card_exp(self, ki, amount, ke=None):
        self._add_card_exp_by_idx(int(self.card_idx[ki]), amount)

    def _add_card_exp_by_idx(self, c, amount):
        # shared exp add + cap (T8c) + level-up loop
        card = self.cards[c]
        if self.opts.get("exp_cap", 0):
            # cap at the level-9 threshold: beyond it exp cannot grow
            ke = self.gd.exps.get(int(card["mech"]))
            if ke is not None and card["level"] >= 9 and ke.upgrade_at[9] > 0 \
                    and card["exp"] >= ke.upgrade_at[9]:
                return
        card["exp"] += amount
        ke = self.gd.exps.get(int(card["mech"]))
        if ke is None:
            return
        # step13 ablation: opts.exp_levelup keeps exp bookkeeping but never
        # levels up mid-fight. step22 定版默认 0 (用户口径: 单位只在部署阶段
        # 手动升级, 经验可攒满但战斗内不自动升级; step13 同判)
        if not self.opts.get("exp_levelup", 0):
            return
        while card["level"] < 9:
            need = ke.upgrade_at[card["level"]]   # step22 T3: 见 _grant_flat_exp 注
            if need <= 0 or card["exp"] < need:
                break
            card["level"] += 1
            self._rescale_card(c, card["level"])
        if self.opts.get("exp_cap", 0) and card["level"] >= 9 \
                and ke.upgrade_at[9] > 0:
            card["exp"] = min(card["exp"], ke.upgrade_at[9])

    def _grant_unowned_exp(self, victim):
        # step18 T8b (user rule): a victim dying to skills/devices/ground
        # effects (no killer card) splits its exp among ALL living enemy
        # units. Only in exp_share="5050" mode ("loot" = old behavior).
        if self.opts.get("exp_share", "5050") != "5050":
            return
        gd = self.gd
        ve = gd.exps.get(int(self.mech_id[victim]))
        if ve is None:
            return
        lv = min(9, max(1, int(self.level[victim])))
        foes = np.where((~self.dead) & (self.team != self.team[victim])
                        & (self.card_idx >= 0)
                        & (~self.is_tower) & (~self.is_device) & (~self.is_bld))[0]
        if not len(foes):
            return
        share = ve.loot_exp[lv] / len(foes)
        for f in foes:
            self._add_card_exp_by_idx(int(self.card_idx[f]), share)

    def _dmg_participants(self, victim):
        # card indices that damaged this victim this fight (参与击杀记账,
        # T8a): participants tracked in _part_dmg on every damage event
        m = getattr(self, "_part_dmg", None)
        if m is None:
            return set()
        out = m.get(int(victim))
        return out or set()

    def _rescale_card(self, c, new_level):
        members = np.where((self.card_idx == c) & ~self.dead)[0]
        if len(members) == 0:
            return
        self.level[members] = new_level
        self._bake_card_mods(c, preserve_hp=True)

    # ---------- movement ----------
    def _update_facing(self):
        # heading dynamics: units with a target turn toward it at rot_spd.
        # v1 simplification: walkers also turn toward their attack target
        # (mv_target is the nearest enemy, i.e. roughly the walking direction).
        has_t = (~self.dead) & (self.target >= 0)
        idx = np.where(has_t)[0]
        if len(idx):
            tt = self.target[idx]
            want = np.arctan2(self.y[tt] - self.y[idx], self.x[tt] - self.x[idx])
            diff = (want - self.head[idx] + math.pi) % (2 * math.pi) - math.pi
            step = self.rot_spd[idx] * DT
            self.head[idx] += np.clip(diff, -step, step)

    def _facing_rows(self):
        """step28 facing 白名单: facing_set 集合内的行启用转向门控 (Q-A 按
        兵种优化口径); 空集合 + facing=1 = 全局开。返回 bool 掩码 (缓存)。"""
        c = getattr(self, "_facing_mask", None)
        if c is not None:
            return c
        fs = self.opts.get("facing_set")
        if fs:
            if isinstance(fs, str):
                s = {int(x) for x in fs.replace(";", ",").split(",") if x.strip()}
            elif isinstance(fs, (int, float)):
                s = {int(fs)}
            else:
                s = {int(x) for x in fs}
            m = np.isin(self.mech_id, list(s))
        else:
            m = np.ones(self.n, dtype=bool)
        self._facing_mask = m
        return m

    def _aimed(self):
        # True where the current target is inside the unit's attack cone
        has_t = (~self.dead) & (self.target >= 0)
        ok = np.zeros(self.n, dtype=bool)
        idx = np.where(has_t)[0]
        if len(idx):
            tt = self.target[idx]
            want = np.arctan2(self.y[tt] - self.y[idx], self.x[tt] - self.x[idx])
            diff = np.abs((want - self.head[idx] + math.pi) % (2 * math.pi) - math.pi)
            ok[idx] = diff <= self.half_cone[idx]
            # whitelist rows outside facing_set are always "aimed"
            fw = self._facing_rows()
            ok &= fw
        return ok

    def _move(self):
        # step5 任务书 §7 T12: move-beacon waypoint override. Selected
        # members walk their own waypoints (B+off then C+off); engagement
        # policy frozen as stop-to-attack — hold while the current target
        # is inside firing range, resume the path once it is not. Beacon
        # rows leave the normal chase movement entirely.
        wp = getattr(self, "_wp_active", None)
        if wp is not None and np.any(wp & (~self.dead)):
            movers = np.where(wp & (~self.dead) & (~self._spawning))[0]
            for u in movers:
                u = int(u)
                stage = int(self._wp_stage[u])
                tx = self._wp_x0[u] if stage == 0 else self._wp_x1[u]
                ty = self._wp_y0[u] if stage == 0 else self._wp_y1[u]
                # stop-to-attack: an in-range target holds the march
                mt = int(self.mv_target[u])
                if mt >= 0 and not self.dead[mt]:
                    d = math.hypot(self.x[mt] - self.x[u],
                                   self.y[mt] - self.y[u]) \
                        - self.radius[mt] - self.radius[u]
                    if d <= self.range[u]:
                        continue
                wx, wy = tx - self.x[u], ty - self.y[u]
                ln = math.hypot(wx, wy)
                if ln <= 1e-6:
                    self._wp_stage[u] = min(stage + 1, 1)
                    if stage >= 1:
                        self._wp_active[u] = False
                    continue
                emp_fac = 0.60 if self.emp_until[u] > self.time else 1.0
                spd = self.move_speed[u] * self._spd_fac[u] \
                    * self._magnet_fac[u] * emp_fac * self._area_fac[u]
                step_len = spd * DT
                if ln <= step_len:
                    self.x[u], self.y[u] = tx, ty
                    if stage >= 1:
                        self._wp_active[u] = False
                    else:
                        self._wp_stage[u] = 1
                else:
                    self.x[u] += wx / ln * step_len
                    self.y[u] += wy / ln * step_len
                    if self.rolling[u]:
                        self.moved[u] += step_len
        movable = (~self.dead) & (self.move_speed > 0) & (self.mv_target >= 0) \
            & (~self._spawning) & (~self._wp_active)
        if not np.any(movable):
            return
        idx = np.where(movable)[0]
        mt = self.mv_target[idx]
        dx = self.x[mt] - self.x[idx]
        dy = self.y[mt] - self.y[idx]
        dist = np.sqrt(dx * dx + dy * dy) - self.radius[mt] - self.radius[idx]
        # step7 aggro rule (opts.aggro = R meters, report-fitted): ranged
        # units only pursue the nearest enemy while it is within R; beyond
        # that they hold position (losers die deep at spawn, winners stop
        # 0-50m past the midline and shoot the towers from range). Melee
        # always advances. Supersedes the rejected hold= variant (step7
        # sweep6: "never close while an enemy is within H" -> standoffs,
        # 44.8% vs 59.6% baseline).
        aggro = float(self.opts.get("aggro", 0) or 0)
        if aggro > 0:
            passive = (~self.is_melee[idx]) & (dist > aggro)
            dist = np.where(passive, -1.0, dist)     # negative -> never walks
        # queue rule: non-lane-leaders hold (see _full_target_pass)
        if self.opts.get("queue"):
            ok = getattr(self, "_adv_ok", None)
            if ok is not None:
                dist = np.where(~ok[idx], -1.0, dist)
        # step19 T12: stop margin - units settle stop_m meters INSIDE max
        # range (firing range itself is unchanged); walk_f is the walk
        # threshold factor (was hardcoded 0.9)
        stop_m = float(self.opts.get("stop_m", 0) or 0)
        walk_f = float(self.opts.get("walk_f", 0.9) or 0.9)
        stop_eff = np.maximum(5.0, self.stop_dist[idx] - stop_m)
        # step19 焦土: once triggered the unit charges into contact
        # regardless of its own range
        if np.any(self.scorch_on[idx]):
            stop_eff = np.where(self.scorch_on[idx], -np.inf, stop_eff)
        walk = dist > stop_eff * walk_f
        w = idx[walk]
        if len(w):
            wx = self.x[self.mv_target[w]] - self.x[w]
            wy = self.y[self.mv_target[w]] - self.y[w]
            ln = np.sqrt(wx * wx + wy * wy)
            ok = ln > 1e-6
            # step19 EMP (电磁弹): speed -40% while the disable is active;
            # step24 骇客光束瘫痪 (step25 C3: hack_par=0 默认撤, 被光束
            # 单位可动); step25 C3 hack_pin: 骇客本体 channel 定身
            emp_fac = np.where(self.emp_until[w] > self.time, 0.6, 1.0)
            if self.hacked.any() and self.opts.get("hack_par", 0):
                emp_fac = np.where(self.hacked[w], 0.0, emp_fac)
            if self.is_hacker.any() and self.opts.get("hack_pin", 1):
                emp_fac = np.where(self.is_hacker[w] & self.beaming[w], 0.0,
                                   emp_fac)
            spd = self.move_speed[w] * self._spd_fac[w] * self._magnet_fac[w] \
                * emp_fac * self._area_fac[w]
            # step29 scorch_spd: 焦土冲锋期间高速飞向目标 (st346 用户口径:
            # 火獾带剩余血量高速飞向目标并自爆, 飞行期间可被打)
            _sspd = float(self.opts.get("scorch_spd", 0) or 0)
            if _sspd > 0 and np.any(self.scorch_on[w]):
                spd = np.where(self.scorch_on[w], spd * _sspd, spd)
            step_len = np.where(ok, spd * DT, 0.0)
            self.x[w] = np.where(ok, self.x[w] + wx / np.maximum(ln, 1e-9) * step_len, self.x[w])
            self.y[w] = np.where(ok, self.y[w] + wy / np.maximum(ln, 1e-9) * step_len, self.y[w])
            # 滚动充能 180808: +1 range per 7m walked (stacks capped at 100)
            if np.any(self.rolling[w]):
                self.moved[w] += step_len
        # step23 走打 (kite v2, oracle 定标): 远程单位 (range≥kite_min=60) 在
        # 最近敌进入近距威胁区 kite_dist (默认 40m) 时背向撤退 —— 不是维持
        # 最大射程 (a_m2_hw: 长弓140 vs 铁锤95, oracle 双方停在 95 对射到长弓
        # 全灭, 不退; a_m7_cw_l2: 野马95 速16=爬虫, 爬虫逼近到近距才退,
        # 边退边打全清 384; 钢球45/兵蜂50 短程不退直接换血)。慢速单位撤退
        # 只延长窗口仍被追上 (暴雨6/长弓8), 与 oracle 一致。
        # opts.kite_dist=0 (默认) 关闭。
        # step24 kite_units 白名单 (benchmark 两臂证据): 全局开走打对
        # 先知/凤凰/暴雨/魔眼 有害 (r 掉 0.2-0.3), 只对受益兵种开
        # (火獾 0.47→1.00 / 恶灵 0.67→1.00 / 尖牙 0.77→0.93 / 野马 0.48→0.58)。
        # step25 C1 (用户 comment 定版): "只有暴雨会有回退现象, 不使用移动
        # 信标正常单位不会后退" —— kite 白名单退役, 默认仅暴雨 (id 12) 撤退
        # (opts.rain_kite=1; =0 恢复 step24 行为)。显式 kite_units 优先。
        kd = float(self.opts.get("kite_dist", 0) or 0)
        kset = self.opts.get("kite_units")
        if kd <= 0 and self.opts.get("rain_kite", 1):
            kd = 40.0
            kset = "12"
        if kd > 0:
            kmin = float(self.opts.get("kite_min", 60) or 60)
            if isinstance(kset, str):
                kset = [x for x in kset.replace(";", ",").split(",") if x.strip()]
            elif kset is not None and not isinstance(kset, (list, tuple)):
                kset = [kset]      # step28b: CLI --opt 可能传来 int
            can = (~self.is_melee[idx]) & (self.range[idx] >= kmin) & (dist >= 0) \
                & (dist < kd) & (~self.scorch_on[idx]) & (self.move_speed[idx] > 0) \
                & (~self._wp_active[idx])
            if kset:
                in_set = np.isin(self.mech_id[idx], [int(x) for x in kset])
                can &= in_set
            if self.hacked.any() and self.opts.get("hack_par", 0):
                can &= ~self.hacked[idx]
            k = idx[can]
            if len(k):
                wx = self.x[k] - self.x[self.mv_target[k]]
                wy = self.y[k] - self.y[self.mv_target[k]]
                ln = np.sqrt(wx * wx + wy * wy)
                ok = ln > 1e-6
                emp_fac = np.where(self.emp_until[k] > self.time, 0.6, 1.0)
                spd = self.move_speed[k] * self._spd_fac[k] * self._magnet_fac[k] \
                    * emp_fac * self._area_fac[k]
                step_len = np.where(ok, spd * DT, 0.0)
                self.x[k] = np.where(ok, self.x[k] + wx / np.maximum(ln, 1e-9) * step_len, self.x[k])
                self.y[k] = np.where(ok, self.y[k] + wy / np.maximum(ln, 1e-9) * step_len, self.y[k])
                if np.any(self.rolling[k]):
                    self.moved[k] += step_len
        np.clip(self.x[idx], -MAP_X, MAP_X, out=self.x[idx])
        np.clip(self.y[idx], -MAP_Y, MAP_Y, out=self.y[idx])

    def _separate(self):
        # sparse: candidate pairs refreshed by the full targeting pass.
        # Default: one soft sweep (each side relieved by half the overlap).
        # opts.stiff_sep: full overlap resolution x 3 sweeps, towers
        # immovable - approximates the game's hard collision / RVO jam.
        # step18: uses the cached _sep_r radius (sep_chaff="half" shrinks
        # chaff rows here; all other modes equal self.radius).
        i = getattr(self, "_sep_i", None)
        if i is None or len(i) == 0:
            return
        sep_r = getattr(self, "_sep_r", None)
        if sep_r is None:
            sep_r = self.radius
        sweeps = int(self.opts.get("sep_sweeps", 0) or 0) \
            or (3 if self.opts.get("stiff_sep") else 1)
        fac = float(self.opts.get("sep_fac", 0) or 0) \
            or (1.0 if self.opts.get("stiff_sep") else 0.5)
        for _ in range(sweeps):
            live = (~self.dead[i]) & (~self.dead[self._sep_j])
            ii = i[live]; jj = self._sep_j[live]
            if len(ii) == 0:
                return
            dx = self.x[ii] - self.x[jj]
            dy = self.y[ii] - self.y[jj]
            mind = (sep_r[ii] + sep_r[jj]) * 0.8
            d2 = dx * dx + dy * dy
            m = (d2 < mind * mind) & (d2 > 1e-9)
            if not m.any():
                return
            ii = ii[m]; jj = jj[m]
            dx = dx[m]; dy = dy[m]; mind = mind[m]
            # step28 chaff_nosep: 爬虫(10) 碰撞对全关 (用户备选口径 —— 量化
            # 爬虫推挤对整体的影响)
            if self.opts.get("chaff_nosep", 0):
                keep = ~(np.isin(self.mech_id[ii], (10,))
                         | np.isin(self.mech_id[jj], (10,)))
                if not keep.all():
                    ii = ii[keep]; jj = jj[keep]
                    dx = dx[keep]; dy = dy[keep]; mind = mind[keep]
                    if len(ii) == 0:
                        return
            # step29 chaff_xsep (用户定版 Q-B(a)): 只关爬虫(10)↔其他单位
            # 的碰撞, 保留爬虫↔爬虫 (爬虫围目标成圈, 内部碰撞该留;
            # C20_01 火神被爬虫推着走 / cb_vulcan_dps 爬虫堆积主病灶)。
            # chaff_xsep=2 扩到 尖牙(9)+爬虫(10) (cal 标定: 带盾尖牙纵队
            # 伤害只打出 oracle 的 ~20%, 杂兵流动学同族)
            elif self.opts.get("chaff_xsep", 0):
                _cx = int(self.opts.get("chaff_xsep", 1) or 1)
                _xset = (9, 10) if _cx >= 2 else (10,)
                _ai = np.isin(self.mech_id[ii], _xset)
                _aj = np.isin(self.mech_id[jj], _xset)
                keep = ~(_ai ^ _aj)
                if not keep.all():
                    ii = ii[keep]; jj = jj[keep]
                    dx = dx[keep]; dy = dy[keep]; mind = mind[keep]
                    if len(ii) == 0:
                        return
            d = np.sqrt(dx * dx + dy * dy)
            push = (mind - d) / np.maximum(d, 1e-9) * fac
            # step29 sep_tan: 友军对的分离方向沿切线滑开 (寻路绕行的代理
            # —— cb_vulcan_dps 爬虫堆积火神正后方: 真实游戏爬虫自己绕开,
            # 只轻微挤压不轴向顶推; C23_02 先知 16 速应越过狼蛛而不是顶着
            # 它跑)。sep_tan = 切向分量占比 (0=纯轴向, 1=纯切向)。
            _tan = float(self.opts.get("sep_tan", 0) or 0)
            if _tan > 0:
                ally_t = self.team[ii] == self.team[jj]
                if ally_t.any():
                    ln = np.maximum(d, 1e-9)
                    ux, uy = dx / ln, dy / ln      # 轴向 (推开)
                    tx, ty = -uy, ux               # 切向 (滑开)
                    mx = ux * (1.0 - _tan) + tx * _tan
                    my = uy * (1.0 - _tan) + ty * _tan
                    mln = np.maximum(np.sqrt(mx * mx + my * my), 1e-9)
                    mx, my = mx / mln * d, my / mln * d   # 保持原位移量纲
                    dx = np.where(ally_t, mx, dx)
                    dy = np.where(ally_t, my, dy)
            # towers and buildings never move; unit-static pairs dump the
            # full push on the unit (walls softly block walker lanes)
            tw_i = self.is_tower[ii] | self.is_bld[ii]
            tw_j = self.is_tower[jj] | self.is_bld[jj]
            push_i = np.where(tw_j & ~tw_i, push * 2.0, np.where(tw_i, 0.0, push))
            push_j = np.where(tw_i & ~tw_j, push * 2.0, np.where(tw_j, 0.0, push))
            # step26 P1 沙虫潜地 (用户: 潜的时候不会推动我方的兵种):
            # 潜地沙虫 (攻击范围内无敌) 对友军不施推力, 重叠全由自身
            # 承担 (友军不动); 对敌照常互推。
            if (self.opts.get("sw_burrow", 0) or self.opts.get("sw_dive", 0)) \
                    and getattr(self, "is_sw", None) is not None \
                    and self.is_sw.any():
                bur = self.is_sw & (~self.dead) & (self.target < 0)
                b_i = bur[ii] & (self.team[ii] == self.team[jj])
                b_j = bur[jj] & (self.team[jj] == self.team[ii])
                push_i = np.where(b_i, push * 2.0, push_i)
                push_j = np.where(b_i, 0.0, push_j)
                push_j = np.where(b_j, push * 2.0, push_j)
                push_i = np.where(b_j, 0.0, push_i)
            # step28 chaff_cover: 快杂兵 (尖牙9/爬虫10) 不把更慢的己方前排
            # 顶向前 (review n2_28_24: 爬虫应掩护狼蛛而不是推着它走; C13 台风
            # +鬼鳐 镜像#2 同源) —— chaff 对更慢己方的推力豁免, 重叠全由
            # chaff 自身承担; 对敌照常互推。
            if self.opts.get("chaff_cover", 0):
                ally = self.team[ii] == self.team[jj]
                chi = ally & np.isin(self.mech_id[ii], (9, 10)) \
                    & (self.move_speed[jj] < self.move_speed[ii] - 1e-9)
                chj = ally & np.isin(self.mech_id[jj], (9, 10)) \
                    & (self.move_speed[ii] < self.move_speed[jj] - 1e-9)
                if chi.any():
                    push_i = np.where(chi, push * 2.0, push_i)
                    push_j = np.where(chi, 0.0, push_j)
                if chj.any():
                    push_j = np.where(chj, push * 2.0, push_j)
                    push_i = np.where(chj, 0.0, push_i)
            np.add.at(self.x, ii, dx * push_i)
            np.add.at(self.y, ii, dy * push_i)
            np.add.at(self.x, jj, -dx * push_j)
            np.add.at(self.y, jj, -dy * push_j)

    def _occlusion_mask(self):
        """step26 P2 遮挡简化模型: 远程单位 (range>=occl_min_rng) 到当前
        目标的线段被任一友军身体挡住 (沿线投影在 (r_i+r_j)*occl_gap 之外、
        L 之内, 横向 < (r_i+r_j)*occl_w) → 停火。塔/建筑/装置不算遮挡体,
        自身与目标不算。每 0.5s 由 step() 重算一次 (成本控制)。"""
        out = np.zeros(self.n, dtype=bool)
        sub = np.where((~self.dead) & (self.target >= 0) & (~self.is_melee)
                       & (self.range >= float(self.opts.get("occl_min_rng", 40)))
                       & (~self.is_tower) & (~self.is_bld) & (~self.is_device))[0]
        blk = np.where((~self.dead) & (~self.is_tower) & (~self.is_bld)
                       & (~self.is_device))[0]
        if not len(sub) or not len(blk):
            return out
        gap = float(self.opts.get("occl_gap", 1.2))
        wid = float(self.opts.get("occl_w", 1.0))
        tt = self.target[sub]
        vx = self.x[tt] - self.x[sub]
        vy = self.y[tt] - self.y[sub]
        ln = np.sqrt(vx * vx + vy * vy)
        ok = ln > 1e-6
        sub = sub[ok]
        if not len(sub):
            return out
        vx = vx[ok] / ln[ok]
        vy = vy[ok] / ln[ok]
        ln = ln[ok]
        bx = self.x[blk][None, :] - self.x[sub][:, None]
        by = self.y[blk][None, :] - self.y[sub][:, None]
        rr = self.radius[sub][:, None] + self.radius[blk][None, :]
        same = self.team[blk][None, :] == self.team[sub][:, None]
        selfrow = np.zeros((len(sub), len(blk)), dtype=bool)
        selfrow[np.arange(len(sub)), np.searchsorted(blk, sub)] = True
        along = bx * vx[:, None] + by * vy[:, None]
        lateral = np.abs(bx * vy[:, None] - by * vx[:, None])
        m = same & (~selfrow) & (along > rr * gap) & (along < ln[:, None] - rr) \
            & (lateral < rr * wid)
        out[sub[m.any(axis=1)]] = True
        return out

    # ---------- trace ----------
    def _emit_frame(self):
        parts = ["%.1f" % self.time]
        alive = ~self.dead
        uid = self.uid; tm = self.team; mid = self.mech_id
        xs = self.x.astype(np.int32); ys = self.y.astype(np.int32)
        hps = np.maximum(self.hp, 0).astype(np.int32)
        for i in range(self.n):
            parts.append("%d,%d,%d,%d,%d,%d,%d" % (
                uid[i], tm[i], mid[i], 1 if alive[i] else 0, xs[i], ys[i], hps[i]))
        self.trace.append("|".join(parts))

    # ---------- burn fields ----------
    def _burned_mask(self):
        # alive units standing inside an ENEMY burning patch ("被引燃"):
        # the patch hurts the caster's enemies, so membership flips team.
        # step19: patches with a finite end time expire (fireIntensify /
        # 燃烧弹 tech patches); legacy whole-fight patches have no end.
        out = np.zeros(self.n, dtype=bool)
        for burn in self._burns:
            team, bx, by, dps, rad = burn[:5]
            if dps <= 0:
                continue
            if len(burn) > 5 and burn[5] < self.time:
                continue
            foes = np.where((~self.dead) & (self.team != team))[0]
            if not len(foes):
                continue
            dx = self.x[foes] - bx
            dy = self.y[foes] - by
            inside = np.sqrt(dx * dx + dy * dy) - self.radius[foes] <= rad
            out[foes[inside]] = True
        return out

    # ---------- step5 battlefield skills (任务书 §4/§6/§7) ----------
    def _step5_finalize(self):
        """Deterministic pre-fight setup: shield-clip ground areas at drop
        time (permanent - the shield disappearing later never regrows oil),
        seed the storm RNGs from the battle seed and select beacon members
        by their own member position (multi-module cards split correctly)."""
        import random as _random
        from .battlefield.effects.areas import capsule_spine
        seed = int(getattr(self, "_battle_seed", 0) or 0)
        # ground areas: clip generation under live barriers (BOTH teams -
        # the frozen rule says 护盾覆盖的地面, no team qualifier)
        bars = np.where((self.mech_id == DEVICE_BARRIER) & (~self.dead))[0]
        for a in self._areas:
            if not a.get("shield_block") or not len(bars):
                continue
            keep = []
            for sx, sy in capsule_spine(a["ax"], a["ay"], a["bx"], a["by"],
                                        a["radius"]):
                covered = False
                for bi in bars:
                    if math.hypot(self.x[bi] - sx, self.y[bi] - sy) \
                            <= self.radius[bi] + 1e-9:
                        covered = True
                        break
                if not covered:
                    keep.append((sx, sy))
            a["samples"] = keep
            if not keep:
                a["dead"] = True      # fully shield-covered: nothing lands
                if self.trace_enabled:
                    self.trace.append("E|0.00|area_blocked|%d|%s"
                                      % (a["team"], a["kind"]))
        # storms: rng = pure function of (battle seed, ref) - same seed
        # replays identically, different seeds distribute (T11)
        for s in self._storms:
            tag = 0
            for ch in s["ref"]:
                tag = (tag * 31 + ord(ch)) & 0xFFFFFFFF
            s["rng"] = _random.Random((seed & 0xFFFFFFF) * 1000003 + tag)
            s["end"] = s["duration"]
        # beacons: member-level selection at t=0 (frozen §2.1.3: center
        # distance <= 40, member's own position; statics never join - QA-6
        # walls/cannons unaffected; air units DO follow)
        for team, pts, rad in self._beacons:
            (ax, ay), (bx, by), (cx, cy) = pts[0], pts[1], pts[2]
            sel = np.where((~self.dead) & (self.team == team)
                           & (~self.is_tower) & (~self.is_device)
                           & (~self.is_bld))[0]
            for u in sel:
                u = int(u)
                if math.hypot(self.x[u] - ax, self.y[u] - ay) > rad + 1e-9:
                    continue
                ox, oy = self.x[u] - ax, self.y[u] - ay
                self._wp_active[u] = True
                self._wp_stage[u] = 0
                self._wp_x0[u], self._wp_y0[u] = bx + ox, by + oy
                self._wp_x1[u], self._wp_y1[u] = cx + ox, cy + oy
                if self.trace_enabled:
                    self.trace.append("E|0.00|waypoint|%d|%d|%.0f,%.0f"
                                      % (team, int(self.uid[u]),
                                         self._wp_x0[u], self._wp_y0[u]))
        self._bursts.sort(key=lambda b: b[0])

    def _step5_bursts(self):
        """Scheduled instant effects (EMP detonation / photon field)."""
        while self._bursts and self._bursts[0][0] <= self.time:
            t0, kind, team, prm = self._bursts.pop(0)
            if kind == "emp":
                self._step5_emp_burst(team, prm)
            else:
                self._step5_photon_burst(team, prm)

    def _step5_emp_burst(self, team, prm):
        """任务书 §7 T7 (user-frozen §2.1.6): 20000 damage to shields in
        radius; barrier-covered GROUND units are immune; everyone else gets
        the tech-disable + speed x slow_mult status for `duration` seconds
        (the engine EMP channel already carries speed x0.60 + tech-off)."""
        x, y, radius = prm["x"], prm["y"], prm["radius"]
        sdur, dur = prm["shield_damage"], prm["duration"]
        bars = np.where((self.mech_id == DEVICE_BARRIER) & (~self.dead))[0]
        for bi in bars:
            bi = int(bi)
            if math.hypot(self.x[bi] - x, self.y[bi] - y) - self.radius[bi] \
                    > radius:
                continue
            self.hp[bi] -= sdur
            if self.trace_enabled:
                self.trace.append("E|%.2f|shield_damage|%d|%.0f|%.0f,%.0f"
                                  % (self.time, int(self.team[bi]), sdur,
                                     self.x[bi], self.y[bi]))
            if self.hp[bi] <= 0:
                self._on_barrier_down(bi, -1)
        live_bars = [(int(bi), self.x[bi], self.y[bi], self.radius[bi])
                     for bi in np.where((self.mech_id == DEVICE_BARRIER)
                                        & (~self.dead) & (self.hp > 0))[0]]
        foes = np.where((~self.dead) & (self.team != team))[0]
        for u in foes:
            u = int(u)
            if math.hypot(self.x[u] - x, self.y[u] - y) - self.radius[u] \
                    > radius:
                continue
            if not self.is_fly[u] and any(
                    math.hypot(self.x[u] - bx, self.y[u] - by) <= br
                    for _, bx, by, br in live_bars):
                continue     # barrier-covered ground unit: fully immune
            # step32 T8: 抗干扰/光子涂层 equipment immunity blocks the whole
            # EMP package (disable + slow; 分量断言见 test_equipment_runtime).
            # Checked before the photon branch so the blocked note carries
            # the equipment source (photon keeps the same block outcome).
            if self._status_immune(u, "emp"):
                self._status_block_note(u, "emp")
                continue
            if self.photon_until[u] > self.time:
                if self.trace_enabled:
                    self.trace.append("E|%.2f|status_blocked|%d|photon|emp"
                                      % (self.time, int(self.uid[u])))
                continue
            if self.shield[u] > 0:
                take = min(float(self.shield[u]), sdur)
                self.shield[u] -= take
                if self.trace_enabled:
                    self.trace.append("E|%.2f|shield_damage|%d|%.0f|%.0f,%.0f"
                                      % (self.time, int(self.uid[u]), take,
                                         self.x[u], self.y[u]))
                if self.shield[u] > 0:
                    continue      # the shield ate the burst: no status
            self.emp_until[u] = max(float(self.emp_until[u]),
                                    self.time + dur)
            if self.trace_enabled:
                self.trace.append("E|%.2f|status_apply|%d|emp|%.0f"
                                  % (self.time, int(self.uid[u]), dur))

    def _step5_photon_burst(self, team, prm):
        """任务书 §7 T8 (user-frozen §2.1.9 + QA-4): friendly units inside
        the swept area gain the photon status for `duration` seconds —
        damage taken x0.70 and immunity to EMP / 引燃 / 酸液 / 退化光束.
        Gaining photon CLEARS those existing statuses (QA-4 user ruling)."""
        from .battlefield.effects.areas import capsule_hit
        ax, ay, bx, by, radius = (prm["ax"], prm["ay"], prm["bx"], prm["by"],
                                  prm["radius"])
        self._photon_taken = prm["dmg_taken_mult"]
        friends = np.where((~self.dead) & (self.team == team)
                           & (~self.is_tower) & (~self.is_device)
                           & (~self.is_bld))[0]
        for u in friends:
            u = int(u)
            if not capsule_hit(self.x[u], self.y[u], ax, ay, bx, by, radius):
                continue
            had = (self.emp_until[u] > self.time) or \
                  (self.burn_pct_until[u] > self.time)
            self.emp_until[u] = -1.0        # QA-4: photon clears them
            self.burn_pct_until[u] = -1.0
            self.photon_until[u] = self.time + prm["duration"]
            if self.trace_enabled:
                self.trace.append("E|%.2f|status_apply|%d|photon|%.0f%s"
                                  % (self.time, int(self.uid[u]),
                                     prm["duration"],
                                     "|clears" if had else ""))

    def _step5_areas_tick(self):
        """Every-frame ground-area pass: membership masks, oil ignition,
        ion beams, storm strikes. Slow/range/vuln factors are recomputed
        whole-cloth each tick (no drift)."""
        from .battlefield.effects.areas import (capsule_hit, circle_hit,
                                                moving_circle_at,
                                                capsule_spine)
        self._ev_victim = getattr(self, "_ev_victim", None) or []
        self._ev_dmg = getattr(self, "_ev_dmg", None) or []
        self._ev_killer = getattr(self, "_ev_killer", None) or []
        t = self.time
        oil_on = np.zeros(self.n, dtype=bool)
        smoke_on = np.zeros(self.n, dtype=bool)
        acid_on = np.zeros(self.n, dtype=bool)
        for a in self._areas:
            if a["dead"]:
                continue
            foes = np.where((~self.dead) & (self.team != a["team"])
                            & (~self.is_tower) & (~self.is_device)
                            & (~self.is_bld))[0]
            if not len(foes):
                continue
            hit = np.array([capsule_hit(self.x[u], self.y[u],
                                        a["ax"], a["ay"],
                                        a["bx"], a["by"], a["radius"],
                                        float(self.radius[u]))
                            for u in foes])
            inside = foes[hit]
            if a["kind"] == "oil":
                # 黏油 slows GROUND units (oil lies on the ground; air cal)
                inside = inside[~self.is_fly[inside]]
                oil_on[inside] = True
            elif a["kind"] == "smoke":
                smoke_on[inside] = True
            elif a["kind"] == "acid":
                # photon immunity blocks the acid status entirely (T8)
                # step32 T8: equipment immunity (光子涂层/抗干扰) likewise
                keep = np.array([self.photon_until[u] <= t
                                 and not self._status_immune(int(u), "acid")
                                 for u in inside], dtype=bool)
                inside = inside[keep]
                acid_on[inside] = True
                self._acid_vuln = a["vuln_mult"]
                if a["pct_dps"] > 0:
                    for u in inside:
                        # 3% maxHP/s DoT, killerless (own DoT never
                        # re-amplified)
                        self._ev_victim.append(int(u))
                        self._ev_dmg.append(float(self.max_hp[u])
                                            * a["pct_dps"] * DT)
                        self._ev_killer.append(-1)
        # oil ignition: any live fire touching the oil turns it into flame
        # circles along its (surviving) spine, carrying the IGNITING fire's
        # dps; the oil object is GONE afterwards (任务书 §2.1.5/T6)
        for a in self._areas:
            if a["dead"] or a["kind"] != "oil" or not self._burns:
                continue
            samples = a.get("samples") or capsule_spine(a["ax"], a["ay"],
                                                        a["bx"], a["by"],
                                                        a["radius"])
            lit_dps = None
            for fire in self._burns:
                fteam, fx, fy, fdps, frad = fire[:5]
                if fdps <= 0:
                    continue
                if len(fire) > 5 and fire[5] < t:
                    continue
                for sx, sy in samples:
                    if math.hypot(fx - sx, fy - sy) <= frad + a["radius"]:
                        lit_dps = fdps
                        break
                if lit_dps is not None:
                    break
            if lit_dps is None:
                continue
            for sx, sy in samples:
                self._burns.append([int(a["team"]), float(sx), float(sy),
                                    float(lit_dps), float(a["radius"])])
            a["dead"] = True
            a["ignited"] = True
            if self.trace_enabled:
                self.trace.append("E|%.2f|area_expire|%d|oil|ignite"
                                  % (t, a["team"]))
        # smoke: edge-triggered in-place range scaling (the emp_full
        # pattern - scale on enter, restore on leave)
        smoke_mult = next((a["range_mult"] for a in self._areas
                           if a["kind"] == "smoke" and not a["dead"]), None)
        if smoke_mult is not None:
            enter = smoke_on & (~self._smoke_on)
            leave = self._smoke_on & (~smoke_on)
            if np.any(enter):
                self.range[enter] *= smoke_mult
                self.stop_dist[enter] = np.where(
                    self.range[enter] > 0, self.range[enter], 5.0)
            if np.any(leave):
                self.range[leave] /= smoke_mult
                self.stop_dist[leave] = np.where(
                    self.range[leave] > 0, self.range[leave], 5.0)
        self._smoke_on = smoke_on
        # slow product: oil x slow_mult; storm slow x slow_mult
        fac = np.ones(self.n)
        oil_mult = next((a["slow_mult"] for a in self._areas
                         if a["kind"] == "oil" and not a["dead"]), None)
        if oil_mult is not None:
            fac[oil_on] *= oil_mult
        storm_slow = self._storm_slow_until > t
        if np.any(storm_slow):
            fac[storm_slow] *= 0.60
        self._area_fac = fac
        self._acid_on = acid_on
        # ion beams: centre moves A->B at `speed`; enemies in the current
        # circle take dps*dt (killerless; no ground trail - T10 default)
        for io in self._ions:
            if io["done"]:
                continue
            cx, cy, arrived = moving_circle_at(io["ax"], io["ay"],
                                               io["bx"], io["by"],
                                               io["speed"], t)
            foes = np.where((~self.dead) & (self.team != io["team"])
                            & (~self.is_tower) & (~self.is_device)
                            & (~self.is_bld))[0]
            for u in foes:
                u = int(u)
                if not circle_hit(self.x[u], self.y[u], cx, cy,
                                  io["radius"], float(self.radius[u])):
                    continue
                if self.photon_until[u] > t:
                    continue
                self._ev_victim.append(u)
                self._ev_dmg.append(io["dps"] * DT)
                self._ev_killer.append(-1)
            if arrived:
                io["done"] = True
                if self.trace_enabled:
                    self.trace.append("E|%.2f|area_expire|%d|ion"
                                      % (t, io["team"]))
        # storm strikes: seeded, provisional distribution (T11): each strike
        # picks a RANDOM enemy unit currently inside the storm circle and
        # lands on it (uniform choice; splash still applies around the point).
        # Same seed replays identically; different seeds give statistics
        for s in self._storms:
            while s["next_t"] <= t and s["next_t"] <= s["end"]:
                foes = np.where((~self.dead) & (self.team != s["team"])
                                & (~self.is_tower) & (~self.is_device)
                                & (~self.is_bld))[0]
                cands = [int(u) for u in foes
                         if circle_hit(self.x[u], self.y[u], s["cx"], s["cy"],
                                       s["radius"], float(self.radius[u]))]
                if cands:
                    u = cands[int(s["rng"].random() * len(cands))]
                    px, py = float(self.x[u]), float(self.y[u])
                    for v2 in foes:
                        v2 = int(v2)
                        if not circle_hit(self.x[v2], self.y[v2], px, py,
                                          s["splash"],
                                          float(self.radius[v2])):
                            continue
                        if self.photon_until[v2] > t:
                            continue
                        self._ev_victim.append(v2)
                        self._ev_dmg.append(s["damage"])
                        self._ev_killer.append(-1)
                        self._storm_slow_until[v2] = max(
                            float(self._storm_slow_until[v2]),
                            t + s["slow_duration"])
                else:
                    px = py = float("nan")     # nobody inside this strike
                if self.trace_enabled:
                    self.trace.append("E|%.2f|strike|%d|storm|%.0f,%.0f"
                                      % (s["next_t"], s["team"], px, py))
                s["next_t"] += s["interval"]

    def area_results(self):
        """step5 任务书 §6: (ref, ignited) per reportable ground area —
        settlement drops ignited oils instead of carrying them on."""
        return tuple((str(a["ref"]), bool(a["ignited"]))
                     for a in self._areas if a.get("report")
                     and a.get("ref"))

    # ---------- main loop ----------
    def step(self, tick):
        self.time = (tick + 1) * DT
        # damage event queue (step19 behavior ticks below enqueue directly;
        # _apply_damage drains + clears at end of tick)
        self._ev_victim = getattr(self, "_ev_victim", None) or []
        self._ev_dmg = getattr(self, "_ev_dmg", None) or []
        self._ev_killer = getattr(self, "_ev_killer", None) or []
        # step5: scheduled bursts (EMP detonation / photon field) land
        # before combat so their statuses gate this tick's fire
        if self._bursts:
            self._step5_bursts()
        # step9: teleport growth + gating mask. Growth runs before combat so a
        # unit hit at time t has maxHP * t / delay HP available; the kill scan
        # in _apply_damage only fires on damaged victims, so the 0-HP start
        # never kills an untouched teleporter.
        sp = (~self.dead) & (self.spawn_at > 0) & (self.spawn_at > self.time)
        if np.any(sp):
            grow = np.minimum(self.max_hp[sp] / self.spawn_at[sp] * DT,
                              np.maximum(self.max_hp[sp] - self.hp[sp], 0.0))
            self.hp[sp] += grow
        self._spawning = sp
        # step16 战地维修: alive units regenerate a fraction of maxHP per
        # second (recoveryLifeRate/recoveryDuration = 4.5%/s).
        # step18 T10b: units standing in an enemy burning field do NOT
        # regenerate (user: 维修类回血在被引燃时只计算引燃)
        # step19 战地维修口径修正: desc = "受伤后每秒自动恢复" —— 回血门槛是
        # 本场受过伤(引擎此前从 t=0 就回, 高估了无伤开局的维修收益)
        if np.any(self.regen > 0):
            rg = (~self.dead) & (self.regen > 0)
            if self.opts.get("regen_gate", 1):
                rg &= self._damaged
            if np.any(rg) and self.opts.get("burn_regen", 1) and self._burns:
                rg &= ~self._burned_mask()
            if np.any(rg):
                rg &= ~(self.emp_until > self.time)
                rg &= ~(self.burn_pct_until > self.time)
            if np.any(rg):
                self.hp[rg] = np.minimum(self.max_hp[rg],
                                         self.hp[rg] + self.max_hp[rg] * self.regen[rg] * DT)
        # step19 EMP: effective tech damage multiplier (1.0 while disabled)
        if np.any(self.emp_until > self.time):
            self._tech_eff = np.where(self.emp_until > self.time,
                                      1.0, self.tech_dmg)
        else:
            self._tech_eff = self.tech_dmg
        # step20 T7 emp_full: EMP also reverts the tech-only parts of
        # range / attack interval / move speed / splash (Technology.Deactive
        # → RemoveData removes ALL channels the tech contributes). Edge
        # triggered: subtract on disable, add back on expiry.
        if self.opts.get("emp_full", 1) and np.any(self.rng_td != 0):
            emp_on = (self.emp_until > self.time) & (~self.dead)
            newly = emp_on & (~self._emp_on)
            recovered = (~emp_on) & self._emp_on
            if np.any(newly):
                self.range[newly] -= self.rng_td[newly]
                self.stop_dist[newly] = np.where(
                    self.range[newly] > 0, self.range[newly], 5.0)
                self.atk_dur[newly] = np.maximum(
                    0.01, self.atk_dur[newly] - self.dur_td[newly])
                self.move_speed[newly] = np.maximum(
                    0.0, self.move_speed[newly] - self.spd_td[newly])
                self.splash[newly] = np.maximum(
                    0.0, self.splash[newly] - self.spl_td[newly])
                if self.trace_enabled:
                    for i in np.where(newly)[0]:
                        self.trace.append("E|%.2f|emp_full|%d|%d" % (
                            self.time, int(self.team[i]), int(self.uid[i])))
            if np.any(recovered):
                self.range[recovered] += self.rng_td[recovered]
                self.stop_dist[recovered] = np.where(
                    self.range[recovered] > 0, self.range[recovered], 5.0)
                self.atk_dur[recovered] += self.dur_td[recovered]
                self.move_speed[recovered] += self.spd_td[recovered]
                self.splash[recovered] += self.spl_td[recovered]
            self._emp_on = emp_on
        # step19 引燃 pct-burn: 6% maxHP/s for 2s after each hit (routed
        # through the damage event queue so deaths settle normally)
        if np.any(self.burn_pct_until > self.time):
            bm = (~self.dead) & (self.burn_pct_until > self.time)
            for i in np.where(bm)[0]:
                self._ev_victim.append(int(i))
                self._ev_dmg.append(float(self.max_hp[i] * self.burn_pct_rate[i] * DT))
                self._ev_killer.append(-1)
        # step19 焦土 11020: below 50% HP the unit charges the nearest
        # enemy and self-destructs on contact (damage = remaining HP,
        # 40m radius + ground fire dps 100 radius 40 for 10s)
        if np.any(self.scorch & ~self.scorch_on):
            trig = np.where(self.scorch & ~self.scorch_on & (~self.dead)
                            & (self.hp < self.max_hp * 0.5))[0]
            for i in trig:
                self.scorch_on[i] = True
                if self.trace_enabled:
                    self.trace.append("E|%.2f|scorch|%d|%d" % (
                        self.time, int(self.team[i]), int(self.uid[i])))
        if np.any(self.scorch_on):
            for i in np.where(self.scorch_on & (~self.dead))[0]:
                i = int(i)
                foes = np.where((~self.dead) & (self.team != self.team[i]))[0]
                if not len(foes):
                    continue
                d = np.hypot(self.x[foes] - self.x[i], self.y[foes] - self.y[i]) \
                    - self.radius[foes] - self.radius[i]
                if np.min(d) > 3.0:
                    continue
                # contact: detonate
                boom = float(self.hp[i])
                hit = foes[np.hypot(self.x[foes] - self.x[i],
                                    self.y[foes] - self.y[i]) - self.radius[foes] <= 40.0]
                for vv in hit:
                    self._ev_victim.append(int(vv))
                    self._ev_dmg.append(boom)
                    self._ev_killer.append(-1)
                self._burns.append([int(self.team[i]), float(self.x[i]),
                                    float(self.y[i]), 100.0, 40.0,
                                    self.time + 10.0])
                self.hp[i] = 0.0
                self.dead[i] = True
                self.total_kills += 1
                self.kills.append({"t": round(self.time, 2), "killer": int(self.uid[i]),
                                   "victim": int(self.uid[i]), "kmech": 20,
                                   "vmech": int(self.mech_id[i]),
                                   "kteam": int(self.team[i])})
                if self.trace_enabled:
                    self.trace.append("E|%.2f|scorch_boom|%d|%d|%.0f" % (
                        self.time, int(self.team[i]), int(self.uid[i]), boom))
                self._grant_unowned_exp(i)
        # step20 T6 tech_summon: supportUnitTechnologies periodic summons -
        # every createDuration seconds each ALIVE summoner card activates
        # createCountPerTime pre-allocated rows at its first living unit's
        # position (+ tech position offsets, cycled). A dead factory stops
        # producing; maxCount>0 caps simultaneously-live summons per tech.
        if self._summon_pool:
            for ent in self._summon_pool:
                while ent["nxt"] <= self.time and ent["rows"]:
                    members = np.where((self.card_idx == ent["card"])
                                       & (~self.dead))[0]
                    if not len(members):
                        ent["nxt"] = self.time + ent["period"]
                        break
                    ent["alive"] = [u for u in ent["alive"] if not self.dead[u]]
                    slots = (ent["maxc"] - len(ent["alive"])) \
                        if ent["maxc"] > 0 else len(ent["rows"])
                    take = min(ent["cnt"], len(ent["rows"]), max(0, slots))
                    anch = int(members[0])
                    offs = ent["pos"] or [[0.0, 0.0]]
                    for j in range(take):
                        g = ent["rows"].pop(0)
                        ox, oy = offs[(len(ent["alive"]) + j) % len(offs)]
                        self.dead[g] = False
                        self.hp[g] = self.max_hp[g]
                        self.x[g] = self.x[anch] + float(ox)
                        self.y[g] = self.y[anch] + float(oy)
                        self.target[g] = -1
                        self.state[g] = IDLE
                        self.state_t[g] = 0.0
                        self.first_attack[g] = True
                        self.mv_target[g] = -1
                        ent["alive"].append(g)
                        if self.trace_enabled:
                            self.trace.append("E|%.2f|summon|%d|%d|%d" % (
                                self.time, int(self.team[g]), int(self.uid[g]),
                                int(self.mech_id[g])))
                    ent["nxt"] += ent["period"]
        # step32 T6/T12: 装备生产线激活 — 每period一批 count 个, 共 batches
        # 批; carrier 死亡取消剩余队列 (任务书 T12 默认, oracle 待证);
        # 召唤行不受 summon_row_cap/summon_max_batch 管 (文案批次数即上限,
        # 池容量按文案理论最大值预分配, 不静默截断)。
        if self._eq_pool:
            for ent in self._eq_pool:
                while ent["done"] < ent["batches"] and ent["nxt"] <= self.time \
                        and ent["rows"]:
                    members = np.where((self.card_idx == ent["card"])
                                       & (~self.dead))[0]
                    if not len(members):
                        ent["rows"] = []
                        if self.trace_enabled:
                            self.trace.append(
                                "E|%.2f|eq_summon_cancel|%d|%.2f"
                                % (self.time, ent["card"], self.time))
                        break
                    take = min(ent["cnt"], len(ent["rows"]))
                    anch = int(members[0])
                    for j in range(take):
                        g = ent["rows"].pop(0)
                        _ang = 2.0 * math.pi * j / max(1, ent["cnt"])
                        self.dead[g] = False
                        self.hp[g] = self.max_hp[g]
                        self.x[g] = self.x[anch] + math.cos(_ang) * 8.0
                        self.y[g] = self.y[anch] + math.sin(_ang) * 8.0
                        self.target[g] = -1
                        self.state[g] = IDLE
                        self.state_t[g] = 0.0
                        self.first_attack[g] = True
                        self.mv_target[g] = -1
                        if self.trace_enabled:
                            self.trace.append("E|%.2f|eq_summon|%d|%d|%d" % (
                                self.time, int(self.team[g]),
                                int(self.uid[g]), int(self.mech_id[g])))
                    ent["done"] += 1
                    ent["nxt"] += ent["period"]
                    if self.trace_enabled:
                        self.trace.append("E|%.2f|eq_summon_batch|%d|%d|%d" % (
                            self.time, ent["card"], ent["done"], take))
        # step29 蜘蛛雷: 周期召唤 + 接敌自爆。雷行是预分配的死行, 每
        # period 在持有者身前 (y 镜像向敌) ±20,40 布 cnt 枚; 雷行走向最近
        # 敌 (mech 1002 速 24), 边缘接触 (<=2m) 即爆: 全额伤害 12m 溅射,
        # 自身消亡 (kills 归 mech 1002)。
        if self._mine_pool:
            for ent in self._mine_pool:
                while ent["nxt"] <= self.time and ent["rows"]:
                    members = np.where((self.card_idx == ent["card"])
                                       & (~self.dead))[0]
                    if not len(members):
                        ent["nxt"] = self.time + ent["period"]
                        break
                    live = np.where(self.is_mine
                                    & (self.team == self.cards[ent["card"]]["team"])
                                    & (~self.dead))[0]
                    slots = (ent["maxc"] - len(live)) \
                        if ent["maxc"] > 0 else len(ent["rows"])
                    take = min(ent["cnt"], len(ent["rows"]), max(0, slots))
                    anch = int(members[0])
                    ys = -1.0 if self.cards[ent["card"]]["team"] == 1 else 1.0
                    offs = ent["pos"] or [[20.0, 40.0], [-20.0, 40.0]]
                    for j in range(take):
                        g = ent["rows"].pop(0)
                        ox, oy = offs[(j) % len(offs)]
                        self.dead[g] = False
                        self.hp[g] = self.max_hp[g]
                        self.x[g] = self.x[anch] + float(ox)
                        self.y[g] = self.y[anch] + float(oy) * ys
                        self.target[g] = -1
                        self.state[g] = IDLE
                        self.state_t[g] = 0.0
                        self.first_attack[g] = True
                        self.mv_target[g] = -1
                        if self.trace_enabled:
                            self.trace.append("E|%.2f|mine|%d|%d" % (
                                self.time, int(self.team[g]), int(self.uid[g])))
                    ent["nxt"] += ent["period"]
        if np.any(self.is_mine & (~self.dead)):
            for i in np.where(self.is_mine & (~self.dead))[0]:
                i = int(i)
                foes = np.where((~self.dead) & (self.team != self.team[i])
                                & (~self.is_device))[0]
                if not len(foes):
                    continue
                d = np.hypot(self.x[foes] - self.x[i], self.y[foes] - self.y[i]) \
                    - self.radius[foes] - self.radius[i]
                if np.min(d) > 2.0:
                    continue
                boom = float(self.base_dmg[i])
                for vv in foes:
                    dvv = math.hypot(self.x[vv] - self.x[i], self.y[vv] - self.y[i]) \
                        - self.radius[vv]
                    if dvv <= 12.0:
                        self._ev_victim.append(int(vv))
                        self._ev_dmg.append(boom)
                        self._ev_killer.append(int(i))
                self.hp[i] = 0.0
                self.dead[i] = True
                if self.trace_enabled:
                    self.trace.append("E|%.2f|mine_boom|%d|%d|%.0f" % (
                        self.time, int(self.team[i]), int(self.uid[i]), boom))
        # step19 燃烧弹 11028: every 16s the card lobs 2 incendiary shells at
        # random enemy positions (range band 40-160m, 12m patch, 10s life)
        if self._incend:
            for ent in self._incend:
                c, nxt, period = ent
                if self.time < nxt:
                    continue
                ent[1] = self.time + period
                ct = self.cards[c]["team"]
                foes = np.where((~self.dead) & (self.team != ct)
                                & (~self.is_tower) & (~self.is_device)
                                & (~self.is_bld))[0]
                if not len(foes):
                    continue
                rng = getattr(self, "_barrage_rng", None)
                if rng is None:
                    import numpy as _np
                    rng = self._barrage_rng = _np.random.RandomState(1401)
                for _ in range(2):
                    v = int(foes[rng.randint(len(foes))])
                    ang = rng.uniform(0.0, 2.0 * math.pi)
                    rad = rng.uniform(0.0, 10.0)
                    px = self.x[v] + math.cos(ang) * rad
                    py = self.y[v] + math.sin(ang) * rad
                    self._burns.append([self.cards[c]["team"], px, py, 352.0,
                                        12.0, self.time + 10.0])
        # step19 防空弹幕 1105: every 10s the card fires 16 AA missiles
        # (900 dmg, 7.5m splash) at air targets within 170m
        if np.any(self.aabar & ~self.dead):
            due = np.where(self.aabar & (~self.dead)
                           & (self._aabar_t <= self.time))[0]
            for i in due:
                self._aabar_t[i] = self.time + 10.0
                foes = np.where((~self.dead) & (self.team != self.team[i])
                                & self.is_fly)[0]
                if not len(foes):
                    continue
                rng = getattr(self, "_barrage_rng", None)
                if rng is None:
                    import numpy as _np
                    rng = self._barrage_rng = _np.random.RandomState(1401)
                for _ in range(16):
                    v = int(foes[rng.randint(len(foes))])
                    self._ev_victim.append(v)
                    self._ev_dmg.append(900.0)
                    self._ev_killer.append(-1)
        done = (~self.dead) & (self.spawn_at > 0) & (~self._spawn_done) \
            & (self.spawn_at <= self.time)
        if np.any(done):
            self._spawn_done |= done
            if self.trace_enabled:
                for i in np.where(done)[0]:
                    self.trace.append("E|%.2f|spawn|%d|%d" % (
                        self.time, self.uid[i], int(self.mech_id[i])))
        if tick % RETARGET_TICKS == 0:
            self._full_target_pass()
            self._update_magnets()
        else:
            self._validate_targets()
        self._ev_victim = getattr(self, "_ev_victim", [])
        self._ev_dmg = getattr(self, "_ev_dmg", [])
        self._ev_killer = getattr(self, "_ev_killer", [])
        # step8-B: scheduled strikes (导弹打击 etc.) land pre-batch; enemy
        # barriers absorb them via the normal redirect in _apply_damage
        while self._strike_k < len(self._strikes) \
                and self._strikes[self._strike_k][5] <= self.time:
            self._fire_strike(*self._strikes[self._strike_k])
            self._strike_k += 1
        # step22 T5-F2 定版: 火獾(20) 自爆 = 科技 11020 焦土冲击 (原表
        # extraWeaponTechnologies: 生命+80%, HP<50% 时冲锋自爆, 半径40m
        # 造成与剩余生命值相同的伤害 + 引燃地面 dps100/半径40; 用户口径
        # "自爆是科技, 基础=类似火神的普通攻击")。基础火獾无自爆。
        if self.opts.get("fb_boom", 1) and np.any((~self.dead) & (self.mech_id == 20)):
            for i in np.where((~self.dead) & (self.mech_id == 20))[0]:
                i = int(i)
                ci = int(self.card_idx[i])
                if ci < 0 or 11020 not in (self.cards[ci].get("techs") or ()):
                    continue
                if self.hp[i] >= 0.5 * self.max_hp[i]:
                    continue
                foes = np.where((~self.dead) & (self.team != self.team[i])
                                & (~self.is_tower) & (~self.is_device)
                                & (~self.is_bld))[0]
                dmg = float(self.hp[i])          # 伤害=剩余生命值 (表定版)
                if len(foes):
                    hit = foes[(np.hypot(self.x[foes] - self.x[i],
                                         self.y[foes] - self.y[i])
                                - self.radius[foes] <= 40.0)
                               & (~self.is_fly[foes])]
                    for v in hit:
                        self._ev_victim.append(int(v))
                        self._ev_dmg.append(dmg)
                        self._ev_killer.append(i)
                self._burns.append([int(self.team[i]), float(self.x[i]),
                                    float(self.y[i]), 100.0, 40.0,
                                    self.time + 10.0])
                if self.trace_enabled:
                    self.trace.append("E|%.2f|fb_boom|%d|%d|%.0f" % (
                        self.time, int(self.team[i]), int(self.uid[i]), dmg))
                self.hp[i] = 0.0
            newly_fb = np.where((~self.dead) & (self.mech_id == 20)
                                & (self.hp <= 0))[0]
            if len(newly_fb):
                self._process_deaths(newly_fb, {int(v): -1 for v in newly_fb})

        # step5 任务书 §4/§6: ground areas (oil slow / smoke range / acid
        # DoT+vuln), oil ignition, ion beams and storm strikes tick right
        # before the legacy fire fields so a conversion joins this pass
        if self._areas or self._ions or self._storms:
            self._step5_areas_tick()
        # step15: burning ground fields tick every frame (killerless DoT,
        # barriers absorb through the same event queue); step19: patches
        # with an end time expire
        if self._burns:
            # step22 T5-F2: 消防装置 4228 (猎犬) —— 原表 clearRangeItem
            # TechDatas: 清除周围 40m 地面燃烧/酸液/烟雾 (用户口径: 带清扫
            # 的猎犬很快扫掉火焰, 只剩爆炸伤害)
            sweepers = np.where((~self.dead) & (self.mech_id == 28))[0] \
                if self.opts.get("fire_sweep", 1) else []
            sweep_set = set()
            for h in sweepers:
                ci = int(self.card_idx[h])
                if ci >= 0 and 4228 in (self.cards[ci].get("techs") or ()):
                    sweep_set.add(int(h))
            if sweep_set:
                keep = []
                for burn in self._burns:
                    bx, by = burn[1], burn[2]
                    gone = False
                    for h in sweep_set:
                        if np.hypot(self.x[h] - bx, self.y[h] - by) <= 40.0:
                            gone = True
                            break
                    if not gone:
                        keep.append(burn)
                    elif self.trace_enabled:
                        self.trace.append("E|%.2f|sweep|%d|%.0f,%.0f" % (
                            self.time, int(self.team[h]), bx, by))
                self._burns[:] = keep
            for burn in self._burns:
                team, bx, by, dps, rad = burn[:5]
                if dps <= 0:
                    continue
                if len(burn) > 5 and burn[5] < self.time:
                    continue
                foes = np.where((~self.dead) & (self.team != team)
                                & (~self.is_tower) & (~self.is_device)
                                & (~self.is_bld))[0]
                if len(foes):
                    dx = self.x[foes] - bx
                    dy = self.y[foes] - by
                    inside = np.sqrt(dx * dx + dy * dy) - self.radius[foes] <= rad
                    for v in foes[inside]:
                        self._ev_victim.append(int(v))
                        self._ev_dmg.append(dps * DT)
                        self._ev_killer.append(-1)

        # step26 P1 沙虫钻地 (用户口径: 攻击范围内没有对方兵种时默认潜地;
        # Q-D 无科技沙虫也有钻出延迟): 索敌瞬间进入 sw_emerge 秒攻击锁定
        # 窗口 (钻出动画), 失去目标 = 重新潜地, 再索敌再吃窗口。
        if getattr(self, "is_sw", None) is not None and self.is_sw.any() \
                and (self.opts.get("sw_emerge", 0) or self.opts.get("sw_burrow", 0)
                     or self.opts.get("sw_dive", 0)):
            sw_alive = self.is_sw & (~self.dead)
            acq = sw_alive & (self.target >= 0) & (~self._sw_had_t)
            if np.any(acq):
                self._sw_emerge_until[acq] = self.time \
                    + float(self.opts.get("sw_emerge", 0) or 0)
                self._sw_had_t[acq] = True
            lost = sw_alive & (self.target < 0) & self._sw_had_t
            if np.any(lost):
                self._sw_had_t[lost] = False
        # step27 3623 复制: 沙虫索敌瞬间 (= 钻出口径) 入队召唤幼虫;
        # 出队时召唤者须存活, 池行耗尽即止 (位移取敌质心方向偏移)
        if self._swsummon_pool:
            if getattr(self, "is_sw", None) is not None:
                acq2 = self.is_sw & (~self.dead) & (self.target >= 0) \
                    & (~self._sw_had2)
                for w in np.where(acq2)[0]:
                    self._sw_had2[w] = True
                    ci = int(self.card_idx[w])
                    if ci in self._swsummon_pool:
                        ent = self._swsummon_pool[ci]
                        self._swsummon_q.append(
                            (int(w), self.time + ent["delay"], ci))
                lost2 = self.is_sw & (~self.dead) & (self.target < 0) \
                    & self._sw_had2
                self._sw_had2[lost2] = False
            still = []
            for wrow, due, ci in self._swsummon_q:
                if self.time < due or self.dead[wrow] or ci not in self._swsummon_pool:
                    if not self.dead[wrow]:
                        still.append((wrow, due, ci))
                    continue
                ent = self._swsummon_pool[ci]
                for _ in range(ent["cnt"]):
                    if not ent["rows"]:
                        break
                    g = ent["rows"].pop(0)
                    ox, oy = ent["offs"][len(ent["live"]) % len(ent["offs"])]
                    # 偏移朝敌方质心 (虫头方向)
                    foes = np.where((~self.dead) & (self.team != self.team[wrow]))[0]
                    fx = np.mean(self.x[foes]) - self.x[wrow] if len(foes) else 1.0
                    fy = np.mean(self.y[foes]) - self.y[wrow] if len(foes) else 0.0
                    fn = max(1e-6, math.hypot(fx, fy))
                    self.dead[g] = False
                    self.hp[g] = self.max_hp[g]
                    self.x[g] = self.x[wrow] + fx / fn * float(ox) \
                        - fy / fn * float(oy)
                    self.y[g] = self.y[wrow] + fy / fn * float(ox) \
                        + fx / fn * float(oy)
                    self.target[g] = -1
                    self.state[g] = IDLE
                    self.state_t[g] = 0.0
                    self.first_attack[g] = True
                    self.mv_target[g] = -1
                    ent["live"].append(g)
                    if self.trace_enabled:
                        self.trace.append("E|%.2f|swsummon|%d|%d|%d" % (
                            self.time, int(self.team[g]), int(self.uid[g]),
                            int(self.mech_id[g])))
            self._swsummon_q = still

        active = (~self.dead) & (self.target >= 0) & (~sp)
        # step26: 沙虫钻出窗口内不能攻击 (sw_dive 冒头阶段可被锁定可被
        # 攻击, 但同样吃钻出前摇)
        if self.opts.get("sw_emerge", 0):
            active &= ~(self.is_sw & (self.time < self._sw_emerge_until))
        # step26 P2 遮挡 (恶灵"后排被前排挡射界"假说): 每 0.5s 重算一次
        # (见 _occlusion_mask), 被挡单位停火 (prep/attack 计时同步暂停)
        if self.opts.get("occl", 0):
            if int(self.time / 0.5) != int((self.time - DT) / 0.5) \
                    or not hasattr(self, "_occl_m"):
                self._occl_m = self._occlusion_mask()
            active &= ~self._occl_m
        # step24 骇客光束瘫痪 (step25 C3: hack_par=0 默认撤 —— 被光束单位
        # 可动可攻, u150 火獾边被骇边反杀 2 骇客)
        if self.hacked.any() and self.opts.get("hack_par", 0):
            active &= ~self.hacked
        self.state_t += DT * active

        # facing model: turn toward current target before the fire gate
        if self.opts.get("facing") or self.opts.get("facing_set"):
            self._update_facing()

        # prepare -> attack
        m = active & (self.state == PREPARE)
        if np.any(m):
            # first-attack delay semantics (opts.init_cd; step5 behavior="sum")
            mode = self.opts.get("init_cd", "sum")
            if mode == "none" or mode == "prep":
                first_prep = self.prep_t
            elif mode == "max":
                first_prep = np.maximum(self.init_cd, self.prep_t)
            elif mode == "replace":
                first_prep = np.where(self.init_cd > 0, self.init_cd, self.prep_t)
            else:   # "sum"
                first_prep = self.init_cd + self.prep_t
            prep_eff = np.where(self.first_attack, first_prep, self.prep_t)
            # step28 lock_delay: 索敌到首发的全局锁定延迟 (Q-B; 只作用于
            # first_attack 的首发窗口, 与 init_cd 口径叠加)
            _ld = float(self.opts.get("lock_delay", 0) or 0)
            if _ld > 0:
                prep_eff = np.where(self.first_attack,
                                    prep_eff + _ld, prep_eff)
            done = m & (self.state_t >= prep_eff)
            if np.any(done):
                self.state[done] = ATTACK
                self.state_t[done] = 0.0
                # step23 cycle_keep: 换靶重进 PREPARE 的单位不清 dmg_applied
                # (清了 = 目标一死就再齐射; 首攻单位本就是 False, 无需清)。
                # step28 cycle_set: 白名单行同通道保闩锁 (武器周期守恒)
                if not self.opts.get("cycle_keep", 0):
                    if self._cycle_set:
                        clr = done & ~np.isin(self.mech_id,
                                              list(self._cycle_set))
                    else:
                        clr = done
                    self.dmg_applied[clr] = False

        # attack: fire + cycle
        atk = active & (self.state == ATTACK)
        fire = atk & (~self.dmg_applied) & (self.state_t >= self.hit_at)
        if self.opts.get("facing") or self.opts.get("facing_set"):
            fire &= self._aimed()   # cannot fire until target is in the cone
        for i in np.where(fire)[0]:
            self._fire_one(int(i))
        self.dmg_applied[fire] = True
        # step12: building cannons have a magazine - after N shots they pause
        # for a full reload (AA 6/10s, RF 10/2.5s, descParams); the pause rides
        # the COOL state so the existing cooling pipeline drives the timer
        bfire = np.where(fire & self.is_bld & (self.bld_shots_cap > 0))[0]
        for i in bfire:
            i = int(i)
            self.bld_shot_cnt[i] += 1
            if self.bld_shot_cnt[i] >= self.bld_shots_cap[i]:
                self.bld_shot_cnt[i] = 0
                self.state[i] = COOL
                self.state_t[i] = 0.0
                self.cool_t[i] = self.bld_reload_t[i]
                self._bld_reloading[i] = True
                if self.trace_enabled:
                    self.trace.append("E|%.2f|bld_reload|%d|%d|%d|%.1f" % (
                        self.time, int(self.team[i]), int(self.bld_cid[i]),
                        int(self.bld_group[i]), self.bld_reload_t[i]))
        cyc = atk & (self.state_t >= self.atk_dur)
        if np.any(cyc):
            self.first_attack[cyc] = False
            to_cool = cyc & (self.cool_t > 0)
            self.state[to_cool] = COOL
            self.state_t[to_cool] = 0.0
            again = cyc & (self.cool_t <= 0)
            self.state_t[again] = 0.0
            self.dmg_applied[again] = False

        # cool -> attack
        m = active & (self.state == COOL) & (self.state_t >= self.cool_t)
        if np.any(m):
            self.state[m] = ATTACK
            self.state_t[m] = 0.0
            self.dmg_applied[m] = False
            # step12: a finished magazine reload restores the skill's own
            # cooling time (reload overwrote cool_t for the pause)
            rl = m & self._bld_reloading
            if np.any(rl):
                self.cool_t[rl] = self._cool0[rl]
                self._bld_reloading[rl] = False

        self._move()
        self._separate()
        # step23 T3: 速度向量 (单位/仿真tick 位移), 供齐射落点前置量
        if not hasattr(self, "_px"):
            self._px = self.x.copy()
            self._py = self.y.copy()
            self.vx = np.zeros(self.n)
            self.vy = np.zeros(self.n)
        else:
            np.subtract(self.x, self._px, out=self.vx)
            np.subtract(self.y, self._py, out=self.vy)
            self._px[:] = self.x
            self._py[:] = self.y
        self._update_projectiles()
        # step32 T4/T10: 装备跟随屏障 — 在本 tick 移动/开火结算前贴到 carrier
        # 卡首个存活成员的位置 (同 tick 跟随); carrier 死亡后原地冻结
        # (oracle Q3 待证: 圆心更新频率/归属规则)。
        if self._eq_follow_bars:
            for _bi, _bc in self._eq_follow_bars:
                if self.dead[_bi]:
                    continue
                _mem = np.where((self.card_idx == _bc) & (~self.dead))[0]
                if len(_mem):
                    self.x[_bi] = self.x[_mem[0]]
                    self.y[_bi] = self.y[_mem[0]]
        self._apply_damage(tick)
        self._check_paralyse_expiry()

        if self.trace_enabled and (tick + 1) % TRACE_TICKS == 0:
            self._emit_frame()

    def simulate(self):
        assert self._finalized
        # step14-P2 sweep: the real battle keeps running briefly after one
        # side's mechs are annihilated (report dcc: losers lose both towers
        # 30%, not 100% -> a bounded cleanup window, not total demolition).
        # Winner stays the first-annihilation result; opts.sweep_flip=1 lets
        # surviving loser cannons flip it if they clear the winner too.
        # step24 bld_term: pure-building teams (兵种vs建筑 benchmark fights)
        # count their constructions toward the annihilation/timeout headcount
        # - the game's buildings are destructible fight objects; default 0
        # keeps the corpus behavior (buildings never decide the outcome).
        sweep_t = float(self.opts.get("sweep", 0) or 0)
        flip = bool(self.opts.get("sweep_flip", 0))
        bld_term = bool(self.opts.get("bld_term", 0))
        # step28 bld_term=2 (Q-E 定版): 一方所有可移动单位死亡 → 立即终局,
        # 存活方 (含建筑方) 胜; 建筑不阻止终局 (bp_fang_aa 蓝尖牙不去和
        # 速射炮决胜负); 双方机动同灭 → 建筑存活模块多者胜 (bp_archer_wall
        # 仅剩墙 5 模块的一方赢)。
        mob_term = int(self.opts.get("bld_term", 0) or 0) == 2

        def _alive(t):
            m = (~self.dead) & (self.team == t) & (~self.is_tower) \
                & (~self.is_device)
            if not bld_term or mob_term:
                m &= ~self.is_bld
            return int(np.count_nonzero(m))

        def _bld_alive(t):
            m = (~self.dead) & (self.team == t) & self.is_bld
            return int(np.count_nonzero(m))
        first_winner = None
        ann_tick = 0
        for tick in range(MAX_TICKS):
            self.step(tick)
            self.end_tick = tick
            if first_winner is None:
                a0 = _alive(0)
                a1 = _alive(1)
                if a0 == 0 or a1 == 0:
                    if a0 == 0 and a1 == 0:
                        if mob_term:
                            # 双方机动同灭: 建筑作为存活方判胜依据
                            b0, b1 = _bld_alive(0), _bld_alive(1)
                            first_winner = 0 if b0 > b1 else (1 if b1 > b0 else -1)
                        else:
                            first_winner = -1
                    else:
                        first_winner = 0 if a1 == 0 else 1
                    ann_tick = tick
                    if sweep_t <= 0:
                        break
            else:
                wiped = 1 - first_winner if first_winner in (0, 1) else -1
                if wiped >= 0 and not np.any((~self.dead) & (self.team == wiped)
                                             & (~self.is_tower) & (~self.is_device)
                                             & ((~self.is_bld) if not bld_term else True)):
                    break   # wiped side fully cleared
                if first_winner in (0, 1) and _alive(first_winner) == 0:
                    break   # cannons cleared the winner during the sweep
                if (tick - ann_tick) * DT >= sweep_t:
                    break
        if first_winner is None:
            a0 = _alive(0)
            a1 = _alive(1)
            if a0 == 0 and a1 == 0:
                return -1
            if a1 == 0:
                return 0
            if a0 == 0:
                return 1
            m0 = (~self.dead) & (self.team == 0) & (~self.is_device)
            m1 = (~self.dead) & (self.team == 1) & (~self.is_device)
            if not bld_term:
                m0 &= ~self.is_bld
                m1 &= ~self.is_bld
            # step27 timeout_judge="score": oracle FightResult 口径 —— 先比
            # score (Σ 存活模块价×血量比, 塔/建筑不计), 再比存活模块数,
            # 双平 = -1; 旧口径 = 模块数 → 血量比。超时局一致率 s25 45% /
            # s26 35% 显著低于均值, 该口径差异为主因 (tools/step27_scan2)。
            if str(self.opts.get("timeout_judge", "") or "") == "score":
                sm0 = m0 & (~self.is_tower) & (~self.is_bld)
                sm1 = m1 & (~self.is_tower) & (~self.is_bld)
                s0 = float(np.sum(self._score_val[sm0]
                                  * self.hp[sm0] / self.max_hp[sm0]))
                s1 = float(np.sum(self._score_val[sm1]
                                  * self.hp[sm1] / self.max_hp[sm1]))
                if abs(s0 - s1) > 1e-9:
                    return 0 if s0 > s1 else 1
                if a0 != a1:
                    return 0 if a0 > a1 else 1
                return -1
            h0 = float(np.sum(self.hp[m0] / self.max_hp[m0]))
            h1 = float(np.sum(self.hp[m1] / self.max_hp[m1]))
            if a0 != a1:
                return 0 if a0 > a1 else 1
            if abs(h0 - h1) > 1e-9:
                return 0 if h0 > h1 else 1
            return -1
        if flip and first_winner in (0, 1) and self.alive_count(first_winner) == 0 \
                and np.any((~self.dead) & (self.team == 1 - first_winner)):
            return 1 - first_winner
        return first_winner

    # ---------- results ----------
    def building_groups(self):
        """step12: {(team, cid, group): alive module count} for every placed
        construction - the survival metric aligns these group keys with the
        next round's constructionSnapshotDatas (by Index)."""
        out = {}
        for u in np.where(self.is_bld)[0]:
            key = (int(self.team[u]), int(self.bld_cid[u]), int(self.bld_group[u]))
            if key not in out:
                out[key] = [0, 0]
            out[key][1] += 1
            if not self.dead[u]:
                out[key][0] += 1
        return {k: tuple(v) for k, v in out.items()}

    def outcome_cards(self):
        """transition v0 public per-card results (no private-array access
        needed by callers): [{card_idx, team, mech, level, exp, damage,
        kills, survived, n_modules, modules_alive}] in card order."""
        out = []
        for ci, c in enumerate(self.cards):
            members = np.where(self.card_idx == ci)[0]
            out.append({
                "card_idx": ci, "team": int(c["team"]), "mech": int(c["mech"]),
                "level": int(c["level"]), "exp": float(c["exp"]),
                "damage": round(float(self.card_damage.get(ci, 0.0)), 3),
                "kills": int(sum(1 for k in self.kills if k.get("killer") == ci)),
                "survived": bool(np.any(~self.dead[members])),
                "n_modules": int(len(members)),
                "modules_alive": int(np.count_nonzero(~self.dead[members])),
            })
        return out

    def team_score(self, team):
        """Survivor value of one team (pysim_survivor_value_v1): sum over
        surviving non-tower/building/device modules of
        (card price / mech_count * level) * hp/max_hp."""
        m = (~self.dead) & (self.team == team) & (~self.is_tower) \
            & (~self.is_bld) & (~self.is_device) & (self.card_idx >= 0)
        if not np.any(m):
            return 0.0
        return float(np.sum(self._score_val[m] * self.hp[m] / self.max_hp[m]))

    def result(self, winner):
        survivors = {}
        for team in (0, 1):
            mask = (~self.dead) & (self.team == team) & (~self.is_tower) \
                & (~self.is_device) & (~self.is_bld)
            survivors[team] = {
                "mechs": int(np.count_nonzero(mask)),
                "cards": sorted({int(self.card_idx[i]) for i in np.where(mask)[0]}),
            }
        devices = {}
        for team in (0, 1):
            dmask = (~self.dead) & (self.team == team) & self.is_device
            devices[team] = {"alive": int(np.count_nonzero(dmask)),
                             "hp": [round(float(self.hp[i]), 1) for i in np.where(dmask)[0]]}
        buildings = {}
        for (team, cid, grp), (alive, total) in self.building_groups().items():
            buildings.setdefault(str(team), []).append(
                {"cid": cid, "group": grp, "alive": alive, "total": total})
        return {
            "winner": winner,
            "end_time": round(self.end_tick * DT, 2),
            "kills": self.kills,
            "survivors": survivors,
            "devices": devices,
            "buildings": buildings,
            "towers_down": dict(self.towers_down),
            "bld_groups_down": dict(self.bld_groups_down),
            "stats": {"damage": round(self.total_damage, 1), "kills": self.total_kills,
                      "attacks": self.total_attacks},
            "trace": self.trace,
        }


def battle_from_units(gd, units0, units1, trace=False, tech_map0=None, tech_map1=None,
                      opts=None, tower_mods0=None, tower_mods1=None,
                      towers0=None, towers1=None, skills0=None, skills1=None,
                      buildings0=None, buildings1=None, officers0=None,
                      officers1=None):
    """Build a Battle from replay-style unit lists ({id, level, x, y, isRotate}).
    tech_map0/1: per-team {mech_id: [tech ids]} overriding defaults for that
    mech type; per-unit u["techs"] takes precedence over the team map.
    opts: engine experiment switches (see module header).
    tower_mods0/1: per-team round buffs {"range": m, "speed": m} (step7-A).
    towers0/1: per-team strengthen levels [lvl_x_neg, lvl_x_pos] enabling the
    two crystals at the fixed TOWER_POS slots (step8; None = no towers).
    u["spawnAt"]: flank teleport seconds for this card (step9; 0 = normal).
    skills0/1: step8-B battlefield skill events (pysim/skills.py format),
    released pre-fight -> effects at battle t=0.
    buildings0/1: step12 construction placements from the replay snapshot,
    [{cid, x, y, index}] (cid 1-4; index = snapshot Index for the
    survival metric).
    officers0/1: step14 officer id lists per player (gd.officers modifiers
    baked at finalize; blueprint-granted entries 20300/20310 are already
    inside the replay officers list)."""
    b = Battle(gd)
    if opts:
        b.opts.update(opts)
    b.officer_ids[0] = tuple(int(o) for o in officers0 or ())
    b.officer_ids[1] = tuple(int(o) for o in officers1 or ())
    # step18 T6: the replay officers list carries only the EQUIPPED blueprint
    # tier - II replaces I in the list (corpus: [20300,20310] -> [20301,20311],
    # never both), but the game STACKS II on top of I (user Q3: 增益叠加,
    # 防御 I+II = +60% life, 进攻 I+II = +48% dmg). Re-add the base tier when
    # II is present; opts.bp_stack=0 restores the raw-list behavior.
    if b.opts.get("bp_stack", 1):
        BP_BASE = {20301: 20300, 20311: 20310}
        for t in (0, 1):
            ids = b.officer_ids[t]
            extra = tuple(BP_BASE[o] for o in ids if o in BP_BASE and BP_BASE[o] not in ids)
            if extra:
                b.officer_ids[t] = ids + extra
    # step18 T6b causal probe: drop specific officer ids entirely (e.g.
    # off_officers=20300 removes 防御强化I from both players)
    if b.opts.get("off_officers"):
        drop = {int(x) for x in str(b.opts["off_officers"]).split(",") if x}
        for t in (0, 1):
            b.officer_ids[t] = tuple(o for o in b.officer_ids[t] if o not in drop)
    if tower_mods0:
        b.tower_mods[0] = tower_mods0
    if tower_mods1:
        b.tower_mods[1] = tower_mods1
    for team, evs in ((0, skills0), (1, skills1)):
        for ev in evs or []:
            b.add_skill_event(team, ev)
    for team, bl in ((0, buildings0), (1, buildings1)):
        for c in bl or []:
            b.add_building(team, int(c.get("cid", c.get("id", 0))),
                           float(c["x"]), float(c["y"]),
                           index=c.get("index"))
    from .deploy import TOWER_POS
    for team, levels in ((0, towers0), (1, towers1)):
        if levels:
            for k in range(min(2, len(levels))):
                tx, ty = TOWER_POS[team][k]
                b.add_tower(team, tx, ty, levels[k])
    for team, units, tmap in ((0, units0, tech_map0), (1, units1, tech_map1)):
        for u in units:
            techs = u.get("techs")
            if techs is None and tmap is not None:
                techs = tmap.get(int(u["id"]))
            # step22 T3 定版 (2026-08-22, 构造局四点实测 lv1..lv4 精确吻合):
            # 游戏 XML/corpus 的 Level 是 0 基升档数, 真实属性 = 表值×(L+1)
            # (尖牙 117: L0=117/L1=234/L2=351/L3=468/L4=585); 引擎内部等级
            # 1 基 —— 在此边界 +1 对齐 (旧代码 L1 单位被低配为 ×1, 全语料
            # 37% 单位血量/攻击偏低, r1-4 miss 的主因之一)。
            b.add_card(team, int(u["id"]), int(u.get("level", 0)) + 1,
                       float(u["x"]), float(u["y"]), bool(u.get("isRotate", False)),
                       techs=list(techs) if techs is not None else None,
                       spawn_at=float(u.get("spawnAt", 0) or 0),
                       exp=int(u.get("exp", 0) or 0),
                       equipment_id=int(u.get("equipmentId", 0) or 0))
    b.trace_enabled = trace
    b.finalize()
    return b
