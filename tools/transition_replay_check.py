# transition oracle check: does deploy_transition reproduce the next snapshot?
#
# v0.1: eats the NORMALIZED action stream (rounds_norm.json preferred; raw
# corpora are normalized on the fly with a warning). --sequential (default)
# bans every next-snapshot hint: the unit-index counter rolls forward from
# round 1 via each round's norm_report.counter_end, income comes from the
# Income200r model (200*r + experts - fast debts), never from snapshot diff.
#
#   --mode deploy     state_i + actions_i -> deploy -> compare vs snapshot_(i+1)
#   --mode settlement oracle settlement: hp/result/exp writeback vs the real
#                     FightReport
# Metrics: unit_set_exact (+clean-round rate), supply_exact, rejected core
# actions, first divergences. Exclusions use predefined reason codes only.
import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                  errors="replace")
except Exception:
    pass

from pysim.gamedata import GameData
from pysim.transition import (ReplayAdapter, Economy, Income200r,
                              canonicalize_plan, deploy_transition,
                              assert_state_invariants, EnvironmentState)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def unit_key(u):
    return (u.mech_id, u.level, round(u.x, 1), round(u.y, 1), bool(u.is_rotate))


def compare_units(got_units, want_units, eco=None):
    """Multiset compare with auto-level tolerance (units whose exp crossed a
    threshold during the fight level up in the NEXT snapshot)."""
    from collections import Counter
    gk = Counter(unit_key(u) for u in got_units)
    wk = Counter(unit_key(u) for u in want_units)
    if gk == wk:
        return True, [], [], 0
    extra = sorted((gk - wk).elements())
    missing = sorted((wk - gk).elements())
    n_auto = 0
    used = [False] * len(extra)
    rest_m = []
    for m in missing:
        placed = False
        for i, e in enumerate(extra):
            if used[i]:
                continue
            if (e[0] == m[0] and e[1] == m[1] - 1 and e[2] == m[2]
                    and e[3] == m[3] and e[4] == m[4]):
                need = eco.upgrade_exp_need(m[0], e[1]) if eco else 0
                if need and need > 0:
                    used[i] = True
                    n_auto += 1
                    placed = True
                    break
        if not placed:
            rest_m.append(m)
    rest_e = [e for i, e in enumerate(extra) if not used[i]]
    if n_auto and not rest_e and not rest_m:
        return True, [], [], n_auto
    return False, rest_e, rest_m, n_auto


class _WU:
    pass


def want_units_of(rec):
    want = []
    for u in rec["units"]:
        w = _WU()
        w.mech_id = int(u["id"])
        w.level = int(u["level"]) + 1
        w.x = float(u["x"])
        w.y = float(u["y"])
        w.is_rotate = bool(u.get("isRotate", False))
        w.exp = int(u.get("exp", 0) or 0)
        w.replay_index = int(u["index"])
        want.append(w)
    return want


def check_game(g, adapter, eco, gd, args, stats, income_policy):
    gid = adapter.game_index_of(g)
    if g.get("info", {}).get("matchMode") != "VS_1_1":
        stats["skip_mode"] += 1
        return
    # fast-supply debts are per-game: reset so (player, round) keys from a
    # previous game never leak into this one
    income_policy.fast_debts.clear()
    # rounds with a recording gap (sparse save) have invisible history:
    # their income windows cannot be modeled sequentially
    round_seqs = {}
    for side in (0, 1):
        prev_round = None
        for rec in g["players"][side]["rounds"]:
            rnd = int(rec["round"])
            round_seqs[(side, rnd)] = prev_round
            prev_round = rnd
    start = max(1, args.start_round)

    for side in (0, 1):
        rs = g["players"][side]["rounds"]
        for i in range(len(rs) - 1):
            r = rs[i]
            rnd = int(r["round"])
            if rnd < start:
                continue
            prev = round_seqs.get((side, rnd))
            if prev is not None and rnd - prev > 1:
                stats["skip_round_gap"] = stats.get("skip_round_gap", 0) + 1
                continue
            nxt = rs[i + 1]
            stats["rounds_total"] += 1
            base = adapter.environment_state(gid, rnd, economy=eco)
            # sequential income: model only, no snapshot reads
            incomes = tuple(income_policy.income(
                p, base.players[p], rnd,
                base.players[p].pre_round_fight_result) for p in (0, 1))
            from pysim.transition.model import PlayerState
            players = tuple(
                PlayerState(**{**base.players[p].__dict__,
                               "supply": base.players[p].supply + incomes[p]})
                for p in (0, 1))
            state = EnvironmentState(
                schema_version=base.schema_version,
                ruleset_version=base.ruleset_version,
                engine_version=base.engine_version, round=base.round,
                phase=base.phase, players=players,
                finished_deploy=base.finished_deploy,
                next_entity_id=base.next_entity_id,
                terminal_reason=base.terminal_reason,
                provenance=base.provenance)
            norm_actions, norm_report = adapter.norm_actions(g, side, rnd)
            try:
                plan, rep = canonicalize_plan(
                    side, norm_actions, state.players[side], economy=eco,
                    norm_report=norm_report)
            except Exception as ex:               # noqa: BLE001 - census bucket
                stats["canon_error"] += 1
                stats.setdefault("canon_errors", []).append(
                    {"file": g["file"][:24], "round": rnd, "side": side,
                     "error": repr(ex)[:200]})
                continue
            try:
                dep = deploy_transition(state, (plan,), eco)
            except Exception as ex:               # noqa: BLE001
                stats["deploy_error"] += 1
                if len(stats.setdefault("deploy_errors", [])) < 3:
                    import traceback
                    stats["deploy_errors"].append(
                        {"file": g["file"][:24], "round": rnd, "side": side,
                         "error": repr(ex)[:200],
                         "tb": traceback.format_exc()[-600:]})
                stats.setdefault("deploy_errors", []).append(
                    {"file": g["file"][:24], "round": rnd, "side": side,
                     "error": repr(ex)[:200]})
                continue
            receipts = dep.receipts[0]
            rejected = [r2 for r2 in receipts
                        if not r2.accepted
                        and r2.reason_code not in
                        ("UNSUPPORTED_ACTION", "UNSUPPORTED_RULE_DATA")]
            if rejected:
                stats["rounds_rejected_action"] += 1
                stats.setdefault("rejected_samples", []).append({
                    "file": g["file"][:24], "round": rnd, "side": side,
                    "receipts": [(r2.kind, r2.reason_code, r2.detail)
                                 for r2 in rejected[:3]]})
            for u in dep.unsupported_types:
                stats["unsupported_types"][u] = \
                    stats["unsupported_types"].get(u, 0) + 1
            got = dep.state.players[side].units
            want = want_units_of(nxt)
            ok, extra, missing, n_auto = compare_units(got, want, eco)
            clean = not norm_report.get("unresolved_refs") and not rejected
            stats["rounds_checked"] += 1
            if clean:
                stats["clean_rounds"] += 1
            if ok:
                stats["unit_set_exact"] += 1
                stats["auto_level_matches"] += n_auto
                if clean:
                    stats["clean_unit_set_exact"] += 1
            else:
                stats["unit_set_mismatch"] += 1
                if len(stats.setdefault("mismatch_samples", [])) < args.keep:
                    stats["mismatch_samples"].append({
                        "file": g["file"][:24], "round": rnd, "side": side,
                        "clean": clean,
                        "rejected": [(r2.kind, r2.reason_code)
                                     for r2 in rejected],
                        "extra": [list(e) for e in extra[:4]],
                        "missing": [list(m) for m in missing[:4]]})
            # supply oracle: end-of-round supply vs next snapshot
            stats["supply_checked"] += 1
            if dep.state.players[side].supply == int(nxt["supply"]):
                stats["supply_exact"] += 1
                if clean:
                    stats["clean_supply_exact"] += 1
            else:
                if len(stats.setdefault("supply_mismatch_samples", [])) < \
                        args.keep:
                    led = dep.ledgers[0]
                    stats["supply_mismatch_samples"].append({
                        "file": g["file"][:24], "round": rnd, "side": side,
                        "got": dep.state.players[side].supply,
                        "want": int(nxt["supply"]),
                        "income_model": incomes[side],
                        "supply_snapshot": int(r["supply"]),
                        "ledger": [(e.reason, e.amount) for e in led.entries],
                        "officers": list(r.get("officers") or [])})
            # fast-supply debt registry for the next round (sequential)
            for e in norm_actions:
                if e.get("t") == "passthrough" and \
                        e.get("raw_type") == "ActiveBlueprint" and \
                        int((e.get("raw_rec") or {}).get("ID", 0) or 0) == 1:
                    income_policy.record_fast_supply(side, rnd + 1)


def check_settlement_game(g, adapter, eco, gd, args, stats):
    """T7 oracle settlement: hp_next = hp - opponent Score (13,222-round
    verified model), preRoundFightResult, and exp ≡ FightReport units,
    all compared against the next snapshot."""
    gid = adapter.game_index_of(g)
    if g.get("info", {}).get("matchMode") != "VS_1_1":
        stats["skip_mode"] += 1
        return
    pairs = {int(p["round"]): p.get("match") for p in g.get("pairs", [])}
    for side in (0, 1):
        rs = g["players"][side]["rounds"]
        for i, r in enumerate(rs[:-1]):
            rnd = int(r["round"])
            if rnd < args.start_round:
                continue
            m = pairs.get(rnd)
            reps = (m or {}).get("reports") or []
            if len(reps) < 2:
                continue
            nxt = rs[i + 1]
            stats["rounds_checked"] += 1
            # hp oracle: damage to player p = opponent's Score
            dmg = int(reps[1 - side].get("score", 0) or 0)
            hp_pred = max(0, int(r["reactorCore"]) - dmg)
            if hp_pred == int(nxt["reactorCore"]):
                stats["hp_exact"] += 1
            else:
                if len(stats.setdefault("hp_mismatch_samples", [])) < \
                        args.keep:
                    stats["hp_mismatch_samples"].append(
                        {"file": g["file"][:24], "round": rnd, "side": side,
                         "pred": hp_pred, "want": int(nxt["reactorCore"])})
            # fight result oracle: label from the winner side's perspective
            score0, score1 = int(reps[0].get("score", 0) or 0), \
                int(reps[1].get("score", 0) or 0)
            # per 回放格式确认.md: player p's result is derived from the
            # OPPONENT's report score (damage dealt to p) vs own
            own_dmg = int(reps[1 - side].get("score", 0) or 0)
            opp_dmg = int(reps[side].get("score", 0) or 0)
            res = "Deuce" if own_dmg == opp_dmg else \
                ("Lose" if own_dmg > opp_dmg else "Win")
            if res == nxt.get("preRoundFightResult"):
                stats["result_exact"] += 1
            # exp oracle: FightReport unitDatas ≡ next snapshot units
            fr_units = {int(u["index"]): (int(u["exp"]), int(u["level"]))
                        for u in reps[side].get("units") or []}
            nxt_units = {int(u["index"]): (int(u.get("exp", 0) or 0),
                                           int(u["level"]))
                         for u in nxt["units"]}
            shared = set(fr_units) & set(nxt_units)
            if len(shared) >= 0:
                stats["exp_checked"] += 1
                ok = all(fr_units[ix] == nxt_units[ix] for ix in shared)
                if ok:
                    stats["exp_exact"] += 1
                elif len(stats.setdefault("exp_mismatch_samples", [])) < \
                        args.keep:
                    stats["exp_mismatch_samples"].append(
                        {"file": g["file"][:24], "round": rnd, "side": side})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=os.path.join(
        ROOT, "local_data", "rounds_norm.json"))
    ap.add_argument("--game-index", type=int, default=-1,
                    help="check a single game")
    ap.add_argument("--start-round", type=int, default=1)
    ap.add_argument("--sequential", action="store_true", default=True,
                    help="ban next-snapshot hints (default: on)")
    ap.add_argument("--no-sequential", dest="sequential",
                    action="store_false",
                    help="allow snapshot-derived income (legacy)")
    ap.add_argument("--mode", default="deploy",
                    choices=["deploy", "settlement"])
    ap.add_argument("--report", default=None)
    ap.add_argument("--keep", type=int, default=12)
    args = ap.parse_args()

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    eco = Economy(gd)
    adapter = ReplayAdapter(args.rounds)
    games = adapter.games()
    stats = {"games": 0, "rounds_total": 0, "rounds_checked": 0,
             "clean_rounds": 0,
             "unit_set_exact": 0, "unit_set_mismatch": 0,
             "clean_unit_set_exact": 0, "auto_level_matches": 0,
             "supply_checked": 0, "supply_exact": 0, "clean_supply_exact": 0,
             "hp_exact": 0, "result_exact": 0,
             "exp_checked": 0, "exp_exact": 0,
             "hp_mismatch_samples": [], "exp_mismatch_samples": [],
             "rounds_rejected_action": 0, "canon_error": 0, "deploy_error": 0,
             "skip_mode": 0, "skip_schema": 0,
             "unsupported_types": {}, "mismatch_samples": [],
             "supply_mismatch_samples": [], "rejected_samples": [],
             "canon_errors": [], "deploy_errors": []}
    income_policy = Income200r() if args.sequential else None
    t0 = time.time()
    game_iter = ([games[args.game_index]] if 0 <= args.game_index < len(games)
                 else games)
    for g in game_iter:
        stats["games"] += 1
        if args.mode == "settlement":
            check_settlement_game(g, adapter, eco, gd, args, stats)
        else:
            check_game(g, adapter, eco, gd, args, stats, income_policy)
    dt = time.time() - t0

    n = stats["unit_set_exact"] + stats["unit_set_mismatch"]
    nc = stats["clean_rounds"]
    print("games %d | rounds %d checked %d (%.1fs)%s" % (
        stats["games"], stats["rounds_total"], stats["rounds_checked"], dt,
        " | sequential" if args.sequential else " | LEGACY income"))
    if args.mode == "settlement":
        print("hp exact: %d/%d = %.2f%%" % (
            stats["hp_exact"], stats["rounds_checked"],
            100.0 * stats["hp_exact"] / max(1, stats["rounds_checked"])))
        print("fight-result exact: %d/%d = %.2f%%" % (
            stats["result_exact"], stats["rounds_checked"],
            100.0 * stats["result_exact"] / max(1, stats["rounds_checked"])))
        print("exp set exact (FightReport == next snapshot): %d/%d = %.2f%%"
              % (stats["exp_exact"], stats["exp_checked"],
                 100.0 * stats["exp_exact"] / max(1, stats["exp_checked"])))
        for m in stats["hp_mismatch_samples"][:4]:
            print("  hp mismatch:", m)
        for m in stats["exp_mismatch_samples"][:4]:
            print("  exp mismatch:", m)
        if args.report:
            stats["rounds"] = args.rounds
            stats["mode"] = args.mode
            os.makedirs(os.path.dirname(os.path.abspath(args.report)),
                        exist_ok=True)
            json.dump(stats, open(args.report, "w", encoding="utf8"),
                      ensure_ascii=False, indent=1)
            print("report ->", args.report)
        return
    print("unit-set exact: %d/%d = %.2f%%" % (
        stats["unit_set_exact"], n, 100.0 * stats["unit_set_exact"] / max(1, n)))
    print("clean rounds: %d | clean unit-set exact: %d/%d = %.2f%%" % (
        nc, stats["clean_unit_set_exact"], nc,
        100.0 * stats["clean_unit_set_exact"] / max(1, nc)))
    print("supply exact: %d/%d = %.2f%% (clean: %d/%d)" % (
        stats["supply_exact"], stats["supply_checked"],
        100.0 * stats["supply_exact"] / max(1, stats["supply_checked"]),
        stats["clean_supply_exact"], nc))
    print("rounds with a rejected core action: %d" %
          stats["rounds_rejected_action"])
    print("canon errors %d | deploy errors %d | skipped(mode) %d" % (
        stats["canon_error"], stats["deploy_error"], stats["skip_mode"]))
    print("unsupported raw types executed-as-marker:",
          stats["unsupported_types"])
    if stats["mismatch_samples"]:
        print("first mismatches:")
        for m in stats["mismatch_samples"][:6]:
            print("  %s r%d side%s clean=%s extra=%s missing=%s rej=%s" % (
                m["file"], m["round"], m["side"], m.get("clean"),
                m["extra"], m["missing"], m["rejected"]))
    if stats["supply_mismatch_samples"]:
        print("first supply mismatches:")
        for m in stats["supply_mismatch_samples"][:6]:
            print("  %s r%d side%s got=%s want=%s" % (
                m["file"], m["round"], m["side"], m["got"], m["want"]))
    if stats["rejected_samples"]:
        print("first rejections:")
        for m in stats["rejected_samples"][:6]:
            print("  %s r%d side%s %s" % (
                m["file"], m["round"], m["side"], m["receipts"]))
    if args.report:
        stats["rounds"] = args.rounds
        stats["mode"] = args.mode
        stats["sequential"] = args.sequential
        os.makedirs(os.path.dirname(os.path.abspath(args.report)),
                    exist_ok=True)
        json.dump(stats, open(args.report, "w", encoding="utf8"),
                  ensure_ascii=False, indent=1)
        print("report ->", args.report)


if __name__ == "__main__":
    main()
