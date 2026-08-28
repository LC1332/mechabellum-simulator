# Observation -> direct pysim bridge (task §3.1).
#
# V_battle_sim(s_prebattle) is trained on pysim outcomes of the SAME
# pre-battle boards the model sees, so the sim labels are generated from the
# serialized BattleObservationV1 — guaranteeing the label is a function of
# the model input (no leakage through hidden state). This module is the only
# RL->engine path; seed is always explicit (task §2.2 seed hardening).
from __future__ import annotations

from .contracts import derive_seed


def _units(obs_side):
    out = []
    for u in obs_side["units"]:
        out.append({
            "id": int(u["mech"]),
            "level": int(u["level"]) - 1,     # obs 1-based -> engine 0-based
            "exp": int(u["exp"]),
            "x": float(u["x"]),
            "y": float(u["y"]),
            "isRotate": bool(u["rot"]),
            "equipmentId": int(u.get("equip", 0) or 0),
        })
    return out


def _techs(techs_dict):
    out = {}
    for m_str, ts in techs_dict.items():
        out[int(m_str)] = [int(t) for t in ts]
    return out


def _events(events):
    """skill events / devices -> pysim skills/buildings format."""
    out = []
    for e in events:
        out.append({"id": int(e["id"]), "x": float(e["x"]),
                    "y": float(e["y"])})
    return out


def _tower_mods(mods):
    m = {}
    if 5 in mods:
        m["range"] = 15
    if 6 in mods:
        m["speed"] = 3
    return m or None


def battle_input_from_observation(obs: dict, gd, seed: int, opts=None):
    """BattleObservationV1.to_dict() -> engine Battle (side 0 = ego).

    The ego frame maps straight back to engine side 0 (the observation was
    built ego-lower-half, which is exactly engine side 0's geometry)."""
    from ..engine import battle_from_units
    a, b = obs["self"], obs["opp"]
    return battle_from_units(
        gd,
        _units(a), _units(b),
        tech_map0=_techs(a["techs"]), tech_map1=_techs(b["techs"]),
        tower_mods0=_tower_mods(a["tower_mods"]),
        tower_mods1=_tower_mods(b["tower_mods"]),
        towers0=list(a["tower_strengthen"]), towers1=list(b["tower_strengthen"]),
        skills0=_events(a["skill_events"]), skills1=_events(b["skill_events"]),
        buildings0=[dict(cid=e["id"], x=e["x"], y=e["y"], index=i)
                    for i, e in enumerate(a["devices"])],
        buildings1=[dict(cid=e["id"], x=e["x"], y=e["y"], index=i)
                    for i, e in enumerate(b["devices"])],
        officers0=list(a["officers"]), officers1=list(b["officers"]),
        opts={"seed": int(seed), **(opts or {})})


def simulate_observation(obs: dict, gd, seed: int, opts=None) -> dict:
    """Run one seeded pysim battle; returns the WDL/damage label dict.

    Determinism contract: identical (observation, seed, opts) -> identical
    outcome digest."""
    b = battle_input_from_observation(obs, gd, seed, opts)
    winner = b.simulate()
    res = b.result(winner)
    # damage_to_player = opponent survivors' value (pysim_survivor_value_v1)
    score0 = b.team_score(0)
    score1 = b.team_score(1)
    outcome = {
        "seed": int(seed),
        "winner": int(winner),                    # 0 / 1 / -1 (ego frame)
        "damage_to_player": [int(score1), int(score0)],
        "end_time": round(float(res.get("end_time", 0.0)), 2),
    }
    return outcome


def state_seeds(sample_id: str, n: int, label_version: str) -> list:
    return [derive_seed(sample_id, k, label_version) for k in range(n)]
