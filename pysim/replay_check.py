# Validation driver: simulate all round pairs, compare with replay labels.
# Metrics: win accuracy (C# baseline 42.6%, step4 pysim 48.7%, step5 53.9%),
# winner AliveMechCount error (from FightReport), survivors card count, avg sim time.
# step9 (flank teleports, towers on): full-938 54.2% -> 55.2% with --sneak
# round (226 affected pairs: 52.7% -> 54.4%; card/round/game all beat off,
# round matches user Q6 "re-deploy after death doesn't re-eat the wait").
# step5 switches:
#   --techs off|def|full   off=no techs, def=default only, full=defaults+rebuilt
#   --deploy snap|fight    snap=pre-deploy snapshot (step4 behavior),
#                          fight=+this round's BuyUnit/Move/Upgrade replay
#   --report PATH          append stage metrics to a json report
# step11 additions:
#   --rounds PATH          load an alternative dataset (default data/rounds.json)
#   --round-filter SPEC    r1 / r1-2 / r3+ / all (bucket filter for quick probes)
#   per-round-bucket accuracy + per-side signed alive error (N1a diagnostics)
# step9 additions:
#   --sneak off|card|round|game   flank teleport delays (pysim/flank.py);
#                          quick-teleport officers (10009) drop to 5s
#   --flank-pairs          simulate only pairs where the per-card rule
#                          assigns >=1 delay (fixed A/B subset)
# usage: python -m pysim.replay_check [--trace] [--limit N] [--out trace.txt]
#        [--techs full] [--deploy fight] [--report data/report_step5.json]
import sys, io, time, json
try:  # only rewrap real console streams (breaks under pytest/uvicorn capture)
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8" \
            and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8",
                                      errors="replace")
except Exception:
    pass

from .gamedata import GameData
from .engine import battle_from_units
from .flank import pair_flank_delays, annotate_units, count_delays
from .skills import events_from_skill_actions

import os
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def build_tech_map(gd, player, mode, mechs_present):
    """Per-mech effective tech list for one player's input.
    step16 semantics fix: card defaultTechnologies are the shop's pre-filled
    slots, NOT auto-unlocked (replays show players BUYING their own default
    techs - 长弓 default 双发 702 purchased 30 sides, 铁锤 default 913 x21 -
    impossible if they were already active). Effective set = bought only,
    previousTechID chains folded. mode "def" restores the old default-seeded
    behavior for ablation."""
    if mode == "off":
        return {mid: [] for mid in mechs_present}
    bought = player.get("techMap") or {}
    prev = {t.id: t.previous_tech_id for t in gd.techs.values()}
    out = {}
    for mid in mechs_present:
        card = gd.cards.get(mid)
        lst = list(card.default_technologies) if (mode in ("def", "defull") and card) else []
        # mdefull: seed only MAIN-table defaults (the step5-15 calibration
        # world); sub-table defaults stay locked (replay buys prove they are
        # shop slots, not auto-active) while bought sub techs resolve fully
        if mode == "mdefull" and card:
            lst = [t for t in card.default_technologies
                   if gd.techs.get(t) is not None
                   and gd.techs[t].family == "technologyDatas"]
        if mode in ("full", "defull", "mdefull"):
            for tid in bought.get(str(mid), []):
                if tid not in lst:
                    lst.append(tid)
        # fold previousTechID chains (higher tier replaces lower)
        lst = [t for t in lst if not any(u != t and prev.get(u) == t for u in lst)]
        out[mid] = lst
    return out


def build_tower_mods(player):
    """Round buffs from this round's ActiveEnergyTowerSkill actions
    (step7-A): id5 强化瞄准 = +15 range (ranged), id6 高速移动 = +3 speed."""
    mods = {}
    for a in player.get("actions") or []:
        if a.get("type") == "ActiveEnergyTowerSkill":
            sid = a.get("SkillID")
            if sid == 5:
                mods["range"] = mods.get("range", 0) + 15
            elif sid == 6:
                mods["speed"] = mods.get("speed", 0) + 3
    return mods


def round_bucket(r):
    if r <= 2:
        return "r1-2"
    if r <= 4:
        return "r3-4"
    if r <= 6:
        return "r5-6"
    return "r7+"


def parse_round_filter(spec):
    # "r1" -> {1}; "r1-2" -> {1,2}; "r3+" -> min 3; "all"/None -> None
    spec = (spec or "all").strip().lower()
    if spec in ("all", ""):
        return None
    if spec.endswith("+"):
        return ("min", int(spec[1:-1] if spec[-1] == "+" else spec[1:]))
    if "-" in spec:
        a, b = spec[1:].split("-")
        return ("set", set(range(int(a), int(b) + 1)))
    return ("set", {int(spec[1:])})


def keep_round(rf, r):
    if rf is None:
        return True
    kind, v = rf
    return r >= v if kind == "min" else r in v


def main():
    args = sys.argv[1:]
    trace_path = None
    limit = 0
    # step16 default mdefull: MAIN-table card defaults seeded (step5-15
    # calibration world) + all bought techs resolved incl. step16 sub tables;
    # sub-table defaults stay locked (replay buys prove they are shop slots).
    # --techs full|def|defull|off remain available for ablation.
    techs_mode = "mdefull"
    deploy_mode = "fight"
    report_path = None
    rounds_path = None
    round_filter_spec = "all"
    if "--trace" in args:
        trace_path = args[args.index("--trace") + 1] if len(args) > args.index("--trace") + 1 and not args[args.index("--trace") + 1].startswith("--") else os.path.join(DATA, "trace.txt")
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    if "--techs" in args:
        techs_mode = args[args.index("--techs") + 1]
    if "--deploy" in args:
        deploy_mode = args[args.index("--deploy") + 1]
    if "--report" in args:
        report_path = args[args.index("--report") + 1]
    if "--rounds" in args:
        rounds_path = args[args.index("--rounds") + 1]
    if "--round-filter" in args:
        round_filter_spec = args[args.index("--round-filter") + 1]
    families = None
    if "--families" in args:
        families = set(args[args.index("--families") + 1].split(",")) - {""}
    opts = None
    if "--opt" in args:
        opts = {}
        for kv in args[args.index("--opt") + 1].split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    v = float(v)
                    v = int(v) if v == int(v) else v
                except ValueError:
                    pass
                opts[k] = v
    tower_skills = "--tower-skills" in args
    # step9: flank (sneak) teleport delays. off = old behavior; card/round/
    # game = unlock granularity (pysim/flank.py). Quick-teleport holders
    # (officer 10009) always drop to 5s when a mode is active.
    # step15: default card (r1-3 64.3 vs 63.8 round; full-938 57.9 vs 57.6,
    # r3-4 +1.1 / r7+ +1.1 - card delays every new enemy-half card, round
    # only the first per player per round)
    sneak_mode = "card"
    if "--sneak" in args and args.index("--sneak") + 1 < len(args):
        sneak_mode = args[args.index("--sneak") + 1]
    # step9 A/B helper: simulate only pairs where the per-card rule would
    # assign >=1 teleport delay (fixed subset, independent of --sneak mode,
    # so off/card/round/game run on identical pairs)
    flank_pairs_only = "--flank-pairs" in args
    # step7: towers default ON (measured position + hp 3400, full-938 net
    # +0.4pp, r1-2 +2.4pp, enables dcc tracking); --no-towers for ablation
    towers_on = "--towers" in args or "--no-towers" not in args
    # step12: battlefield constructions (walls/cannons/magnets from the
    # per-round construction snapshot), default ON; --no-buildings ablation.
    # Knobs via --opt: bld_slow (magnet slow 0.3/0.4/0.5), bld_wall_life
    # (1446 decoded default / 1112 base-row), bld_exp=0 (no kill exp).
    buildings_on = "--buildings" in args or "--no-buildings" not in args
    # per-cid selection (step12 P4 ablation). DEFAULT = walls + cannons only:
    # the magnet barricade appears in old snapshots but the live game removed
    # it (user), and modeling its slow field cost r3+ ~5pp (it fights nothing
    # real). Pass --bld-cids 1,2,3,4 to re-enable for sweeps.
    bld_cids = {1, 2, 3}
    if "--bld-cids" in args and args.index("--bld-cids") + 1 < len(args):
        spec = args[args.index("--bld-cids") + 1]
        bld_cids = ({1, 2, 3, 4} if spec == "all"
                    else set(int(x) for x in spec.split(",") if x))
    # step8-B: battlefield skills (contraptions + mapped commander skills,
    # pysim/skills.py). Default off until the A/B validates the mapping.
    skills_on = "--skills" in args
    # step14: officer combat modifiers (default ON; --no-officers ablation)
    officers_on = "--no-officers" not in args
    # kind filter for per-effect ablation (strike/barrier/turret/summon)
    skill_kinds = None
    if "--skill-kinds" in args and args.index("--skill-kinds") + 1 < len(args):
        skill_kinds = set(args[args.index("--skill-kinds") + 1].split(",")) - {""}

    rf = parse_round_filter(round_filter_spec)
    gd = GameData(os.path.join(DATA, "gamedata.json"))
    gd.tech_families = families
    _rp = rounds_path
    if not _rp:  # repo keeps only a sample corpus; full data lives in local_data/
        for cand in (os.path.join(DATA, "rounds.json"),
                     os.path.join(os.path.dirname(DATA), "local_data",
                                  "rounds_new11.json"),
                     os.path.join(os.path.dirname(DATA), "local_data",
                                  "rounds.json"),
                     os.path.join(DATA, "samples", "rounds.json")):
            if os.path.exists(cand):
                _rp = cand
                break
    if not _rp:
        raise SystemExit("no rounds corpus found (data/rounds.json, "
                         "local_data/rounds*.json or data/samples/rounds.json)")
    rounds = json.load(open(_rp, encoding="utf8"))
    # step18 T12: per-pair JSON export (winner / survivors / per-card exp /
    # kill list) - the sim side of the step18 experience diagnostic channel.
    # Join key = (file[:24], round, len(u0), len(u1)) exactly as step17.
    pairs_out = None
    if "--pairs-out" in args and args.index("--pairs-out") + 1 < len(args):
        pairs_out = args[args.index("--pairs-out") + 1]

    total = correct = draw = skipped = 0
    total_ms = 0.0
    alive_err = []          # |sim winner alive mechs - report aliveMechCount|
    sign_err = []           # per-side signed: sim_alive(side) - report_alive(side)
    bucket = {}             # bucket -> [total, correct]
    sneak_bucket = {"clean": [0, 0], "sneak": [0, 0]}   # step7: |x|>250 flank deploys
    skill_bucket = {"skilled": [0, 0], "unskilled": [0, 0]}  # step8-B: pairs
    # where >=1 mapped skill event was injected (the only pairs the feature
    # can change - read the A/B delta here, not on the full set)
    # step9: pairs where the flank rules actually assigned a teleport delay,
    # split by commander-skill interference (sneak usually mixes with
    # battlefield powers - isolate the clean subset to read the signal)
    delayed_bucket = {"delayed": [0, 0], "delayed-clean": [0, 0]}
    n_delay_cards = 0
    winner_tally = {0: 0, 1: 0, -1: 0}                  # side-bias probe
    dcc_match = {"own": [0, 0], "opp": [0, 0]}   # sim towers down vs report dcc
    # step7: distributions to compare against report dcc
    # (report: loser side {0:22%,1:41%,2:37%}, winner side {0:58%,1:33%,2:10%})
    td_dist = {0: {}, 1: {}}        # sim towers_down[side] histogram
    td_wl = {"win": {}, "lose": {}} # sim towers_down by that side's outcome
    dcc_wl = {"win": {}, "lose": {}}
    misses = []
    trace_done = False
    trace_lines = []
    pair_records = []

    def parse_constructions(pdata):
        """step12: [{cid, x, y, index}] from constructions_raw (strings in
        the json); cids outside 1-4 are skipped and counted."""
        out = []
        skipped = 0
        for c in (pdata.get("constructions_raw") or []):
            try:
                cid = int(c.get("id", 0))
                if cid not in (1, 2, 3, 4) or (bld_cids is not None and cid not in bld_cids):
                    skipped += 1
                    continue
                out.append({"cid": cid, "x": float(c["x"]), "y": float(c["y"]),
                            "index": int(c["index"])})
            except (KeyError, TypeError, ValueError):
                skipped += 1
        return out, skipped

    # step12 survival metric accumulators: sim group state at battle end vs
    # the next round's snapshot presence (aligned by Index). killed_gone /
    # alive_kept are consistent outcomes; killed_kept = durability recovery
    # (wall destroyed once) or sim overkill; alive_gone = missed kill.
    bld_bucket = {}
    bld_cid_bucket = {1: {}, 2: {}, 3: {}, 4: {}}
    bld_n_groups = 0
    bld_filtered = 0

    for replay in rounds:
        fname = replay["file"]
        flank_unlock = {0: False, 1: False}   # step9 'game' unlock state
        # step12: per-round snapshot index sets for the survival metric
        constr_by_round = {}
        for pr in replay["pairs"]:
            constr_by_round[int(pr["round"])] = {
                s: {int(c["index"]) for c in (pr["p%d" % s].get("constructions_raw") or [])}
                for s in (0, 1)}
        for pair in replay["pairs"]:
            if not keep_round(rf, pair["round"]):
                continue
            if flank_pairs_only and deploy_mode == "fight":
                fd0, fd1 = pair_flank_delays(pair, mode="card")
                if not (count_delays(fd0) or count_delays(fd1)):
                    continue
            u0, u1 = pair["p0"]["units"], pair["p1"]["units"]
            if deploy_mode == "fight":
                u0 = pair["p0"].get("units_fight") or u0
                u1 = pair["p1"].get("units_fight") or u1
            if not u0 and not u1:
                skipped += 1
                continue
            if sneak_mode != "off" and deploy_mode == "fight":
                d0, d1 = pair_flank_delays(pair, mode=sneak_mode,
                                           unlock_state=flank_unlock,
                                           delay=float(opts.get("sneak_delay", 10))
                                           if opts and "sneak_delay" in opts else None)
                u0 = annotate_units(u0, d0)
                u1 = annotate_units(u1, d1)
                n_delay_cards += count_delays(d0) + count_delays(d1)
            else:
                d0 = d1 = ()
            tm0 = build_tech_map(gd, pair["p0"], techs_mode, {int(u["id"]) for u in u0})
            tm1 = build_tech_map(gd, pair["p1"], techs_mode, {int(u["id"]) for u in u1})
            tw0 = build_tower_mods(pair["p0"]) if tower_skills else None
            tw1 = build_tower_mods(pair["p1"]) if tower_skills else None
            twr0 = twr1 = None
            if towers_on:
                twr0 = [int(x) for x in (pair["p0"].get("towerStrengthen_raw") or [0, 0])][:2]
                twr1 = [int(x) for x in (pair["p1"].get("towerStrengthen_raw") or [0, 0])][:2]
            sk0 = sk1 = None
            # always normalize for the skilled/unskilled split (both A/B arms
            # must slice the same pairs); only inject when --skills is on
            ev0 = events_from_skill_actions(pair["p0"].get("skill_actions"))
            ev1 = events_from_skill_actions(pair["p1"].get("skill_actions"))
            if skill_kinds is not None:
                ev0 = [e for e in ev0 if e.get("kind") in skill_kinds]
                ev1 = [e for e in ev1 if e.get("kind") in skill_kinds]
            if skills_on:
                sk0 = ev0 or None
                sk1 = ev1 or None
            bl0 = bl1 = None
            if buildings_on:
                bl0, s0b = parse_constructions(pair["p0"])
                bl1, s1b = parse_constructions(pair["p1"])
                bld_filtered += s0b + s1b
            # step14: officers (experts + blueprint-granted 20300/20310
            # entries, already merged in the replay officers list)
            of0 = pair["p0"].get("officers") if officers_on else None
            of1 = pair["p1"].get("officers") if officers_on else None
            b = battle_from_units(gd, u0, u1, tech_map0=tm0, tech_map1=tm1, opts=opts,
                                  tower_mods0=tw0, tower_mods1=tw1,
                                  towers0=twr0, towers1=twr1,
                                  skills0=sk0, skills1=sk1,
                                  buildings0=bl0, buildings1=bl1,
                                  officers0=of0, officers1=of1)
            if b.alive_count(0) == 0 or b.alive_count(1) == 0:
                skipped += 1
                continue
            if limit and total >= limit:
                break
            if trace_path and not trace_done and len(u0) >= 6 and len(u1) >= 6:
                b.trace_enabled = True
            t0 = time.perf_counter()
            winner = b.simulate()
            ms = (time.perf_counter() - t0) * 1000
            total_ms += ms
            total += 1
            label = pair["label"]
            actual_win = label == "Win"
            bk = round_bucket(pair["round"])
            bucket.setdefault(bk, [0, 0])
            bucket[bk][0] += 1
            sk_key = "skilled" if (len(ev0) + len(ev1)) else "unskilled"
            skill_bucket[sk_key][0] += 1
            # sneak = flank units deployed in the ENEMY half (step9 zone);
            # own-half flank deploys are ordinary corner placements
            has_sneak = any(
                (u["y"] > 0 if s == 0 else u["y"] < 0) and abs(u["x"]) > 250
                for s, us in ((0, u0), (1, u1)) for u in us)
            sneak_bucket["sneak" if has_sneak else "clean"][0] += 1
            n_del = count_delays(d0) + count_delays(d1)
            if n_del:
                n_skill = sum(1 for s in (0, 1)
                              for a in (pair["p%d" % s].get("actions") or [])
                              if a.get("type") == "ReleaseCommanderSkill")
                key = "delayed-clean" if n_skill == 0 else "delayed"
                delayed_bucket[key][0] += 1
            winner_tally[winner] += 1
            if winner == -1:
                draw += 1
            elif (winner == 0) == actual_win:
                correct += 1
                bucket[bk][1] += 1
                sneak_bucket["sneak" if has_sneak else "clean"][1] += 1
                skill_bucket[sk_key][1] += 1
                if n_del:
                    delayed_bucket[key][1] += 1
            else:
                misses.append("%s r%d: label=%s sim=team%s units %dv%d" % (
                    fname[:24], pair["round"], label, winner, len(u0), len(u1)))

            # step18 T12: per-pair export (cards carry the fight-end exp/level)
            if pairs_out:
                rec = {
                    "file": fname[:24], "round": int(pair["round"]),
                    "label": label, "sim_winner": int(winner),
                    "n_cards": [len(u0), len(u1)],
                    "survivors": {
                        str(t): {"mechs": b.alive_count(t),
                                 "cards": sorted({int(b.card_idx[i]) for i
                                                  in range(b.n) if not b.dead[i]
                                                  and b.team[i] == t
                                                  and b.card_idx[i] >= 0})}
                        for t in (0, 1)},
                    "cards": [{"mech": c["mech"], "team": c["team"],
                               "level": c["level"], "exp": c["exp"],
                               "dmg": round(b.card_damage.get(ci, 0.0), 1)}
                              for ci, c in enumerate(b.cards)],
                    "kills": b.kills,
                }
                pair_records.append(rec)

            # step12: building survival vs the next round's snapshot
            if buildings_on:
                nxt = constr_by_round.get(int(pair["round"]) + 1)
                if nxt is not None:
                    for (team, cid, gidx), (alive, tot) in b.building_groups().items():
                        bld_n_groups += 1
                        present = gidx in nxt[team]
                        key = ("killed" if alive <= 0 else "alive") + \
                              ("_kept" if present else "_gone")
                        bld_bucket[key] = bld_bucket.get(key, 0) + 1
                        cb = bld_cid_bucket.setdefault(cid, {})
                        cb[key] = cb.get(key, 0) + 1

            # report-based metrics
            rep = (pair.get("match") or {}).get("reports") or []
            if len(rep) >= 2:
                wrep = rep[0] if actual_win else rep[1]
                sim_alive = b.alive_count(0 if winner == 0 else 1)
                alive_err.append(abs(sim_alive - wrep["aliveMechCount"]))
                # N1a: signed per-side error (positive = sim leaves too many alive)
                sign_err.append(b.alive_count(0) - rep[0]["aliveMechCount"])
                sign_err.append(b.alive_count(1) - rep[1]["aliveMechCount"])
                if towers_on:
                    # step8: sim towers destroyed vs report destroyedCrystalCount;
                    # both ownership hypotheses (own vs opponent destroyed)
                    dcc_match["own"][1] += 1
                    dcc_match["opp"][1] += 1
                    own_ok = (b.towers_down[0] == rep[0]["destroyedCrystalCount"] and
                              b.towers_down[1] == rep[1]["destroyedCrystalCount"])
                    opp_ok = (b.towers_down[1] == rep[0]["destroyedCrystalCount"] and
                              b.towers_down[0] == rep[1]["destroyedCrystalCount"])
                    if own_ok:
                        dcc_match["own"][0] += 1
                    if opp_ok:
                        dcc_match["opp"][0] += 1
                    for side in (0, 1):
                        k = b.towers_down[side]
                        td_dist[side][k] = td_dist[side].get(k, 0) + 1
                        oc = "win" if (winner == side) else "lose"
                        td_wl[oc][k] = td_wl[oc].get(k, 0) + 1
                        rk = rep[side]["destroyedCrystalCount"]
                        roc = "win" if (0 if pair["label"] == "Win" else 1) == side else "lose"
                        dcc_wl[roc][rk] = dcc_wl[roc].get(rk, 0) + 1

            if trace_path and not trace_done and b.trace_enabled:
                trace_lines.append("# %s round %d label=%s sim=team%s" % (fname, pair["round"], label, winner))
                trace_lines.extend(b.trace)
                trace_done = True
        if limit and total >= limit:
            break

    stage = {"techs": techs_mode, "deploy": deploy_mode,
             "families": sorted(families) if families else None,
             "rounds_file": os.path.basename(rounds_path) if rounds_path else None,
             "round_filter": round_filter_spec,
             "opts": opts or None,
             "tower_skills": bool(tower_skills),
             "towers": bool(towers_on),
             "buildings": bool(buildings_on),
             "bld_cids": sorted(bld_cids) if bld_cids is not None else "all",
             "skills": bool(skills_on),
             "skill_kinds": sorted(skill_kinds) if skill_kinds else None,
             "officers": bool(officers_on),
             "sneak": sneak_mode,
             "flank_pairs": flank_pairs_only}
    print("stage: techs=%s deploy=%s families=%s rounds=%s filter=%s opts=%s tower=%s/%s buildings=%s(%s) battle-skills=%s officers=%s sneak=%s%s" % (
        techs_mode, deploy_mode, ",".join(sorted(families)) if families else "all",
        stage["rounds_file"] or "rounds.json", round_filter_spec, opts or {},
        "skills" if tower_skills else "-", "towers" if towers_on else "-",
        "on" if buildings_on else "off", ",".join(str(c) for c in bld_cids) if bld_cids is not None else "all",
        "on" if skills_on else "off", "on" if officers_on else "off", sneak_mode,
        " [flank-pairs only]" if flank_pairs_only else ""))
    print("pairs simulated: %d (skipped %d)" % (total, skipped))
    if buildings_on and bld_filtered:
        print("building placements filtered by --bld-cids: %d" % bld_filtered)
    if total:
        print("correct: %d/%d = %.1f%%   draws: %d" % (correct, total, 100.0 * correct / total, draw))
        print("avg sim time per round: %.2f ms" % (total_ms / total))
    for bk in sorted(bucket):
        t, c = bucket[bk]
        print("  %s: %d/%d = %.1f%%" % (bk, c, t, 100.0 * c / t if t else 0))
    for sk in ("clean", "sneak"):
        t, c = sneak_bucket[sk]
        if t:
            print("  %s (enemy-half flank deploys): %d/%d = %.1f%%" % (
                sk, c, t, 100.0 * c / t))
    for sk in ("skilled", "unskilled"):
        t, c = skill_bucket[sk]
        if t:
            print("  %s (pairs with >=1 mapped skill event): %d/%d = %.1f%%" % (
                sk, c, t, 100.0 * c / t))
    for sk in ("delayed", "delayed-clean"):
        t, c = delayed_bucket[sk]
        if t:
            print("  %s (pairs with >=1 teleport delay assigned): %d/%d = %.1f%%" % (
                sk, c, t, 100.0 * c / t))
    if sneak_mode != "off":
        print("  delayed cards total: %d" % n_delay_cards)
    print("  sim winner tally: team0 %d / team1 %d / draw %d" % (
        winner_tally[0], winner_tally[1], winner_tally[-1]))
    if alive_err:
        print("winner AliveMechCount |err|: mean %.1f  median %d  (n=%d)" % (
            sum(alive_err) / len(alive_err), sorted(alive_err)[len(alive_err) // 2], len(alive_err)))
    if sign_err:
        pos = sum(1 for e in sign_err if e > 0)
        neg = sum(1 for e in sign_err if e < 0)
        mean = sum(sign_err) / len(sign_err)
        sd = (sum((e - mean) ** 2 for e in sign_err) / len(sign_err)) ** 0.5
        print("per-side signed alive err (sim-report): mean %+.1f  sd %.1f  pos %d / zero %d / neg %d  (n=%d)" % (
            mean, sd, pos, len(sign_err) - pos - neg, neg, len(sign_err)))
    if towers_on and dcc_match["own"][1]:
        print("towers-down vs report dcc match: own %d/%d = %.1f%% | opp %d/%d = %.1f%%" % (
            dcc_match["own"][0], dcc_match["own"][1], 100.0 * dcc_match["own"][0] / dcc_match["own"][1],
            dcc_match["opp"][0], dcc_match["opp"][1], 100.0 * dcc_match["opp"][0] / dcc_match["opp"][1]))
        for oc in ("win", "lose"):
            ntd = sum(td_wl[oc].values()) or 1
            ndc = sum(dcc_wl[oc].values()) or 1
            print("  %-4s towers_down: %s | report dcc: %s" % (
                oc,
                " ".join("%d:%.0f%%" % (k, 100.0 * td_wl[oc].get(k, 0) / ntd) for k in (0, 1, 2)),
                " ".join("%d:%.0f%%" % (k, 100.0 * dcc_wl[oc].get(k, 0) / ndc) for k in (0, 1, 2))))
    if buildings_on and bld_n_groups:
        kg = bld_bucket.get("killed_gone", 0)
        ak = bld_bucket.get("alive_kept", 0)
        kk = bld_bucket.get("killed_kept", 0)
        ag = bld_bucket.get("alive_gone", 0)
        n = kg + ak + kk + ag
        print("building survival vs next-round snapshot (n=%d groups):" % bld_n_groups)
        print("  killed&gone %d (%.0f%%) | alive&kept %d (%.0f%%) | killed&kept %d (%.0f%%)"
              " | alive&gone %d (%.0f%%)" % (
                  kg, 100.0 * kg / n, ak, 100.0 * ak / n, kk, 100.0 * kk / n,
                  ag, 100.0 * ag / n))
        print("  kill recall (killed_gone / truly-gone): %.1f%%" % (
            100.0 * kg / max(1, kg + ag)))
        for cid, nm in ((1, "wall"), (2, "AA"), (3, "RF"), (4, "magnet")):
            cb = bld_cid_bucket.get(cid) or {}
            if not cb:
                continue
            cn = sum(cb.values())
            print("  cid%d %-7s: killed_gone %d alive_kept %d killed_kept %d alive_gone %d (n=%d)" % (
                cid, nm, cb.get("killed_gone", 0), cb.get("alive_kept", 0),
                cb.get("killed_kept", 0), cb.get("alive_gone", 0), cn))
    if misses:
        print("misses (first 10):")
        for m in misses[:10]:
            print("  " + m)
    if trace_path and trace_lines:
        open(trace_path, "w", encoding="utf-8").write("\n".join(trace_lines) + "\n")
        print("trace ->", trace_path)

    if pairs_out and pair_records:
        os.makedirs(os.path.dirname(pairs_out) or ".", exist_ok=True)
        json.dump(pair_records, open(pairs_out, "w", encoding="utf-8"),
                  ensure_ascii=False)
        print("pairs ->", pairs_out, "(%d pairs)" % len(pair_records))

    if report_path:
        entry = dict(stage)
        entry.update({"total": total, "correct": correct,
                      "acc": round(100.0 * correct / total, 1) if total else 0,
                      "draws": draw, "skipped": skipped,
                      "avg_ms": round(total_ms / total, 1) if total else 0,
                      "alive_err_mean": round(sum(alive_err) / len(alive_err), 1) if alive_err else None,
                      "alive_err_median": sorted(alive_err)[len(alive_err) // 2] if alive_err else None,
                      "sign_mean": round(sum(sign_err) / len(sign_err), 2) if sign_err else None,
                      "sign_pos": sum(1 for e in sign_err if e > 0) if sign_err else 0,
                      "sign_neg": sum(1 for e in sign_err if e < 0) if sign_err else 0,
                      "dcc_own_match": round(100.0 * dcc_match["own"][0] / dcc_match["own"][1], 1)
                      if dcc_match["own"][1] else None,
                      "dcc_opp_match": round(100.0 * dcc_match["opp"][0] / dcc_match["opp"][1], 1)
                      if dcc_match["opp"][1] else None,
                      "bld_bucket": dict(bld_bucket),
                      "bld_n_groups": bld_n_groups,
                      "delayed_bucket": {k: {"n": v[0],
                                             "acc": round(100.0 * v[1] / v[0], 1) if v[0] else 0}
                                         for k, v in delayed_bucket.items() if v[0]},
                      "skill_bucket": {k: {"n": v[0],
                                           "acc": round(100.0 * v[1] / v[0], 1) if v[0] else 0}
                                       for k, v in skill_bucket.items() if v[0]},
                      "delayed_cards": n_delay_cards,
                      "bucket": {k: {"n": v[0], "acc": round(100.0 * v[1] / v[0], 1) if v[0] else 0}
                                 for k, v in bucket.items()},
                      "misses": misses})
        report = []
        if os.path.exists(report_path):
            try:
                report = json.load(open(report_path, encoding="utf8"))
            except ValueError:
                report = []
        report = [r for r in report if r.get("techs") != techs_mode
                  or r.get("deploy") != deploy_mode
                  or (r.get("families") or None) != (stage["families"] or None)
                  or (r.get("rounds_file") or None) != (stage["rounds_file"] or None)
                  or (r.get("round_filter") or "all") != round_filter_spec
                  or (r.get("opts") or None) != (stage["opts"] or None)
                  or bool(r.get("tower_skills")) != bool(tower_skills)
                  or bool(r.get("towers")) != bool(towers_on)
                  or bool(r.get("buildings", True)) != bool(buildings_on)
                  or (r.get("bld_cids") or "all") != (stage["bld_cids"] or "all")
                  or bool(r.get("skills")) != bool(skills_on)
                  or bool(r.get("officers", True)) != bool(officers_on)
                  or (r.get("sneak") or "off") != sneak_mode
                  or bool(r.get("flank_pairs")) != flank_pairs_only]
        report.append(entry)
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        json.dump(report, open(report_path, "w", encoding="utf8"), ensure_ascii=False, indent=1)
        print("report ->", report_path)


if __name__ == "__main__":
    main()
