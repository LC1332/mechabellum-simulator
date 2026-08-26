# T0 census: raw action types / fields / frequency / round coverage over a
# rounds corpus. Unknown types (outside the 17-type registry) are reported
# and make the exit code non-zero in --strict mode.
#
#   python tools/transition_action_census.py --rounds data/samples/rounds.json \
#       --out /tmp/transition_census.json
import argparse
import io
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                  errors="replace")
except Exception:
    pass

# registry: raw action types seen across the 1106-game corpus (2026-08-26)
KNOWN_TYPES = {
    "MoveUnit", "BuyUnit", "UpgradeUnit", "ReleaseCommanderSkill",
    "ActiveEnergyTowerSkill", "FinishDeploy", "Undo", "ChooseReinforceItem",
    "UnlockUnit", "UpgradeTechnology", "ReleaseContraption", "ActiveBlueprint",
    "UseEquipment", "ChooseAdvanceTeam", "CancelReleaseCommanderSkill",
    "StrengthenTower", "GiveUp",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="non-zero exit when unknown action types appear")
    args = ap.parse_args()

    data = json.load(open(args.rounds, encoding="utf8"))
    type_counts = Counter()
    field_sets = defaultdict(Counter)
    type_rounds = defaultdict(set)
    type_games = Counter()
    n_games = n_rounds = n_actions = 0
    versions = Counter()

    for g in data:
        n_games += 1
        versions[str(g.get("info", {}).get("gameMode"))] += 1
        for p in g.get("players", []):
            for r in p.get("rounds", []):
                n_rounds += 1
                for a in r.get("actions") or []:
                    n_actions += 1
                    t = a.get("type")
                    type_counts[t] += 1
                    type_rounds[t].add((g.get("file"), r.get("round")))
                    for k in a:
                        field_sets[t][k] += 1
                    type_games[t] += 1 if False else 0
        for t in {a.get("type") for p in g.get("players", [])
                  for r in p.get("rounds", [])
                  for a in (r.get("actions") or [])}:
            type_games[t] += 1

    unknown = sorted(set(type_counts) - KNOWN_TYPES)
    report = {
        "corpus": os.path.basename(args.rounds),
        "games": n_games, "rounds": n_rounds, "actions": n_actions,
        "game_modes": dict(versions),
        "type_counts": dict(type_counts.most_common()),
        "games_containing_type": {k: v for k, v in
                                  type_games.most_common()},
        "fields_by_type": {t: dict(c.most_common())
                           for t, c in field_sets.items()},
        "unknown_types": unknown,
        "known_registry": sorted(KNOWN_TYPES),
    }
    print("games %d rounds %d actions %d" % (n_games, n_rounds, n_actions))
    print("types:", dict(type_counts.most_common()))
    if unknown:
        print("UNKNOWN TYPES:", unknown)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        json.dump(report, open(args.out, "w", encoding="utf8"),
                  ensure_ascii=False, indent=1)
        print("report ->", args.out)
    if args.strict and unknown:
        sys.exit(1)


if __name__ == "__main__":
    main()
