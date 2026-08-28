# -*- coding: utf-8 -*-
"""step32 动态装备 runtime 场景包生成器 (任务书 §7.1 单元微型场景层).

从零构建每件选定装备的 A/B 场景 (control 无装备 / treatment 有装备,
其他变量全部冻结), 输出 data/equipment_runtime_scenarios/
equipment-runtime-v1.json。场景是确定性单元场景 —— 不含语料抽样,
供 benchmarks/run_equipment_runtime.py 重算与将来 oracle 对拍。

每场景: name, kind, carrier mech/level/pos, foe(s), equipment_id,
可选 skills/techs, expect 字段只记录机制方向 (不锁 oracle 数值)。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "data", "equipment_runtime_scenarios",
                   "equipment-runtime-v1.json")

# mech ids: 堡垒1 火神3 熔点4 野马7 钢球8 尖牙9 爬虫10 铁锤13 (data/gamedata.json)
FORT, VULCAN, MELT, MUSTANG, STEELBALL, FANG, CRAWLER, HAMMER = \
    1, 3, 4, 7, 8, 9, 10, 13


def ab(name, eid, carrier, foes, level=1, note="", expect=None, skills0=None,
       skills1=None, techs0=None):
    x0, y0 = -100.0, -80.0
    ctrl = {
        "name": name + "_ctrl", "equipment_id": 0,
        "units0": [{"mech": carrier, "level": level, "x": x0, "y": y0}],
        "units1": [dict(f) for f in foes],
        "skills0": skills0, "skills1": skills1, "techs0": techs0,
    }
    treat = json.loads(json.dumps(ctrl))
    treat["name"] = name + "_treat"
    treat["equipment_id"] = eid
    return {
        "name": name, "equipment_id": eid, "carrier_mech": carrier,
        "level": level, "note": note, "expect": expect or {},
        "arms": [ctrl, treat],
    }


def main():
    scenarios = []

    # ---- T7 次级增幅核心: 静态 22%/22% (数值 oracle 待证, 方向断言)。
    # lv1 野马对爬虫在本引擎是劣势对局 (校准问题, 与装备无关) —— 方向断言
    # 用 "战斗时长更长 = 载体存活更久/输出更高"。
    scenarios.append(ab(
        "eq_secondary_core_dps", 13030009, MUSTANG,
        [{"mech": CRAWLER, "level": 1, "x": 100.0, "y": 80.0}],
        note="次级增幅核心 攻击+22%/生命+22% (任务书用户注记, oracle 待证)",
        expect={"direction": "survival_gain", "hp_mult": 0.22,
                "dmg_mult": 0.22}))

    # ---- T8 光子涂层: EMP 免疫窗口 + 过期 (burst t<30 vs t>30)
    scenarios.append(ab(
        "eq_photon_coating_emp_early", 1305003, FORT,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        skills1=[{"kind": "emp", "x": -100.0, "y": -80.0, "radius": 200.0,
                  "t": 5.0, "shield_damage": 10}],
        note="开战30s内 EMP 被涂层拦截",
        expect={"emp_blocked": True}))
    scenarios.append(ab(
        "eq_photon_coating_emp_late", 1305003, FORT,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        skills1=[{"kind": "emp", "x": -100.0, "y": -80.0, "radius": 200.0,
                  "t": 30.1, "shield_damage": 10}],
        note="30s 窗口外 EMP 生效 (边界 oracle 待证)",
        expect={"emp_blocked": False}))

    # ---- T8 抗干扰: EMP / 骇客 / 核心瘫痪三分离
    scenarios.append(ab(
        "eq_anti_jam_emp", 1308001, MUSTANG,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        skills1=[{"kind": "emp", "x": -100.0, "y": -80.0, "radius": 200.0,
                  "t": 0.0}],
        note="抗干扰: EMP disable+slow 整包免疫",
        expect={"emp_blocked": True}))

    # ---- T9 便携式护盾: 盾=maxHP, 先挡后破
    scenarios.append(ab(
        "eq_portable_shield_soak", 13010001, MUSTANG,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        note="便携式护盾: 盾值=装备后 maxHP (至少挡一次语义 oracle Q2 待证)",
        expect={"shield": "max_hp", "survive_longer": True}))

    # ---- T10 保护/超级屏障: 敌方 missile strike 只命中单位 (不命中装置),
    # 屏障重定向吸收会进 receipt
    scenarios.append(ab(
        "eq_protective_barrier", 1307001, VULCAN,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        skills1=[{"kind": "strike", "x": -100.0, "y": -80.0, "damage": 8000.0,
                  "splash": 15.0, "t": 2.0}],
        note="保护屏障 60000 跟随屏障 (半径30 cal; 覆盖语义 oracle Q3 待证)",
        expect={"barrier_hp": 60000.0, "barrier_radius": 30.0}))
    scenarios.append(ab(
        "eq_super_barrier", 1307002, VULCAN,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        skills1=[{"kind": "strike", "x": -100.0, "y": -80.0, "damage": 8000.0,
                  "splash": 15.0, "t": 2.0}],
        note="超级屏障 180000 跟随屏障",
        expect={"barrier_hp": 180000.0, "barrier_radius": 30.0}))

    # ---- T11 汲取 / 纳米维修
    scenarios.append(ab(
        "eq_siphon_sustain", 1309001, FORT,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        note="汲取: HP+30% + 90% 吸血 (基数=实际伤害, receipt 化待 oracle)",
        expect={"hp_mult": 0.30, "lifesteal": 0.90}))
    scenarios.append(ab(
        "eq_nano_repair_sustain", 13020001, FORT,
        [{"mech": FANG, "level": 1, "x": 100.0, "y": 80.0}],
        note="纳米维修 4.5% maxHP/s, 覆盖战地维修 (酸液禁疗 oracle Q5 待证)",
        expect={"regen_frac": 0.045, "replaces_field_repair": True}))

    # ---- T12 三类生产线 (巨型载体)
    scenarios.append(ab(
        "eq_tank_line", 1306001, MELT,
        [{"mech": CRAWLER, "level": 1, "x": 150.0, "y": 100.0}],
        note="坦克生产线: 13s x 2 铁锤 x 7 批 (首批时刻/继承 oracle Q6 待证)",
        expect={"summon_mech": HAMMER, "period": 13.0, "count": 2,
                "batches": 7}))
    scenarios.append(ab(
        "eq_mustang_line", 1306002, MELT,
        [{"mech": CRAWLER, "level": 1, "x": 150.0, "y": 100.0}],
        note="野马生产线: 11s x 4 野马 x 8 批",
        expect={"summon_mech": MUSTANG, "period": 11.0, "count": 4,
                "batches": 8}))
    scenarios.append(ab(
        "eq_steelball_line", 1306003, MELT,
        [{"mech": CRAWLER, "level": 1, "x": 150.0, "y": 100.0}],
        note="钢球生产线: 16s x 2 钢球 x 6 批",
        expect={"summon_mech": STEELBALL, "period": 16.0, "count": 2,
                "batches": 6}))

    doc = {
        "scenarios_version": "equipment-runtime-v1",
        "task": "pysim动态装备与伤害管线修正任务书-2026-08-28",
        "note": "expect 只记录机制方向/任务书文案值, 数值 confidence 全部 "
                "provisional; oracle 对拍见 data/equipment_oracle/",
        "seed": 20220822,
        "scenarios": scenarios,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("wrote %s (%d scenarios)" % (OUT, len(scenarios)))


if __name__ == "__main__":
    main()
