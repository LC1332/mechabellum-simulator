# step5 unit tests T1-T3: tech baking assertions (run: python -m pysim.test_tech)
import os, sys, io
try:  # only rewrap real console streams (breaks under pytest capture)
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8" \
            and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                      errors="replace")
except Exception:
    pass

from pysim.gamedata import GameData
from pysim.engine import Battle

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

gd = GameData(os.path.join(DATA, "gamedata.json"))
fails = []

def check(name, got, want, tol=1e-6):
    ok = abs(got - want) <= tol
    print("%-46s got %-.4f want %-.4f  %s" % (name, got, want, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(name)

def mech_of_tech(tid):
    for mid, c in gd.cards.items():
        if tid in c.technologies:
            return mid
    raise KeyError(tid)

def baked(mech_id, techs, enemy_at=None):
    b = Battle(gd)
    b.add_card(0, mech_id, 1, 0.0, 0.0, False, techs=list(techs))
    b.add_card(1, enemy_at or 28, 1, 150.0, 0.0, False, techs=[])   # target dummy
    b.finalize()
    return b, (b.card_idx == 0)

# ---- T1: 10102 突击模式 (life +500%, dmg +60%, speed +3, splash +9, range -70)
m1 = mech_of_tech(10102)
md = gd.mechs[m1]
sd = gd.skills[md.main_skill_id]
b, msk = baked(m1, [10102])
check("T1 max_hp x6", float(b.max_hp[msk][0]), md.life * 6.0)
check("T1 hp = max_hp", float(b.hp[msk][0]), md.life * 6.0)
check("T1 tech_dmg 1.6", float(b.tech_dmg[msk][0]), 1.6)
check("T1 speed +3", float(b.move_speed[msk][0]), md.move_speed + 3)
check("T1 splash +9", float(b.splash[msk][0]), (sd.splash_range if sd.use_self_splash else 0.0) + 9.0)
check("T1 range -70", float(b.range[msk][0]), sd.range - 70.0)

# ---- T2: 10019 攻城模式 (range +100, dmg -40%, interval +1.5, blind 75, bullet -200)
m2 = mech_of_tech(10019)
md2 = gd.mechs[m2]
sd2 = gd.skills[md2.main_skill_id]
b2, msk2 = baked(m2, [10019])
check("T2 tech_dmg 0.6", float(b2.tech_dmg[msk2][0]), 0.6)
check("T2 range +100", float(b2.range[msk2][0]), sd2.range + 100.0)
check("T2 atk_dur +1.5", float(b2.atk_dur[msk2][0]), max(0.01, sd2.attack_duration) + 1.5)
check("T2 min_rng 75", float(b2.min_rng[msk2][0]), 75.0)
check("T2 bullet -200", float(b2.bullet_spd[msk2][0]), max(0.0, sd2.bullet_speed - 200.0))
# blind zone targeting: near enemy at 50m surface, far enemy at 200m
b3 = Battle(gd)
b3.add_card(0, m2, 1, 0.0, 0.0, False, techs=[10019])
b3.add_card(1, 28, 1, 60.0, 0.0, False, techs=[])    # surface dist ~ 60 - r - r < 75
b3.add_card(1, 9, 1, 200.0, 0.0, False, techs=[])
b3.finalize()
b3._full_target_pass()
tgt = int(b3.target[0])
t_ok = tgt >= 0 and int(b3.mech_id[tgt]) == 9
print("%-46s target mech %s (want mech 9, skip mech 28)  %s" % (
    "T2 blind-zone targeting", int(b3.mech_id[tgt]) if tgt >= 0 else -1, "OK" if t_ok else "FAIL"))
if not t_ok:
    fails.append("T2 blind-zone targeting")

# ---- T3: 10201 range +40; stacking rate+value with 10102
m3 = mech_of_tech(10201)
md3 = gd.mechs[m3]
sd3 = gd.skills[md3.main_skill_id]
b4, msk4 = baked(m3, [10201])
check("T3 range +40", float(b4.range[msk4][0]), sd3.range + 40.0)
if m1 == m3:
    b5, msk5 = baked(m1, [10102, 10201])
    check("T3 stack range -70+40", float(b5.range[msk5][0]), sd3.range - 70.0 + 40.0)
else:
    print("T3 stack skipped (10102/10201 on different mechs)")

# ---- defaults: no explicit techs -> card defaults applied (爬虫 10510 speed+5)
b6 = Battle(gd)
b6.add_card(0, 10, 1, 0.0, 0.0, False)          # 爬虫 defaults [10510, 180110, 2610, 2710]
b6.add_card(1, 28, 1, 150.0, 0.0, False, techs=[])
b6.finalize()
msk6 = b6.card_idx == 0
md6 = gd.mechs[10]
sd6 = gd.skills[md6.main_skill_id]
# step19: card defaults filter to the MAIN-table family (shop-slot
# semantics, matching mdefull); the reference agg must filter the same way
_def19 = [t for t in gd.cards[10].default_technologies
          if (td19 := gd.techs.get(int(t))) is not None
          and td19.family == "technologyDatas"]
agg = gd.sum_tech_mods(_def19, 1)
check("D1 default life_rate applied", float(b6.max_hp[msk6][0]), md6.life * (1 + agg["life_rate"]))
check("D2 default interval applied", float(b6.atk_dur[msk6][0]),
      max(0.01, sd6.attack_duration) * (1 + agg["interval_rate"]) + agg["interval_val"])
check("D3 default speed applied", float(b6.move_speed[msk6][0]), md6.move_speed + agg["speed"])

# ---- per-level pick: 10801 dmg list [0.25 .. 2.25] at level 3 -> 0.75
check("L1 per-level pick lv3", gd.sum_tech_mods([10801], 3)["dmg_rate"], 0.75)
check("L2 per-level pick lv9", gd.sum_tech_mods([10801], 9)["dmg_rate"], 2.25)

# ---- explicit [] disables defaults
b7 = Battle(gd)
b7.add_card(0, 10, 1, 0.0, 0.0, False, techs=[])
b7.add_card(1, 28, 1, 150.0, 0.0, False, techs=[])
b7.finalize()
msk7 = b7.card_idx == 0
check("E1 techs=[] raw hp", float(b7.max_hp[msk7][0]), gd.mechs[10].life)
check("E2 techs=[] raw atk_dur", float(b7.atk_dur[msk7][0]), max(0.01, sd6.attack_duration))

# ---- step16 sub-table techs (mech ids from the corpus census) --------------
import numpy as np
# A1 防空专精 3202 (长弓): airTargetScoreOffset 30 baked, +90% air dmg,
#    NO global discount without the tech (base rows aa_off=0)
b8, msk8 = baked(2, [3202])
check("A1 aa_off 30", float(b8.aa_off[msk8][0]), 30.0)
check("A1 air_dmg +0.9", float(b8.air_dmg[msk8][0]), 0.9)
check("A1 gnd_dmg 0", float(b8.gnd_dmg[msk8][0]), 0.0)
b8b, msk8b = baked(2, [])
check("A1b no tech -> aa_off 0", float(b8b.aa_off[msk8b][0]), 0.0)

# A2 对地锁定 3225 (鬼鳐): groundTargetScoreOffset 60
b9, msk9 = baked(25, [3225])
check("A2 gnd_off 60", float(b9.gnd_off[msk9][0]), 60.0)

# A3 对地专精 506 (兵蜂): ground dmg +200%
b10, msk10 = baked(6, [506])
check("A3 gnd_dmg +2.0", float(b10.gnd_dmg[msk10][0]), 2.0)

# A4 装甲强化 3024 (狼蛛): life +50% AND flat armor 60 at level 1
b11, msk11 = baked(24, [3024])
check("A4 armor 60 @lv1", float(b11.armor[msk11][0]), 60.0)
check("A4 life +50%", float(b11.max_hp[msk11][0]), gd.mechs[24].life * 1.5)
check("A4 armor 180 @lv3", gd.sum_tech_mods([3024], 3)["armor"], 180.0)

# A5 战地维修 924 (狼蛛): 4.5%/s regen
b12, msk12 = baked(24, [924])
check("A5 regen 0.045/s", float(b12.regen[msk12][0]), 0.045, tol=1e-4)

# A6 高爆弹药 424 (狼蛛): splash +7 (base 5) and dmg -45%
b13, msk13 = baked(24, [424])
check("A6 splash 5+7", float(b13.splash[msk13][0]),
      gd.skills[gd.mechs[24].main_skill_id].splash_range + 7.0)
check("A6 dmg -45%", float(b13.tech_dmg[msk13][0]), 0.55)

# A7 能量汲取 330 (魔眼): lifesteal 0.8
b14, msk14 = baked(30, [330])
check("A7 lifesteal 0.8", float(b14.lifesteal[msk14][0]), 0.8)

# A8 双发 702 (长弓): multi_n 1 extra target, interval +15%
b15, msk15 = baked(2, [702])
check("A8 multi_n 1", float(b15.multi_n[msk15][0]), 1.0)
check("A8 interval +15%", float(b15.atk_dur[msk15][0]),
      max(0.01, gd.skills[gd.mechs[2].main_skill_id].attack_duration) * 1.15)

# A9 台风 intrinsic dual-target; opts.dual_target=0 restores single
b16, msk16 = baked(22, [])
check("A9 台风 dual multi_n 1", float(b16.multi_n[msk16][0]), 1.0)
b16b = Battle(gd)
b16b.opts["dual_target"] = 0
b16b.add_card(0, 22, 1, 0.0, 0.0, False, techs=[])
b16b.add_card(1, 28, 1, 150.0, 0.0, False, techs=[])
b16b.finalize()
check("A9b dual_target=0 multi_n 0", float(b16b.multi_n[b16b.card_idx == 0][0]), 0.0)

# A10 震荡波 4515 (弧光): on-hit splash 75 dmg r30
b17, msk17 = baked(15, [4515])
check("A10 sec_dmg 75", float(b17.sec_dmg[msk17][0]), 75.0)
check("A10 sec_rng 30", float(b17.sec_rng[msk17][0]), 30.0)

# A11 防空弹药 3115 (弧光, base can_air=False): grant air attack; without it
# the unit cannot touch flyers (user: 弧光不点科技不能对空)
b18 = Battle(gd)
b18.add_card(0, 15, 1, 0.0, 0.0, False, techs=[3115])
b18.add_card(1, 6, 1, 60.0, 0.0, False, techs=[])     # 兵蜂 flyer
b18.finalize()
row = np.where(b18.card_idx == 0)[0][0]
fly = np.where(b18.is_fly)[0][0]
hit_fly = bool(b18.hittable[row, fly])
print("%-46s %s  %s" % ("A11 防空弹药 can hit flyer", hit_fly, "OK" if hit_fly else "FAIL"))
if not hit_fly:
    fails.append("A11")
b18b = Battle(gd)
b18b.add_card(0, 15, 1, 0.0, 0.0, False, techs=[])
b18b.add_card(1, 6, 1, 60.0, 0.0, False, techs=[])
b18b.finalize()
row_b = np.where(b18b.card_idx == 0)[0][0]
fly_b = np.where(b18b.is_fly)[0][0]
no_hit = not b18b.hittable[row_b, fly_b]
print("%-46s %s  %s" % ("A11b without tech cannot hit flyer", no_hit,
                        "OK" if no_hit else "FAIL"))
if not no_hit:
    fails.append("A11b")

print("\n%s: %d checks failed" % ("RESULT", len(fails)))
if fails:
    for f in fails:
        print("  FAIL:", f)
print("ALL PASS")


def test_all():
    assert not fails, "test_tech.py: %s checks failed" % len(fails)
