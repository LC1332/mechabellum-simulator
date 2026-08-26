# Game session service for the /game audit game (任务书 §2.2, G10-G14).
#
# Owns: in-memory sessions with per-session locks, optimistic versioning,
# replay-library shards + opening catalog, the historical opponent policy
# (canonicalized norm streams + exp overrides + atomic commit), session
# phase machine (opening/reinforcement/deployment/round_result/terminal/
# blocked) and the GameView serializer. Never touches EnvironmentState
# fields directly — every mutation goes through the transition API.
import copy
import hashlib
import threading
import time
import uuid

from pysim.transition import (TransitionEnv, Economy, Income200r,
                              EnvironmentState, PlayerState, UnitCard, Phase,
                              ActionKind, CanonicalAction, CanonicalActionPlan,
                              EntityRef, BuyArgs, MoveArgs, UpgradeArgs,
                              UnlockArgs, TechArgs, SellArgs, GiftArgs,
                              ChooseReinforceArgs, UnsupportedArgs,
                              deploy_transition, state_digest, state_to_dict,
                              copy_state, canonicalize_plan,
                              capability, opening as opening_mod)
from pysim.transition.normalize import GIFT_OFFICERS
from pysim.transition.deploy import TOWER_STRENGTHEN_COST, BLUEPRINT_COSTS

GAME_VIEW_SCHEMA = "game_view_v1"
SESSION_PHASES = ("opening", "reinforcement", "deployment", "pre_battle",
                  "round_result", "terminal", "blocked")
MIN_ROUNDS_DEFAULT = 5
TOWER_MAX_LEVEL = 9
BLUEPRINT_INFO = {
    1: {"name": "快速补给", "cost": 0, "detail": "+200 立即, 下回合 -300 (收入侧)"},
    2: {"name": "批量征召", "cost": 0, "detail": "本回合购买上限 +1"},
    3: {"name": "精英征召", "cost": 100, "detail": "本回合后续购买单位 +1 级"},
    4: {"name": "进攻强化 I", "cost": 100, "detail": "永久 +12% 伤害"},
    5: {"name": "防御强化 I", "cost": 100, "detail": "永久 +15% 生命"},
    401: {"name": "进攻强化 II", "cost": 300, "detail": "永久 +36% 伤害(替换I)"},
    501: {"name": "防御强化 II", "cost": 300, "detail": "永久 +45% 生命(替换I)"},
}


class GameError(Exception):
    """Stable, API-facing error: (code, http_status, detail)."""

    def __init__(self, code, http_status, detail=""):
        super().__init__(detail)
        self.code = code
        self.http_status = http_status
        self.detail = detail


# ---------------------------------------------------------------- actions
def action_from_json(d):
    """Public CanonicalAction JSON -> CanonicalAction (transition arg model,
    no web-specific semantics). Unit refs: {"handle": int} only (handles are
    the per-player game unit index exposed in GameView.players[].units)."""
    kind = ActionKind(str(d.get("kind")))
    a = d.get("args") or {}
    if kind is ActionKind.BUY_UNIT:
        return CanonicalAction(kind, BuyArgs(
            mech_id=int(a["mech_id"]), x=float(a["x"]), y=float(a["y"]),
            new_ref=int(a.get("new_ref", 0)), is_rotate=bool(a.get("is_rotate", False))))
    if kind is ActionKind.MOVE_UNIT:
        rot = a.get("is_rotate")
        return CanonicalAction(kind, MoveArgs(
            ref=_ref(a["ref"]), x=float(a["x"]), y=float(a["y"]),
            is_rotate=None if rot is None else bool(rot)))
    if kind is ActionKind.UPGRADE_UNIT:
        return CanonicalAction(kind, UpgradeArgs(ref=_ref(a["ref"])))
    if kind is ActionKind.BUY_TECH:
        return CanonicalAction(kind, TechArgs(mech_id=int(a["mech_id"]),
                                              tech_id=int(a["tech_id"])))
    if kind is ActionKind.UNLOCK_UNIT:
        return CanonicalAction(kind, UnlockArgs(mech_id=int(a["mech_id"])))
    if kind is ActionKind.SELL_UNIT:
        return CanonicalAction(kind, SellArgs(ref=_ref(a["ref"])))
    if kind is ActionKind.RAW_UNSUPPORTED:
        raw_in = a.get("raw") or {}
        if isinstance(raw_in, dict):
            raw = tuple(sorted((str(k), v) for k, v in raw_in.items()))
        else:                      # [[k, v], ...] pair list (frontend form)
            raw = tuple(sorted((str(kv[0]), kv[1]) for kv in raw_in
                               if isinstance(kv, (list, tuple)) and len(kv) == 2))
        return CanonicalAction(kind, UnsupportedArgs(
            raw_type=str(a["raw_type"]), raw=raw))
    raise GameError("UNKNOWN_ACTION_KIND", 400, "kind %s not submittable" % d.get("kind"))


def _ref(d):
    if not isinstance(d, dict) or d.get("handle") is None:
        raise GameError("MALFORMED_ACTION_REF", 400, "ref needs {handle}")
    return EntityRef(handle=int(d["handle"]))


APPLYABLE_KINDS = {ActionKind.BUY_UNIT, ActionKind.MOVE_UNIT,
                   ActionKind.UPGRADE_UNIT, ActionKind.BUY_TECH,
                   ActionKind.UNLOCK_UNIT, ActionKind.SELL_UNIT,
                   ActionKind.RAW_UNSUPPORTED}


def receipt_to_dict(r, seq, player, round_no):
    return {"seq": seq, "round": round_no, "player": player,
            "action_index": r.action_index, "kind": r.kind,
            "accepted": r.accepted, "reason_code": r.reason_code,
            "resource_delta": r.resource_delta,
            "created_entity_id": r.created_entity_id,
            "removed_entity_id": r.removed_entity_id,
            "changed_paths": list(r.changed_paths), "detail": r.detail}


def diff_dicts(a, b, path="", out=None, cap=64):
    """Recursive canonical-dict diff -> [{path, before, after}] (capped)."""
    if out is None:
        out = []
    if len(out) >= cap:
        return out
    if type(a) is not type(b):
        out.append({"path": path or "$", "before": a, "after": b})
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append({"path": "%s.%s" % (path, k),
                            "before": a.get(k, "<missing>"),
                            "after": b.get(k, "<missing>")})
            else:
                diff_dicts(a[k], b[k], "%s.%s" % (path, k), out, cap)
            if len(out) >= cap:
                break
        return out
    if isinstance(a, list):
        if a != b:
            out.append({"path": path or "$", "before": a, "after": b})
        return out
    if a != b:
        out.append({"path": path or "$", "before": a, "after": b})
    return out


# ---------------------------------------------------------------- session
class GameSession:
    def __init__(self, session_id, shard, option, gd, eco, catalog,
                 min_rounds=MIN_ROUNDS_DEFAULT, battle_seed_base=None):
        self.id = session_id
        self.gd = gd
        self.eco = eco
        self.catalog = catalog
        self.shard = shard
        self.option = option
        self.game = shard["game"]
        self.opponent = int(option["opponent_player"])
        self.human = 1 - self.opponent
        self.lock = threading.Lock()
        self.created_at = time.time()
        self.version = 0
        self.phase = "opening"
        self.round = 0
        self.stop_reason = None
        self.blocker = None
        self.battle = None
        self.min_rounds = min_rounds
        self.replay_id = shard["replay_id"]

        self.env = TransitionEnv(gd, eco, income_policy=Income200r())
        info = self.game.get("info", {})
        self.system_seed = int(info.get("systemSeed", 0) or 0)
        self.player_seeds = tuple(int(p.get("seed", 0) or 0)
                                  for p in self.game["players"])
        if battle_seed_base is None:
            battle_seed_base = self.system_seed % (1 << 30)
        self.battle_seed_base = int(battle_seed_base) & ((1 << 30) - 1)

        # opening offers: recorded candidate pinned at its historical index,
        # three deterministic generated candidates (opening_offer_generator_v1)
        self.opening_offers = []
        self._build_opening_offers()

        # per-round accumulators
        self._receipts = []            # all receipts (dicts), tagged by round
        self._ledger = []              # [{player, reason, amount, entity_id, round}]
        self._audit = []               # audit events (dicts)
        self._undo_stack = []          # (env_snapshot, receipts_len, ledger_len)
        self._last_dict = None         # previous canonical state dict (diffing)
        self._reinforce_done_round = None
        self._battle_seed = None
        self._income_last = (0, 0)
        self._emit_audit("SESSION_CREATED", player=None,
                         detail="replay %s opponent=%d" % (self.replay_id, self.opponent))

    # ------------------------------------------------------------ helpers
    def _emit_audit(self, kind, player=None, detail="", changed_paths=(),
                    digest_before=None, digest_after=None, context=None):
        self._audit.append({
            "seq": len(self._audit), "version": self.version,
            "round": self.round, "player": player, "kind": kind,
            "detail": detail, "changed_paths": list(changed_paths or ()),
            "digest_before": digest_before, "digest_after": digest_after,
            "context": context or {}})

    def _snapshot(self):
        return {"state": state_to_dict(self.env.state)}

    def _commit_state(self, new_state):
        self.env.load({"state": state_to_dict(new_state)})

    def _round_rec(self, side, round_no):
        for r in self.game["players"][side]["rounds"]:
            if int(r["round"]) == int(round_no):
                return r
        return None

    def _offers_for_round(self, round_no):
        raw = self.game.get("reinforce_offers") or {}
        return [int(x) for x in raw.get(str(int(round_no)), [])]

    def _build_opening_offers(self):
        hum_team, hum_idx = opening_mod.recorded_team_of(self.game, self.human)
        if hum_team is None:
            hum_team, hum_idx = int(next(iter(self.catalog["packages"]))), 0
        seed = opening_mod.generator_seed(
            self.game["info"].get("systemSeed", 0), self.system_seed,
            self.player_seeds[self.human], self.replay_id, self.human)
        picks = opening_mod.generate_offers(self.catalog, hum_team, hum_idx, seed)
        for slot, team_id, source in picks:
            pkg = opening_mod.package_of(self.catalog, team_id)
            self.opening_offers.append({
                "index": slot, "team_id": team_id, "offer_id": "team-%s" % team_id,
                "name": pkg.get("name", "team %s" % team_id),
                "specialists": [self._officer_name(o) for o in pkg.get("officers", [])],
                "hp": int(pkg.get("hp", 4500)), "supply": int(pkg.get("supply", 0)),
                "units": [{"mech_id": g["mech"], "name": self._mech_name(g["mech"]),
                           "count": len(g["formation"]), "level": g.get("level", 1)}
                          for g in pkg.get("units", [])],
                "source": source, "ruleset_version": self.catalog.get("schema_version", ""),
            })
        self.opening_offers.sort(key=lambda o: o["index"])

    def _officer_name(self, oid):
        o = self.gd.officers.get(int(oid))
        return "%s(%s)" % (o.name, oid) if o else str(oid)

    def _mech_name(self, mid):
        c = self.gd.cards.get(int(mid))
        return c.name if c else str(mid)

    def _reinforce_offer_views(self, round_no):
        ids = self._offers_for_round(round_no)
        views = []
        for i, item_id in enumerate(ids):
            info = self.eco.items.get(item_id) or {}
            cost = self.eco.item_cost(item_id)
            blocker = None
            if cost is None:
                blocker = "UNSUPPORTED_REINFORCEMENT"
            else:
                blocker = capability.classify_norm_entry(
                    {"t": "reinforce", "id": item_id}, None, self.eco, self.gd)
            views.append({
                "index": i, "card_id": item_id,
                "name": info.get("name", str(item_id)),
                "category": info.get("kind", "?"),
                "cost": cost if cost is not None else 0,
                "description": self._item_description(item_id),
                "supported": blocker is None,
                "unsupported_reason": blocker,
            })
        return views

    def _item_description(self, item_id):
        if not hasattr(self, "_item_desc_cache"):
            self._item_desc_cache = {}
            try:
                import json as _json
                from pysim.transition.economy import _REINFORCE_JSON
                raw = _json.load(open(_REINFORCE_JSON, encoding="utf8"))
                for c in raw.get("cards", []):
                    self._item_desc_cache[int(c.get("id", 0))] = \
                        str(c.get("描述") or "")
            except (OSError, ValueError, TypeError):
                pass
        return self._item_desc_cache.get(int(item_id), "")

    def _record_ledgers(self, ledgers, round_no):
        for side, led in enumerate(ledgers or ()):
            for e in led.entries:
                self._ledger.append({"player": side, "reason": e.reason,
                                     "amount": int(e.amount),
                                     "entity_id": e.entity_id,
                                     "round": round_no})

    def _next_battle_seed(self):
        return (self.battle_seed_base + self.round * 7919) & ((1 << 30) - 1)

    # ------------------------------------------------------------ commands
    def execute(self, expected_version, kind, payload):
        """One command transaction; returns (view_dict, receipt_or_None).

        Raises GameError for transport-level failures (stale/unknown phase/
        malformed). Illegal strategy actions come back as rejected receipts
        (version unchanged), never as HTTP errors."""
        with self.lock:
            if expected_version != self.version:
                raise GameError("STALE_SESSION_VERSION", 409,
                                "expected %d, current %d"
                                % (expected_version, self.version))
            handlers = {
                "CHOOSE_OPENING": self._cmd_choose_opening,
                "CHOOSE_REINFORCEMENT": self._cmd_choose_reinforcement,
                "APPLY_ACTION": self._cmd_apply_action,
                "UNDO_LAST_HUMAN_ACTION": self._cmd_undo,
                "FINISH_DEPLOYMENT": self._cmd_finish_deployment,
                "ACK_ROUND_RESULT": self._cmd_ack_round_result,
            }
            h = handlers.get(kind)
            if h is None:
                raise GameError("UNKNOWN_COMMAND", 400, "kind %s" % kind)
            return h(payload or {})

    def _require_phase(self, *phases):
        if self.phase not in phases:
            raise GameError("WRONG_SESSION_PHASE", 409,
                            "command needs %s, session in %s" % (phases, self.phase))

    def _blocked_guard(self):
        if self.phase == "blocked":
            raise GameError("SESSION_BLOCKED", 409,
                            detail=self.blocker and self.blocker.get("detail") or "")

    # -- opening -------------------------------------------------------
    def _cmd_choose_opening(self, p):
        self._blocked_guard()
        self._require_phase("opening")
        try:
            idx = int(p.get("index"))
        except (TypeError, ValueError):
            raise GameError("MALFORMED_PAYLOAD", 400, "index required")
        if not 0 <= idx < len(self.opening_offers):
            raise GameError("OFFER_OUT_OF_RANGE", 400, "index %d" % idx)
        offer = self.opening_offers[idx]
        hum_pkg = opening_mod.package_of(self.catalog, offer["team_id"])
        opp_team, _ = opening_mod.recorded_team_of(self.game, self.opponent)
        opp_pkg = opening_mod.package_of(self.catalog, opp_team)
        pkgs = {self.human: hum_pkg, self.opponent: opp_pkg}
        provenance = (
            ("game", "audit_game_v1"), ("replay_id", self.replay_id),
            ("opponent_player", str(self.opponent)),
            ("human_opening_team", str(offer["team_id"])),
            ("human_opening_source", offer["source"]),
            ("opponent_opening_team", str(opp_team)),
            ("generator", opening_mod.GENERATOR_VERSION),
        )
        state = opening_mod.build_initial_state(
            pkgs[0], pkgs[1], provenance=provenance, eco=self.eco)
        self.env.reset(state)
        # round-1 income (Income200r round tick at deploy start)
        inc = tuple(int(self.env.income_policy.income(
            side, state.players[side], 1, None)) for side in (0, 1))
        self.env.add_incomes(inc)
        self._income_last = inc
        for side in (0, 1):
            self._ledger.append({"player": side, "reason": "income_r1",
                                 "amount": inc[side], "entity_id": None, "round": 1})
        self.round = 1
        self.phase = "deployment"
        self._last_dict = state_to_dict(self.env.state)
        self.version += 1
        self._emit_audit("ACTION_ACCEPTED", player=self.human,
                         detail="CHOOSE_OPENING team %s (%s)"
                                % (offer["team_id"], offer["source"]),
                         changed_paths=("players", "round"),
                         digest_after=state_digest(self.env.state))
        self._emit_audit("ROUND_ADVANCED", player=None, detail="round 1 (opening)",
                         digest_after=state_digest(self.env.state),
                         context={"incomes": inc})
        return self.view(), None

    # -- reinforcement -------------------------------------------------
    def _cmd_choose_reinforcement(self, p):
        self._blocked_guard()
        self._require_phase("reinforcement")
        try:
            idx = int(p.get("index"))
        except (TypeError, ValueError):
            raise GameError("MALFORMED_PAYLOAD", 400, "index required")
        offers = self._reinforce_offer_views(self.round)
        if idx == -1:
            item_id = 0
        elif 0 <= idx < len(offers):
            if not offers[idx]["supported"]:
                return self._rejected_receipt(
                    "choose_reinforce",
                    "UNSUPPORTED_REINFORCEMENT_%s" % (offers[idx]["unsupported_reason"] or ""),
                    "card %s unsupported" % offers[idx]["card_id"])
            item_id = offers[idx]["card_id"]
        else:
            raise GameError("OFFER_OUT_OF_RANGE", 400, "index %d" % idx)
        # opponent historical pick for this round (norm stream entry)
        opp_entries = [e for e in
                       (self._round_rec(self.opponent, self.round) or {}).get(
                           "actions_norm") or []
                       if e.get("t") == "reinforce"]
        plans = [CanonicalActionPlan(player=self.human, actions=(
            CanonicalAction(ActionKind.CHOOSE_REINFORCE,
                            ChooseReinforceArgs(item_id=item_id)),))]
        opp_item = None
        if opp_entries:
            opp_plan, _ = canonicalize_plan(self.opponent, opp_entries)
            plans.append(opp_plan)
            opp_item = opp_entries[0].get("id")
        before_digest = state_digest(self.env.state)
        base = copy_state(self.env.state)
        res = deploy_transition(base, tuple(plans), self.eco)
        rec_h = res.receipts[0][0] if res.receipts and res.receipts[0] else None
        rec_o = res.receipts[1][0] if len(res.receipts) > 1 and res.receipts[1] else None
        if rec_h is not None and not rec_h.accepted:
            return self._rejected_receipt("choose_reinforce", rec_h.reason_code,
                                          rec_h.detail)
        if rec_o is not None and not rec_o.accepted:
            # opponent historical pick failed in the diverged timeline:
            # atomic rollback (official state untouched) + BLOCKED
            self._commit_block(rec_o, {"raw": opp_entries[0]}, "reinforcement")
            return self.view(), None
        self._commit_state(res.state)
        self._record_ledgers(res.ledgers, self.round)
        seq = len(self._receipts)
        if rec_h is not None:
            self._receipts.append(receipt_to_dict(rec_h, seq, self.human, self.round))
        if rec_o is not None:
            self._receipts.append(receipt_to_dict(rec_o, seq + 1, self.opponent,
                                                  self.round))
        self._reinforce_done_round = self.round
        self.phase = "deployment"
        self._undo_stack = []
        self.version += 1
        self._emit_audit("ACTION_ACCEPTED", player=self.human,
                         detail="CHOOSE_REINFORCEMENT item %s" % item_id,
                         digest_before=before_digest,
                         digest_after=state_digest(self.env.state))
        if opp_item is not None:
            self._emit_audit("REPLAY_ACTION_CANONICALIZED", player=self.opponent,
                             detail="historical reinforce item %s" % opp_item)
        return self.view(), None

    def _rejected_receipt(self, kind, reason, detail=""):
        r = {"seq": len(self._receipts), "round": self.round, "player": self.human,
             "action_index": 0, "kind": kind, "accepted": False,
             "reason_code": reason, "resource_delta": 0,
             "created_entity_id": None, "removed_entity_id": None,
             "changed_paths": [], "detail": detail or ""}
        self._emit_audit("ACTION_REJECTED", player=self.human,
                         detail="%s: %s" % (kind, reason))
        return self.view(), r

    # -- deployment ------------------------------------------------------
    def _cmd_apply_action(self, p):
        self._blocked_guard()
        self._require_phase("deployment")
        if self.env.state.finished_deploy[self.human]:
            return self._rejected_receipt("end_deploy", "PLAYER_ALREADY_FINISHED",
                                          "already finished")
        act = action_from_json(p.get("action") or {})
        if act.kind not in APPLYABLE_KINDS:
            raise GameError("UNKNOWN_ACTION_KIND", 400,
                            "kind %s not applyable" % act.kind)
        before_digest = state_digest(self.env.state)
        plan = CanonicalActionPlan(player=self.human, actions=(act,))
        res = deploy_transition(self.env.state, (plan,), self.eco)
        rec = res.receipts[0][0]
        if not rec.accepted:
            # rejection invariant: official state untouched, version unchanged
            assert state_digest(self.env.state) == before_digest
            return self._rejected_receipt(rec.kind, rec.reason_code, rec.detail)
        checkpoint = (self._snapshot(), len(self._receipts), len(self._ledger))
        self._commit_state(res.state)
        self._record_ledgers(res.ledgers, self.round)
        self._receipts.append(receipt_to_dict(rec, len(self._receipts),
                                              self.human, self.round))
        self._undo_stack.append(checkpoint)
        self.version += 1
        self._emit_audit("ACTION_ACCEPTED", player=self.human,
                         detail="%s %s" % (rec.kind, rec.detail),
                         changed_paths=rec.changed_paths,
                         digest_before=before_digest,
                         digest_after=state_digest(self.env.state))
        return self.view(), None

    def _cmd_undo(self, p):
        self._blocked_guard()
        self._require_phase("deployment")
        if not self._undo_stack:
            return self._rejected_receipt("undo", "UNDO_EMPTY", "no checkpoint")
        snap, n_rec, n_led = self._undo_stack.pop()
        before_digest = state_digest(self.env.state)
        self.env.load(snap)
        del self._receipts[n_rec:]
        del self._ledger[n_led:]
        self.version += 1
        self._emit_audit("ACTION_UNDO", player=self.human,
                         detail="restored checkpoint",
                         digest_before=before_digest,
                         digest_after=state_digest(self.env.state))
        return self.view(), None

    # -- finish: opponent plan + battle ----------------------------------
    def _cmd_finish_deployment(self, p):
        self._blocked_guard()
        self._require_phase("deployment")
        # 1) human FinishDeploy on the official state
        finish = CanonicalAction(ActionKind.END_DEPLOY, None)
        res = deploy_transition(self.env.state, (CanonicalActionPlan(
            player=self.human, actions=(finish,)),), self.eco)
        rec = res.receipts[0][0]
        if not rec.accepted:
            return self._rejected_receipt(rec.kind, rec.reason_code, rec.detail)
        self._commit_state(res.state)
        self._receipts.append(receipt_to_dict(rec, len(self._receipts),
                                              self.human, self.round))
        self.version += 1
        self._emit_audit("ACTION_ACCEPTED", player=self.human,
                         detail="FinishDeploy",
                         digest_after=state_digest(self.env.state))
        # 2) opponent historical plan, atomically on a clone
        opp_receipts, blocked = self._run_opponent_plan()
        if blocked:
            return self.view(), None
        self._receipts.extend(opp_receipts)
        self.version += 1
        # 3) one battle + settlement + round tick
        self._run_battle_phase()
        return self.view(), None

    def _opponent_entries(self):
        rec = self._round_rec(self.opponent, self.round)
        if rec is None:
            return None, None
        entries = list(rec.get("actions_norm") or [])
        if self._reinforce_done_round == self.round:
            entries = [e for e in entries if e.get("t") != "reinforce"]
        return entries, rec

    def _run_opponent_plan(self):
        """Strict execution of the canonicalized historical plan on a cloned
        state; exp top-ups are audited overrides; any other failure rolls the
        whole plan back and BLOCKs the session (no auto-skip path)."""
        entries, rec = self._opponent_entries()
        if entries is None:
            self._commit_block(None, {"round": self.round},
                               "opponent round %d missing" % self.round)
            return [], True
        plan, crep = canonicalize_plan(self.opponent, entries,
                                       norm_report=rec.get("norm_report"))
        if crep.unresolved_refs:
            self._commit_block(None, {"unresolved": crep.unresolved_refs[:3]},
                               "unresolved refs in opponent plan")
            return [], True
        base = copy_state(self.env.state)
        state = base
        receipts = []
        idx = 0
        finished = False
        for act in plan.actions:
            if act.kind is ActionKind.UPGRADE_UNIT and act.args.ref is not None:
                state = self._exp_override(state, act)
            one = CanonicalActionPlan(player=self.opponent, actions=(act,))
            res = deploy_transition(state, (one,), self.eco)
            r = res.receipts[0][0]
            if not r.accepted:
                # exp overrides aside, historical failures are hard blockers
                self._commit_block(r, self._entry_of(entries, act),
                                   "opponent action rejected")
                return [], True
            state = res.state
            self._record_ledgers(res.ledgers, self.round)
            receipts.append(receipt_to_dict(r, len(self._receipts) + idx,
                                            self.opponent, self.round))
            idx += 1
            if act.kind is ActionKind.END_DEPLOY:
                finished = True
        if not finished:
            self._commit_block(None, {"entries": len(entries)},
                               "opponent plan has no FinishDeploy")
            return [], True
        if state.phase is not Phase.PRE_BATTLE:
            self._commit_block(None, {"phase": state.phase.value},
                               "opponent plan did not reach PRE_BATTLE")
            return [], True
        self._commit_state(state)
        self._emit_audit("REPLAY_ACTION_CANONICALIZED", player=self.opponent,
                         detail="%d historical actions executed" % len(plan.actions),
                         digest_after=state_digest(state))
        return receipts, False

    @staticmethod
    def _entry_of(entries, act):
        k = act.raw_index
        if 0 <= k < len(entries):
            return entries[k]
        return {"t": str(act.kind)}

    def _exp_override(self, state, act):
        """G11: before a historical UpgradeUnit, top the unit's exp up to the
        level's threshold (pysim battle exp differs from the real fight).
        Only the difference is granted; every override is an audit event."""
        ref = act.args.ref
        p = state.players[self.opponent]
        unit = None
        for u in p.units:
            if ref.handle is not None and u.replay_index == ref.handle:
                unit = u
                break
        if unit is None:
            return state
        need = self.eco.upgrade_exp_need(unit.mech_id, unit.level)
        if not need or need <= 0 or unit.exp >= need:
            return state
        units = tuple(UnitCard(**{**u.__dict__, "exp": need})
                      if u is unit else u for u in p.units)
        new_p = PlayerState(**{**p.__dict__, "units": units})
        players = tuple(new_p if i == self.opponent else pl
                        for i, pl in enumerate(state.players))
        st = EnvironmentState(
            schema_version=state.schema_version,
            ruleset_version=state.ruleset_version,
            engine_version=state.engine_version, round=state.round,
            phase=state.phase, players=players,
            finished_deploy=state.finished_deploy,
            next_entity_id=state.next_entity_id,
            terminal_reason=state.terminal_reason,
            provenance=state.provenance)
        self._emit_audit("OPPONENT_EXP_OVERRIDE", player=self.opponent,
                         detail="unit handle %d exp %d -> %d (threshold)"
                                % (ref.handle, unit.exp, need),
                         changed_paths=("players[%d].units[entity=%d].exp"
                                        % (self.opponent, unit.entity_id),),
                         context={"entity_id": unit.entity_id,
                                  "before": unit.exp, "required": need,
                                  "delta": need - unit.exp})
        return st

    def _commit_block(self, receipt, entry, detail):
        self.phase = "blocked"
        self.stop_reason = "OPPONENT_PLAN_FAILED"
        self.blocker = {
            "detail": detail,
            "receipt": receipt_to_dict(receipt, len(self._receipts),
                                       self.opponent, self.round)
            if receipt is not None else None,
            "entry": entry,
        }
        self.version += 1
        self._emit_audit("SESSION_BLOCKED", player=self.opponent,
                         detail=detail,
                         context={"entry": entry})

    def _run_battle_phase(self):
        hp_before = tuple(p.hp for p in self.env.state.players)
        seed = self._next_battle_seed()
        self._battle_seed = seed
        self._emit_audit("BATTLE_STARTED", player=None,
                         detail="pysim battle seed %d round %d" % (seed, self.round))
        result = self.env.finish_round(seed, with_trace=True)
        outcome = result.battle_outcome
        extra = result.info.get("battle_extra") or {}
        # fast-supply debts recorded from this round's ledger
        self._scan_fast_supply()
        self.battle = {
            "round": self.round, "battle_seed": seed,
            "winner": outcome.winner,
            "winner_label": {0: "human" if self.human == 0 else "opponent",
                             1: "human" if self.human == 1 else "opponent",
                             -1: "deuce"}[outcome.winner],
            "score_by_team": list(outcome.score_by_team),
            "damage_to_player": list(outcome.damage_to_player),
            "hp_before": list(hp_before),
            "hp_after": tuple(p.hp for p in result.state.players)
            if result.state.phase is not Phase.TERMINAL
            else tuple(p.hp for p in result.state.players),
            "reward": list(result.reward),
            "end_time": outcome.end_time,
            "engine_version": outcome.engine_version,
            "cards": [self._card_view(c) for c in outcome.cards],
            "trace": extra.get("trace") or [],
            "towers_down": extra.get("towers_down") or {},
            "survivors": extra.get("survivors") or {},
            "replay_oracle": self._replay_oracle(self.round),
            "note": "pysim 模拟结果 (非真实对局胜负)",
        }
        self._emit_audit("BATTLE_SETTLED", player=None,
                         detail="winner=%s damage=%s" % (
                             outcome.winner, tuple(outcome.damage_to_player)),
                         digest_after=state_digest(self.env.state))
        if result.done:
            self.phase = "terminal"
            reason = result.state.terminal_reason
            self.stop_reason = {
                "double_ko": "DOUBLE_KO",
                "player0_wins": "PLAYER0_WINS",
                "player1_wins": "PLAYER1_WINS",
                "max_round": "MAX_ROUND"}[reason] if reason else "TERMINAL"
            self.round = result.state.round
            self._emit_audit("SESSION_TERMINAL", player=None,
                             detail="terminal %s" % reason)
        else:
            self.phase = "round_result"
        self.version += 1

    def _scan_fast_supply(self):
        pol = self.env.income_policy
        if hasattr(pol, "record_fast_supply"):
            for e in self._ledger:
                if e["round"] == self.round and e["reason"] == "blueprint_loan:+200":
                    pol.record_fast_supply(e["player"], self.round + 1, 1)

    def _card_view(self, c):
        owner = None
        for side, p in enumerate(self.env.state.players):
            for u in p.units:
                if u.entity_id == c.entity_id:
                    owner = (side, u)
        side, u = owner if owner else (None, None)
        return {
            "handle": u.replay_index if u else None,
            "player": side,
            "role": "human" if side == self.human else
                    ("opponent" if side is not None else "?"),
            "mech_id": u.mech_id if u else None,
            "name": self._mech_name(u.mech_id) if u else "?",
            "exp_before": c.exp_before, "exp_delta": c.exp_delta,
            "exp_after": c.exp_after, "damage": c.damage, "kills": c.kills,
            "survived": c.survived, "level_after": c.level_after,
        }

    def _replay_oracle(self, round_no):
        """The real fight outcome of this round (contrast only)."""
        for pair in self.game.get("pairs", []):
            if int(pair.get("round", -1)) == int(round_no):
                reps = (pair.get("match") or {}).get("reports") or []
                label = pair.get("label")
                return {
                    "label": label,
                    "real_scores": [r.get("score") for r in reps[:2]],
                    "note": "回放真实结果, 仅对照",
                }
        return None

    # -- ack --------------------------------------------------------------
    def _cmd_ack_round_result(self, p):
        self._blocked_guard()
        self._require_phase("round_result", "terminal")
        if self.phase == "terminal":
            self.version += 1
            return self.view(), None
        next_round = self.round + 1
        opp_rec = self._round_rec(self.opponent, next_round)
        if opp_rec is None:
            self.phase = "terminal"
            self.stop_reason = "REPLAY_EXHAUSTED"
            self.version += 1
            self._emit_audit("SESSION_TERMINAL", player=None,
                             detail="no opponent plan for round %d" % next_round)
            return self.view(), None
        # the env already advanced (finish_round ran settle+advance+income)
        self.round = next_round
        self._income_last = self._incomes_of(next_round)
        # opening-team delayed gifts for the human (opponent's come from their
        # own norm stream)
        self._human_gifts(next_round)
        offers = self._offers_for_round(next_round)
        if offers:
            self.phase = "reinforcement"
            self._emit_audit("REINFORCEMENT_OFFERS_INJECTED", player=self.human,
                             detail="round %d offers %s" % (next_round, offers))
        else:
            self.phase = "deployment"
            if next_round >= 2:
                self._commit_block(None, {"round": next_round},
                                   "missing reinforcement offers")
                return self.view(), None
        self._undo_stack = []
        self._reinforce_done_round = None
        self.version += 1
        self._emit_audit("ROUND_ADVANCED", player=None,
                         detail="round %d, incomes %s" % (next_round,
                                                          self._income_last),
                         digest_after=state_digest(self.env.state),
                         context={"incomes": self._income_last})
        return self.view(), None

    def _incomes_of(self, round_no):
        s = self.env.state
        return tuple(int(self.env.income_policy.income(
            side, s.players[side], round_no,
            s.players[side].pre_round_fight_result)) for side in (0, 1))

    def _human_gifts(self, round_no):
        p = self.env.state.players[self.human]
        for officer, (arrival, mech) in GIFT_OFFICERS.items():
            if officer in set(p.officers) and int(arrival) == int(round_no):
                act = CanonicalAction(ActionKind.GIFT_UNIT,
                                      GiftArgs(mech_id=int(mech)))
                res = deploy_transition(self.env.state, (CanonicalActionPlan(
                    player=self.human, actions=(act,)),), self.eco)
                r = res.receipts[0][0]
                if r.accepted:
                    self._commit_state(res.state)
                    self._record_ledgers(res.ledgers, round_no)
                    self._receipts.append(receipt_to_dict(
                        r, len(self._receipts), self.human, round_no))
                    self._emit_audit("ACTION_ACCEPTED", player=self.human,
                                     detail="opening gift officer %d -> mech %d"
                                            % (officer, mech),
                                     digest_after=state_digest(self.env.state))

    # ------------------------------------------------------------ view
    def view(self, audit_tail=200):
        try:
            s = self.env.state
        except Exception:
            s = None
        digest = state_digest(s) if s is not None else None
        diff = []
        if s is not None:
            cur_dict = state_to_dict(s)
            if self._last_dict is not None:
                diff = diff_dicts(self._last_dict, cur_dict, cap=64)
            self._last_dict = cur_dict
        return {
            "schema_version": GAME_VIEW_SCHEMA,
            "session_id": self.id,
            "version": self.version,
            "phase": self.phase,
            "round": self.round,
            "replay": {
                "replay_id": self.replay_id,
                "file_label": self.option.get("file_label", ""),
                "game_version": self.option.get("game_version", ""),
                "opponent_player": self.opponent,
                "opponent_name": self.option.get("opponent_name", ""),
                "human_player": self.human,
                "human_name": self.option.get("human_name", ""),
                "system_seed": self.system_seed,
                "battle_seed_base": self.battle_seed_base,
                "playable_through_round": self.option.get("playable_through_round"),
            },
            "players": self._player_views(s),
            "opening_offers": self.opening_offers if self.phase == "opening" else [],
            "reinforcement_offers": (self._reinforce_offer_views(self.round)
                                     if self.phase == "reinforcement" else []),
            "legal_actions": self._legal_view(s),
            "receipts": self._receipts[-160:],
            "ledger": [e for e in self._ledger if e["round"] == self.round],
            "audit_events": self._audit[-audit_tail:],
            "state_digest": digest,
            "state_diff": diff,
            "invariants": "ok" if s is not None else "n/a",
            "historical_actions": self._historical_view(),
            "battle": self.battle,
            "stop_reason": self.stop_reason,
            "blocker": self.blocker,
        }

    def _player_views(self, s):
        out = []
        for side in (0, 1):
            if s is None:
                out.append({"role": "human" if side == self.human else "opponent",
                            "name": self.game["players"][side].get("name", "")})
                continue
            p = s.players[side]
            units = []
            for u in p.units:
                up_price = self.eco.upgrade_price(u.mech_id)
                up_price = max(0, (up_price or 0) +
                               self.eco.upgrade_price_mod(u.mech_id, p.officers))
                units.append({
                    "handle": u.replay_index,
                    "mech_id": u.mech_id, "name": self._mech_name(u.mech_id),
                    "level": u.level, "exp": u.exp,
                    "exp_need": max(0, self.eco.upgrade_exp_need(u.mech_id, u.level)
                                    if u.level < 9 else 0),
                    "x": u.x, "y": u.y, "is_rotate": u.is_rotate,
                    "sell_supply": u.sell_supply,
                    "equipment_id": u.equipment_id,
                    "upgrade_price": up_price if u.level < 9 else None,
                    "round_count": u.round_count,
                })
            out.append({
                "role": "human" if side == self.human else "opponent",
                "name": self.game["players"][side].get("name", ""),
                "hp": p.hp, "max_hp": p.max_hp, "supply": p.supply,
                "income_last": self._income_last[side],
                "pre_round_fight_result": p.pre_round_fight_result,
                "finished_deploy": s.finished_deploy[side],
                "units": units,
                "unlocked_mechs": sorted(p.unlocked_mechs),
                "tech_map": [[m, list(t)] for m, t in p.tech_map],
                "officers": [self._officer_name(o) for o in p.officers],
                "blueprints": list(p.blueprints),
                "tower_strengthen": list(p.tower_strengthen),
                "constructions": [list(c) for c in p.constructions_raw],
            })
        return out

    def _legal_view(self, s):
        """UI-facing legality summary — every number traces to eco/gamedata;
        the frontend never re-implements a price."""
        if s is None or self.phase not in ("deployment", "reinforcement"):
            return {}
        p = s.players[self.human]
        buy, unlock, tech = [], [], []
        for mech in sorted(set(self.gd.cards)):
            card = self.gd.cards[mech]
            if mech in p.unlocked_mechs:
                price = (self.eco.buy_price(mech) or 0) \
                    + self.eco.buy_price_mod(mech, p.officers)
                buy.append({"mech_id": mech, "name": card.name,
                            "price": max(0, price),
                            "affordable": p.supply >= max(0, price),
                            "slot_size": card.slot_size,
                            "mech_count": card.mech_count})
            else:
                price = self.eco.unlock_price(mech)
                if price is not None:
                    unlock.append({"mech_id": mech, "name": card.name,
                                   "price": price,
                                   "affordable": p.supply >= price})
        for mech, owned in p.tech_map:
            card = self.gd.cards.get(mech)
            owned = set(owned)
            for tid in (card.technologies if card else ()):
                if tid in owned:
                    continue
                td = self.gd.techs.get(tid)
                if td is None:
                    continue
                ok = not td.previous_tech_id or td.previous_tech_id in owned
                price = self.eco.tech_price(mech, tid, len(owned))
                tech.append({"mech_id": mech, "tech_id": tid,
                             "name": td.name if hasattr(td, "name") else str(tid),
                             "price": price, "prerequisite_ok": ok,
                             "affordable": price is not None and p.supply >= price,
                             "owned": False})
        towers = []
        for k in (0, 1):
            lv = p.tower_strengthen[k] if k < len(p.tower_strengthen) else 0
            towers.append({"index": k, "level": lv,
                           "cost": TOWER_STRENGTHEN_COST,
                           "affordable": p.supply >= TOWER_STRENGTHEN_COST,
                           "max": TOWER_MAX_LEVEL})
        n5 = sum(1 for s in (p.tower_mods_raw or ()) if int(s) == 5)
        n6 = sum(1 for s in (p.tower_mods_raw or ()) if int(s) == 6)
        tower_skills = [
            {"skill_id": 5, "name": "强化瞄准", "cost": 0,
             "detail": "本回合全体远程射程 +15 (可叠加)",
             "active_count": n5, "affordable": True},
            {"skill_id": 6, "name": "高速移动", "cost": 0,
             "detail": "本回合全体移速 +3 (可叠加)",
             "active_count": n6, "affordable": True},
        ]
        # releasable battlefield skills from the player's inventory
        releases = []
        try:
            from pysim.skills import COMMANDER_SKILLS
            for e in p.commander_skills_raw or ():
                try:
                    sid = int(e[1])
                except (TypeError, ValueError):
                    continue
                d = COMMANDER_SKILLS.get(sid)
                if d:
                    releases.append({"skill_id": sid, "name": d.get("name", str(sid)),
                                     "kind": d["kind"],
                                     "released_this_round": sum(
                                         1 for r in (p.skill_events_raw or ())
                                         if int(r[0]) == sid)})
        except Exception:
            pass
        blueprints = []
        for bid, info in BLUEPRINT_INFO.items():
            cost = BLUEPRINT_COSTS.get(bid, info["cost"])
            owned = bid in set(p.blueprints)
            blueprints.append({"id": bid, "name": info["name"], "cost": cost,
                               "owned": owned,
                               "affordable": p.supply >= cost,
                               "detail": info["detail"],
                               "supported": True})
        return {
            "buy": buy, "unlock": unlock, "tech": tech,
            "towers": towers, "tower_skills": tower_skills,
            "skill_releases": releases,
            "blueprints": blueprints,
            "map_bounds": {"x": 350.0, "y": 300.0},
            "finished": s.finished_deploy[self.human],
        }

    def _historical_view(self):
        """Original players' actions of the current round (contrast only)."""
        if self.round <= 0:
            return {"round": 0, "players": []}
        out = {"round": self.round, "players": []}
        for side in (0, 1):
            rec = self._round_rec(side, self.round)
            raw = rec.get("actions") or [] if rec else []
            norm = rec.get("actions_norm") or [] if rec else []
            out["players"].append({
                "side": side,
                "role": "human" if side == self.human else "opponent",
                "raw": [{"type": a.get("type"),
                         **{"arg": {k: v for k, v in a.items()
                                    if k not in ("type",)}}} for a in raw],
                "norm_kinds": [e.get("t") for e in norm],
            })
        out["real_fight"] = self._replay_oracle(self.round)
        return out


# ---------------------------------------------------------------- store
class GameSessionStore:
    """In-memory session registry; lost on restart (任务书 §1.4)."""

    def __init__(self, library, gd, eco):
        self.library = library      # GameLibrary
        self.gd = gd
        self.eco = eco
        self.catalog = library.catalog
        self._sessions = {}
        self._lock = threading.Lock()

    def create(self, replay_id, opponent_player, battle_seed_base=None,
               min_rounds=MIN_ROUNDS_DEFAULT):
        option = self.library.option(replay_id, opponent_player)
        if option is None:
            raise GameError("REPLAY_OPTION_NOT_FOUND", 404,
                            "replay %s opponent %s" % (replay_id, opponent_player))
        if option.get("playable_through_round", 0) < min_rounds:
            raise GameError("REPLAY_OPTION_DISABLED", 409,
                            "playable_through_round %s < %s"
                            % (option.get("playable_through_round"), min_rounds))
        shard = self.library.shard(option)
        sid = uuid.uuid4().hex[:16]
        sess = GameSession(sid, shard, option, self.gd, self.eco,
                           self.catalog, min_rounds=min_rounds,
                           battle_seed_base=battle_seed_base)
        with self._lock:
            self._sessions[sid] = sess
        return sess

    def get(self, session_id):
        sess = self._sessions.get(session_id)
        if sess is None:
            raise GameError("SESSION_NOT_FOUND", 404, session_id)
        return sess

    def delete(self, session_id):
        with self._lock:
            sess = self._sessions.pop(session_id, None)
        if sess is None:
            raise GameError("SESSION_NOT_FOUND", 404, session_id)
        return True

    def count(self):
        return len(self._sessions)
