# step32 动态装备与伤害管线专项测试 (pysim动态装备与伤害管线修正任务书
# -2026-08-28 §5 T1-T12 / §8 gate 表):
#   - runtime registry: 11 个选定 ID 有 spec, 静态 E2 7 件 digest 不变
#   - 伤害 receipt: shield/barrier/HP/overkill/prevented 记账
#   - 状态免疫: 光子涂层 30s 窗口 (减伤+免疫+过期), 抗干扰 EMP/骇客/瘫痪
#   - 护盾: 便携式护盾 = maxHP, 保护/超级跟随屏障
#   - 回复/吸血: 纳米维修覆盖战地维修, 汲取 +30%HP + 90% 吸血
#   - 生产线: 首批 t=period / 批次上限 / carrier 死亡取消
#   - feature flags: eq_runtime / eq_off / eq_only 逐机制切换
# 全部数值 provisional (oracle 待采集); 这里只断言实现语义, 不锁 oracle 值。
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pysim.gamedata import GameData
from pysim.engine import Battle, battle_from_units
from pysim.battlefield.effects.equipment import (
    EQUIPMENT_BATTLE_SPECS, EQUIPMENT_RUNTIME_SPECS,
    SELECTED_RUNTIME_EQUIPMENT_IDS, EquipmentBattleSpec)
from pysim.battlefield import registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))

# 熔点(4)/火神(3) 是陆地巨型 (生产线/保护屏障的合法载体); 爬虫(10) 当沙袋
GIANT = 3
GIANT2 = 4


def unit(mech, x, y, eq=0, level=1):
    return {"id": mech, "level": level, "x": x, "y": y, "equipmentId": eq}


def duo(eq=0, mech=9):
    """两辆同型车在 p0 (p1 空 → 立即终局不可用, 这里 p1 给一个对称沙包)。"""
    return [unit(mech, -100.0, -60.0, eq), unit(mech, -110.0, -60.0, eq)]


def fight(units0, units1, **kw):
    trace = kw.pop("trace", False)
    b = battle_from_units(GD, units0, units1, trace=trace, **kw)
    w = b.simulate()
    return b, w


# ================================================================ T1 registry
def test_selected_ids_have_runtime_specs():
    assert len(SELECTED_RUNTIME_EQUIPMENT_IDS) == 11
    for eid in SELECTED_RUNTIME_EQUIPMENT_IDS:
        spec = EQUIPMENT_RUNTIME_SPECS[eid]
        assert spec.battle_implemented
        assert spec.confidence == "provisional"   # oracle 未采集, 不许 verified
        assert spec.evidence
        d = spec.digest()
        assert len(d) == 16 and d == spec.digest()   # digest 稳定


def test_legacy_static_specs_unchanged():
    """静态 E2 的 7 个装备保持原行为: 数值与 confidence 不被 runtime 扩展
    触碰 (任务书 T1: digest 稳定)。"""
    expect = {13030001: dict(range_add=20.0),
              13030002: dict(hp_mult=0.75),
              13030003: dict(dmg_mult=0.65),
              13030004: dict(hp_mult=0.25, dmg_mult=0.25),
              13030005: dict(dmg_mult=0.35, speed_add=5.0),
              13030006: dict(hp_mult=1.50),
              13030007: dict(hp_mult=0.50, dmg_mult=0.50)}
    assert set(expect) == set(EQUIPMENT_BATTLE_SPECS)
    for eid, kv in expect.items():
        s = EQUIPMENT_BATTLE_SPECS[eid]
        for k, v in kv.items():
            assert getattr(s, k) == v
        assert s.confidence == "provisional"
        assert not EQUIPMENT_RUNTIME_SPECS.get(eid)


def test_registry_warns_only_unimplemented():
    for eid in SELECTED_RUNTIME_EQUIPMENT_IDS:
        assert registry.equipment_battle_warning(eid) is None
    assert registry.equipment_battle_warning(13120001) is not None
    assert registry.equipment_battle_warning(0) is None
    s = registry.mechanism_support("equipment", 1306001)
    assert s.battle == "complete" and s.confidence == "provisional"
    # 生产线召唤已建模 → settlement 不再因召唤记 partial
    assert s.settlement == "complete"


# ================================================================ T2 receipts
def test_damage_receipts_ledger():
    b, w = fight(duo(13010001), duo(0))
    assert b.damage_receipts, "runtime equipment on field -> ledger armed"
    for r in b.damage_receipts:
        assert {"ref", "t", "source_row", "source_card", "source_kind",
                "victim_row", "raw_damage", "shield_absorbed",
                "barrier_absorbed", "hp_damage", "overkill", "prevented",
                "killed", "tags"} <= set(r)
        assert r["hp_damage"] >= 0 and r["overkill"] >= 0
        assert r["source_kind"] in ("attack", "environment")
    # 至少一条真实吸收: 便携式护盾 = maxHP
    assert any(r["shield_absorbed"] > 0 for r in b.damage_receipts)


def test_receipts_absent_without_runtime_equipment():
    b, w = fight(duo(0), duo(0))
    assert b.damage_receipts == []
    b2, _ = fight(duo(13010001), duo(0), opts={"eq_ledger": 0})
    assert b2.damage_receipts == []


# ================================================================ T3/T8 immunity
def _ignite_tech_id():
    for tid, td in GD.techs.items():
        if td.family == "buffTechnologies" and "引燃" in (td.name or ""):
            return int(tid)
    pytest.fail("no 引燃 tech in gamedata")


def test_photon_coating_window_and_immunity():
    units0 = [unit(9, -100.0, -60.0, 1305003)]
    units1 = [unit(9, 100.0, 60.0)]
    b, w = fight(units0, units1,
                 skills1=[{"kind": "emp", "x": -100.0, "y": -60.0,
                           "radius": 200.0, "t": 0.0}])
    # 30s 窗口: 减伤通道 + 免疫位
    assert float(b.photon_until[0]) == 30.0
    assert b._photon_taken == 0.70
    assert float(b.emp_until[0]) == -1.0        # EMP 被整包拦截
    blocked = [e for e in b.status_events
               if e["action"] == "status_blocked" and e["kind"] == "emp"]
    assert blocked and blocked[0]["source"] == "equipment"
    # 引燃 rider 同样进不来
    itid = _ignite_tech_id()
    b2, _ = fight([unit(9, -100.0, -60.0, 1305003)], [unit(9, 100.0, 60.0)],
                  tech_map1={9: [itid]})
    assert float(b2.burn_pct_until[0]) == -1.0


def test_photon_coating_expires_at_30s():
    # 30s 限时边界 (tick 精度): 29.9s 拦截 / 30.1s 放行。white-box 直呼
    # EMP 爆发, 避免依赖真实战斗打到 30s 之后。
    b = Battle(GD)
    b.add_card(0, 1, 1, -100.0, -60.0, equipment_id=1305003)
    b.add_card(1, 9, 1, 100.0, 60.0)
    b.officer_ids = {0: (), 1: ()}
    b.finalize()
    prm = {"x": -100.0, "y": -60.0, "radius": 200.0, "shield_damage": 0.0,
           "duration": 25.0, "slow_mult": 0.60}
    b.time = 29.9
    b._step5_emp_burst(1, prm)
    assert float(b.emp_until[0]) == -1.0          # 窗口内: 拦截
    b.time = 30.1
    b._step5_emp_burst(1, prm)
    assert float(b.emp_until[0]) == pytest.approx(55.1)   # 窗口外: 生效


def test_anti_jam_blocks_emp_hacker_paralysis():
    # EMP
    b, _ = fight(duo(1308001), duo(0),
                 skills1=[{"kind": "emp", "x": -100.0, "y": -60.0,
                           "radius": 200.0, "t": 0.0}])
    assert float(b.emp_until[0]) == -1.0 and float(b.emp_until[1]) == -1.0
    # 骇客: 找 controllBeamSkillDatas mech
    hacker = next((int(mid) for mid, m in GD.mechs.items()
                   if m.main_skill_id
                   and GD.skills.get(m.main_skill_id) is not None
                   and GD.skills[m.main_skill_id].type
                   == "controllBeamSkillDatas"), None)
    if hacker is not None:
        b2, _ = fight([unit(9, -100.0, -60.0, 1308001)],
                      [unit(hacker, 100.0, 60.0)])
        assert float(b2.hack_progress[0]) == 0.0
        assert not bool(b2.hacked[0])
    # 核心瘫痪: white-box 触发塔倒, 免疫行系数不动 (add_card 会展开成
    # formation 多行, 用 card_idx 找两个卡各自的成员行)
    b3 = Battle(GD)
    b3.add_card(0, 9, 1, -60.0, 0.0, equipment_id=1308001)
    b3.add_card(0, 9, 1, -70.0, 0.0)
    b3.add_card(1, 9, 1, 60.0, 0.0)
    b3.add_tower(0, 0.0, 80.0)
    b3.officer_ids = {0: (), 1: ()}
    b3.finalize()
    tower_row = int(np.where(b3.is_tower)[0][0])
    b3._on_tower_down(tower_row)
    eq_rows = np.where((b3.card_idx == 0) & (~b3.dead))[0]
    plain_rows = np.where((b3.card_idx == 1) & (~b3.dead))[0]
    assert all(float(b3._dmg_fac[i]) == 1.0 for i in eq_rows)   # 抗干扰行
    assert all(float(b3._dmg_fac[i]) == pytest.approx(0.1)
               for i in plain_rows)                             # 同队无装备行


# ================================================================ T4 shields
def test_portable_shield_equals_max_hp():
    b0, _ = fight([unit(9, -100.0, -60.0)], [unit(9, 100.0, 60.0)])
    b1, _ = fight([unit(9, -100.0, -60.0, 13010001)], [unit(9, 100.0, 60.0)])
    assert float(b1.shield[0]) == pytest.approx(float(b1.max_hp[0]))
    assert float(b0.shield[0]) == 0.0
    # EMP 爆发先打盾 (shield_damage 可调小) 再上状态: 盾活着 → 无 EMP
    b2, _ = fight([unit(9, -100.0, -60.0, 13010001)], [unit(9, 100.0, 60.0)],
                  skills1=[{"kind": "emp", "x": -100.0, "y": -60.0,
                            "radius": 200.0, "t": 0.0, "shield_damage": 10}])
    assert float(b2.emp_until[0]) == -1.0
    assert float(b2.shield[0]) < float(b2.max_hp[0])   # 盾吃了爆发
    # 盾被打穿 (默认 shield_damage=20000 > 尖牙 maxHP) → 状态照常施加
    b3, _ = fight([unit(9, -100.0, -60.0, 13010001)], [unit(9, 100.0, 60.0)],
                  skills1=[{"kind": "emp", "x": -100.0, "y": -60.0,
                            "radius": 200.0, "t": 0.0}])
    assert float(b3.emp_until[0]) > 0.0


def test_follow_barrier_device_and_absorption():
    # 火神镜像: 敌方火神会打 carrier 成员 → 屏障重定向吸收进 receipt
    b0, _ = fight([unit(GIANT, -100.0, -60.0, 1307001)], [unit(GIANT, 100.0, 60.0)])
    bar = [i for i in range(b0.n) if b0.mech_id[i] == -2]
    assert len(bar) == 1
    assert float(b0.max_hp[bar[0]]) == 60000.0
    assert float(b0.radius[bar[0]]) == 30.0
    assert any(r["barrier_absorbed"] > 0 for r in b0.damage_receipts)
    # 超级屏障 180000
    b1, _ = fight([unit(GIANT, -100.0, -60.0, 1307002)], [unit(9, 100.0, 60.0)])
    bar1 = [i for i in range(b1.n) if b1.mech_id[i] == -2]
    assert float(b1.max_hp[bar1[0]]) == 180000.0


def test_follow_barrier_tracks_carrier():
    b = Battle(GD)
    b.add_card(0, GIANT, 1, -100.0, -60.0, equipment_id=1307001)
    b.add_card(1, 9, 1, 100.0, 60.0)
    b.officer_ids = {0: (), 1: ()}
    b.finalize()
    bar = int(np.where(b.mech_id == -2)[0][0])
    x0, y0 = float(b.x[bar]), float(b.y[bar])
    for tick in range(50):                     # carrier 移动 0.5s
        b.step(tick)
    assert (float(b.x[bar]), float(b.y[bar])) != (x0, y0)
    # 同 tick 跟随: 屏障在结算前贴上 carrier 本 tick 位置 (距离=0)
    assert math.hypot(float(b.x[bar]) - float(b.x[0]),
                      float(b.y[bar]) - float(b.y[0])) < 1e-6
    # carrier 死亡 → 屏障原地冻结, 不再跟随
    b.dead[0] = True
    xf, yf = float(b.x[bar]), float(b.y[bar])
    for tick in range(50, 100):
        b.step(tick)
    assert (float(b.x[bar]), float(b.y[bar])) == (xf, yf)


# ================================================================ T5 regen/lifesteal
def _field_repair_tech_id():
    for tid, td in GD.techs.items():
        if abs((td.extra or {}).get("recoveryLifeRate", 0) or 0) > 1e-9 \
                or "战地维修" in (td.name or ""):
            return int(tid)
    return None


def test_nano_repair_replaces_field_repair():
    fr = _field_repair_tech_id()
    b, _ = fight([unit(9, -100.0, -60.0, 13020001)], [unit(9, 100.0, 60.0)])
    assert float(b.regen[0]) == pytest.approx(0.045)
    if fr:
        b2, _ = fight([unit(9, -100.0, -60.0, 13020001)],
                      [unit(9, 100.0, 60.0)], tech_map0={9: [fr]})
        # 覆盖: 战地维修不再叠加 (任务书用户注记)
        assert float(b2.regen[0]) == pytest.approx(0.045)


def test_siphon_module_static_and_lifesteal():
    b0, _ = fight([unit(9, -100.0, -60.0)], [unit(9, 100.0, 60.0)])
    b1, _ = fight([unit(9, -100.0, -60.0, 1309001)], [unit(9, 100.0, 60.0)])
    assert float(b1.max_hp[0]) == pytest.approx(float(b0.max_hp[0]) * 1.3)
    assert float(b1.lifesteal[0]) == pytest.approx(0.90)
    assert float(b0.lifesteal[0]) == 0.0


# ================================================================ T6/T12 summons
def test_production_line_schedule():
    b = Battle(GD)
    b.add_card(0, GIANT2, 1, -120.0, -80.0, equipment_id=1306002)   # 野马线
    b.add_card(1, 10, 1, 120.0, 80.0)                               # 沙袋爬虫
    b.officer_ids = {0: (), 1: ()}
    b.finalize()
    mustang = 7
    ent = b._eq_pool[0]
    # t=0 没有召唤 (首批 t=period, 不是 t=0)
    for tick in range(int(10.0 / 0.01)):
        b.step(tick)
    live = int(np.count_nonzero((~b.dead) & (b.mech_id == mustang)))
    assert live == 0
    # 首批 t=11s: 4 辆
    for tick in range(int(10.0 / 0.01), int(11.01 / 0.01)):
        b.step(tick)
    live = int(np.count_nonzero((~b.dead) & (b.mech_id == mustang)))
    assert live == 4
    # t=22s: 第二批 (沙袋可能咬死召唤物, 用批次计数断言, 存活数下限保护)
    for tick in range(int(11.01 / 0.01), int(22.01 / 0.01)):
        b.step(tick)
    assert ent["done"] == 2
    live = int(np.count_nonzero((~b.dead) & (b.mech_id == mustang)))
    assert live >= 6


def test_production_line_batch_cap_and_carrier_death():
    b = Battle(GD)
    b.add_card(0, GIANT, 1, -120.0, -80.0, equipment_id=1306003)    # 钢球线 6 批
    b.add_card(1, 9, 1, 120.0, 80.0)
    b.officer_ids = {0: (), 1: ()}
    b.finalize()
    ent = b._eq_pool[0]
    assert len(ent["rows"]) == 2 * 6                    # 池容量 = 文案上限
    carrier = int(np.where((b.card_idx == 0) & (~b.dead))[0][0])
    for tick in range(int(16.5 / 0.01)):                # 首批后杀 carrier
        b.step(tick)
    assert ent["done"] == 1
    b.dead[carrier] = True
    b.hp[carrier] = 0.0
    for tick in range(int(16.5 / 0.01), int(60.0 / 0.01)):
        b.step(tick)
    assert ent["done"] == 1                             # 队列取消
    assert int(np.count_nonzero((~b.dead) & (b.mech_id == 8))) == 2


def test_summoned_units_are_battle_transient():
    """召唤物不是卡 (card_idx=-1), 死亡经验走无主通道, 不写回 persistent。"""
    b = Battle(GD)
    b.add_card(0, GIANT2, 1, -120.0, -80.0, equipment_id=1306002)
    b.add_card(1, 9, 1, 120.0, 80.0)
    b.officer_ids = {0: (), 1: ()}
    b.finalize()
    for tick in range(int(11.5 / 0.01)):
        b.step(tick)
    rows = np.where((~b.dead) & (b.mech_id == 7))[0]
    assert len(rows) == 4
    assert all(int(b.card_idx[i]) == -1 for i in rows)


# ================================================================ T0 flags
def test_feature_flags_switch_single_mechanism():
    on, _ = fight(duo(13010001), duo(0))
    assert on._eq_runtime and any(r["shield_absorbed"] > 0
                                  for r in on.damage_receipts)
    # eq_runtime=0 → 全部 runtime 行为关闭
    off, _ = fight(duo(13010001), duo(0), opts={"eq_runtime": 0})
    assert float(off.shield[0]) == 0.0 and off.damage_receipts == []
    # eq_off 单 ID 停用
    off1, _ = fight(duo(13010001), duo(0), opts={"eq_off": "13010001"})
    assert float(off1.shield[0]) == 0.0 and off1.damage_receipts == []
    # eq_only 白名单: 场上另一 ID 的行为被隔离
    both, _ = fight([unit(9, -100.0, -60.0, 13010001),
                     unit(9, -110.0, -60.0, 1309001)], duo(0),
                    opts={"eq_only": "1309001"})
    assert set(both._eq_runtime.keys()) == {1}
    siphon_rows = np.where(both.card_idx == 1)[0]
    shield_rows = np.where(both.card_idx == 0)[0]
    assert all(float(both.lifesteal[i]) == pytest.approx(0.90)
               for i in siphon_rows)
    assert all(float(both.shield[i]) == 0.0 for i in shield_rows)


def test_determinism_with_runtime_equipment():
    _, w1a = fight(duo(13010001), duo(0), opts={"seed": 7})
    _, w1b = fight(duo(13010001), duo(0), opts={"seed": 7})
    _, w2 = fight(duo(13010001), duo(0), opts={"seed": 8})
    assert w1a == w1b
    assert w2 == w1a      # 镜像无随机分沙袋; 换 seed 不应改变 winner


def test_no_equipment_battle_unchanged_by_runtime_pipeline():
    """无装备 → 引擎全部新通道零开销零副作用 (逐 case digest 不变的
    单元级代理断言; 全量回归见 benchmarks/run_equipment.py)。"""
    b0, w0 = fight(duo(0), duo(0))
    assert b0.damage_receipts == [] and b0.status_events == []
    assert b0._eq_runtime == {} and b0._eq_pool == []
    assert float(b0.shield.max()) == 0.0
    assert int(b0._eq_immune_perm.max()) == 0
