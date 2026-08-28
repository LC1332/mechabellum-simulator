# step5 任务书 engine tests (§4/§6/§7): ground areas (oil/smoke/acid),
# EMP/photon bursts, ion beam, seeded storm and move-beacon waypoints.
# run: pytest tests/test_battlefield_skills.py
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pysim.gamedata import GameData
from pysim.engine import Battle
from pysim.deploy import DEVICE_BARRIER

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
gd = GameData(os.path.join(DATA, "data", "gamedata.json"))


def emp_ev(x=0.0, y=100.0, radius=60.0):
    return {"kind": "emp", "x": x, "y": y, "radius": radius,
            "shield_damage": 20000.0, "duration": 25.0, "slow_mult": 0.6,
            "name": "电磁冲击", "ref": "area:0"}


# ================================================================ EMP (T7)
def test_emp_damage_barrier_and_covered_immunity():
    b = Battle(gd)
    b.add_skill_event(0, {"kind": "barrier", "x": 0.0, "y": 100.0,
                          "hp": 50000.0, "radius": 30.0, "name": "护盾装置"})
    b.add_card(1, 28, 1, 0.0, 100.0)      # inside the barrier
    b.add_card(1, 10, 1, 0.0, -100.0)     # far outside
    b.add_skill_event(0, emp_ev())
    b.finalize()
    bar = int(np.where(b.mech_id == DEVICE_BARRIER)[0][0])
    b.step(0)
    assert round(50000.0 - float(b.hp[bar])) == 20000
    hound = int(np.where(b.mech_id == 28)[0][0])
    crawl = int(np.where(b.mech_id == 10)[0][0])
    assert float(b.emp_until[hound]) < 0     # barrier-covered: immune
    assert float(b.emp_until[crawl]) < 0     # outside radius: untouched


def test_emp_hits_unprotected_unit_25s():
    b = Battle(gd)
    b.add_card(1, 28, 1, 0.0, 100.0)
    b.add_skill_event(0, emp_ev())
    b.finalize()
    b.step(0)
    hound = int(np.where(b.mech_id == 28)[0][0])
    assert 24.0 < float(b.emp_until[hound]) <= 25.01
    assert float(b._area_fac[hound]) == 1.0   # EMP slow rides emp_fac


def test_emp_giant_radius_130():
    b = Battle(gd)
    b.add_card(1, 28, 1, 0.0, 120.0)     # outside r60, inside r130
    b.add_skill_event(0, emp_ev(radius=130.0))
    b.finalize()
    b.step(0)
    hound = int(np.where(b.mech_id == 28)[0][0])
    assert float(b.emp_until[hound]) > 0


# ================================================================ oil (T6)
def test_oil_slows_enemies_x045():
    b = Battle(gd)
    b.add_card(1, 10, 1, 0.0, 50.0)
    b.add_skill_event(0, {"kind": "oil", "x": 0.0, "y": 50.0, "radius": 30.0,
                          "slow_mult": 0.45, "shield_block": True,
                          "points": [[0.0, 50.0], [40.0, 50.0]],
                          "ref": "area:0"})
    b.finalize()
    b.step(0)
    crawl = int(np.where(b.mech_id == 10)[0][0])
    assert round(float(b._area_fac[crawl]), 4) == 0.45


def test_oil_ignition_converts_to_flame():
    b = Battle(gd)
    b.add_card(1, 10, 1, 200.0, 0.0)      # far away, not in the oil
    b.add_skill_event(0, {"kind": "oil", "x": 0.0, "y": 0.0, "radius": 30.0,
                          "slow_mult": 0.45,
                          "points": [[0.0, 0.0], [30.0, 0.0]], "ref": "area:0"})
    b.add_skill_event(1, {"kind": "burn", "x": 20.0, "y": 0.0, "dps": 270.0,
                          "radius": 15.0})
    b.finalize()
    n0 = len(b._burns)
    b.step(0)
    assert b.area_results() == (("area:0", True),)
    assert len(b._burns) > n0 + 1           # spine circles became fire


def test_oil_shield_clipped_generation():
    """A barrier covering the whole capsule: nothing lands (permanent)."""
    b = Battle(gd)
    b.add_card(1, 10, 1, 200.0, 0.0)
    b.add_skill_event(1, {"kind": "barrier", "x": 15.0, "y": 0.0,
                          "hp": 999999.0, "radius": 60.0, "name": "护盾装置"})
    b.add_skill_event(0, {"kind": "oil", "x": 0.0, "y": 0.0, "radius": 30.0,
                          "slow_mult": 0.45, "shield_block": True,
                          "points": [[0.0, 0.0], [30.0, 0.0]], "ref": "area:0"})
    b.finalize()
    assert b._areas[0]["dead"] is True      # fully shield-covered
    b.step(0)
    crawl = int(np.where(b.mech_id == 10)[0][0])
    assert float(b._area_fac[crawl]) == 1.0


# ================================================================ smoke (T6)
def test_smoke_cuts_enemy_range_35pct():
    b = Battle(gd)
    b.add_card(1, 2, 1, 0.0, 60.0)
    b.add_skill_event(0, {"kind": "smoke", "x": 0.0, "y": 60.0,
                          "radius": 30.0, "range_mult": 0.65,
                          "points": [[0.0, 60.0], [50.0, 60.0]]})
    b.finalize()
    lb = int(np.where(b.mech_id == 2)[0][0])
    r0 = float(b.range[lb])
    b.step(0)
    assert round(float(b.range[lb]) / r0, 4) == 0.65
    # walking out of the smoke restores the range (edge-triggered)
    b.x[lb] = 500.0
    b.step(1)
    assert round(float(b.range[lb]) / r0, 4) == 1.0


# ================================================================ acid (T9)
def test_acid_dot_three_pct_per_second():
    b = Battle(gd)
    b.add_card(1, 28, 1, 0.0, 50.0)
    b.add_skill_event(0, {"kind": "acid", "x": 0.0, "y": 50.0, "radius": 30.0,
                          "pct_dps": 0.03, "vuln_mult": 2.5,
                          "points": [[0.0, 50.0], [40.0, 50.0]]})
    b.finalize()
    hound = int(np.where(b.mech_id == 28)[0][0])
    hp0 = float(b.hp[hound])
    b.step(0)
    assert round(float(hp0 - b.hp[hound]), 1) == \
        round(float(b.max_hp[hound]) * 0.03 * 0.01, 1)
    assert bool(b._acid_on[hound])


def test_acid_vulnerability_amplifies_attacks_not_dot():
    """A/B: identical attack events, with vs without acid on the victim.
    Attack damage (killer >= 0) x2.5; killerless area ticks stay raw."""
    def run(with_acid, killer):
        b = Battle(gd)
        b.add_card(1, 28, 1, 0.0, 50.0)
        if with_acid:
            b.add_skill_event(0, {"kind": "acid", "x": 0.0, "y": 50.0,
                                  "radius": 30.0, "pct_dps": 0.0,
                                  "vuln_mult": 2.5,
                                  "points": [[0.0, 50.0], [40.0, 50.0]]})
        b.finalize()
        hound = int(np.where(b.mech_id == 28)[0][0])
        if with_acid:
            b._step5_areas_tick()            # set _acid_on (pct 0: no DoT)
            assert not b._ev_dmg             # pct_dps 0 queues nothing
        b._ev_victim = [hound]
        b._ev_dmg = [1000.0]
        b._ev_killer = [0 if killer else -1]
        hp0 = float(b.hp[hound])
        b._apply_damage(0)
        return hp0 - float(b.hp[hound])

    base_atk = run(False, killer=True)
    acid_atk = run(True, killer=True)
    assert round(acid_atk - base_atk, 3) == round(base_atk * 1.5, 3)
    # killerless events (area ticks / strikes) never re-amplify
    assert round(run(True, killer=False), 3) == round(base_atk, 3)


# ================================================================ photon (T8)
def test_photon_20s_and_clears_and_blocks():
    b = Battle(gd)
    b.add_card(0, 28, 1, 0.0, 50.0)
    b.add_skill_event(0, {"kind": "photon", "x": 0.0, "y": 50.0,
                          "radius": 30.0, "duration": 20.0,
                          "dmg_taken_mult": 0.7,
                          "points": [[0.0, 50.0], [40.0, 50.0]]})
    b.finalize()
    hound = int(np.where(b.mech_id == 28)[0][0])
    b.emp_until[hound] = 5.0                # pre-existing EMP
    b.burn_pct_until[hound] = 5.0           # pre-existing 引燃
    b.step(0)
    assert 19.0 < float(b.photon_until[hound]) <= 20.01
    assert float(b.emp_until[hound]) < 0    # QA-4: cleared
    assert float(b.burn_pct_until[hound]) < 0
    # damage taken x0.70 through the queue
    b._ev_victim = [hound]
    b._ev_dmg = [100.0]
    b._ev_killer = [-1]
    hp0 = float(b.hp[hound])
    b._apply_damage(1)
    assert round(float(hp0 - b.hp[hound]), 1) == 70.0


def test_photon_blocks_acid_and_emp():
    b = Battle(gd)
    b.add_card(0, 28, 1, 0.0, 50.0)
    b.add_skill_event(0, {"kind": "photon", "x": 0.0, "y": 50.0,
                          "radius": 30.0, "duration": 20.0,
                          "dmg_taken_mult": 0.7,
                          "points": [[0.0, 50.0], [40.0, 50.0]]})
    b.add_card(1, 10, 1, 0.0, -200.0)
    b.add_skill_event(1, {"kind": "acid", "x": 0.0, "y": 50.0, "radius": 30.0,
                          "pct_dps": 0.03, "vuln_mult": 2.5,
                          "points": [[0.0, 50.0], [40.0, 50.0]]})
    b.add_skill_event(1, emp_ev(x=0.0, y=50.0))
    b.finalize()
    hound = int(np.where(b.mech_id == 28)[0][0])
    b.step(0)                               # photon first (side 0 burst)
    hp0 = float(b.hp[hound])
    b.step(1)
    assert float(b.hp[hound]) == hp0        # no acid DoT through photon
    assert float(b.emp_until[hound]) < 0    # EMP blocked


# ================================================================ storm (T11)
def _run_storm(seed):
    b = Battle(gd)
    b.add_card(1, 10, 1, 0.0, 50.0)
    b.add_skill_event(0, {"kind": "storm", "x": 0.0, "y": 50.0,
                          "radius": 130.0, "duration": 12.0, "interval": 0.8,
                          "damage": 800.0, "splash": 8.0, "slow_mult": 0.6,
                          "slow_duration": 1.0})
    b._battle_seed = seed
    b.finalize()
    crawl = int(np.where(b.mech_id == 10)[0][0])
    log = []
    for t in range(1200):
        hp = float(b.hp[crawl])
        b.step(t)
        log.append(round(hp - float(b.hp[crawl]), 4))
    return log


def test_storm_seeded_deterministic_and_harmful():
    log1 = _run_storm(42)
    assert sum(log1) > 0                    # provisional unit-guided strikes
    assert _run_storm(42) == log1           # same seed replays identically
    assert _run_storm(43) != log1           # different seed distributes


# ================================================================ ion (T10)
def test_ion_beam_sweeps_and_expires():
    b = Battle(gd)
    b.add_card(1, 10, 1, 0.0, 0.0)
    b.add_skill_event(0, {"kind": "ion", "x": -60.0, "y": 0.0,
                          "radius": 20.0, "speed": 25.0, "dps": 600.0,
                          "points": [[-60.0, 0.0], [60.0, 0.0]]})
    b.finalize()
    crawl = int(np.where(b.mech_id == 10)[0][0])
    hp0 = float(b.hp[crawl])
    for t in range(600):                    # 6s > 120m / 25mps travel
        b.step(t)
    assert float(hp0 - b.hp[crawl]) > 0
    assert all(io["done"] for io in b._ions)   # no ground trail


# ================================================================ beacon (T12)
def test_beacon_member_selection_offsets_and_arrival():
    b = Battle(gd)
    b.add_card(0, 2, 1, 0.0, 0.0)           # longbow card at A
    b.add_tower(1, 0.0, 290.0)              # static distant foe (no chase)
    b.add_skill_event(0, {"kind": "beacon", "x": 0.0, "y": 0.0,
                          "radius": 40.0,
                          "points": [[0.0, 0.0], [0.0, -60.0],
                                     [0.0, -120.0]]})
    b.finalize()
    members = np.where((b.mech_id == 2) & (b.team == 0))[0]
    assert len(members) >= 1
    assert all(b._wp_active[u] for u in members)
    # offset law: wp0 - B == member - A (relative formation preserved)
    for u in members:
        u = int(u)
        assert abs(float(b._wp_x0[u]) - 0.0 - float(b.x[u])) < 1e-6
        assert abs(float(b._wp_y0[u]) + 60.0 - float(b.y[u])) < 1e-6
    y0s = {int(u): float(b.y[int(u)]) for u in members}
    released = {}
    for t in range(3000):
        b.step(t)
        for u in y0s:
            if not b._wp_active[u] and u not in released:
                released[u] = float(b.y[u])
    assert not np.any(b._wp_active[members])
    for u, y0 in y0s.items():
        assert abs(released[u] - (-120.0 + (y0 - 0.0))) < 0.2


def test_beacon_partial_selection_splits_card():
    """A card with members spread wider than r40: only covered members
    follow; the rest keep normal behaviour (任务书 §7 T12)."""
    b = Battle(gd)
    b.add_card(0, 10, 1, 0.0, 0.0)          # 24-crawler card centred at A
    b.add_tower(1, 0.0, 290.0)
    b.add_skill_event(0, {"kind": "beacon", "x": 0.0, "y": 0.0,
                          "radius": 40.0,
                          "points": [[0.0, 0.0], [0.0, -60.0],
                                     [0.0, -120.0]]})
    b.finalize()
    crawlers = np.where((b.mech_id == 10) & (b.team == 0))[0]
    inside = sum(1 for u in crawlers
                 if abs(float(b.y[u])) <= 40.0)
    selected = int(np.count_nonzero(b._wp_active[crawlers]))
    assert 0 < selected < len(crawlers) or selected == len(crawlers)
    assert selected == inside


def test_beacon_statics_never_selected():
    b = Battle(gd)
    b.add_tower(0, 0.0, 0.0)                # own tower at A: unaffected
    b.add_card(0, 2, 1, 0.0, 10.0)
    b.add_tower(1, 0.0, 290.0)
    b.add_skill_event(0, {"kind": "beacon", "x": 0.0, "y": 0.0,
                          "radius": 40.0,
                          "points": [[0.0, 0.0], [0.0, -60.0],
                                     [0.0, -120.0]]})
    b.finalize()
    towers = np.where((b.mech_id != 2) & (b.team == 0))[0]
    assert not np.any(b._wp_active[towers])


def test_beacon_stop_to_attack_policy():
    """Frozen rule: a unit with an in-range target holds and fights instead
    of walking its waypoint."""
    b = Battle(gd)
    b.add_card(0, 2, 1, 0.0, 0.0)
    b.add_card(1, 10, 1, 0.0, 150.0)        # crawler will enter range
    b.add_skill_event(0, {"kind": "beacon", "x": 0.0, "y": 0.0,
                          "radius": 40.0,
                          "points": [[0.0, 0.0], [0.0, -60.0],
                                     [0.0, -120.0]]})
    b.finalize()
    u = int(np.where((b.mech_id == 2) & (b.team == 0))[0][0])
    held = False
    for t in range(3000):
        y_before = float(b.y[u])
        b.step(t)
        mt = int(b.mv_target[u])
        if mt >= 0 and not b.dead[mt]:
            d = float(np.hypot(b.x[mt] - b.x[u], b.y[mt] - b.y[u])
                      - b.radius[mt] - b.radius[u])
            if d <= float(b.range[u]) and abs(float(b.y[u]) - y_before) < 1e-9:
                held = True
                break
    assert held                              # stopped marching to fight


# ================================================================ geometry
def test_capsule_geometry_laws():
    from pysim.battlefield.effects.areas import (capsule_hit, circle_hit,
                                                 moving_circle_at,
                                                 capsule_spine)
    assert capsule_hit(0.0, 0.0, -30.0, 0.0, 30.0, 0.0, 30.0)
    assert capsule_hit(50.0, 0.0, -30.0, 0.0, 30.0, 0.0, 30.0)
    assert not capsule_hit(0.0, 61.0, -30.0, 0.0, 30.0, 0.0, 30.0)
    assert capsule_hit(0.0, 34.0, -30.0, 0.0, 30.0, 0.0, 30.0,
                       unit_radius=5.0)      # unit radius counts
    assert circle_hit(0.0, 0.0, 0.0, 0.0, 10.0)
    assert not circle_hit(10.1, 0.0, 0.0, 0.0, 10.0)
    cx, cy, arr = moving_circle_at(0.0, 0.0, 100.0, 0.0, 25.0, 2.0)
    assert (cx, cy, arr) == (50.0, 0.0, False)
    cx, cy, arr = moving_circle_at(0.0, 0.0, 100.0, 0.0, 25.0, 10.0)
    assert (cx, cy, arr) == (100.0, 0.0, True)
    # diagonal capsule: perpendicular distance law
    assert capsule_hit(50.0, 50.0, 0.0, 0.0, 100.0, 100.0, 10.0)
    assert not capsule_hit(60.0, 40.0, 0.0, 0.0, 100.0, 100.0, 10.0)
    spine = capsule_spine(0.0, 0.0, 30.0, 0.0, 30.0)
    assert spine[0] == (0.0, 0.0) and spine[-1] == (30.0, 0.0)
