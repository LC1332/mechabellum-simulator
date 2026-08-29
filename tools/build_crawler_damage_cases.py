# T0 (爬虫动力学与伤害标定修正任务书 2026-08-29 §5/T0): build the versioned,
# round-aligned oracle case manifest for the two calibration replays
# (伤害标定.grbr / 清杂标定.grbr).
#
# Round alignment is EXPLICIT, never positional (任务书 §1.3): for a pair
# recorded at replay round N the fight snapshot is round N's units_fight and
# the post-fight report is match.round = N+1. Every unit is keyed by
# (side, unit index, mech id) so no台风 result can be booked to a火神.
# Records that cannot be uniquely re-linked are marked "ambiguous" and are
# excluded from numeric fitting by construction (they never become cases).
#
# Usage:
#   python tools/build_crawler_damage_cases.py \
#       --rounds local_data/crawler_damage_replay_v1.json \
#       --out data/crawler_damage_oracle/crawler-damage-replay-v1
import argparse
import hashlib
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

MANIFEST_ID = "crawler-damage-replay-v1"
ALIGNMENT_SCHEMA = "crawler_damage_alignment_v1"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unit_key(u):
    return (int(u.get("index", -1)), int(u.get("id", -1)))


def align_units(pre_units, post_units):
    """Re-link the fight roster (units_fight of the fight round) against the
    post-fight report rows by (index, mech id).

    Report semantics frozen on the exp chain of both replays (2026-08-29):
    the report carried by match.round = N+1 is the round-N+1 deploy-time
    snapshot AFTER the round-N fight, so
      - exp_delta = report.exp - units_fight(N).exp is the per-fight exp
        gain (the damage attribution channel), and
      - the report unit set is NOT the fight roster: it also contains units
        that arrive with round N+1 (post-only rows) and it misses roster
        units that died in the fight (or were sold before the report).
    Therefore post-only rows are informational, never ambiguous, and absent
    rows lose survival certainty (death vs sell) but keep their pre state."""
    pre_by_key = {}
    dup = False
    for u in pre_units:
        k = unit_key(u)
        if k in pre_by_key:
            dup = True
        pre_by_key[k] = u
    post_by_key = {}
    for u in post_units:
        k = unit_key(u)
        if k in post_by_key:
            dup = True
        post_by_key[k] = u
    matched, ambiguous = [], []
    if dup:
        for k in sorted(pre_by_key):
            ambiguous.append({
                "key": {"index": k[0], "mech_id": k[1]},
                "reason": "duplicate_identity_in_fight_roster",
            })
        return matched, ambiguous
    post_keys = set(post_by_key)
    for k in sorted(pre_by_key):
        pre = pre_by_key[k]
        row = {
            "index": k[0], "mech_id": k[1],
            "level_pre": int(pre.get("level", 0)),   # 0-based replay level
            "exp_pre": int(pre.get("exp", 0)),
            "x": pre.get("x"), "y": pre.get("y"),
            "equipment": int(pre.get("equipment", 0)),
            "is_rotate": bool(pre.get("isRotate", False)),
        }
        post = post_by_key.get(k)
        if post is None:
            # absent from the report: died in this fight OR sold before the
            # round N+1 snapshot — exp attribution impossible, survival is
            # two-sided evidence, never silently claimed either way
            row.update({"exp_post": None, "exp_delta": None,
                        "survived": None,
                        "absence_reason": "died_in_fight_or_sold_before_report"})
        else:
            row.update({
                "exp_post": int(post.get("exp", 0)),
                "exp_delta": int(post.get("exp", 0)) - int(pre.get("exp", 0)),
                "survived": True,
            })
        matched.append(row)
    post_only = [
        {"index": k[0], "mech_id": k[1],
         "level": int(post_by_key[k].get("level", 0)),
         "reason": "not_in_fight_roster (arrived by report round)"}
        for k in sorted(post_keys - set(pre_by_key))
    ]
    return matched, ambiguous, post_only


def side_case(rounds_by_no, pair, side, replay_name):
    """Build one side's alignment record; status empty|ok|ambiguous."""
    fight_round = int(pair["round"])
    report_round = int(pair["match"]["round"])
    r = rounds_by_no.get(fight_round)
    if r is None:
        return {"status": "ambiguous",
                "reason": "no_round_record_for_fight_round"}
    pre = r.get("units_fight") or []
    reports = pair["match"].get("reports") or []
    post = reports[side].get("units") if side < len(reports) else None
    if post is None:
        if not pre and not reports:
            # 双方空场 r0: no roster and no report at all — a clean skip,
            # not an alignment failure (任务书 §1.2: 10 pair, 2 空场)
            return {"status": "empty", "side": side}
        return {"status": "ambiguous",
                "reason": "missing_report_for_side", "side": side}
    matched, ambiguous, post_only = align_units(pre, post)
    if not matched and not pre:
        return {"status": "empty", "side": side}
    status = "ambiguous" if ambiguous else "ok"
    return {
        "status": status,
        "side": side,
        "deploy_round": fight_round,
        "fight_round": fight_round,
        "report_round": report_round,
        "units": matched,
        "post_only_report_units": post_only,
        "ambiguous": ambiguous,
        # snapshot of every rule-affecting field of the round (任务书 §1.3)
        "round_context": {
            "techs": r.get("techs"),
            "techMap": r.get("techMap"),
            "officers": r.get("officers"),
            "unlocked_units": r.get("unlocked_units"),
            "energyTowerSkills_raw": r.get("energyTowerSkills_raw"),
            "towerStrengthen_raw": r.get("towerStrengthen_raw"),
            "supply": r.get("supply"),
            "preRoundFightResult": r.get("preRoundFightResult"),
        },
        "report_summary": {
            k: reports[side].get(k)
            for k in ("score", "deadScore", "aliveMechCount",
                      "destroyedCrystalCount", "destroyHugeMechCount")
            if side < len(reports)
        } if side < len(reports) else {},
    }


def build(rounds_path, game_build=None):
    replays = json.load(open(rounds_path, encoding="utf-8"))
    cases, alignment = [], []
    for rec in replays:
        src = rec.get("file")
        src_path = os.path.join("data", src)
        src_hash = sha256_file(src_path) if os.path.exists(src_path) else None
        players = rec["players"]
        rounds_by_no = [{r["round"]: r for r in p["rounds"]}
                        for p in players]
        for pair in rec.get("pairs", []):
            fight_round = int(pair["round"])
            report_round = int(pair["match"].get("round", fight_round + 1))
            case_id = "%s-r%d" % (os.path.splitext(src)[0], fight_round)
            sides = [side_case(rounds_by_no[i], pair, i, src)
                     for i in (0, 1)]
            statuses = [s["status"] for s in sides]
            if all(s == "empty" for s in statuses):
                status = "empty_field"
            elif "ambiguous" in statuses:
                status = "ambiguous"
            else:
                status = "ok"
            label = pair.get("label")
            entry = {
                "oracle_case_id": case_id,
                "source_replay": src,
                "source_replay_sha256": src_hash,
                "deploy_round": fight_round,
                "fight_round": fight_round,
                "report_round": report_round,
                "label": label,               # player0 view: Win/Lose
                "match_seed": pair["match"].get("rng"),
                "status": status,
                "attribution": "per_source_ok" if status == "ok"
                               else "mixed_unattributable"
                                    if status == "ambiguous" else "skipped",
                "sides": sides,
            }
            alignment.append(entry)
            if status == "ok":
                cases.append(entry)
    manifest = {
        "manifest_id": MANIFEST_ID,
        "alignment_schema": ALIGNMENT_SCHEMA,
        "rounds_source": os.path.basename(rounds_path),
        "assets": {
            "伤害标定.grbr": sha256_file("data/伤害标定.grbr"),
            "清杂标定.grbr": sha256_file("data/清杂标定.grbr"),
            "爬虫动力学.jpg": sha256_file("data/爬虫动力学.jpg"),
        },
        "game_build": game_build or "unverified_pending_oracle",
        "pysim_commit": "unverified_pending_oracle",
        "round_alignment_rule":
            "fight_round = pair.round (units_fight of the same round); "
            "report_round = match.round = pair.round + 1; unit identity = "
            "(side, index, mech_id); ambiguous records never become cases",
        "counts": {
            "pairs_total": len(alignment),
            "cases_ok": len(cases),
            "empty_field": sum(1 for a in alignment
                               if a["status"] == "empty_field"),
            "ambiguous": sum(1 for a in alignment
                             if a["status"] == "ambiguous"),
        },
        "cases": cases,
    }
    return manifest, alignment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default="local_data/crawler_damage_replay_v1.json")
    ap.add_argument("--out",
                    default="data/crawler_damage_oracle/crawler-damage-replay-v1")
    ap.add_argument("--game-build", default=None)
    ap.add_argument("--validate", action="store_true",
                    help="refuse to write if any non-empty pair is ambiguous")
    args = ap.parse_args()
    manifest, alignment = build(args.rounds, args.game_build)
    os.makedirs(args.out, exist_ok=True)
    if args.validate:
        bad = [a["oracle_case_id"] for a in alignment
               if a["status"] == "ambiguous"]
        if bad:
            print("VALIDATION FAILED: ambiguous pairs:", bad)
            sys.exit(1)
    with open(os.path.join(args.out, "manifest.json"), "w",
              encoding="utf8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "alignment.json"), "w",
              encoding="utf8") as f:
        json.dump({"schema": ALIGNMENT_SCHEMA, "pairs": alignment}, f,
                  ensure_ascii=False, indent=1)
    c = manifest["counts"]
    print("manifest: %s -> %s" % (MANIFEST_ID, args.out))
    print("  pairs_total=%d cases_ok=%d empty_field=%d ambiguous=%d"
          % (c["pairs_total"], c["cases_ok"], c["empty_field"],
             c["ambiguous"]))
    for a in alignment:
        print("  %-24s status=%-10s label=%-5s report_round=r%d"
              % (a["oracle_case_id"], a["status"], a["label"],
                 a["report_round"]))


if __name__ == "__main__":
    main()
