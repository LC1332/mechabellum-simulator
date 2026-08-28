# Shared structured featurization (task §11 features.py): the single
# vocabulary + array encoding consumed by the non-neural baselines AND the
# DeepSets value model AND pi_BC. Operates on the serialized observation
# dicts stored in dataset rows — never on replay XML or live engine state.
from __future__ import annotations

import numpy as np

from .contracts import MAX_UNITS_PAD

# unit token layout (float part)
UNIT_FLOATS = ("level", "exp", "x", "y", "rot", "move_ok")
N_UNIT_FLOAT = len(UNIT_FLOATS)


class Vocab:
    """id spaces built from gamedata + observed registry extras."""

    def __init__(self, gd=None, extra_mechs=(), extra_equips=()):
        mechs = set(extra_mechs)
        equips = set(extra_equips) | {0}
        techs = set()
        if gd is not None:
            mechs |= set(int(m) for m in gd.mechs)
            techs |= set(int(t) for t in gd.techs)
        self.mech2id = {m: i + 1 for i, m in enumerate(sorted(mechs))}
        self.equip2id = {e: i + 1 for i, e in enumerate(sorted(equips))}
        self.tech2id = {t: i + 1 for i, t in enumerate(sorted(techs))}
        # officers / blueprints / skills observed ids get hashed ids
        self.officer2id = {}
        self.skill2id = {}

    def mech(self, m) -> int:
        return self.mech2id.get(int(m), 0)

    def equip(self, e) -> int:
        return self.equip2id.get(int(e), 0)

    def tech(self, t) -> int:
        return self.tech2id.get(int(t), 0)

    @property
    def n_mech(self) -> int:
        return len(self.mech2id) + 1

    @property
    def n_equip(self) -> int:
        return len(self.equip2id) + 1

    @property
    def n_tech(self) -> int:
        return len(self.tech2id) + 1

    def to_dict(self) -> dict:
        return {"mech2id": self.mech2id, "equip2id": self.equip2id,
                "tech2id": self.tech2id}

    @staticmethod
    def from_dict(d: dict) -> "Vocab":
        v = Vocab()
        v.mech2id = {int(k): int(val) for k, val in d["mech2id"].items()}
        v.equip2id = {int(k): int(val) for k, val in d["equip2id"].items()}
        v.tech2id = {int(k): int(val) for k, val in d["tech2id"].items()}
        return v


def _unit_rows(units: list, move_ok: list | None, vocab: Vocab,
               max_units: int = MAX_UNITS_PAD):
    """(float matrix [max_units, N_UNIT_FLOAT], mech ids, equip ids, mask)"""
    f = np.zeros((max_units, N_UNIT_FLOAT), dtype=np.float32)
    mech = np.zeros(max_units, dtype=np.int64)
    equip = np.zeros(max_units, dtype=np.int64)
    mask = np.zeros(max_units, dtype=np.float32)
    for i, u in enumerate(units[:max_units]):
        f[i] = (u["level"] / 9.0,
                min(u["exp"], 20000.0) / 20000.0,
                u["x"] / 350.0,
                u["y"] / 300.0,
                1.0 if u["rot"] else 0.0,
                (1.0 if move_ok[i] else 0.0) if move_ok is not None else 0.0)
        mech[i] = vocab.mech(u["mech"])
        equip[i] = vocab.equip(u.get("equip", 0))
        mask[i] = 1.0
    return f, mech, equip, mask


def _tech_vector(techs: dict, vocab: Vocab) -> dict:
    """mech -> owned tech id list (vocab ids), flattened with counts."""
    ids, owners = [], []
    for m_str, ts in techs.items():
        m = vocab.mech(int(m_str))
        for t in ts:
            ids.append(vocab.tech(int(t)))
            owners.append(m)
    return {"tech_ids": np.asarray(ids, dtype=np.int64),
            "tech_owners": np.asarray(owners, dtype=np.int64)}


def _officer_vector(ids: list) -> np.ndarray:
    """Small hashed officer/bp feature: count + max id (stable)."""
    if not ids:
        return np.zeros(2, dtype=np.float32)
    return np.asarray([min(len(ids), 8) / 8.0,
                       (sum(int(o) for o in ids) % 997) / 997.0],
                      dtype=np.float32)


# ------------------------------------------------------------- battle (V_sim)
def battle_features(obs: dict, vocab: Vocab,
                    max_units: int = MAX_UNITS_PAD) -> dict:
    """BattleObservationV1.to_dict() -> fixed-size arrays (ego frame)."""
    sf, sm, se, smask = _unit_rows(obs["self"]["units"], None, vocab, max_units)
    of, om, oe, omask = _unit_rows(obs["opp"]["units"], None, vocab, max_units)
    glob = np.asarray([
        obs["round"] / 40.0,
        obs["self"]["hp"] / max(1, obs["self"]["max_hp"]),
        obs["opp"]["hp"] / max(1, obs["opp"]["max_hp"]),
        obs["self"]["max_hp"] / 6000.0,
        obs["opp"]["max_hp"] / 6000.0,
        obs["self"]["tower_strengthen"][0] / 9.0,
        obs["self"]["tower_strengthen"][1] / 9.0,
        obs["opp"]["tower_strengthen"][0] / 9.0,
        obs["opp"]["tower_strengthen"][1] / 9.0,
        len(obs["self"]["tower_mods"]) / 2.0,
        len(obs["opp"]["tower_mods"]) / 2.0,
        len(obs["self"]["devices"]) / 4.0,
        len(obs["opp"]["devices"]) / 4.0,
        len(obs["self"]["skill_events"]) / 4.0,
        len(obs["opp"]["skill_events"]) / 4.0,
    ], dtype=np.float32)
    return {
        "self_f": sf, "self_mech": sm, "self_equip": se, "self_mask": smask,
        "opp_f": of, "opp_mech": om, "opp_equip": oe, "opp_mask": omask,
        "self_tech": _tech_vector(obs["self"]["techs"], vocab),
        "opp_tech": _tech_vector(obs["opp"]["techs"], vocab),
        "self_off": _officer_vector(obs["self"]["officers"]),
        "opp_off": _officer_vector(obs["opp"]["officers"]),
        "self_bp": _officer_vector(obs["self"]["blueprints"]),
        "opp_bp": _officer_vector(obs["opp"]["blueprints"]),
        "global": glob,
    }


# ------------------------------------------------------------- policy (BC)
N_POLICY_GLOBAL = 10


def policy_global_features(obs: dict, vocab: Vocab) -> np.ndarray:
    own = obs["self"] if "self" in obs else None
    opp = obs["opp"]
    return np.asarray([
        obs["round"] / 40.0,
        obs["hp"] / max(1, obs["max_hp"]),
        obs["max_hp"] / 6000.0,
        min(obs["supply"], 3000) / 3000.0,
        obs["buy_remaining"] / 4.0,
        obs["opp"]["hp"] / max(1, opp["max_hp"]),
        min(len(obs["skills"]), 6) / 6.0,
        min(len(obs["equipment_inventory"]), 8) / 8.0,
        obs["prefix_len"] / 64.0,
        obs["budget_left"] / 64.0,
    ], dtype=np.float32)


def policy_features(obs: dict, space: dict, vocab: Vocab,
                    max_units: int = MAX_UNITS_PAD) -> dict:
    """PolicyObservationV1.to_dict() + space_to_dict() -> arrays.

    Unit rows carry the observation's canonical order so pointer heads score
    unit encodings directly (handles = padded indices)."""
    f, mech, equip, mask = _unit_rows(obs["units"], obs["unit_move_ok"],
                                      vocab, max_units)
    of, om, oe, omask = _unit_rows(obs["opp"]["units"], None, vocab, max_units)
    n_units = int(mask.sum())
    return {
        "self_f": f, "self_mech": mech, "self_equip": equip,
        "self_mask": mask,
        "opp_f": of, "opp_mech": om, "opp_equip": oe, "opp_mask": omask,
        "self_tech": _tech_vector(obs["techs"], vocab),
        "opp_tech": _tech_vector(obs["opp"]["techs"], vocab),
        "self_off": _officer_vector(obs.get("officers") or []),
        "global": policy_global_features(obs, vocab),
        "n_units": n_units,
        "space": space,           # candidate pools + masks (variable length)
    }
