# -*- coding: utf-8 -*-
"""Shared helpers for the 0829 crawler-damage benchmark runners.

Scenario JSON schema (data/crawler_damage_scenarios/): versioned, hashable,
each case a standalone battle spec {name, group, p0:{units,techs}, p1, opts}
with the engine feature flags of the arm recorded per run (not baked).
"""
import hashlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") \
            != "utf8" and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                      errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SCEN_DIR = os.path.join(DATA, "crawler_damage_scenarios")

SEED = 20220822
CRAWLER = 10
SABERTOOTH = 21
REPAIR_TECH = 10321

# crawler card = 24 modules (mech_count); densities of the 任务书 §5/T8
CRAWLER_DENSITY_CARDS = {"24": 1, "96": 4, "384": 16, "768": 32}


def unit(mech, x, y, level=0, techs=None, equip=0):
    u = {"id": mech, "level": level, "x": x, "y": y}
    if techs:
        u["techs"] = list(techs)
    if equip:
        u["equipmentId"] = equip
    return u


def crawler_cards(n_cards, x0=-30.0, y0=-60.0, cols=4, spacing=50.0):
    """Crawler cards in a marching block (24 modules each)."""
    return [unit(CRAWLER, x0 + (k % cols) * spacing,
                 y0 - (k // cols) * spacing) for k in range(n_cards)]


def repair_saber(x=0.0, y=0.0, level=8):
    """9 级维修剑齿虎 calibration target (超重装甲 equipment + 战地维修,
    review-0829 pattern; 先进防御 buffs arrive with the oracle freeze)."""
    return unit(SABERTOOTH, x, y, level=level, techs=[REPAIR_TECH],
                equip=13030006)


def fp_registry():
    from pysim.battlefield.geometry import (FootprintRegistry,
                                           register_provisional_defaults)
    reg = FootprintRegistry()
    register_provisional_defaults(reg)
    return reg


def arm_opts(arm, extra=None):
    """control = step32 legacy behaviour; treatment = 0829 mechanics on."""
    if arm == "control":
        o = {"seed": SEED}
    elif arm == "treatment":
        o = {"seed": SEED, "footprint_box": 1, "footprint_reg": fp_registry(),
             "crawler_flow": 1, "crawler_retarget": 1}
    else:
        raise SystemExit("unknown arm %r (control|treatment)" % arm)
    if extra:
        o.update(extra)
    return o


def drive(b, stop_pred=None, sample_every=100, max_ticks=12000):
    """Manual tick driver mirroring simulate(); samples mid-battle state."""
    for tick in range(max_ticks):
        b.step(tick)
        b.end_tick = tick
        if stop_pred is not None and tick % sample_every == 0 \
                and stop_pred(b):
            return tick
    return max_ticks - 1


def crawler_metrics(b, sample_ticks=()):
    """(alive crawlers, contact ring, in-range count, rear count, min gap,
    box surface distances) sampled from a battle."""
    import numpy as np
    craw = np.where((b.mech_id == CRAWLER) & (~b.dead))[0]
    box_rows = [int(g) for g in np.where(getattr(b, "fp_box", np.zeros(0, bool))
                                         & ~b.dead)[0]]
    if not craw.size:
        return {"alive": 0}
    out = {"alive": int(craw.size)}
    pts = np.stack([b.x[craw], b.y[craw]], axis=1)
    if craw.size > 1:
        d2 = np.sum((pts[:, None, :] - pts[None, :, :]) ** 2, axis=2)
        np.fill_diagonal(d2, np.inf)
        out["min_gap"] = round(float(np.sqrt(d2.min())), 3)
    if box_rows:
        from pysim.battlefield import geometry as fpgeo
        g = box_rows[0]
        sd = fpgeo.surface_distance_circle_box(
            b.x[craw], b.y[craw], b.radius[craw], float(b.x[g]),
            float(b.y[g]), float(b.fp_hw[g]), float(b.fp_hl[g]))
        out["contact_ring"] = int(np.count_nonzero(sd <= 4.0))
        out["min_box_surface"] = round(float(sd.min()), 3)
    return out


def case_digest(obj):
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def write_lib(name, scenarios):
    os.makedirs(SCEN_DIR, exist_ok=True)
    path = os.path.join(SCEN_DIR, name)
    lib = {"meta": {"seed": SEED, "n": len(scenarios),
                    "schema": "crawler_damage_scenarios_v1"},
           "scenarios": scenarios}
    with open(path, "w", encoding="utf8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=1)
    return path
