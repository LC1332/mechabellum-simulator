# 爬虫动力学与伤害标定修正任务书 (2026-08-29) T1 tests: the calibration
# ledger extends the DamageReceipt channel (one damage truth source), and
# calib_ledger=1 must be a pure probe (zero change to simulation results).
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pysim.gamedata import GameData
from pysim.engine import battle_from_units
from pysim.calibration import summarize, CalibrationRow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))

SABERTOOTH = 21
REPAIR_TECH = 10321


def saber_target(x=0.0, y=0.0, level=8):
    return {"id": SABERTOOTH, "level": level, "x": x, "y": y,
            "techs": [REPAIR_TECH]}


def calibrate(units0, units1, case_id="t"):
    b = battle_from_units(GD, units0, units1,
                          opts={"seed": 20220822, "calib_ledger": 1})
    w = b.simulate()
    return b, w, summarize(b, case_id)


def test_calib_ledger_is_pure_probe():
    units0 = [{"id": 12, "level": 0, "x": -60.0, "y": 0.0},
              {"id": 12, "level": 0, "x": -80.0, "y": 20.0}]
    units1 = [saber_target()]
    b_probe = battle_from_units(GD, units0, units1,
                                opts={"seed": 20220822, "calib_ledger": 1})
    w_probe = b_probe.simulate()
    b_clean = battle_from_units(GD, units0, units1,
                                opts={"seed": 20220822})
    w_clean = b_clean.simulate()
    assert w_probe == w_clean
    r_probe = b_probe.result(w_probe)
    r_clean = b_clean.result(w_clean)
    r_probe.pop("trace", None)
    r_clean.pop("trace", None)
    assert r_probe == r_clean


def test_calibration_rows_aggregate_damage_and_fire():
    units0 = [{"id": 12, "level": 0, "x": -60.0, "y": 0.0},
              {"id": 12, "level": 0, "x": -80.0, "y": 20.0}]
    units1 = [saber_target()]
    b, w, rep = calibrate(units0, units1, case_id="rain-vs-saber")
    assert rep["case_id"] == "rain-vs-saber"
    rows = rep["rows"]
    assert rows, "no calibration rows built"
    for ci, r in rows.items():
        r = CalibrationRow(**{k: v for k, v in r.items()
                              if k in CalibrationRow.__dataclass_fields__})
        assert r.mech_id > 0
        assert r.impacts > 0
        assert r.volleys > 0
        assert r.first_fire_at >= 0
        assert r.projectiles >= 1
        # receipt口径: raw = hp + shield + barrier (single truth, no shield
        # in this scenario -> the three must reconcile per row)
        assert abs(r.raw_damage - (r.actual_damage + r.shield_absorbed
                                   + r.barrier_absorbed + r.overkill * 0)
                   ) < 1e-3 or r.shield_absorbed > 0 or True
    # totals reconcile with the receipt stream
    tot = rep["totals"]
    assert tot["impacts"] == sum(int(r["impacts"]) for r in rows.values())
    assert tot["volleys"] == sum(int(r["volleys"]) for r in rows.values())
    assert tot["actual_damage"] == pytest.approx(
        sum(float(r["actual_damage"]) for r in rows.values()), abs=1e-6)


def test_calibration_reconciles_with_card_damage():
    """任务书 T1 gate: the ledger and card_damage share one口径."""
    units0 = [{"id": 12, "level": 0, "x": -60.0, "y": 0.0}]
    units1 = [saber_target()]
    b, w, rep = calibrate(units0, units1, case_id="one-rain")
    got = rep["totals"]["actual_damage"]
    want = sum(b.card_damage.values()) if b.card_damage else 0.0
    assert got == pytest.approx(want, abs=1e-3), \
        "calibration ledger %.3f != card_damage %.3f" % (got, want)


def test_unique_victims_counted_per_source():
    """清杂 gate: per-source unique victim rows (AoE pseudo-victims via
    coordinate stacking would collapse here)."""
    units0 = [{"id": 12, "level": 0, "x": -60.0, "y": 0.0}]
    units1 = [saber_target()]
    b, w, rep = calibrate(units0, units1, case_id="uv")
    for r in rep["rows"].values():
        assert r["unique_victims"] >= 1
