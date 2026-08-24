# Multi-module card deployment: expand one card into mechCount unit positions.
# Formation: grid filling the card's base rectangle (cardBaseSize [w, h]),
# centered on the card position. isRotate swaps the rectangle orientation.
import math

MAP_X, MAP_Y = 350.0, 300.0

# step7 tower constants. Position measured from the deployment heatmap of
# rounds_new11 (30985 card footprints, tools/step7_pos3/4.py): the two
# never-covered 20x20m = 2x2-cell blocks (cell = 10m) sit at x[+-130..+-150],
# y[-180..-160] for team0 - cards park directly against them on all sides
# (user: towers are 2x2 unbuildable cells with units placed around them).
# The old guess (+-140, -55) had the right x but was 115m too far forward.
TOWER_POS = {0: [(-140.0, -170.0), (140.0, -170.0)],
             1: [(-140.0, 170.0), (140.0, 170.0)]}
TOWER_MECH = -1
# Base HP 3400 (user-confirmed level-1 value; wiki was right after all).
# The step6b-P4 "50000 peak" was an artifact of the wrong y=-55 position
# (towers massively over-exposed there, so calibration inflated HP); at the
# measured position the accuracy is monotone in HP and flat below 8k:
# 50000:56.0 / 20000:58.4 / 8000:59.6 / 3400:59.6 (vs no-towers 56.4,
# r3+ 250 pairs).
TOWER_HP_BASE = 3400.0
TOWER_RADIUS = 10.0     # half of the measured 20x20m footprint
TOWER_STRENGTH_LIFE = [0, 20000, 36000, 72000, 104000]   # by strengthen level 0-4
PARALYSE_DURATION = [9.0, 7.0, 5.0, 3.0, 1.0]            # by strengthen level 0-4
PARALYSE_DMG = 0.1        # damage dealt x0.1  (buffDatas damageChangeRate -0.9)
PARALYSE_SPEED = 0.2      # move speed x0.2   (speedChangeRate -0.8)
PARALYSE_AMPLIFY = 1.5    # damage taken x1.5  (amplifyDamageRate +0.5)

# step8-B battlefield-skill device entities (released pre-fight via
# ReleaseContraption / ReleaseCommanderSkill; see pysim/skills.py). They join
# the SoA arrays as pseudo-mechs like towers, are excluded from alive_count
# (reports count mechs only) and never feed killer exp.
DEVICE_BARRIER = -2       # 护盾装置/空投护盾: absorbs damage to covered allies
DEVICE_MISSILE = -3       # 飞弹: sentry-missile auto-turret
BARRIER_RADIUS = 30.0     # covers a squad; cal value
TURRET_RADIUS = 5.0
TURRET_HP = 3000.0        # cal: targetable but frail, tower-scale-ish

# step12 battlefield constructions (player-deployed defenses, replay snapshot
# constructionSnapshotDatas). They join the SoA arrays as pseudo-mechs like
# towers; mech_id = -10 - cid. Stats come from gamedata buildings layer
# (Construction table decode); only the per-cid mech ids and module geometry
# live here.
BLD_WALL, BLD_AA, BLD_RF, BLD_MAGNET = -11, -12, -13, -14
BLD_MECH_OF_CID = {1: BLD_WALL, 2: BLD_AA, 3: BLD_RF, 4: BLD_MAGNET}
MAGNET_TRIGGER = 10.0     # skill 3004001 range: enemy pops it within 10m
MAGNET_SLOW_R = 15.0      # skill 3004001 splash: slow-field radius
MAGNET_SELF_T = 5.0       # popped -> self-destruct (descParams '5')


def building_module_offsets(bdef):
    """Local module offsets of one construction placement around its snapshot
    anchor (x, y). Walls span blockWidth along X (the defense line at y=+-55
    runs along X); magnets form 2 rows of 5 on the 5m grid; cannons are 1."""
    n = bdef.count
    if n <= 1:
        return [(0.0, 0.0)]
    if bdef.cid == 1:
        span = bdef.block_width if bdef.block_width > 0 else (n - 1) * 5.0
        step = span / (n - 1)
        return [(-span / 2.0 + k * step, 0.0) for k in range(n)]
    if bdef.cid == 4:
        cols = 5
        return [((k % cols - (cols - 1) / 2.0) * 5.0,
                 (-2.5 if k < cols else 2.5)) for k in range(n)]
    step = 5.0
    return [(-step * (n - 1) / 2.0 + k * step, 0.0) for k in range(n)]


def formation_positions(gd, mech_id, x, y, is_rotate=False, scale=1.0):
    """Return list of (px, py) for all modules of one card.
    scale multiplies the card rectangle (step11 formation calibration)."""
    card = gd.cards.get(mech_id)
    m = gd.mechs.get(mech_id)
    count = card.mech_count if card else 1
    if count <= 1:
        return [(x, y)]
    w, h = (card.card_base_size if card and card.card_base_size and card.card_base_size != [0, 0]
            else (m.radius * 2.2 * count if m else 20.0, m.radius * 2.2 if m else 6.0))
    w, h = float(w) * scale, float(h) * scale
    if is_rotate:
        w, h = h, w
    if w <= 0 or h <= 0:
        w = max(w, m.radius * 2.2 if m else 6)
        h = max(h, m.radius * 2.2 if m else 6)
    # grid dimensions fitting `count` cells into a w x h rectangle
    cols = max(1, round(math.sqrt(count * w / h)))
    rows = math.ceil(count / cols)
    sx = w / cols
    sy = h / rows
    out = []
    for i in range(count):
        row, col = divmod(i, cols)
        in_row = cols if row < rows - 1 else count - cols * (rows - 1)
        px = x + (col - (in_row - 1) / 2.0) * sx
        py = y + (row - (rows - 1) / 2.0) * sy
        px = max(-MAP_X, min(MAP_X, px))
        py = max(-MAP_Y, min(MAP_Y, py))
        out.append((px, py))
    return out
