# step8-B unit tests: battlefield skills (devices / summons / strikes).
# run: pytest tests/test_skills.py
import os, sys
import numpy as np

from pysim.gamedata import GameData
from pysim.engine import Battle
from pysim.deploy import DEVICE_BARRIER, DEVICE_MISSILE
from pysim.skills import events_from_skill_actions, COMMANDER_SKILLS, CONTRAPTIONS

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print("%-56s got %-24s want %-18s %s" % (name, got, want, "OK" if ok else "FAIL"))


def turret_ev(x=0.0, y=0.0, **kw):
    ev = {"kind": "turret", "x": x, "y": y, "name": "飞弹", "id": 10001}
    ev.update(CONTRAPTIONS[10001]["def"])
    ev.update(kw)
    return ev


def main():
    gd = GameData(os.path.join(DATA, "gamedata.json"))

    # T1: sentry missile turret (飞弹) engages and kills an enemy in range
    b = Battle(gd)
    b.add_skill_event(1, turret_ev(0.0, 0.0))
    b.add_card(0, 28, 1, 40.0, 0.0)      # hound dummy, well inside range 100
    b.add_card(1, 28, 1, 250.0, 250.0)   # distant helper (a side with only
    b.finalize()                         # devices loses instantly, T5)
    ti = int(np.where(b.mech_id == DEVICE_MISSILE)[0][0])
    check("T1 turret base_dmg", int(b.base_dmg[ti]), 5000)
    check("T1 turret range", int(b.range[ti]), 100)
    w = b.simulate()
    check("T1 winner (turret side)", w, 1)
    check("T1 dummy died", bool(b.dead[np.where(b.mech_id == 28)[0][0]]), True)
    check("T1 turret fired", b.total_attacks > 0, True)
    check("T1 alive_count excludes devices",
          b.alive_count(1),
          int(np.count_nonzero((~b.dead) & (b.team == 1) & (~b.is_device))))

    # T2: barrier (护盾装置/空投护盾) absorbs damage to covered allies.
    # Small test radius so the shooter targets the ALLY (a 30m barrier would
    # be the nearest surface target and soak directly - also valid, but this
    # exercises the redirect path). The ally may level up (hp rescales,
    # fraction kept), so "untouched" = hp stayed at max.
    b = Battle(gd)
    b.add_skill_event(0, {"kind": "barrier", "x": 0.0, "y": 14.0,
                          "hp": 100000.0, "radius": 5.0, "name": "护盾装置"})
    b.add_card(0, 2, 1, 0.0, 10.0)       # protected longbow, covered (d=4)
    b.add_card(1, 2, 9, 0.0, -150.0)     # lv9 longbow ~8.4k/1.55s: breaks the
    b.finalize()                         # 100k pool at ~18s, ally safe at 5s
    bi = int(np.where(b.mech_id == DEVICE_BARRIER)[0][0])
    ui = int(np.where(b.mech_id == 2)[0][0])
    for tick in range(500):              # 5s: ~3 hits absorbed, pool intact
        b.step(tick)
    check("T2 ally untouched while shield holds",
          float(b.hp[ui]), float(b.max_hp[ui]))
    check("T2 shield drained", bool(b.hp[bi] < b.max_hp[bi]), True)
    for tick in range(2500):             # run to 30s: pool eventually breaks
        b.step(tick)
    check("T2 shield broke under sustained fire",
          bool(b.hp[bi] <= 0 or b.dead[bi]), True)

    # T2b: once the shield pool breaks, damage passes through
    b2 = Battle(gd)
    b2.add_skill_event(0, {"kind": "barrier", "x": 0.0, "y": 14.0,
                           "hp": 10.0, "radius": 5.0, "name": "护盾装置"})
    b2.add_card(0, 2, 1, 0.0, 10.0)
    b2.add_card(1, 2, 9, 0.0, -150.0)
    b2.finalize()
    ui2 = int(np.where(b2.mech_id == 2)[0][0])
    for tick in range(3000):
        b2.step(tick)
    check("T2b overflow passes after shield break",
          bool(b2.dead[ui2] or b2.hp[ui2] < b2.max_hp[ui2]), True)

    # T3: strike (导弹打击) hits enemies in splash only, no exp killer
    b = Battle(gd)
    b.add_card(0, 2, 1, -200.0, 0.0)     # far away, untouched
    b.add_card(1, 28, 9, 100.0, 0.0)     # inside splash (hound lv9, 2630 hp?)
    b.add_skill_event(0, {"kind": "strike", "x": 100.0, "y": 0.0,
                          "damage": 3000.0, "splash": 20.0, "name": "导弹打击"})
    b.finalize()
    hi = int(np.where(b.mech_id == 28)[0][0])
    hp_before = b.hp[hi]
    b.step(0)                            # strike lands on first tick
    b._apply_damage(0)
    check("T3 target hit by strike", float(b.hp[hi]) < float(hp_before), True)
    check("T3 strike damage", float(hp_before - b.hp[hi]), 3000.0)

    # T4: summon (呼叫机群) expands a real 12-wasp card at the position
    b = Battle(gd)
    b.add_skill_event(1, {"kind": "summon", "x": 0.0, "y": 150.0, "mech": 6,
                          "count": 12, "level": 1, "name": "呼叫机群"})
    b.add_card(0, 28, 1, 0.0, -100.0)
    b.finalize()
    check("T4 wasp units spawned", int(np.sum(b.mech_id == 6)), 12)
    check("T4 wasps fly", bool(np.all(b.is_fly[b.mech_id == 6])), True)
    check("T4 wasps count as mechs", b.alive_count(1), 12)

    # T5: devices never decide the fight - a device-only side has alive 0
    b = Battle(gd)
    b.add_skill_event(0, turret_ev(0.0, -50.0))
    b.add_card(1, 28, 1, 0.0, 100.0)
    b.finalize()
    check("T5 device-only side alive=0", b.alive_count(0), 0)

    # T6: action -> event normalization (mapping table + positions)
    acts = [
        {"type": "contraption", "id": 10001, "x": -135.0, "y": 297.0, "localTime": 71.4},
        {"type": "commander", "id": 300001, "rawId": 300001, "skillIndex": 0,
         "positions": [(100.0, 0.0)], "unitIndex": -1, "constructionIndex": -1,
         "localTime": 10.0},
        {"type": "commander", "id": 900001, "rawId": 0, "skillIndex": 1,
         "positions": [(0.0, 0.0)], "unitIndex": -1, "constructionIndex": 2,
         "localTime": 12.0},
    ]
    evs = events_from_skill_actions(acts)
    check("T6 mapped event count", len(evs), 2)
    check("T6 turret kind", evs[0]["kind"], "turret")
    check("T6 strike kind", evs[1]["kind"], "strike")
    check("T6 unmapped 900001 dropped", all(e.get("id") != 900001 for e in evs), True)

    return fails



def test_all():
    fails_n = main()
    assert not fails_n, "test_skills.py: %s checks failed" % fails_n
