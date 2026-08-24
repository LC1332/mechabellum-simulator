# step9 flank ("sneak") deploy detection and spawn-delay annotation.
# Mechanics (user-confirmed answers to the step9 plan questions):
#   - The sneak zone is the OPPONENT's side: judge by y (a card deployed
#     across the midline is a flank deploy; own-half corner placements never
#     delay, even at |x|>250).
#   - Round 1: zone locked, no delays ever.
#   - A NEW card finally standing in the enemy half teleports in over
#     FLANK_DELAY seconds (Quick Teleport officer 10009 halves it).
#   - Cards already on the board (snapshot indices) never delay - whether
#     they stand in the zone from last round or were moved in this round.
#   - Re-deploying after a flank death does NOT re-trigger the wait (Q6),
#     so beyond the per-card rule the unlock granularity is an experiment
#     switch: mode "card" delays every new zone card, "round" only the first
#     per player per round, "game" only the first per player per match.
from collections.abc import Mapping

FLANK_DELAY = 10.0       # wiki: first deploy into the flank zone
QT_DELAY = 5.0           # quick teleport halves it
QT_OFFICER = 10009       # OfficerData: Quick Teleport / 快速传送


def enemy_half(side, y):
    # engine convention: side 0 owns the y<0 half
    return y > 0.0 if side == 0 else y < 0.0


def is_new_card(unit, snapshot_indices):
    return unit.get("index", -1) < 0 or unit["index"] not in snapshot_indices


def pair_flank_delays(pair, mode="card", delay=None,
                      qt_delay=QT_DELAY, unlock_state=None):
    """Per-card teleport seconds for one round pair.

    Returns (delays0, delays1): lists aligned with pN['units_fight'] (or
    pN['units'] when units_fight is absent). unlock_state: dict with
    {0: bool, 1: bool} carried across the pairs of one replay for mode
    'game' (mutated in place)."""
    if delay is None:
        delay = FLANK_DELAY
    out = []
    if mode == "off" or pair["round"] < 2:
        for s in (0, 1):
            p = pair["p%d" % s]
            uf = p.get("units_fight") or p["units"]
            out.append([0.0] * len(uf))
        return out[0], out[1]
    if unlock_state is None:
        unlock_state = {0: False, 1: False}
    for s in (0, 1):
        p = pair["p%d" % s]
        snap = {u["index"] for u in p["units"]}
        uf = p.get("units_fight") or p["units"]
        qt = QT_OFFICER in (p.get("officers") or [])
        d = 0.0 if qt else float(delay)
        delays = []
        used_this_round = False
        for u in uf:
            cand = is_new_card(u, snap) and enemy_half(s, u["y"])
            if not cand:
                delays.append(0.0)
                continue
            if mode == "card" or (mode == "round" and not used_this_round) or \
               (mode == "game" and not unlock_state[s]):
                delays.append(qt_delay if qt else delay)
            else:
                delays.append(0.0)
            used_this_round = True
            unlock_state[s] = True
        out.append(delays)
    return out[0], out[1]


def annotate_units(units, delays):
    """Copy unit dicts with spawnAt set (engine battle_from_units reads it)."""
    out = []
    for u, d in zip(units, delays):
        if d:
            u = dict(u)
            u["spawnAt"] = float(d)
        out.append(u)
    return out


def count_delays(delays):
    return sum(1 for d in delays if d)
