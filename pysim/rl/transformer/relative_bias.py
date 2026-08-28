# 2D relative attention bias (任务书 §4.4).
#
# Pairwise additive bias components over entity tokens:
#   dx bucket + dy bucket + distance bucket + same/opposite side
#   + entity type pair (grouped) + inside/outside known area + air/ground.
# Each component has its own embedding table; the bias is the SUM, so the
# layout stays auditable and every bucket edge is pinned in config/contract.
#
# Geometry ALWAYS comes from ego coordinates through the SAME functions on
# both the original and the mirrored observation (mirror(mirror(s)) == s):
# mirroring negates dy only, so dy bucket edges are symmetric around 0 and
# dy_mirror_bucket(dy) == -dy_bucket(dy). Nothing here learns to mirror.
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn

# default bucket edges (frozen into configs + contract "bias_buckets");
# coordinates are ego board units (board is x∈[-350,350], y∈[-300,300])
DEFAULT_DX_EDGES = tuple(float(v) for v in
                         (-350, -240, -150, -80, -40, -16, -6, 0, 6, 16, 40,
                          80, 150, 240, 350))
DEFAULT_DY_EDGES = tuple(float(v) for v in
                         (-300, -200, -120, -60, -24, -8, 0, 8, 24, 60, 120,
                          200, 300))
DEFAULT_DIST_EDGES = tuple(float(v) for v in
                           (0, 12, 30, 60, 100, 160, 240, 340, 480, 700))

# entity type groups for the type-pair component (small closed set; the
# full token-type enum lives in tokenizer — grouped here to keep the
# type-pair table tiny and auditable)
TYPE_GROUPS = ("cls", "global", "tower", "unit", "tech", "structure",
               "skill", "area", "inventory", "action", "pad")
_N_GROUP = len(TYPE_GROUPS)


def bucket_idx(value: float, edges: tuple) -> int:
    """Right-open buckets; below the first edge -> 0, above the last -> len."""
    i = int(np.searchsorted(edges, value, side="right"))
    return i


def dx_bucket(dx: float, edges: tuple = DEFAULT_DX_EDGES) -> int:
    return bucket_idx(float(dx), edges)


def dy_bucket(dy: float, edges: tuple = DEFAULT_DY_EDGES) -> int:
    return bucket_idx(float(dy), edges)


def mirror_dy_bucket(b: int, edges: tuple = DEFAULT_DY_EDGES) -> int:
    """dy' = -dy reflects bucket b to (n-1-b) for symmetric edges — valid
    for dy values strictly inside a bucket; boundary values (dy on an
    edge, incl. 0) sit on shared edges, and the swap path RE-DERIVES
    components from the mirrored geometry instead of reflecting indices
    (exactness is checked by tests/rl_transformer)."""
    n = len(edges) + 1
    return (n - 1) - b


def dist_bucket(dx: float, dy: float, edges: tuple = DEFAULT_DIST_EDGES) -> int:
    return bucket_idx(float(np.hypot(dx, dy)), edges)


def side_rel_bucket(side_a: int, side_b: int) -> int:
    """0 same side, 1 opposite, 2 involving a neutral/non-spatial token."""
    if side_a < 0 or side_b < 0:
        return 2
    return 0 if side_a == side_b else 1


def air_rel_bucket(air_a: int, air_b: int) -> int:
    """0 air-air, 1 ground-ground, 2 mixed (neutral sentinels -1 also land
    here and are constant by construction)."""
    if air_a != air_b:
        return 2
    return 0 if air_a == 1 else 1


def area_rel_bucket(area_a: int, area_b: int) -> int:
    """0 same known area, 1 different areas, 2 at least one outside."""
    if area_a < 0 or area_b < 0:
        return 2
    return 0 if area_a == area_b else 1


def pair_components(dx: float, dy: float, side_a: int, side_b: int,
                    group_a: int, group_b: int, air_a: int, air_b: int,
                    area_a: int, area_b: int,
                    dx_edges=DEFAULT_DX_EDGES, dy_edges=DEFAULT_DY_EDGES,
                    dist_edges=DEFAULT_DIST_EDGES) -> tuple[int, ...]:
    """All component bucket indices for one ordered pair — the auditable
    numpy path; the torch module consumes the same mapping vectorized."""
    return (dx_bucket(dx, dx_edges), dy_bucket(dy, dy_edges),
            dist_bucket(dx, dy, dist_edges),
            side_rel_bucket(side_a, side_b),
            group_a * _N_GROUP + group_b,
            air_rel_bucket(air_a, air_b),
            area_rel_bucket(area_a, area_b))


COMPONENT_SIZES = (
    len(DEFAULT_DX_EDGES) + 1, len(DEFAULT_DY_EDGES) + 1,
    len(DEFAULT_DIST_EDGES) + 1, 3, _N_GROUP * _N_GROUP, 4, 3,
)
COMPONENT_NAMES = ("dx", "dy", "dist", "side", "type_pair", "air", "area")


class RelativeBiasTable(nn.Module):
    """Sum-of-embeddings pairwise additive attention bias.

    Input per batch: the vectorized component index tensor [B,7,T,T]
    (built by tokenizer.relative_bias_components). Output [B,H,T,T] added
    to attention logits. A per-head table is used so different heads can
    specialize; `zero` disables the bias entirely (ablation §6.4-2)."""

    def __init__(self, n_heads: int, dx_edges=DEFAULT_DX_EDGES,
                 dy_edges=DEFAULT_DY_EDGES, dist_edges=DEFAULT_DIST_EDGES,
                 zero: bool = False):
        super().__init__()
        self.n_heads = n_heads
        self.zero = bool(zero)
        self.dx_edges = tuple(dx_edges)
        self.dy_edges = tuple(dy_edges)
        self.dist_edges = tuple(dist_edges)
        sizes = (len(self.dx_edges) + 1, len(self.dy_edges) + 1,
                 len(self.dist_edges) + 1) + COMPONENT_SIZES[3:]
        self.tables = nn.ModuleList(
            nn.Embedding(s, n_heads) for s in sizes)
        for t in self.tables:
            nn.init.zeros_(t.weight)

    def forward(self, comp: torch.Tensor) -> torch.Tensor:
        # comp: int64 [B, 7, T, T] -> bias [B, H, T, T]
        if self.zero:
            b, _, t, tt = comp.shape
            return comp.new_zeros((b, self.n_heads, t, tt),
                                  dtype=torch.get_default_dtype())
        parts = [table(comp[:, i]) for i, table in enumerate(self.tables)]
        return torch.stack(parts, dim=0).sum(dim=0).permute(0, 3, 1, 2)


def relative_bias_components_numpy(x, y, side, group, air, area,
                                   pad_mask, dx_edges=DEFAULT_DX_EDGES,
                                   dy_edges=DEFAULT_DY_EDGES,
                                   dist_edges=DEFAULT_DIST_EDGES):
    """Numpy [T,7,T,T] component indices for one sample (tests + reference).

    pad rows/cols use the neutral sentinels (side=-1, air=-1, area=-1,
    group=pad) so padding contributes a CONSTANT bias — padding cannot
    change real-token attention (§4.5)."""
    t = len(x)
    dx = np.asarray(x)[:, None] - np.asarray(x)[None, :]
    dy = np.asarray(y)[:, None] - np.asarray(y)[None, :]
    out = np.zeros((7, t, t), dtype=np.int64)
    for i in range(t):
        for j in range(t):
            out[:, i, j] = pair_components(
                dx[i, j], dy[i, j], side[i], side[j], group[i], group[j],
                air[i], air[j], area[i], area[j], dx_edges, dy_edges,
                dist_edges)
    return out
