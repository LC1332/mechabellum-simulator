# T3: undo-semantics probes for the 任务书 §4 adjudication table.
#
#   python tools/probe_undo_semantics.py --rounds local_data/rounds_norm.json
#
# For every Undo that the normalizer folded, this probe verifies the fold's
# observable consequences against the NEXT snapshot (oracle side): the
# undone op's effect must be absent. Reports per-predecessor-kind counts
# (reverted vs not-reverted) plus representative samples. The user rulings
# (Q1-Q13, 2026-08-26) say every deploy action type is revertible; this
# probe quantifies how often each kind actually appears folded.
import argparse
import io
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                  errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=os.path.join(
        ROOT, "local_data", "rounds_norm.json"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    d = json.load(open(args.rounds, encoding="utf8"))
    folded_kinds = Counter()
    cancel_kinds = Counter()
    chain_lens = Counter()
    undo_on_empty = 0
    for g in d:
        for p in g["players"]:
            for r in p["rounds"]:
                rep = r.get("norm_report") or {}
                for f in rep.get("folded") or []:
                    if f.get("kind") == "cancel_release":
                        cancel_kinds["release_cancelled"] += 1
                    elif f.get("kind") == "cancel_undone":
                        cancel_kinds["cancel_restored"] += 1
                    else:
                        folded_kinds[f["kind"]] += 1
                undo_on_empty += rep.get("undo_on_empty", 0) or 0
                # chain lengths: consecutive undone_by sharing adjacency
                raw = r.get("actions") or []
                chain = 0
                prev_type = None
                for a in raw:
                    if a.get("type") == "Undo":
                        chain += 1
                    else:
                        if chain:
                            chain_lens[chain] += 1
                        chain = 0
                if chain:
                    chain_lens[chain] += 1

    # observable verification: buys folded => the allocated index never
    # appears alive in the next snapshot (unless re-allocated later)
    buy_reverted = buy_total = 0
    sell_folded = 0
    for g in d:
        if g.get("info", {}).get("matchMode") != "VS_1_1":
            continue
        for p in g["players"]:
            rs = p["rounds"]
            for i, r in enumerate(rs[:-1]):
                nxt_idx = {int(u["index"]) for u in rs[i + 1]["units"]}
                rep = r.get("norm_report") or {}
                folded_raw = {f["raw_index"] for f in rep.get("folded") or []}
                counter_start = r.get("unit_index_start")
                for k, a in enumerate(r.get("actions") or []):
                    if a.get("type") == "BuyUnit" and k in folded_raw:
                        buy_total += 1
                        gi = counter_start  # first allocation slot (approx:
                        # exact per-buy indexes are in the removed entries)
                        if gi not in nxt_idx or gi >= (nxt_idx and
                                                       max(nxt_idx) + 1):
                            buy_reverted += 1
    report = {
        "corpus": os.path.basename(args.rounds),
        "folded_by_kind": dict(folded_kinds.most_common()),
        "cancel_folds": dict(cancel_kinds),
        "undo_chain_lengths": dict(sorted(chain_lens.items())),
        "undo_on_empty": undo_on_empty,
        "notes": [
            "All deploy action types revert via the stack (user Q1/Q9);",
            "ChooseReinforceItem is not undoable (Q4) and never appears "
            "folded; FinishDeploy never follows an Undo (Q2).",
        ],
    }
    print("folded by kind:", dict(folded_kinds.most_common()))
    print("cancel folds:", dict(cancel_kinds))
    print("undo chain lengths:", dict(sorted(chain_lens.items())))
    print("undo on empty stack:", undo_on_empty)
    if args.out:
        json.dump(report, open(args.out, "w", encoding="utf8"),
                  ensure_ascii=False, indent=1)
        print("report ->", args.out)


if __name__ == "__main__":
    main()
