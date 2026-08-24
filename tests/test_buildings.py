# step12 unit tests: battlefield constructions (wall / AA cannon / RF cannon /
# magnetic barricade). run: python -m pysim.test_buildings
import os, sys
import numpy as np

from pysim.gamedata import GameData
from pysim.engine import Battle
from pysim.deploy import BLD_WALL, BLD_AA, BLD_RF, BLD_MAGNET, MAGNET_SELF_T

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print("%-46s got %-22s want %-18s %s" % (name, got, want, "OK" if ok else "FAIL"))


def check_true(name, cond, detail=""):
    global fails
    if not cond:
        fails += 1
    print("%-46s %-40s %s" % (name, detail, "OK" if cond else "FAIL"))


def main():
    gd = GameData(os.path.join(DATA, "gamedata.json"))

    # T7 (cheap sanity first): snapshot replay - the engine keeps no state
    # between battles; wall module HP is the decoded per-module value (Q1).
    def wall_battle():
        b = Battle(gd)
        b.add_building(0, 1, 0.0, -55.0, index=3)
        b.add_card(1, 10, 1, 0.0, 100.0)
        b.finalize()
        return b

    b7a = wall_battle()
    wi = np.where(b7a.is_bld)[0]
    check("T7 wall module count", len(wi), 5)
    check("T7 wall module HP (per module, Q1)", int(b7a.max_hp[wi][0]), 1446)
    b7a.hp[wi] = 0.0
    b7a._ev_victim, b7a._ev_dmg, b7a._ev_killer = list(wi), [1.0] * len(wi), [-1] * len(wi)
    b7a.time = 1.0
    b7a._apply_damage(0)
    check("T7 wall group down after full kill", b7a.bld_groups_down[0], 1)
    b7b = wall_battle()
    check("T7 fresh battle wall alive (no residue)",
          int(np.count_nonzero(b7b.dead[wi])), 0)
    check("T7 fresh battle wall full HP",
          float(b7b.hp[np.where(b7b.is_bld)[0][0]]), 1446.0)

    # T1 wall shield: the wall line is the attackers' nearest enemy, so they
    # must chew through the modules before anything else (rule-1 nearest).
    # A token crawler in the far corner keeps team0's alive count > 0.
    b1 = Battle(gd)
    for k in range(4):
        b1.add_card(1, 2, 9, -60.0 + 40.0 * k, 100.0)
    b1.add_card(0, 10, 1, -300.0, -280.0)
    b1.add_building(0, 1, 0.0, -55.0, index=0)
    b1.trace_enabled = True
    b1.finalize()
    w1 = b1.simulate()
    first_kill = next((e for e in b1.trace if "|kill|" in e), None)
    p = first_kill.split("|") if first_kill else []
    check("T1 first kill is a wall module", int(p[6]) if p else 0, BLD_WALL)
    check("T1 wall group destroyed", b1.bld_groups_down[0], 1)
    check("T1 wall modules all dead",
          int(np.count_nonzero(b1.dead[np.where(b1.is_bld)[0]])), 5)

    # T2 wall group semantics: modules bleed independently, the group event
    # only fires when the last module dies.
    b2 = Battle(gd)
    b2.add_building(0, 1, 0.0, -55.0, index=0)
    b2.add_card(1, 10, 1, 0.0, 100.0)
    b2.trace_enabled = True
    b2.finalize()
    wi2 = np.where(b2.is_bld)[0]
    b2._ev_victim, b2._ev_dmg, b2._ev_killer = list(wi2[:2]), [1e9] * 2, [-1, -1]
    b2.time = 0.5
    b2._apply_damage(0)
    check("T2 partial kill: no group event", b2.bld_groups_down[0], 0)
    grp = b2.building_groups()
    check("T2 group 3/5 alive", [v for v in grp.values() if v[1] == 5][0], (3, 5))
    b2._ev_victim, b2._ev_dmg, b2._ev_killer = list(wi2[2:]), [1e9] * 3, [-1] * 3
    b2.time = 0.6
    b2._apply_damage(0)
    check("T2 last module: group event", b2.bld_groups_down[0], 1)

    # T3 AA magazine: 6 shots @2.5s then a 10s reload. A huge-HP tower is the
    # perfect immobile dummy (towers never fire back).
    b3 = Battle(gd)
    b3.opts["tower_hp"] = 100000.0
    b3.add_building(0, 2, 0.0, 0.0, index=1)
    b3.add_tower(1, 0.0, 100.0, 0)
    b3.trace_enabled = True
    b3.finalize()
    for t in range(2500):
        b3.step(t)
    shots = b3.total_attacks
    reloads = [e for e in b3.trace if "|bld_reload|" in e]
    rt = float(reloads[0].split("|")[1]) if reloads else -1.0
    check_true("T3 AA 6 shots then 10s reload @~13.5s",
               len(reloads) == 1 and abs(rt - 13.5) < 0.35,
               "shots=%d reload@%s" % (shots, rt))
    check_true("T3 AA 7 shots in 25s (6+1 after reload)", shots == 7, "shots=%d" % shots)
    check_true("T3 AA damaged the tower dummy",
               float(np.max(b3.max_hp[np.where(b3.is_tower)[0]]
                            - b3.hp[np.where(b3.is_tower)[0]])) > 5 * 2748, "dmg>5 shots")

    # T4 RF magazine: 10 shots @0.3s then 2.5s reload, ~20 shots per 10s.
    b4 = Battle(gd)
    b4.opts["tower_hp"] = 100000.0
    b4.add_building(0, 3, 0.0, 0.0, index=1)
    b4.add_tower(1, 0.0, 100.0, 0)
    b4.trace_enabled = True
    b4.finalize()
    for t in range(1000):
        b4.step(t)
    shots4 = b4.total_attacks
    reloads4 = [e for e in b4.trace if "|bld_reload|" in e]
    check_true("T4 RF ~20 shots in 10s", 19 <= shots4 <= 21, "shots=%d" % shots4)
    check_true("T4 RF reload events fired", len(reloads4) >= 2, "n=%d" % len(reloads4))

    # T5 magnet three states: hidden -> pop (enemy within 10m) -> slow field
    # (15m, x(1-slow)) -> self-destruct 5s after pop, no exp, no kill entry.
    # The pull target is a huge-HP tower BEHIND the magnet line, so the walker
    # must cross the line (and pop modules) to get within its own range.
    b5 = Battle(gd)
    b5.opts["tower_hp"] = 1000000.0
    b5.add_building(1, 4, 0.0, 50.0, index=2)     # enemy magnet line
    b5.add_card(0, 2, 9, 0.0, -100.0)             # single-module walker
    b5.add_tower(1, 0.0, 290.0, 0)                # far pull target
    b5.trace_enabled = True
    b5.finalize()
    mag = np.where(b5.is_bld & (b5.mech_id == BLD_MAGNET))[0]
    check("T5 magnet module count (count=10)", len(mag), 10)
    # before pop: hidden magnets are never attack/movement targets
    b5.step(0)
    check_true("T5 hidden magnet not targeted",
               not np.any(b5.target[mag] >= 0) and not np.any(mag == b5.mv_target[0]))
    pops = []
    slow_seen = False
    u0 = int(np.where(~b5.is_bld & (b5.team == 0))[0][0])
    for t in range(4500):
        b5.step(t)
        if not pops and any("|magnet_pop|" in e for e in b5.trace):
            pops = [e for e in b5.trace if "|magnet_pop|" in e]
        if pops and b5._magnet_fac[u0] < 1.0:
            slow_seen = True
    tp = float(pops[0].split("|")[1]) if pops else -1.0
    downs = [e for e in b5.trace if "|magnet_down|" in e]
    td = float(downs[0].split("|")[1]) if downs else -1.0
    check_true("T5 magnet popped when enemy neared", len(pops) >= 1, "pop@%s" % tp)
    check_true("T5 slow field applied (fac %.2f)" % b5._magnet_fac[u0], slow_seen)
    check("T5 magnet group fully gone", b5.bld_groups_down[1], 1)
    check_true("T5 magnet self-destruct grants no kill entry",
               not any(int(e.split("|")[5]) == BLD_MAGNET for e in b5.trace if "|kill|" in e))

    # T5b self-destruct timer driven directly (in the battle above the popped
    # modules die to the walker's cannon before their own 5s fuse runs out)
    b5b = Battle(gd)
    b5b.add_building(1, 4, 0.0, 50.0, index=2)
    b5b.add_card(0, 10, 1, 0.0, -100.0)
    b5b.trace_enabled = True
    b5b.finalize()
    m5b = int(np.where(b5b.is_bld)[0][0])
    b5b.bld_state[m5b] = 1
    b5b.bld_pop_at[m5b] = 10.0
    b5b.time = 14.9
    b5b._update_magnets()
    check("T5b alive just before fuse", bool(b5b.dead[m5b]), False)
    b5b.time = 15.1
    b5b._update_magnets()
    check("T5b self-destruct at pop+5s", bool(b5b.dead[m5b]), True)

    # T6 splash soaks walls: a splash shooter hitting a unit standing at the
    # wall damages the wall modules in the same volley (BuildingDamage query).
    # A huge-HP tower at the wall line is the deterministic impact point.
    b6 = Battle(gd)
    b6.opts["tower_hp"] = 1000000.0
    b6.add_card(0, 3, 9, 0.0, -20.0)             # 火神 splash 15, range 95
    b6.add_tower(1, 0.0, 60.0, 0)
    b6.add_building(1, 1, 0.0, 75.0, index=0)    # enemy wall behind the dummy
    b6.finalize()
    w6i = np.where(b6.is_bld)[0]
    hp0 = b6.hp[w6i].copy()
    for t in range(400):
        b6.step(t)
        if np.any(b6.hp[w6i] < hp0):
            break
    check_true("T6 splash damaged wall modules",
               bool(np.any(b6.hp[w6i] < hp0[0])),
               "hp %s" % [int(v) for v in b6.hp[w6i][:3]])

    # T8 air semantics (user Q3): cannons never hit flyers; buildings are
    # aggro for anything that can shoot ground; air-only attackers skip them.
    flyer_id = next(mid for mid, m in gd.mechs.items() if m.is_fly)
    b8 = Battle(gd)
    b8.add_building(0, 2, 0.0, -50.0, index=0)
    b8.add_card(1, flyer_id, 1, 0.0, 100.0)
    b8.finalize()
    aa = int(np.where(b8.is_bld)[0][0])
    fl = int(np.where(b8.mech_id == flyer_id)[0][0])
    check_true("T8 AA cannot hit flyers", not b8.hittable[aa, fl],
               "flyer=%s" % gd.mechs[flyer_id].name)
    check("T8 flyer hits building iff it can attack ground",
          bool(b8.hittable[fl, aa]), bool(gd.mechs[flyer_id].can_attack_ground))
    m51 = gd.mechs.get(51)
    if m51 is not None and m51.is_fly and not m51.can_attack_ground:
        b8b = Battle(gd)
        b8b.add_building(0, 2, 0.0, -50.0, index=0)
        b8b.add_card(1, 51, 1, 0.0, 100.0)
        b8b.finalize()
        a51 = int(np.where(b8b.mech_id == 51)[0][0])
        w8 = int(np.where(b8b.is_bld)[0][0])
        check("T8 air-only attacker cannot hit buildings", bool(b8b.hittable[a51, w8]), False)

    # T9 paralysis never disables buildings (they keep firing at full damage).
    b9 = Battle(gd)
    b9.add_card(0, 2, 9, 0.0, -100.0)
    b9.add_card(1, 10, 1, 40.0, 100.0)           # team1 mech for the fac check
    b9.add_building(1, 2, 0.0, 100.0, index=0)
    b9.add_tower(1, 20.0, 100.0, 0)
    b9.finalize()
    ti = int(np.where(b9.is_tower)[0][0])
    b9.hp[ti] = 1.0
    b9._ev_victim, b9._ev_dmg, b9._ev_killer = [ti], [10.0], [0]
    b9.time = 1.0
    b9._apply_damage(0)
    aa9 = int(np.where(b9.is_bld)[0][0])
    u91 = int(np.where(~b9.is_tower & ~b9.is_bld & (b9.team == 1))[0][0])
    check("T9 unit paralysed (dmg x0.1)", float(b9._dmg_fac[u91]), 0.1)
    check("T9 building immune (dmg x1.0)", float(b9._dmg_fac[aa9]), 1.0)
    check("T9 building immune (amp x1.0)", float(b9._amp_fac[aa9]), 1.0)

    print()
    print("RESULT: %d checks failed" % fails)
    print("ALL PASS" if fails == 0 else "HAS FAILURES")
    return fails



def test_all():
    fails_n = main()
    assert not fails_n, "test_buildings.py: %s checks failed" % fails_n
