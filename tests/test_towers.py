# step8 unit tests: crystal towers + paralysis.
# run: pytest tests/test_towers.py
import os, sys
import numpy as np

from pysim.gamedata import GameData
from pysim.engine import Battle

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print("%-46s got %-22s want %-18s %s" % (name, got, want, "OK" if ok else "FAIL"))


def main():
    gd = GameData(os.path.join(DATA, "gamedata.json"))

    # T1: ranged army vs weak crawlers; a tower stands in the lane.
    # Rule (1): the tower is attacked en route and dies before annihilation.
    # (one lone longbow actually loses to a 24-crawler card with default
    # techs - use a real army)
    b = Battle(gd)
    for k in range(6):
        b.add_card(0, 2, 9, -90.0 + 36.0 * k, -100.0)
    b.add_card(1, 10, 1, 0.0, 100.0)        # weak crawler far side
    b.add_tower(1, 0.0, 60.0, 0)            # tower in the advance lane
    b.trace_enabled = True
    b.finalize()
    w = b.simulate()
    kills_t = None
    tower_t = None
    par_t = None
    for e in b.trace:
        p = e.split("|")
        if "kill" in e and kills_t is None:
            kills_t = float(p[1])
        if "tower_down" in e and tower_t is None:
            tower_t = float(p[1])
        if "paralyse" in e and par_t is None:
            par_t = (float(p[1]), float(p[-1]))
    check("T1 winner team0", w, 0)
    check("T1 tower destroyed", b.towers_down[1], 1)
    check("T1 tower died during fight", tower_t is not None, True)
    check("T1 paralyse fired", par_t is not None, True)
    check("T1 paralyse 9s (level0)", par_t is not None and abs((par_t[1] - par_t[0]) - 9.0) < 0.05, True)

    # T2: strengthen L2 -> 5s (same army as T1 so the tower reliably dies)
    b2 = Battle(gd)
    for k in range(6):
        b2.add_card(0, 2, 9, -90.0 + 36.0 * k, -100.0)
    b2.add_card(1, 10, 1, 0.0, 100.0)
    b2.add_tower(1, 0.0, 60.0, 2)
    b2.trace_enabled = True
    b2.finalize()
    b2.simulate()
    par = [e for e in b2.trace if "paralyse" in e]
    p = par[0].split("|")
    check("T2 L2 paralyse duration ~5s", abs((float(p[-1]) - float(p[1])) - 5.0) < 0.05, True)

    # T3: rule "last": tower only attacked when no enemy units remain
    b3 = Battle(gd)
    b3.opts["tower_target"] = "last"
    b3.add_card(0, 2, 9, 0.0, -100.0)
    b3.add_card(1, 10, 1, 0.0, 100.0)
    b3.add_tower(1, 0.0, 100.0, 0)
    b3.finalize()
    w3 = b3.simulate()
    # fight ends at crawler annihilation; tower must still stand
    check("T3 rule-last tower survives", b3.towers_down[1], 0)

    # T4: paralysis factors flip and restore on expiry
    b4 = Battle(gd)
    b4.add_card(0, 2, 9, 0.0, -100.0)
    b4.add_card(1, 10, 1, 10.0, 100.0)
    b4.add_tower(1, 0.0, 100.0, 0)
    b4.finalize()
    ti = int(np.where(b4.is_tower)[0][0])
    b4.hp[ti] = 1.0
    b4._ev_victim, b4._ev_dmg, b4._ev_killer = [ti], [10.0], [0]
    b4.time = 1.0
    b4._apply_damage(0)
    team1 = np.where((b4.team == 1) & (~b4.is_tower))[0]
    check("T4 paralysis factors on", float(b4._dmg_fac[team1][0]), 0.1)
    check("T4 amp factor on", float(b4._amp_fac[team1][0]), 1.5)
    b4.time = b4.paralyse_until[1] + 0.01
    b4._check_paralyse_expiry()
    check("T4 paralysis restored", float(b4._dmg_fac[team1][0]), 1.0)

    # T5: tower hp option honored
    b5 = Battle(gd)
    b5.opts["tower_hp"] = 50000.0
    b5.add_card(0, 2, 9, 0.0, -100.0)
    b5.add_card(1, 10, 1, 0.0, 100.0)
    b5.add_tower(1, 0.0, 100.0, 0)
    b5.finalize()
    ti5 = int(np.where(b5.is_tower)[0][0])
    check("T5 tower_hp override", float(b5.max_hp[ti5]), 50000.0)

    print()
    print("RESULT: %d checks failed" % fails)
    print("ALL PASS" if fails == 0 else "HAS FAILURES")
    return fails



def test_all():
    fails_n = main()
    assert not fails_n, "test_towers.py: %s checks failed" % fails_n
