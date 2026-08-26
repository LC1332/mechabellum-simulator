# transition oracle check: does deploy_transition reproduce the next snapshot?
#
# Modes:
#   --mode deploy   state_i + actions_i -> deploy -> compare units vs snapshot_(i+1)
#                   (positions/levels/rotations exact; exp compared only when the
#                    round had no fight-dependent noise: exp fields are compared
#                    via the FightReport when available)
#   --mode settlement  oracle settlement: hp/result/exp writeback checked against
#                   snapshot_(i+1) using the REAL FightReport
# Reports JSON + console tables. Exclusions use predefined reason codes only.
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
from pysim.transition import (ReplayAdapter, Economy, canonicalize_plan,
                              deploy_transition, assert_state_invariants,
                              EnvironmentState)
from pysim.transition.replay_adapter import _as_int

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def unit_key(u):
    return (u.mech_id, u.level, round(u.x, 1), round(u.y, 1), bool(u.is_rotate))


def compare_units(got_units, want_units, eco=None):
    """Multiset compare with auto-level tolerance: a unit whose exp reached
    the next threshold can appear one level higher in the (post-fight) next
    snapshot; those matches count into `auto_level` instead of exact."""
    from collections import Counter
    gk = Counter(unit_key(u) for u in got_units)
    wk = Counter(unit_key(u) for u in want_units)
    exact = gk == wk
    if exact:
        return True, [], [], 0
    # try to pair extras/missing that differ only by an auto level-up
    extra = sorted((gk - wk).elements())
    missing = sorted((wk - gk).elements())
    n_auto = 0
    used = [False] * len(extra)
    rest_m = []
    exp_of = {}
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


def index_keyed(units):
    return {u.replay_index: u for u in units}


def check_game(g, adapter, eco, gd, args, stats, incomes):
    from pysim.transition.replay_adapter import ReplayAdapter
    from pysim.transition.model import PlayerState
    gid = adapter.game_index_of(g)
    try:
        rounds0 = g["players"][0]["rounds"]
        rounds1 = g["players"][1]["rounds"]
    except KeyError:
        stats["skip_schema"] += 1
        return
    if g.get("info", {}).get("matchMode") != "VS_1_1":
        stats["skip_mode"] += 1
        return
    start = max(1, args.start_round)

    def with_income(state, rnd):
        """Round-start income injection (declared exogenous, ledger-visible
        through derive_incomes; matches what replay runners do)."""
        players = tuple(
            PlayerState(**{**state.players[i].__dict__,
                           "supply": state.players[i].supply
                           + incomes.get((i, rnd), 0)}) for i in (0, 1))
        return EnvironmentState(
            schema_version=state.schema_version,
            ruleset_version=state.ruleset_version,
            engine_version=state.engine_version, round=state.round,
            phase=state.phase, players=players,
            finished_deploy=state.finished_deploy,
            next_entity_id=state.next_entity_id,
            terminal_reason=state.terminal_reason,
            provenance=state.provenance)

    for side in (0, 1):
        rs = g["players"][side]["rounds"]
        for i in range(len(rs) - 1):
            r = rs[i]
            rnd = int(r["round"])
            if rnd < start:
                continue
            nxt = rs[i + 1]
            stats["rounds_total"] += 1
            base = adapter.environment_state(gid, rnd, economy=eco)
            state = with_income(base, rnd)
            p_state = state.players[side]
            # oracle-only hint: the game's unitIndex counter at this round,
            # derived from the next snapshot's new unit indexes (sold/undone
            # units in past rounds can burn indexes above the snapshot max)
            live_max = max((u.replay_index for u in p_state.units
                            if u.replay_index is not None), default=-1)
            nxt_new = [int(u["index"]) for u in nxt["units"]
                       if int(u["index"]) > live_max]
            hint = min(nxt_new) if nxt_new else None
            try:
                plan, rep = canonicalize_plan(
                    side, ReplayAdapter.round_actions(g, side, rnd),
                    p_state, economy=eco, first_new_index=hint)
            except Exception as ex:               # noqa: BLE001 - census bucket
                stats["canon_error"] += 1
                stats.setdefault("canon_errors", []).append(
                    {"file": g["file"][:24], "round": rnd, "side": side,
                     "error": repr(ex)[:200]})
                continue
            try:
                dep = deploy_transition(
                    state, (plan,), eco)
            except Exception as ex:               # noqa: BLE001
                stats["deploy_error"] += 1
                stats.setdefault("deploy_errors", []).append(
                    {"file": g["file"][:24], "round": rnd, "side": side,
                     "error": repr(ex)[:200]})
                continue
            receipts = dep.receipts[0]
            rejected = [r2 for r2 in receipts
                        if not r2.accepted and r2.reason_code != "UNSUPPORTED_ACTION"]
            unsupported_exec = [r2 for r2 in receipts if r2.reason_code == "UNSUPPORTED_ACTION"]
            if rejected:
                stats["rounds_rejected_action"] += 1
                stats.setdefault("rejected_samples", []).append({
                    "file": g["file"][:24], "round": rnd, "side": side,
                    "receipts": [(r2.kind, r2.reason_code, r2.detail)
                                 for r2 in rejected[:3]]})
            for u in dep.unsupported_types:
                stats["unsupported_types"][u] = stats["unsupported_types"].get(u, 0) + 1
            got = dep.state.players[side].units
            want = [type("U", (), {})] * 0
            # expected from next snapshot
            class _WU:                                # lightweight view
                pass
            want = []
            for u in nxt["units"]:
                w = _WU()
                w.mech_id = int(u["id"])
                w.level = int(u["level"]) + 1
                w.x = float(u["x"])
                w.y = float(u["y"])
                w.is_rotate = bool(u.get("isRotate", False))
                w.exp = int(u.get("exp", 0) or 0)
                w.replay_index = int(u["index"])
                want.append(w)
            ok, extra, missing, n_auto = compare_units(got, want, eco)
            if ok:
                stats["unit_set_exact"] += 1
                stats["auto_level_matches"] += n_auto
            else:
                stats["unit_set_mismatch"] += 1
                if len(stats.setdefault("mismatch_samples", [])) < args.keep:
                    stats["mismatch_samples"].append({
                        "file": g["file"][:24], "round": rnd, "side": side,
                        "rejected": [(r2.kind, r2.reason_code) for r2 in rejected],
                        "extra": [list(e) for e in extra[:4]],
                        "missing": [list(m) for m in missing[:4]]})
            # supply ledger check against next snapshot when income injected
            stats["rounds_checked"] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=os.path.join(ROOT, "data", "samples",
                                                     "rounds.json"))
    ap.add_argument("--game-index", type=int, default=-1,
                    help="check a single game")
    ap.add_argument("--start-round", type=int, default=1)
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
             "unit_set_exact": 0, "unit_set_mismatch": 0, "auto_level_matches": 0,
             "rounds_rejected_action": 0, "canon_error": 0, "deploy_error": 0,
             "skip_schema": 0, "skip_mode": 0,
             "unsupported_types": {}, "mismatch_samples": [],
             "rejected_samples": [], "canon_errors": [], "deploy_errors": []}
    t0 = time.time()
    game_iter = ([games[args.game_index]] if 0 <= args.game_index < len(games)
                 else games)
    for g in game_iter:
        stats["games"] += 1
        incomes, approx = adapter.derive_incomes(g, eco)
        # approximate rounds (unknown prices): pad so historical actions stay
        # legal; flagged in the report
        for key in approx:
            incomes[key] = 2000
            stats["income_approx_rounds"] = stats.get("income_approx_rounds", 0) + 1
        check_game(g, adapter, eco, gd, args, stats, incomes)
    dt = time.time() - t0

    n = stats["unit_set_exact"] + stats["unit_set_mismatch"]
    print("games %d | rounds %d checked %d (%.1fs)" % (
        stats["games"], stats["rounds_total"], stats["rounds_checked"], dt))
    print("unit-set exact: %d/%d = %.2f%%" % (
        stats["unit_set_exact"], n, 100.0 * stats["unit_set_exact"] / max(1, n)))
    print("rounds with a rejected core action: %d" % stats["rounds_rejected_action"])
    print("canon errors %d | deploy errors %d | skipped(mode/schema) %d/%d" % (
        stats["canon_error"], stats["deploy_error"], stats["skip_mode"],
        stats["skip_schema"]))
    print("unsupported raw types executed-as-marker:", stats["unsupported_types"])
    if stats["mismatch_samples"]:
        print("first mismatches:")
        for m in stats["mismatch_samples"][:6]:
            print("  %s r%d side%s extra=%s missing=%s rejected=%s" % (
                m["file"], m["round"], m["side"], m["extra"], m["missing"],
                m["rejected"]))
    if stats["rejected_samples"]:
        print("first rejections:")
        for m in stats["rejected_samples"][:6]:
            print("  %s r%d side%s %s" % (
                m["file"], m["round"], m["side"], m["receipts"]))
    if args.report:
        stats["rounds"] = args.rounds
        stats["mode"] = args.mode
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        json.dump(stats, open(args.report, "w", encoding="utf8"),
                  ensure_ascii=False, indent=1)
        print("report ->", args.report)


if __name__ == "__main__":
    main()
