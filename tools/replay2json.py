# Convert .grbr replays -> per-round training JSON.
# Each output record: round input = both players' units/techs/RNG, label =
# fight result (preRoundFightResult of NEXT round record).
# step5 additions:
#   - actionRecords extraction (all PAD_* types, per player round)
#   - techMap: cumulative per-mech bought techs, folded by previousTechID
#   - units_fight: snapshot units + this round's deploy actions replayed
#     (BuyUnit/Undo/MoveUnit/UpgradeUnit) = the cards that actually fight
#     (the playerData snapshot is taken BEFORE the deploy phase, verified
#     against FightReport participant sets: 137 reports have extra uids
#     matching the same round's buys, only 5 the other way)
# usage: python replay2json.py [replay_dir] [out.json]
import os, sys, io, json, glob
import xml.etree.ElementTree as ET
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
REPLAY_DIR = sys.argv[1] if len(sys.argv) > 1 else r"E:\SteamLibrary\steamapps\common\Mechabellum\ProjectDatas\Replay"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_ROOT, "local_data", "rounds.json")

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

_INT_TAGS = {"UID", "UIDX", "ID", "TechID", "SkillID", "Index", "unitID",
             "unitIndex", "EquipmentID", "ConstructionIndex", "SkillIndex",
             "UnitIndex", "Level"}
_FLOAT_TAGS = {"Time", "LocalTime", "x", "y"}
_BOOL_TAGS = {"isRotate", "rotateRecord", "superDeployRecord"}

def extract_xml(path):
    data = open(path, "rb").read()
    i = data.find(b"<BattleRecord")
    j = data.find(b"</BattleRecord>", i)
    if i < 0 or j < 0:
        return None
    return data[i : j + len(b"</BattleRecord>")]

def txt(el, default=None):
    if el is None or el.text is None:
        return default
    t = el.text.strip()
    return t if t else default

def to_int(el, default=0):
    t = txt(el)
    try:
        return int(t) if t is not None else default
    except (ValueError, TypeError):
        return default

def el_value(el):
    # leaf -> typed scalar; container -> {tag: value} with same-tag lists
    kids = list(el)
    if not kids:
        t = txt(el)
        if t is None:
            return None
        if el.tag in _INT_TAGS:
            try:
                return int(t)
            except ValueError:
                return t
        if el.tag in _FLOAT_TAGS:
            try:
                return float(t)
            except ValueError:
                return t
        if el.tag in _BOOL_TAGS:
            return t == "true"
        return t
    out = {}
    for c in kids:
        v = el_value(c)
        if c.tag in out:
            if not isinstance(out[c.tag], list):
                out[c.tag] = [out[c.tag]]
            out[c.tag].append(v)
        else:
            out[c.tag] = v
    # list containers (all children share one tag, e.g. moveUnitDatas/
    # Positions) unwrap to a plain list
    if len(out) == 1 and len(kids) >= 1:
        only = next(iter(out.values()))
        return only if isinstance(only, list) else [only]
    return out

def parse_action_el(a):
    typ = a.get(XSI_TYPE, "").replace("PAD_", "")
    rec = {"type": typ}
    for c in a:
        v = el_value(c)
        if v is not None:
            rec.setdefault(c.tag, v)
    return rec

def parse_action_records(prr):
    ar = prr.find("actionRecords")
    if ar is None:
        return []
    return [parse_action_el(a) for a in ar]

def fold_tech_chains(active, prev_map):
    # drop techs replaced by a higher tier sharing previousTechID
    out = {}
    for uid, lst in active.items():
        keep = [t for t in lst if not any(u != t and prev_map.get(u) == t for u in lst)]
        out[uid] = keep
    return out

def build_units_fight(units, actions):
    # replay deploy-phase actions on the pre-deploy snapshot
    units = [dict(u) for u in units]
    by_index = {u["index"]: u for u in units}
    taken = set(by_index)
    last_buy = None
    for a in actions:
        t = a["type"]
        if t == "BuyUnit":
            pos = a.get("position") or {}
            u = {"id": int(a.get("UID", 0)), "index": -1, "roundCount": 0,
                 "durability": 0, "exp": 0, "level": 1,
                 "x": float(pos.get("x", 0.0) or 0.0), "y": float(pos.get("y", 0.0) or 0.0),
                 "equipment": 0, "isRotate": False, "sellSupply": 0, "_new": True}
            units.append(u)
            last_buy = u
        elif t == "Undo":
            if last_buy is not None:
                units.remove(last_buy)
                if last_buy["index"] >= 0:
                    taken.discard(last_buy["index"])
                last_buy = None
        elif t == "MoveUnit":
            for m in a.get("moveUnitDatas") or []:
                idx = m.get("unitIndex")
                uid = m.get("unitID")
                pos = m.get("position") or {}
                tgt = by_index.get(idx)
                if tgt is None or tgt.get("id") != uid:
                    # newly bought card: adopt the game-assigned index
                    tgt = next((u for u in units
                                if u.get("_new") and u["id"] == uid and u["index"] == -1), None)
                    if tgt is not None and idx not in taken:
                        tgt["index"] = idx
                        taken.add(idx)
                        by_index[idx] = tgt
                if tgt is not None:
                    tgt["x"] = float(pos.get("x", tgt["x"]) or 0.0)
                    tgt["y"] = float(pos.get("y", tgt["y"]) or 0.0)
                    if "isRotate" in m:
                        tgt["isRotate"] = bool(m["isRotate"])
            last_buy = None
        elif t == "UpgradeUnit":
            uid = a.get("UID")
            uidx = a.get("UIDX")
            tgt = None
            if uidx is not None and uidx >= 0 and by_index.get(uidx, {}).get("id") == uid:
                tgt = by_index[uidx]
            else:
                tgt = next((u for u in units if u["id"] == uid), None)
            if tgt is not None:
                tgt["level"] = min(9, max(1, tgt.get("level", 1)) + 1)
            last_buy = None
    for u in units:
        u.pop("_new", None)
    return units

def parse_unit_el(u):
    pos = u.find("Position")
    return {
        "id": int(txt(u.find("id"), "0")),
        "index": int(txt(u.find("Index"), "0")),
        "roundCount": int(txt(u.find("RoundCount"), "0")),
        "durability": int(txt(u.find("Durability"), "0")),
        "exp": int(txt(u.find("Exp"), "0")),
        "level": int(txt(u.find("Level"), "0")),
        "x": float(txt(pos.find("x"), "0")) if pos is not None else 0.0,
        "y": float(txt(pos.find("y"), "0")) if pos is not None else 0.0,
        "equipment": int(txt(u.find("EquipmentID"), "0")),
        "isRotate": (txt(u.find("IsRotate"), "false") == "true"),
        "sellSupply": int(txt(u.find("SellSupply"), "0")),
    }

def parse_units(parent, tag):
    # tag = list container ("units" in playerData, "unitDatas" in FightReport)
    out = []
    c = parent.find(tag)
    if c is None:
        return out
    for u in c:
        out.append(parse_unit_el(u))
    return out

def _opt_int(el, default=None):
    # optional counter export: missing tag -> default (not 0, 0 is meaningful)
    return to_int(el, default) if el is not None else default


def parse_player_round(prr):
    pd = prr.find("playerData")
    rs = pd.find("randomStateData")
    techs = pd.find("activeTechnologies")
    off = pd.find("officers")
    bps = pd.find("bluepints")
    # step8-B raw state fields (lost in the step9 regen, restored 2026-08-20):
    # commanderSkills state is the authoritative ID source for releases whose
    # action ID=0 (54% of them); towerStrengthen_raw drives replay towers.
    cs = pd.find("commanderSkills")
    ets = pd.find("energyTowerSkills")
    tsl = pd.find("towerStrengthenLevels")
    csd = pd.find("constructionSnapshotDatas")
    shop = pd.find("shop")
    unlocked = shop.find("unlockedUnits") if shop is not None else None
    return {
        "round": int(txt(prr.find("round"), "0")),
        "reactorCore": int(txt(pd.find("reactorCore"), "0")),
        "supply": int(txt(pd.find("supply"), "0")),
        "preRoundFightResult": txt(pd.find("preRoundFightResult")),
        "rng": [to_int(e) for e in rs.find("randomStates")] if rs is not None else [],
        "units": parse_units(pd, "units"),
        "techs": [to_int(e) for e in techs] if techs is not None else [],
        # v0.1 counters (transition-v0.1正规化任务书 T1): unitIndex is the
        # game's NEXT allocatable unit Index (== maxlive+1 verified on 662
        # snapshots; burned indexes from sells create holes below it).
        # contraption/construction counters exported for later use only.
        "unit_index": _opt_int(pd.find("unitIndex")),
        "contraption_index": _opt_int(pd.find("contraptionIndex")),
        "construction_index": _opt_int(pd.find("constructionIndex")),
        # shop/unlockedUnits is the AUTHORITATIVE unlock set (v0 derived it
        # from whole-game buy scans; this replaces that approximation)
        "unlocked_units": [to_int(e) for e in unlocked] if unlocked is not None else None,
        "shop_buy_count": _opt_int(shop.find("BuyCount")) if shop is not None else None,
        # step9: officers (specialists; 10009 = Quick Teleport -> flank
        # teleport 10s -> 5s) and research-center blueprints (1-9/401/501/1001+)
        "officers": [to_int(e) for e in off] if off is not None else [],
        "blueprints": [to_int(e) for e in bps] if bps is not None else [],
        # step8-B raw state (index/id/coolingRound kept as strings, census-era
        # schema; consumers int() on demand)
        "commanderSkills_raw": [
            {"index": txt(e.find("index")), "id": txt(e.find("id")),
             "isActive": txt(e.find("isActive")),
             "coolingRound": txt(e.find("coolingRound"))}
            for e in cs.findall("CommanderSkillData")] if cs is not None else [],
        "energyTowerSkills_raw": [txt(e) for e in ets] if ets is not None else [],
        "towerStrengthen_raw": [txt(e) for e in tsl] if tsl is not None else [],
        "constructions_raw": [
            {"index": txt(c.find("Index")), "id": txt(c.find("ID")),
             "x": txt(c.find("Position").find("x")) if c.find("Position") is not None else None,
             "y": txt(c.find("Position").find("y")) if c.find("Position") is not None else None}
            for c in csd.findall("ConstructionSnapshotData")] if csd is not None else [],
    }


def extract_skill_actions(rounds):
    """step8-B: normalize release actions into per-round rec["skill_actions"].
    Commander releases with ID=0 are resolved through the commanderSkills
    state (same round first, then next round - skills bought this round show
    up in the next snapshot; resolves 95% of ID=0, tools/step8_probe9).
    CancelRelease removes the matching prior release of the same round (all
    188 observed cancels match one, median gap 3s = pre-fight undo).
    """
    for i, rec in enumerate(rounds):
        cur = {e["index"]: e.get("id") for e in rec.get("commanderSkills_raw") or []}
        nxt = {}
        if i + 1 < len(rounds):
            nxt = {e["index"]: e.get("id")
                   for e in rounds[i + 1].get("commanderSkills_raw") or []}
        out = []
        for a in rec.get("actions") or []:
            t = a.get("type")
            if t == "ReleaseContraption":
                pos = a.get("Position") or {}
                out.append({
                    "type": "contraption", "id": int(a.get("ContraptionID", 0) or 0),
                    "x": float(pos.get("x", 0.0) or 0.0),
                    "y": float(pos.get("y", 0.0) or 0.0),
                    "localTime": float(a.get("LocalTime", 0.0) or 0.0),
                })
            elif t == "ReleaseCommanderSkill":
                raw = int(a.get("ID", 0) or 0)
                sid = raw
                if not sid:
                    k = str(a.get("SkillIndex"))
                    sid = int(cur.get(k) or nxt.get(k) or 0)
                ps = [(float(p.get("x", 0.0) or 0.0), float(p.get("y", 0.0) or 0.0))
                      for p in (a.get("Positions") or []) if isinstance(p, dict)]
                out.append({
                    "type": "commander", "id": sid, "rawId": raw,
                    "skillIndex": int(a.get("SkillIndex", 0) or 0),
                    "positions": ps,
                    "unitIndex": int(a.get("UnitIndex", -1)),
                    "constructionIndex": int(a.get("ConstructionIndex", -1)),
                    "localTime": float(a.get("LocalTime", 0.0) or 0.0),
                })
            elif t == "CancelReleaseCommanderSkill":
                k = str(a.get("SkillIndex"))
                for j in range(len(out) - 1, -1, -1):
                    e = out[j]
                    if e.get("type") == "commander" and str(e.get("skillIndex")) == k:
                        out.pop(j)
                        break
        rec["skill_actions"] = out

def parse_fight_report(fr):
    # per-player post-fight report: survivor card snapshot + scores
    # step18 T13: Bonus (FightRecord 0x14) added; DeadScore is 0 across the
    # whole 938-pair corpus (no information), Score = the blood deduction
    # dealt to the opponent by surviving units (Q7 model, see
    # tools/step18_t13_q7.py: r=0.751 vs own aliveMechCount, winner side only)
    fr_rec = fr.find("FightRecord")
    return {
        "units": parse_units(fr, "unitDatas"),
        "score": to_int(fr.find("Score")),
        "deadScore": to_int(fr.find("DeadScore")),
        "destroyedCrystalCount": to_int(fr.find("DestroyedCrystalCount")),
        "aliveMechCount": to_int(fr.find("AliveMechCount")),
        "destroyHugeMechCount": to_int(fr_rec.find("DestroyHugeMechCount")) if fr_rec is not None else 0,
        "bonus": to_int(fr_rec.find("Bonus")) if fr_rec is not None else 0,
    }

def convert(path, tech_prev):
    xml = extract_xml(path)
    if xml is None:
        return None
    root = ET.fromstring(xml)
    players = []
    for pr in root.find("playerRecords").findall("PlayerRecord"):
        rounds = []
        active_techs = {}   # mech id -> ordered bought tech ids (cumulative)
        for rr in pr.find("playerRoundRecords").findall("PlayerRoundRecord"):
            rec = parse_player_round(rr)
            # techMap is the PRE-deploy state: it must NOT contain techs
            # bought during this round's own deploy (v0.1 fix: charging the
            # tech staircase needs the before-round active count)
            rec["techMap"] = fold_tech_chains(
                {m: list(t) for m, t in active_techs.items()}, tech_prev)
            rec["actions"] = parse_action_records(rr)
            for a in rec["actions"]:
                if a["type"] == "UpgradeTechnology":
                    uid = str(a.get("UID", 0))
                    tid = a.get("TechID", 0)
                    lst = active_techs.setdefault(uid, [])
                    if tid not in lst:
                        lst.append(tid)
            rec["techActions"] = [(a.get("UID", 0), a.get("TechID", 0))
                                  for a in rec["actions"] if a["type"] == "UpgradeTechnology"]
            rec["units_fight"] = build_units_fight(rec["units"], rec["actions"])
            rounds.append(rec)
        # step8-B: needs the full round list (next-round ID=0 resolution)
        extract_skill_actions(rounds)
        players.append({
            "id": txt(pr.find("id")),
            "name": txt(pr.find("name")),
            "seed": int(txt(pr.find("seed"), "0")),
            "team": int(txt(pr.find("data").find("team"), "0")),
            "rounds": rounds,
        })
    bi = root.find("BattleInfo")
    match = []
    reinforce_offer_errors = []   # G1: ChooseReinforceItem ID/Index mismatches
    for m in root.find("matchDatas").findall("MatchSnapshotData"):
        lfr = m.find("lastFightResult")
        reports = []
        if lfr is not None:
            rc = lfr.find("Reports")
            if rc is not None:
                reports = [parse_fight_report(r) for r in rc.findall("FightReport")]
        # G1 (transition前后端审计游戏任务书): keep the reinforcement
        # candidates. The 4 ids are SHARED by both players (corpus: 24/26
        # sample picks align; 2 stale client clicks recorded as errors).
        ri = m.find("reinforceItems")
        offers = [int(e.text) for e in ri.iter("int")] if ri is not None else []
        match.append({
            "round": int(txt(m.find("round"), "0")),
            "rng": [to_int(e) for e in m.find("randomStateData").find("randomStates")] if m.find("randomStateData") is not None else [],
            "deadCount": int(txt(m.find("deadCount"), "0")),
            "reports": reports,
            "reinforceItems": offers,
        })
    info = {
        "systemSeed": int(txt(bi.find("SystemSeed"), "0") or 0),
        "mapID": int(txt(bi.find("MapID"), "0") or 0),
        "prepareTime": float(txt(bi.find("PrepareTime"), "30") or 30),
        "deployTime": float(txt(bi.find("DeployTime"), "100") or 100),
        "fightTime": float(txt(bi.find("FightTime"), "120") or 120),
        "maxRound": int(txt(bi.find("MaxRound"), "40") or 40),
        "gameMode": txt(bi.find("GameMode")),
        "matchMode": txt(bi.find("MatchMode")),
    }
    # build training pairs: input = state at round i (both players), label = fight result of round i
    by_round = {m["round"]: m for m in match}
    # G1: per-round shared reinforcement offers + ID/Index alignment check.
    # A mismatch is recorded, never auto-repaired (the raw click is the truth).
    offers_by_round = {r: list(m.get("reinforceItems") or []) for r, m in by_round.items()}
    reinforce_errors = []
    for pi, pr in enumerate(players):
        for rr in pr["rounds"]:
            for ai, a in enumerate(rr.get("actions") or []):
                if a.get("type") != "ChooseReinforceItem":
                    continue
                idx, cid = a.get("Index"), a.get("ID")
                off = offers_by_round.get(int(rr["round"]), [])
                if isinstance(idx, int) and 0 <= idx < len(off) and int(cid or 0) != 0 \
                        and int(cid) != off[idx]:
                    reinforce_errors.append({
                        "player": pi, "round": int(rr["round"]), "raw_index": ai,
                        "id": cid, "index": idx, "offers": off,
                        "reason": "ID_INDEX_MISMATCH"})
    pairs = []
    n = min(len(players[0]["rounds"]), len(players[1]["rounds"]))
    for i in range(n):
        nxt = players[0]["rounds"][i + 1] if i + 1 < n else None
        if nxt is None:
            break
        label = nxt["preRoundFightResult"]
        if label not in ("Win", "Lose"):
            continue
        r = players[0]["rounds"][i]["round"]
        # fight of round r is recorded in the next round's match snapshot
        nxt_match = by_round.get(r + 1)
        pairs.append({
            "round": r,
            "p0": players[0]["rounds"][i],
            "p1": players[1]["rounds"][i],
            "match": nxt_match,
            "label": label,   # from player0's perspective
        })
    return {"file": os.path.basename(path), "info": info, "players": players,
            "pairs": pairs,
            "reinforce_offers": {str(k): v for k, v in sorted(offers_by_round.items())},
            "reinforce_errors": reinforce_errors,
            "schema_version": "replay_rounds_v0.2"}

def load_tech_prev():
    """id -> previousTechID for chain folding. tools/tech.json is a decode
    artifact kept out of the repo; data/gamedata.json carries the same
    previousTechID column and is the in-repo fallback."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        tj = json.load(open(os.path.join(here, "tech.json"), encoding="utf8"))
        return {e["id"]: e.get("previousTechID", 0) for e in tj["technologyDatas"]}
    except OSError:
        pass
    try:
        gj = json.load(open(os.path.join(_ROOT, "data", "gamedata.json"),
                            encoding="utf8"))
        return {int(e["id"]): e.get("previousTechID", 0)
                for e in gj["techs"].values()}
    except (OSError, KeyError, ValueError):
        return {}

def main():
    files = sorted(glob.glob(os.path.join(REPLAY_DIR, "*.grbr")))
    tech_prev = load_tech_prev()
    print(f"replays: {len(files)}")
    alldat = []
    nfail = 0
    for f in files:
        try:
            rec = convert(f, tech_prev)
        except Exception as ex:
            print(f"  FAIL {os.path.basename(f)}: {ex}")
            nfail += 1
            continue
        if rec is None:
            nfail += 1
            continue
        alldat.append(rec)
        print(f"  {os.path.basename(f)}: {len(rec['pairs'])} pairs, "
              f"{len(rec['players'][0]['rounds'])} rounds")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(alldat, open(OUT, "w", encoding="utf8"), ensure_ascii=False)
    total = sum(len(r["pairs"]) for r in alldat)
    print(f"\ndone: {len(alldat)} replays ok ({nfail} fail), {total} round pairs -> {OUT}")

if __name__ == "__main__":
    main()
