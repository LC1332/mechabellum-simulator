# Battlefield timed-area/status contracts (step5 任务书 §3 T4/T5).
#
# Pure geometry + shape laws ONLY - no engine, no transition imports (effects/
# layer rule). The single source of the NUMBERS stays pysim/skills.py (the
# def table with per-id provenance); this module provides:
#   - the swept shapes' unified hit functions (circle / capsule / moving
#     circle) with the frozen boundary law (unit radius counts, inclusive
#     edge, 1e-9 float tolerance);
#   - shield-clip sampling: a capsule dropped under a live enemy barrier is
#     permanently clipped at release time (盾后消失不能补生成) - approximated
#     deterministically by sampling the capsule spine.
#
# legacy_engine.py is the ONLY module allowed to translate these contracts
# into the Battle's internal arrays (重构计划 boundary).
import math

# step5 任务书 §4/T4: edge comparison law - inclusive boundary, unit radius
# counts as inside, 1e-9 float tolerance. Frozen here so every hit test in
# the engine and the oracle differ shares one definition.
EPS = 1e-9


def circle_hit(px, py, cx, cy, r, unit_radius=0.0) -> bool:
    """Unit at (px, py) with radius unit_radius inside circle(cx, cy, r)."""
    return math.hypot(px - cx, py - cy) - unit_radius <= r + EPS


def _seg_dist_sq(px, py, ax, ay, bx, by) -> float:
    """Squared distance point -> segment AB."""
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    denom = abx * abx + aby * aby
    if denom <= EPS:
        return apx * apx + apy * apy
    t = (apx * abx + apy * aby) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    dx, dy = apx - t * abx, apy - t * aby
    return dx * dx + dy * dy


def capsule_hit(px, py, ax, ay, bx, by, r, unit_radius=0.0) -> bool:
    """Capsule(A, B, r) = the swept circle from A's r-circle to B's r-circle
    (黏油/烟雾/酸液 shape law, step5 任务书 §2.1.4)."""
    return _seg_dist_sq(px, py, ax, ay, bx, by) <= (r + unit_radius) ** 2 + EPS


def moving_circle_at(ax, ay, bx, by, speed, t) -> tuple:
    """离子轰炸 moving_circle(A->B, r, speed): the beam centre at time t.
    Returns (cx, cy, arrived: bool) - clamped at B after arrival."""
    dx, dy = bx - ax, by - ay
    ln = math.hypot(dx, dy)
    if ln <= EPS or speed <= 0.0:
        return bx, by, True
    travelled = speed * t
    if travelled >= ln:
        return bx, by, True
    return ax + dx / ln * travelled, ay + dy / ln * travelled, False


def capsule_spine(ax, ay, bx, by, r) -> list:
    """Deterministic sample points along the capsule spine used for shield
    clipping and oil->flame conversion: every r metres plus both endpoints.
    Pure function of the geometry so the BattleInput digest is stable."""
    ln = math.hypot(bx - ax, by - ay)
    n = max(1, int(math.ceil(ln / max(r, 1.0))))
    out = []
    for i in range(n + 1):
        t = i / n
        out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return out


# ---------------------------------------------------------------- contracts
# Shape grammar for TimedAreaEffect-style compiled events (kept as plain
# tuples on the frozen BattleInput TimedEvent: `points` + params). The
# names mirror the 任务书 §3 suggested contract; expressiveness must not
# shrink: shape / points / radius / affects / layers / shield_rule /
# tick_interval all ride the digest through TimedEvent.params.
AREA_SHAPES = {
    "oil": "capsule",
    "smoke": "capsule",
    "acid": "capsule",
    "photon": "capsule",      # cal: shape unconfirmed until oracle (T8)
    "emp": "circle",
    "storm": "circle",
    "ion": "moving_circle",
    "beacon": "circle",
}


def area_shape(kind: str) -> str:
    return AREA_SHAPES.get(str(kind), "circle")


def shape_points(kind: str, points) -> tuple:
    """Normalized (A, B) spine endpoints per shape: capsule/ion use the two
    ordered release points; circle kinds use the first point only."""
    pts = tuple((float(p[0]), float(p[1])) for p in (points or ()))
    if area_shape(kind) == "circle":
        return pts[:1]
    return pts[:2]
