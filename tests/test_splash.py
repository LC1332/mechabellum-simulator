# step6b splash unit tests.
# run: pytest tests/test_splash.py
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
    print("%-52s got %-24s want %-16s %s" % (name, got, want, "OK" if ok else "FAIL"))


def main():
    gd = GameData(os.path.join(DATA, "gamedata.json"))

    # S1: 火神 base splash = 15 (was gated to 0 by useSelfSplash=False)
    b = Battle(gd)
    b.add_card(0, 3, 1, 0.0, -80.0, techs=[])
    b.add_card(1, 10, 1, 0.0, 80.0, techs=[])
    b.finalize()
    vul = int(np.where(b.mech_id == 3)[0][0])
    check("S1 vulcan splash on", float(b.splash[vul]), 15.0)

    b0 = Battle(gd)
    b0.opts["splash"] = 0
    b0.add_card(0, 3, 1, 0.0, -80.0, techs=[])
    b0.add_card(1, 10, 1, 0.0, 80.0, techs=[])
    b0.finalize()
    vul0 = int(np.where(b0.mech_id == 3)[0][0])
    check("S1 legacy ablation splash off", float(b0.splash[vul0]), 0.0)

    # S2: one splash volley damages every crawler module within radius,
    # single-target units hit only the primary (deterministic _deal_damage)
    b2 = Battle(gd)
    b2.add_card(0, 3, 1, 0.0, -40.0, techs=[])    # 火神 splash 15
    b2.add_card(0, 7, 1, -60.0, -40.0, techs=[])  # 野马 single-target
    b2.add_card(1, 10, 1, 0.0, 40.0, techs=[])    # 24-module crawler card
    b2.finalize()
    crawlers = np.where(b2.mech_id == 10)[0]
    tgt = int(crawlers[0])
    vul = int(np.where(b2.mech_id == 3)[0][0])
    mus = int(np.where(b2.mech_id == 7)[0][0])
    b2._ev_victim, b2._ev_dmg, b2._ev_killer = [], [], []
    # splash volley at the target's position (damage lands via _apply_damage)
    b2._deal_damage(vul, tgt, b2.x[tgt], b2.y[tgt], 100.0, b2.splash[vul])
    n_splash = len(b2._ev_victim)
    b2._apply_damage(0)
    hit_splash = int(np.sum(b2.hp[crawlers] < b2.max_hp[crawlers]))
    check("S2 splash queue covers many modules", n_splash, hit_splash)
    check("S2 splash hits many modules", hit_splash > 1, True)
    # single-target volley at the same point
    b2._ev_victim, b2._ev_dmg, b2._ev_killer = [], [], []
    b2._deal_damage(mus, tgt, b2.x[tgt], b2.y[tgt], 100.0, 0.0)
    n_single = len(b2._ev_victim)
    b2._apply_damage(0)
    check("S2 single-target hits exactly 1", n_single, 1)

    # S3: splash survives level-up re-bake
    card = b2.cards[int(b2.card_idx[vul])]
    card["level"] = 5
    b2._rescale_card(int(b2.card_idx[vul]), 5)
    check("S3 splash kept after re-bake", float(b2.splash[vul]), 15.0)

    # S4: tech splash still stacks on top (突击模式 longbow +9)
    b4 = Battle(gd)
    b4.add_card(0, 2, 1, 0.0, -40.0, techs=[10102])
    b4.add_card(1, 10, 1, 0.0, 40.0, techs=[])
    b4.finalize()
    lb = int(np.where(b4.mech_id == 2)[0][0])
    check("S4 tech splash +9 (base 0)", float(b4.splash[lb]), 9.0)

    # S5: end-to-end - vulcan army clears crawler swarm much faster
    def clear_time(splash_on):
        bb = Battle(gd)
        if not splash_on:
            bb.opts["splash"] = 0
        for k in range(3):
            bb.add_card(0, 3, 5, -40.0 + 40.0 * k, -60.0, techs=[])
        bb.add_card(1, 10, 3, 0.0, 60.0, techs=[])
        bb.add_card(1, 9, 3, 40.0, 60.0, techs=[])
        bb.finalize()
        bb.simulate()
        return bb.end_tick * 0.01

    t_on = clear_time(True)
    t_off = clear_time(False)
    check("S5 splash clears chaff faster", t_on < t_off, True)
    print("    clear time: splash %.1fs vs single %.1fs" % (t_on, t_off))

    print()
    print("RESULT: %d checks failed" % fails)
    print("ALL PASS" if fails == 0 else "HAS FAILURES")
    return fails



def test_all():
    fails_n = main()
    assert not fails_n, "test_splash.py: %s checks failed" % fails_n
