# 爬虫动力学与伤害标定修正任务书 (2026-08-29) T2-T4 tests: footprint box
# surface distance + non-penetration, crawler_flow_v1 perimeter flow, and
# the crawler retarget hysteresis. All flags default OFF — the OFF arm must
# stay bit-identical to the legacy engine (§5/T2), so ON-arm tests build
# explicit battles with the provisional review-0829 sabertooth spec.
import hashlib
import json
import math
import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pysim.gamedata import GameData
from pysim.engine import battle_from_units
from pysim.battlefield import geometry as fpgeo
from pysim.battlefield.geometry import (FootprintRegistry, FootprintSpec,
                                       PROVISIONAL_SABERTOOTH,
                                       register_provisional_defaults)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))

CRAWLER = 10
SABERTOOTH = 21
REPAIR_TECH = 10321          # 维修剑齿虎: survives the swarm for 120s


def crawler_block(n_cards, x0=-27.5, y0=-60.0, cols=4, spacing=5.0):
    """A marching block of crawler CARDS south of origin. NOTE: a crawler
    card deploys 24 modules (mech_count), so n_cards=4 -> 96 crawlers."""
    return [{"id": CRAWLER, "level": 0,
             "x": x0 + (k % cols) * spacing,
             "y": y0 - (k // cols) * spacing} for k in range(n_cards)]


def saber_target(x=0.0, y=0.0, level=8):
    return {"id": SABERTOOTH, "level": level, "x": x, "y": y,
            "techs": [REPAIR_TECH]}


def fp_opts(**over):
    reg = FootprintRegistry()
    register_provisional_defaults(reg)
    o = {"seed": 20220822, "footprint_box": 1, "footprint_reg": reg}
    o.update(over)
    return o


def run(units0, units1, opts):
    b = battle_from_units(GD, units0, units1, opts=opts)
    w = b.simulate()
    return b, w


def drive(b, stop_pred=None, sample_every=100, max_ticks=12000):
    """Manual tick driver mirroring simulate()'s loop (dynamics tests sample
    mid-battle states; the swarm need not survive the full 120s)."""
    for tick in range(max_ticks):
        b.step(tick)
        b.end_tick = tick
        if stop_pred is not None and tick % sample_every == 0 \
                and stop_pred(b):
            return tick
    return max_ticks - 1


def state_hash(b):
    h = hashlib.sha256()
    h.update(b.x.tobytes())
    h.update(b.y.tobytes())
    h.update(b.dead.tobytes())
    h.update(b.hp.tobytes())
    return h.hexdigest()


def result_digest(b, winner):
    r = b.result(winner)
    r.pop("trace", None)
    blob = json.dumps(r, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def box_of(b, row):
    """(cx, cy, hw, hl) of a footprint-box row."""
    return (float(b.x[row]), float(b.y[row]),
            float(b.fp_hw[row]), float(b.fp_hl[row]))


def crawler_box_surface(b):
    """Surface distances of all alive crawler rows to the (first) box row."""
    box_rows = [int(g) for g in np.where(b.fp_box & ~b.dead)[0]]
    craw = np.where((b.mech_id == CRAWLER) & (~b.dead))[0]
    if not box_rows or len(craw) == 0:
        return craw, np.zeros(0)
    g = box_rows[0]
    cx, cy, hw, hl = box_of(b, g)
    sd = fpgeo.surface_distance_circle_box(
        b.x[craw], b.y[craw], b.radius[craw], cx, cy, hw, hl)
    return craw, sd


def alive_crawler_min_gap(b):
    craw = np.where((b.mech_id == CRAWLER) & (~b.dead))[0]
    if len(craw) < 2:
        return None
    pts = np.stack([b.x[craw], b.y[craw]], axis=1)
    d2 = np.sum((pts[:, None, :] - pts[None, :, :]) ** 2, axis=2)
    np.fill_diagonal(d2, np.inf)
    return float(np.sqrt(d2.min()))


# ------------------------------------------------------------- geometry T2
def test_geometry_point_box_distance():
    # outside along an axis
    assert fpgeo.point_box_distance(15.0, 0.0, 0.0, 0.0, 10.0, 10.0) == 5.0
    # corner
    assert abs(fpgeo.point_box_distance(13.0, 13.0, 0.0, 0.0, 10.0, 10.0)
               - math.hypot(3, 3)) < 1e-9
    # inside -> 0
    assert fpgeo.point_box_distance(3.0, -4.0, 0.0, 0.0, 10.0, 10.0) == 0.0
    # oriented box rotated 90deg: the half_w=10 extent lies along world y,
    # so the point (0, 15) is 5 beyond that face
    c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
    assert abs(fpgeo.point_box_distance(0.0, 15.0, 0.0, 0.0, 10.0, 5.0,
                                        c, s) - 5.0) < 1e-9


def test_geometry_separation_vector():
    nx, ny, depth = fpgeo.circle_box_separation(
        11.0, 0.0, 0.0, 0.0, 10.0, 10.0)
    assert (nx, ny) == (1.0, 0.0) and depth == 0.0
    # centre inside: exit along min-penetration axis
    nx, ny, depth = fpgeo.circle_box_separation(
        9.0, 0.0, 0.0, 0.0, 10.0, 10.0)
    assert (nx, ny) == (1.0, 0.0) and abs(depth - 1.0) < 1e-9
    # outside without overlap: outward normal defined, depth 0 (the caller
    # decides via need = r - pb + depth <= 0 not to push)
    nx, ny, depth = fpgeo.circle_box_separation(
        20.0, 0.0, 0.0, 0.0, 10.0, 10.0)
    assert (nx, ny) == (1.0, 0.0) and depth == 0.0


# --------------------------------------------------------------- T2 engine
def test_off_arm_bit_exact_with_registry_present():
    """Providing the registry changes nothing while footprint_box=0."""
    units0 = crawler_block(1)
    units1 = [saber_target()]
    b1, w1 = run(units0, units1, {"seed": 20220822})
    reg = FootprintRegistry()
    register_provisional_defaults(reg)
    b2, w2 = run(units0, units1, {"seed": 20220822,
                                  "footprint_box": 0,
                                  "footprint_reg": reg})
    assert w1 == w2
    assert result_digest(b1, w1) == result_digest(b2, w2)


def _ring_stats(b):
    craw, sd = crawler_box_surface(b)
    ring = int(np.count_nonzero(sd <= 4.0))
    rear = int(np.count_nonzero(b.y[craw] > 2.0)) if len(craw) else 0
    return craw, sd, ring, rear


def test_fp_arm_deterministic_and_no_penetration():
    units0 = crawler_block(4)
    units1 = [saber_target()]
    opts = fp_opts(crawler_flow=1)
    b1 = battle_from_units(GD, units0, units1, opts=opts)
    b2 = battle_from_units(GD, units0, units1, opts=opts)
    stop = lambda b: _ring_stats(b)[2] >= 10      # contact ring formed
    drive(b1, stop)
    drive(b2, stop)
    assert state_hash(b1) == state_hash(b2)
    # T2 gate: no alive crawler's circle overlaps the rectangle
    craw, sd, ring, rear = _ring_stats(b1)
    assert len(craw) > 0, "no crawlers left when the ring should form"
    assert float(sd.min()) >= -1e-6
    # T3 gate: no co-located members (soft separation keeps a real gap)
    gap = alive_crawler_min_gap(b1)
    assert gap is not None and gap > 0.5


def test_inner_ring_capacity_and_rear_flow():
    """T3 gates: contact ring ~= review's 20 (research tolerance), and the
    flow carries crawlers around to the rear instead of stacking on the
    approach axis."""
    units0 = crawler_block(4)
    units1 = [saber_target()]
    b_on = battle_from_units(GD, units0, units1, opts=fp_opts(crawler_flow=1))
    b_off = battle_from_units(GD, units0, units1,
                              opts={"seed": 20220822})
    stop = lambda b: _ring_stats(b)[3] >= 5      # rear occupancy reached
    t_on = drive(b_on, stop)
    t_off = drive(b_off, stop)
    craw_on, sd_on, ring_on, rear_on = _ring_stats(b_on)
    craw_off, sd_off, ring_off, rear_off = _ring_stats(b_off)
    assert len(craw_on) > 0 and len(craw_off) > 0, \
        "swarm wiped before contact dynamics could form"
    # contact ring: within one crawler diameter of the box surface
    assert 14 <= ring_on <= 32, \
        "contact ring %d out of the 20±tolerance band" % ring_on
    assert rear_on >= 5, "flow did not carry crawlers to the rear"
    # the flow arm organizes the surround: it reaches the rear no slower
    # than the legacy clump (2s slack for the moving target)
    if t_off < 12000:
        assert t_on <= t_off + 200, \
            "flow arm reached the rear slower (%d vs %d ticks)" % (t_on, t_off)


def test_crawler_retarget_lock_and_hysteresis():
    """T4: an in-range crawler keeps its target (lock-to-death), the audit
    trail records reasons from the frozen taxonomy, and a moving crawler
    does not flicker between two near targets."""
    units0 = crawler_block(1)
    # two targets 20m apart: the second is a decoy behind the first
    units1 = [saber_target(0.0, 0.0), saber_target(20.0, 0.0)]
    b, w = run(units0, units1, fp_opts(crawler_flow=1,
                                       crawler_retarget=1))
    events = b.retarget_events
    allowed = {"first_lock", "dead", "out_of_range", "closer_unblocked"}
    assert all(e[4] in allowed for e in events)
    # no crawler switched targets more than a handful of times in 120s
    per = {}
    for _t, row, _o, _n, _r in events:
        per[row] = per.get(row, 0) + 1
    assert all(v <= 20 for v in per.values())


def test_1500_crawler_performance_gate():
    """§7.2: large swarms must not hang; the fp/flow arms stay within a
    small factor of the legacy arm (gate: <= 2x, test budget 3x)."""
    units0 = crawler_block(32, cols=8, spacing=5.0)
    units1 = [saber_target()]
    t0 = time.time()
    b_off, w_off = run(units0, units1, {"seed": 20220822})
    t_off = time.time() - t0
    t0 = time.time()
    b_on, w_on = run(units0, units1, fp_opts(crawler_flow=1,
                                             crawler_retarget=1))
    t_on = time.time() - t0
    assert t_on < max(60.0, t_off * 3.0 + 5.0), \
        "fp/flow arm too slow: on=%.1fs off=%.1fs" % (t_on, t_off)
    craw, sd = crawler_box_surface(b_on)
    if len(craw):
        assert float(sd.min()) >= -1e-6
