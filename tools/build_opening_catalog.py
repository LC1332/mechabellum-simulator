# Build the opening catalog: ChooseAdvanceTeam ID -> round-1 package evidence
# induced from a replay corpus (transition前后端审计游戏任务书 G3).
#
# Evidence rule: round-0 has ONLY the ChooseAdvanceTeam action (no moves), so
# the round-1 snapshot (units/positions/officers/HP/unlocks) is the package
# the game auto-granted. Formations are stored in TEAM-0 orientation; the
# runtime mirrors y for side 1. Variant formations are counted; the modal
# one is frozen (recorded in the catalog for audit).
#
# usage:
#   python tools/build_opening_catalog.py --replay-dir local_data/humen_replay \
#       --out data/game/opening_catalog.json
#   python tools/build_opening_catalog.py --rounds data/samples/rounds.json \
#       --out data/game/opening_catalog.json
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def game_entries(args):
    if args.replay_dir:
        from tools.replay2json import convert, load_tech_prev
        tech_prev = load_tech_prev()
        import glob
        out = []
        for f in sorted(glob.glob(os.path.join(args.replay_dir, "*.grbr"))):
            try:
                g = convert(f, tech_prev)
            except Exception as ex:
                print("  FAIL %s: %s" % (os.path.basename(f), ex))
                continue
            if g:
                out.append(g)
        return out
    return json.load(open(args.rounds, encoding="utf8"))


def team_of(game, side):
    rounds = game["players"][side]["rounds"]
    if not rounds:
        return None
    for a in rounds[0].get("actions") or []:
        if a.get("type") == "ChooseAdvanceTeam":
            return a
    return None


def formation_key(r1):
    """Canonical (player-0 orientation) formation signature of a round-1 rec.

    World halves (step2 任务书 §4.1): player 0 owns y<0, player 1 y>0 —
    mirror y so every package is frozen in player-0 orientation regardless
    of which side the evidence came from."""
    units = sorted(r1.get("units") or [], key=lambda u: int(u["index"]))
    sign = 1.0 if (units and float(units[0]["y"]) < 0) else -1.0
    return tuple((int(u["id"]), int(u["level"]) + 1,
                  round(float(u["x"]), 1), round(float(u["y"]) * sign, 1),
                  bool(u.get("isRotate"))) for u in units)


def build(games):
    catalog = {}
    for g in games:
        info = g.get("info", {})
        if info.get("matchMode") != "VS_1_1" or info.get("gameMode") != "Normal":
            continue
        for side in (0, 1):
            act = team_of(g, side)
            if not act:
                continue
            try:
                team_id = int(act["ID"])
            except (TypeError, ValueError):
                continue
            rounds = g["players"][side]["rounds"]
            if len(rounds) < 2:
                continue
            r1 = rounds[1]
            key = formation_key(r1)
            entry = catalog.setdefault(str(team_id), {
                "team_id": team_id,
                "formations": {},
                "officers": {}, "hp": {}, "supply": {}, "unlocked": {},
                "skills": {},
                "examples": 0,
            })
            entry["examples"] += 1
            entry["formations"][json.dumps(key)] = \
                entry["formations"].get(json.dumps(key), 0) + 1
            off = json.dumps([int(o) for o in r1.get("officers") or []])
            entry["officers"][off] = entry["officers"].get(off, 0) + 1
            entry["hp"][str(int(r1.get("reactorCore", 0) or 0))] = \
                entry["hp"].get(str(int(r1.get("reactorCore", 0) or 0)), 0) + 1
            entry["supply"][str(int(r1.get("supply", 0) or 0))] = \
                entry["supply"].get(str(int(r1.get("supply", 0) or 0)), 0) + 1
            unl = json.dumps(sorted(int(u) for u in
                                    (r1.get("unlocked_units") or [])))
            entry["unlocked"][unl] = entry["unlocked"].get(unl, 0) + 1
            sk = json.dumps([[e.get("index"), e.get("id"), e.get("isActive"),
                              e.get("coolingRound")]
                             for e in (r1.get("commanderSkills_raw") or [])])
            entry["skills"][sk] = entry["skills"].get(sk, 0) + 1
    # freeze: modal evidence per team
    packages = {}
    for tid, e in catalog.items():
        form = json.loads(max(e["formations"].items(), key=lambda kv: kv[1])[0])
        officers = json.loads(max(e["officers"].items(), key=lambda kv: kv[1])[0])
        unlocked = json.loads(max(e["unlocked"].items(), key=lambda kv: kv[1])[0])
        skills = json.loads(max(e["skills"].items(), key=lambda kv: kv[1])[0])
        hp = int(max(e["hp"].items(), key=lambda kv: kv[1])[0])
        supply = int(max(e["supply"].items(), key=lambda kv: kv[1])[0])
        groups = []
        for mech, level, x, y, rot in form:
            if groups and groups[-1]["mech"] == mech and \
                    groups[-1]["level"] == level:
                groups[-1]["formation"].append([x, y])
            else:
                groups.append({"mech": mech, "level": level,
                               "formation": [[x, y]], "is_rotate": rot})
        packages[tid] = {
            "team_id": int(tid),
            "name": "team %s" % tid,
            "evidence_games": e["examples"],
            "formation_variants": len(e["formations"]),
            "officers": officers, "hp": hp, "supply": supply,
            "unlocked": unlocked,
            "units": groups,
            "tech_map": {}, "constructions": [],
            "commander_skills": [list(s) for s in skills],
        }
    return packages


def name_packages(packages, gd):
    """Human-readable names: 开局 <team> · 专家 X · 3×A+2×B (evidence n)."""
    def mname(mid):
        c = gd.cards.get(int(mid))
        return c.name if c else str(mid)

    def oname(oid):
        o = gd.officers.get(int(oid))
        return o.name if o else str(oid)

    for tid, p in packages.items():
        parts = ["%d×%s" % (len(g["formation"]), mname(g["mech"]))
                 for g in p["units"]]
        spec = "/".join(oname(o) for o in p["officers"]) or "无专家"
        p["name"] = "开局#%s [%s] %s" % (tid, spec, "+".join(parts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-dir")
    ap.add_argument("--rounds")
    ap.add_argument("--from-catalog",
                    help="adapt an existing v1 catalog to v2 (y negated; "
                         "the only difference between the conventions)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gamedata", default=None)
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gd_path = args.gamedata or os.path.join(root, "data", "gamedata.json")
    from pysim.gamedata import GameData
    gd = GameData(gd_path)

    if args.from_catalog:
        import json as _json
        sys.path.insert(0, root)
        from pysim.transition import opening as opening_mod
        cat = opening_mod.load_catalog(args.from_catalog)
        if cat.get("adapted_from"):
            cat.pop("adapted_from", None)
            cat["formation_space"] = dict(opening_mod.FORMATION_SPACE)
            cat["generator"] = ("%s (upgraded from %s)"
                                % (cat.get("generator",
                                           "tools/build_opening_catalog.py"),
                                   opening_mod.CATALOG_SCHEMA_V1))
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        _json.dump(cat, open(args.out, "w", encoding="utf8"),
                   ensure_ascii=False, indent=1)
        print("catalog upgraded: %d teams -> %s"
              % (len(cat.get("packages") or {}), args.out))
        return

    if not args.replay_dir and not args.rounds:
        ap.error("need --replay-dir, --rounds or --from-catalog")
    games = game_entries(args)
    print("games:", len(games))
    packages = build(games)
    name_packages(packages, gd)
    from pysim.transition import opening as opening_mod
    out = {
        "schema_version": opening_mod.CATALOG_SCHEMA,
        "formation_space": dict(opening_mod.FORMATION_SPACE),
        "generator": "tools/build_opening_catalog.py",
        "package_count": len(packages),
        "packages": packages,
    }
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    json.dump(out, open(args.out, "w", encoding="utf8"), ensure_ascii=False,
              indent=1)
    print("catalog: %d teams -> %s" % (len(packages), args.out))
    for tid, p in sorted(packages.items(), key=lambda kv: -kv[1]["evidence_games"])[:40]:
        print("  %s evid=%d variants=%d hp=%d %s" % (
            tid, p["evidence_games"], p["formation_variants"], p["hp"], p["name"]))


if __name__ == "__main__":
    main()
