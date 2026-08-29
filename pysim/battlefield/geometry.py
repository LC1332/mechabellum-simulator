# Footprint geometry for ground units (爬虫动力学与伤害标定修正任务书
# 2026-08-29 T2). Versioned, evidence-carrying, OFF by default: the engine
# only consumes a spec when the `footprint_box` opt is set, and only for
# mechs with a registered spec — every other row keeps the legacy circle
# radius semantics bit-exact (任务书 §5/T2: 老单位 OFF 时逐 case 不变).
#
# Evidence rules (Q1 remains oracle-pending):
#   - specs come from game-table/collision reverse engineering or measured
#     oracle contact boundaries — NEVER from unit price or a guess;
#   - confidence "provisional" entries (e.g. the review-0829 sabertooth
#     square) must stay behind explicit opt-in in the runners until the
#     oracle freezes them.
import math
from dataclasses import dataclass

import numpy as np

FOOTPRINT_SPEC_VERSION = "footprint-spec-v1"

SHAPE_CIRCLE = "circle"
SHAPE_AABB = "axis_aligned_box"     # world-frame axis-aligned rectangle
SHAPE_OBOX = "oriented_box"         # rectangle aligned to the unit heading


@dataclass(frozen=True)
class FootprintSpec:
    mech_id: int
    shape: str                      # circle | axis_aligned_box | oriented_box
    half_width: float               # box half extents (world metres)
    half_length: float
    collision_layer: str = "ground"   # ground | air (air rows never use boxes)
    evidence: str = ""
    confidence: str = "provisional"   # provisional | measured | frozen

    @property
    def bounding_radius(self) -> float:
        return math.hypot(self.half_width, self.half_length)


class FootprintRegistry:
    """mech_id -> FootprintSpec. The engine snapshots this at finalize; the
    registry itself stays plain data (auditable, dumpable, digestable)."""

    def __init__(self):
        self._specs = {}

    def register(self, spec: FootprintSpec):
        self._specs[int(spec.mech_id)] = spec

    def get(self, mech_id):
        return self._specs.get(int(mech_id))

    def has_box(self, mech_id) -> bool:
        s = self._specs.get(int(mech_id))
        return s is not None and s.shape in (SHAPE_AABB, SHAPE_OBOX)

    def dump(self) -> dict:
        return {str(m): {"shape": s.shape, "half_width": s.half_width,
                         "half_length": s.half_length,
                         "collision_layer": s.collision_layer,
                         "evidence": s.evidence,
                         "confidence": s.confidence,
                         "spec_version": FOOTPRINT_SPEC_VERSION}
                for m, s in sorted(self._specs.items())}


# ---- provisional entries (review 0829 + engine self-consistency, Q1 pending)
# 剑齿虎 (21): review "3x3 单位可先近似为正方形" + inner ring ~=20 crawlers.
# Crawler diameter 4 (radius 2): a square with half-size 10 has perimeter 80
# ~= 20 contact-spaced crawlers, matching both the review count and the
# legacy circle radius 11 behaviour (2*pi*13/4 ~= 20). confidence stays
# provisional until the oracle freezes the contact boundary.
PROVISIONAL_SABERTOOTH = FootprintSpec(
    mech_id=21, shape=SHAPE_AABB, half_width=10.0, half_length=10.0,
    collision_layer="ground",
    evidence="review pysim_0829修正.md: 3x3 square obstacle, inner ring "
             "~=20 crawlers at contact spacing (crawler diameter 4)",
    confidence="provisional")


def register_provisional_defaults(registry: FootprintRegistry):
    """Opt-in helper for runners/tests: register the review-0829 provisional
    specs. NEVER called by the engine itself (nothing bakes without oracle)."""
    registry.register(PROVISIONAL_SABERTOOTH)


# ------------------------------------------------------------ distance math
def point_box_distance(px, py, bx, by, half_w, half_l, cos_h=None, sin_h=None):
    """Distance from point (px,py) to the box exterior centred at (bx,by).

    Axis-aligned when cos_h/sin_h are None; otherwise the box frame is the
    heading (cos_h, sin_h). Zero when the point is inside the box.
    Vectorized over all arguments."""
    dx = np.asarray(px, dtype=float) - bx
    dy = np.asarray(py, dtype=float) - by
    if cos_h is not None:
        # rotate into the box frame
        rx = dx * cos_h + dy * sin_h
        ry = -dx * sin_h + dy * cos_h
        dx, dy = rx, ry
    qx = np.maximum(np.abs(dx) - half_w, 0.0)
    qy = np.maximum(np.abs(dy) - half_l, 0.0)
    return np.hypot(qx, qy)


def surface_distance_circle_box(cx, cy, r, bx, by, half_w, half_l,
                                cos_h=None, sin_h=None):
    """Surface distance between a circle (centre cx,cy radius r) and a box:
    point_box_distance(center, box) - r. Negative values mean overlap, the
    same convention as the engine's circle-circle surface matrix."""
    return point_box_distance(cx, cy, bx, by, half_w, half_l,
                              cos_h, sin_h) - r


def circle_box_separation(px, py, bx, by, half_w, half_l,
                          cos_h=None, sin_h=None):
    """Unit push vector (nx, ny) that moves a circle centred at (px,py) OUT
    of the box along the minimum-penetration direction, plus the penetration
    depth. When the centre sits outside the box the direction is from the
    closest box-boundary point to the centre; when inside, along the axis
    with the smallest exit distance. Zero-vector when strictly outside
    without overlap (the caller adds the circle radius on top)."""
    dx = np.asarray(px, dtype=float) - bx
    dy = np.asarray(py, dtype=float) - by
    if cos_h is not None:
        rx = dx * cos_h + dy * sin_h
        ry = -dx * sin_h + dy * cos_h
        dx, dy = rx, ry
    ox = half_w - np.abs(dx)      # signed exit distance along local x
    oy = half_l - np.abs(dy)
    inside = (ox > 0) & (oy > 0)
    qx = np.maximum(np.abs(dx) - half_w, 0.0)
    qy = np.maximum(np.abs(dy) - half_l, 0.0)
    qn = np.hypot(qx, qy)
    # outside the box: push along the boundary normal (closest-point offset)
    out_ok = qn > 1e-9
    rx = np.where(qx > 0, dx - np.sign(dx) * half_w, 0.0)
    ry = np.where(qy > 0, dy - np.sign(dy) * half_l, 0.0)
    rn = np.hypot(rx, ry)
    nx_out = np.where(out_ok & (rn > 1e-9), rx / np.maximum(rn, 1e-9), 0.0)
    ny_out = np.where(out_ok & (rn > 1e-9), ry / np.maximum(rn, 1e-9), 0.0)
    # inside: exit along the axis of minimum penetration
    use_x = ox <= oy
    nx_in = np.where(use_x, np.sign(dx), 0.0)
    ny_in = np.where(~use_x, np.sign(dy), 0.0)
    depth_in = np.minimum(ox, oy) + np.hypot(qx, qy)
    nx = np.where(inside, nx_in, nx_out)
    ny = np.where(inside, ny_in, ny_out)
    depth = np.where(inside, depth_in, 0.0)
    if cos_h is not None:
        # rotate the push back to world frame
        wx = nx * cos_h - ny * sin_h
        wy = nx * sin_h + ny * cos_h
        nx, ny = wx, wy
    return nx, ny, depth
