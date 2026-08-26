# ReplayAdapter: rounds.json records -> structured EnvironmentState.
#
# The rounds.json schema is tools/replay2json.py's output (kept at the repo
# boundary). This adapter is the only module allowed to understand it.
# Snapshot timing (information/回放格式确认.md):
#   playerData(round i) is PRE-deploy: buys/moves/upgrades of round i are NOT
#   in it; income for round i arrives during the deploy phase (after snapshot).
# Level boundary: XML Level is 0-based; UnitCard.level is 1-based (+1 here,
# -1 never happens downstream: battle_adapter feeds Battle directly).
import hashlib
import json
import os

from .model import (EnvironmentState, PlayerState, UnitCard, Phase,
                    SCHEMA_VERSION, RULESET_VERSION, ENGINE_VERSION)
from .state_tools import state_digest


def _as_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class ReplayAdapter:
    def __init__(self, rounds_path):
        self.path = str(rounds_path)
        if isinstance(rounds_path, (str, os.PathLike)) and os.path.exists(self.path):
            with open(self.path, "rb") as f:
                self.corpus_hash = hashlib.sha256(f.read()).hexdigest()[:16]
        else:
            self.corpus_hash = "unknown"
        self._games = None

    # ---------------------------------------------------------------- load
    def games(self):
        if self._games is None:
            data = self.path if isinstance(self.path, list) else \
                json.load(open(self.path, encoding="utf8"))
            self._games = data
        return self._games

    def game(self, index: int) -> dict:
        return self.games()[index]

    def game_index_of(self, game: dict) -> int:
        for i, g in enumerate(self.games()):
            if g is game:
                return i
        raise KeyError("game not from this adapter")

    # ---------------------------------------------------------------- state
    def environment_state(self, game_index: int, round_no: int,
                          start_entity_id: int = 1, economy=None) -> EnvironmentState:
        """Build the pre-deploy state of `round_no` for one game.

        Round 0 is the ChooseAdvanceTeam special round (units empty): callers
        that need a playable state should start at round >= 1."""
        g = self.game(game_index)
        players = []
        next_id = start_entity_id
        for side, pr in enumerate(g["players"]):
            rounds = {int(r["round"]): r for r in pr["rounds"]}
            rec = rounds.get(int(round_no))
            if rec is None:
                raise KeyError("round %s missing for player %d" % (round_no, side))
            players.append(self.player_state(rec, pr, before_round=round_no,
                                             start_entity_id=next_id,
                                             economy=economy))
            next_id = max([u.entity_id for u in players[-1].units],
                          default=next_id) + 1
        return EnvironmentState(
            schema_version=SCHEMA_VERSION, ruleset_version=RULESET_VERSION,
            engine_version=ENGINE_VERSION, round=int(round_no),
            phase=Phase.DEPLOYMENT, players=tuple(players),
            finished_deploy=(False, False), next_entity_id=next_id,
            provenance=(
                ("file", g.get("file", "")),
                ("game_index", str(game_index)),
                ("round", str(round_no)),
                ("corpus", os.path.basename(self.path)),
                ("corpus_hash", self.corpus_hash),
                ("match_mode", str(g.get("info", {}).get("matchMode"))),
            ))

    def player_state(self, round_rec: dict, player_rec: dict, before_round: int,
                     start_entity_id: int = 1, economy=None) -> PlayerState:
        units = []
        eid = start_entity_id
        for u in round_rec.get("units") or []:
            units.append(UnitCard(
                entity_id=eid,
                mech_id=int(u["id"]),
                level=int(u["level"]) + 1,          # 0-based XML -> 1-based
                exp=int(u.get("exp", 0) or 0),
                x=_as_float(u.get("x")), y=_as_float(u.get("y")),
                is_rotate=bool(u.get("isRotate", False)),
                equipment_id=int(u.get("equipment", 0) or 0),
                sell_supply=int(u.get("sellSupply", 0) or 0),
                round_count=int(u.get("roundCount", 0) or 0),
                replay_index=int(u["index"])))
            eid += 1
        # unlock provenance: snapshot presence + earlier UnlockUnit actions +
        # every mech bought/granted anywhere in the game (the ChooseAdvanceTeam
        # package grants unlocks that carry no UnlockUnit action; whole-game
        # buy scan is an initialization approximation, noted in provenance)
        unlocked = {u.mech_id for u in units}
        for r in player_rec["rounds"]:
            for a in r.get("actions") or []:
                if a.get("type") == "UnlockUnit":
                    unlocked.add(int(a.get("UID", 0)))
                elif a.get("type") == "BuyUnit":
                    unlocked.add(int(a.get("UID", 0)))
                elif a.get("type") == "ChooseReinforceItem" and economy:
                    g = economy.item_grant(int(a.get("ID", 0) or 0))
                    if g:
                        unlocked.add(g[0])
        tech_map = tuple(sorted(
            (int(m), tuple(int(t) for t in lst))
            for m, lst in (round_rec.get("techMap") or {}).items()))
        cs = tuple(tuple(str(x) for x in (
            e.get("index"), e.get("id"), e.get("isActive"), e.get("coolingRound")))
            for e in (round_rec.get("commanderSkills_raw") or []))
        cons = tuple(tuple(str(x) for x in (
            e.get("index"), e.get("id"), e.get("x"), e.get("y")))
            for e in (round_rec.get("constructions_raw") or []))
        twr = tuple(_as_int(x) for x in
                    (round_rec.get("towerStrengthen_raw") or (0, 0))[:2])
        max_hp = self._max_hp(round_rec, player_rec)
        return PlayerState(
            hp=int(round_rec.get("reactorCore", 0) or 0),
            max_hp=max_hp,
            supply=int(round_rec.get("supply", 0) or 0),
            pre_round_fight_result=round_rec.get("preRoundFightResult"),
            units=tuple(units),
            unlocked_mechs=frozenset(unlocked),
            tech_map=tech_map,
            officers=tuple(int(o) for o in round_rec.get("officers") or ()),
            blueprints=tuple(int(b) for b in round_rec.get("blueprints") or ()),
            commander_skills_raw=cs,
            tower_strengthen=twr,
            constructions_raw=cons,
            bought_this_round=0)

    @staticmethod
    def _max_hp(round_rec, player_rec):
        """Max HP: the opening ChooseAdvanceTeam modifies initial 4500; use the
        highest reactorCore seen in this game as a stable upper bound (scores
        only deduct). The exact modifier table is not needed for v0 because
        settlement clamps at 0 and terminal detection only needs current hp."""
        best = 0
        for r in player_rec["rounds"]:
            best = max(best, _as_int(r.get("reactorCore")))
        return max(best, _as_int(round_rec.get("reactorCore")), 4500)

    # ------------------------------------------------------- exogenous data
    @staticmethod
    def round_actions(game: dict, side: int, round_no: int) -> list:
        for r in game["players"][side]["rounds"]:
            if int(r["round"]) == int(round_no):
                return r.get("actions") or []
        return []

    @staticmethod
    def fight_reports(game: dict, round_no: int):
        """FightReports of round `round_no`'s fight (the replay stores them in
        the next round's MatchSnapshotData; replay2json exposes them via
        pairs[i]["match"]["reports"]), or None when absent."""
        for pair in game.get("pairs", []):
            if int(pair["round"]) == int(round_no):
                reps = (pair.get("match") or {}).get("reports") or []
                return reps if len(reps) >= 2 else None
        return None

    @staticmethod
    def derive_incomes(game: dict, eco):
        """({(side, round): income}, {(side, round)} approx) from snapshots
        minus raw prices.

        Used by replay runners as the InjectedIncome table. Rounds touching
        an unknown price are derived with that cost estimated as 0 and marked
        approximate — callers should pad those with a safety margin so the
        historical actions stay legal."""
        from collections import defaultdict
        prev_techs = {0: defaultdict(int), 1: defaultdict(int)}
        out = {}
        approx = set()
        for side, pr in enumerate(game["players"]):
            rs = pr["rounds"]
            for i, r in enumerate(rs[:-1]):
                cost = 0
                ok = True
                for a in r.get("actions") or []:
                    t = a.get("type")
                    try:
                        if t == "BuyUnit":
                            p = eco.buy_price(int(a["UID"]))
                            if p is None:
                                ok = False
                                break
                            cost += p
                        elif t == "UpgradeUnit":
                            uid = int(a.get("UID", 0) or 0)
                            uidx = a.get("UIDX")
                            mech = None
                            if uidx is not None and int(uidx) >= 0:
                                mech = next((int(u["id"]) for u in r["units"]
                                             if int(u["index"]) == int(uidx)), None)
                            if mech is None and uid:
                                mech = uid
                            if mech is None:
                                ok = False
                                break
                            p = eco.upgrade_price(mech)
                            if p is None:
                                ok = False
                                break
                            cost += p
                        elif t == "UnlockUnit":
                            p = eco.unlock_price(int(a["UID"]))
                            if p is None:
                                ok = False
                                break
                            cost += p
                        elif t == "UpgradeTechnology":
                            uid, tid = int(a["UID"]), int(a["TechID"])
                            p = eco.tech_price(uid, tid, prev_techs[side][uid])
                            if p is None:
                                ok = False
                                cost += 0        # unknown-price tech: skip
                                continue
                            cost += p
                            prev_techs[side][uid] += 1
                        elif t == "ChooseReinforceItem":
                            p = eco.item_cost(int(a.get("ID", 0) or 0))
                            if p is None:
                                ok = False
                                break
                            cost += p
                    except (KeyError, TypeError, ValueError):
                        ok = False
                        break
                if ok:
                    out[(side, int(r["round"]))] = int(
                        rs[i + 1]["supply"]) - int(r["supply"]) + cost
                else:
                    approx.add((side, int(r["round"])))
        return out, approx


def digest_of_round(adapter: ReplayAdapter, game_index: int, round_no: int) -> str:
    return state_digest(adapter.environment_state(game_index, round_no))
