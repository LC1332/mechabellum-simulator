# v0.1 终点 A': build the de-undoed norm artifact (rounds_norm.json).
#
#   python tools/normalize_actions.py \
#     --rounds local_data/rounds.json \
#     --out local_data/rounds_norm.json \
#     --report /tmp/normalize_report.json [--diagnostic]
#
# Pure data transform: each (player, round) is normalized independently from
# its OWN record only (snapshot fields provided by replay2json). --diagnostic
# additionally diffs counters/spawns against the NEXT snapshot (report only,
# never written into the artifact). Re-running on the same input produces a
# byte-identical artifact.
import argparse
import io
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                  errors="replace")
except Exception:
    pass

from pysim.gamedata import GameData
from pysim.transition.economy import Economy
from pysim.transition.normalize import Normalizer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_game(g, norm: Normalizer, diag: bool, agg: dict):
    """Returns the normalized copy of one game record."""
    g2 = {k: v for k, v in g.items() if k != "players"}
    players = []
    for pr in g["players"]:
        rounds = []
        rs = pr["rounds"]
        rolling_counter = None      # T5: chained counter_end -> next start
        prev_round = None
        for i, rec in enumerate(rs):
            res = norm.normalize_round(rec)
            nrec = dict(rec)               # keep raw snapshot + actions for
            nrec["actions_norm"] = res.actions_norm   # audit/provenance
            nrec["unit_index_start"] = res.counter_start
            nrec["norm_report"] = res.report
            rounds.append(nrec)
            # ---------------- aggregate + optional oracle diagnostics
            agg["rounds"] += 1
            rep = res.report
            agg["undo_folded"] += rep["n_undo_folded"]
            agg["cancel_folded"] += rep["n_cancel_folded"]
            if rep["unresolved_refs"]:
                agg["rounds_unresolved"] += 1
                for u in rep["unresolved_refs"]:
                    agg["unresolved_reasons"][u["reason"]] += 1
                    if len(agg["unresolved_samples"]) < 200:
                        agg["unresolved_samples"].append(
                            {"file": g["file"][:24], "round": rec["round"],
                             **u})
            for note in rep["notes"]:
                key = note.split("@")[0]
                agg["notes"][key] = agg["notes"].get(key, 0) + 1
            nxt = rs[i + 1] if i + 1 < len(rs) else None
            if diag and nxt is not None and int(rec.get("round", 0)) >= 1:
                _diagnose(nrec, nxt, res, agg, g["file"][:24],
                          str(g.get("info", {}).get("matchMode")))
            # T5 rolling gate: the chained counter (previous round's
            # counter_end) must equal this round's snapshot unit_index;
            # contiguous rounds only (gaps hide unrecorded history). The
            # r0->r1 boundary is EXCLUDED: the ChooseAdvanceTeam package
            # grants the opening army outside any recorded deploy action
            # (T8 unsupported_round0 by design).
            if rolling_counter is not None and prev_round is not None and \
                    prev_round >= 1 and \
                    int(rec.get("round", 0)) == prev_round + 1 and \
                    rec.get("unit_index") is not None:
                agg["rolling_total"] += 1
                if int(rec["unit_index"]) == rolling_counter:
                    agg["rolling_match"] += 1
                else:
                    agg["rolling_mismatch"] += 1
                    if len(agg["rolling_samples"]) < 50:
                        agg["rolling_samples"].append(
                            {"file": g["file"][:24],
                             "round": rec["round"],
                             "rolling_prev_end": rolling_counter,
                             "snapshot_unit_index": rec["unit_index"]})
            rolling_counter = res.counter_end
            prev_round = int(rec.get("round", 0))
        players.append({k: v for k, v in pr.items() if k != "rounds"}
                       | {"rounds": rounds})
    g2["players"] = players
    g2["norm_version"] = "rounds_norm_v0.1"
    return g2


def _diagnose(rec, nxt, res, agg, fname, mode=""):
    """Oracle-only comparison against the next snapshot (report side)."""
    agg["diag_rounds"] += 1
    key = mode or "?"
    nxt_ui = nxt.get("unit_index")
    if nxt_ui is not None:
        if int(nxt_ui) == res.counter_end:
            agg["counter_match"] += 1
            agg["counter_match_by_mode"][key] += 1
        else:
            agg["counter_mismatch"] += 1
            agg["counter_mismatch_by_mode"][key] += 1
            if len(agg["counter_mismatch_samples"]) < 50:
                agg["counter_mismatch_samples"].append(
                    {"file": fname, "round": rec["round"], "mode": key,
                     "counter_end": res.counter_end, "next_unit_index": nxt_ui})
    # spawn set comparison: next snapshot's new indexes vs our spawns
    # (spawns sold later in the same round are legitimately absent from the
    # next snapshot: exclude them from both sides)
    sold = {e.get("unit") for e in rec.get("actions_norm") or []
            if e.get("t") == "sell"}
    nxt_idx = {int(u["index"]) for u in nxt.get("units") or []}
    want_new = {ix for ix in nxt_idx if ix >= res.counter_start}
    got_new = {ix for ix in res.spawn_indexes if ix not in sold}
    if want_new == got_new:
        agg["spawn_set_match"] += 1
        agg["spawn_set_match_by_mode"][key] += 1
    else:
        agg["spawn_set_mismatch"] += 1
        agg["spawn_set_mismatch_by_mode"][key] += 1
        if len(agg["spawn_mismatch_samples"]) < 50:
            agg["spawn_mismatch_samples"].append(
                {"file": fname, "round": rec["round"], "mode": key,
                 "want": sorted(want_new), "got": sorted(got_new),
                 "missing": sorted(want_new - got_new),
                 "extra": sorted(got_new - want_new)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=os.path.join(ROOT, "local_data",
                                                     "rounds.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "local_data",
                                                  "rounds_norm.json"))
    ap.add_argument("--report", default=None)
    ap.add_argument("--diagnostic", action="store_true",
                    help="oracle mode: diff counters/spawns vs next snapshot "
                         "(report only, not in the artifact)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only normalize the first N games (debug)")
    args = ap.parse_args()

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    norm = Normalizer(Economy(gd))
    data = json.load(open(args.rounds, encoding="utf8"))
    if args.limit:
        data = data[:args.limit]
    agg = Counter()
    agg.update({"rounds": 0, "undo_folded": 0, "cancel_folded": 0,
                "rounds_unresolved": 0, "diag_rounds": 0, "counter_match": 0,
                "counter_mismatch": 0, "spawn_set_match": 0,
                "spawn_set_mismatch": 0})
    agg["unresolved_reasons"] = Counter()
    agg["unresolved_samples"] = []
    agg["counter_mismatch_samples"] = []
    agg["spawn_mismatch_samples"] = []
    agg["counter_match_by_mode"] = Counter()
    agg["counter_mismatch_by_mode"] = Counter()
    agg["spawn_set_match_by_mode"] = Counter()
    agg["spawn_set_mismatch_by_mode"] = Counter()
    agg["rolling_total"] = 0
    agg["rolling_match"] = 0
    agg["rolling_mismatch"] = 0
    agg["rolling_samples"] = []
    agg["notes"] = {}

    t0 = time.time()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf8") as f:
        f.write("[")
        for i, g in enumerate(data):
            g2 = normalize_game(g, norm, args.diagnostic, agg)
            f.write(json.dumps(g2, ensure_ascii=False, separators=(",", ":")))
            if i < len(data) - 1:
                f.write(",")
        f.write("]")
    dt = time.time() - t0

    rounds = agg["rounds"]
    report = {
        "corpus": args.rounds, "artifact": args.out,
        "games": len(data), "rounds": rounds,
        "undo_folded": agg["undo_folded"], "cancel_folded": agg["cancel_folded"],
        "rounds_unresolved": agg["rounds_unresolved"],
        "unresolved_round_rate": round(agg["rounds_unresolved"] / max(1, rounds), 4),
        "unresolved_reasons": dict(agg["unresolved_reasons"]),
        "unresolved_samples": agg["unresolved_samples"][:50],
        "notes": agg["notes"],
        "diagnostic": {
            "rounds": agg["diag_rounds"],
            "counter_match": agg["counter_match"],
            "counter_mismatch": agg["counter_mismatch"],
            "counter_match_rate": round(agg["counter_match"]
                                        / max(1, agg["counter_match"]
                                              + agg["counter_mismatch"]), 4),
            "counter_match_by_mode": dict(agg["counter_match_by_mode"]),
            "counter_mismatch_by_mode": dict(agg["counter_mismatch_by_mode"]),
            "counter_mismatch_samples": agg["counter_mismatch_samples"][:20],
            "spawn_set_match": agg["spawn_set_match"],
            "spawn_set_mismatch": agg["spawn_set_mismatch"],
            "spawn_set_match_rate": round(agg["spawn_set_match"]
                                          / max(1, agg["spawn_set_match"]
                                                + agg["spawn_set_mismatch"]), 4),
            "spawn_set_match_by_mode": dict(agg["spawn_set_match_by_mode"]),
            "spawn_set_mismatch_by_mode": dict(agg["spawn_set_mismatch_by_mode"]),
            "spawn_mismatch_samples": agg["spawn_mismatch_samples"][:20],
            "rolling_counter": {
                "total": agg["rolling_total"],
                "match": agg["rolling_match"],
                "mismatch": agg["rolling_mismatch"],
                "match_rate": round(agg["rolling_match"]
                                    / max(1, agg["rolling_total"]), 4),
                "samples": agg["rolling_samples"][:20],
            },
        } if args.diagnostic else None,
        "seconds": round(dt, 1),
    }
    print("games %d rounds %d in %.1fs -> %s" % (
        len(data), rounds, dt, args.out))
    print("undo folded %d | cancel folded %d | rounds with unresolved refs "
          "%d (%.2f%%)" % (agg["undo_folded"], agg["cancel_folded"],
                           agg["rounds_unresolved"],
                           100.0 * report["unresolved_round_rate"]))
    print("unresolved reasons:", dict(agg["unresolved_reasons"]))
    print("notes:", agg["notes"])
    if args.diagnostic:
        dm = report["diagnostic"]
        print("counter match %d/%d = %.2f%% | spawn-set match %d/%d = %.2f%%"
              % (dm["counter_match"],
                 dm["counter_match"] + dm["counter_mismatch"],
                 100 * dm["counter_match_rate"], dm["spawn_set_match"],
                 dm["spawn_set_match"] + dm["spawn_set_mismatch"],
                 100 * dm["spawn_set_match_rate"]))
        rc = dm["rolling_counter"]
        print("rolling counter_end -> next unitIndex: %d/%d = %.2f%%"
              % (rc["match"], rc["total"], 100 * rc["match_rate"]))
        print("by mode (match/mismatch):",
              {k: (dm["counter_match_by_mode"].get(k, 0),
                   dm["counter_mismatch_by_mode"].get(k, 0))
               for k in set(dm["counter_match_by_mode"])
               | set(dm["counter_mismatch_by_mode"])})
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        json.dump(report, open(args.report, "w", encoding="utf8"),
                  ensure_ascii=False, indent=1)
        print("report ->", args.report)


if __name__ == "__main__":
    main()
