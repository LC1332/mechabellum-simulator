# Economy: prices, reinforcement-item table, income policies, supply ledger.
#
# Price policy `prices_v0_raw` (frozen 2026-08-26, information/科技的购买价格.md
# + data/gamedata.json):
#   buy      = card.baseMoney            (officer discounts NOT modeled yet)
#   upgrade  = card.baseMoney // 2       (exp gate/consumption lives in deploy)
#   unlock   = card.unlockPrice          (one-time)
#   tech     = tech.supply + 200 * owned_active_techs(mech)
#   sell     = unit.sell_supply          (field from the replay snapshot)
# Income policy: see IncomePolicy docstrings; replay mode injects per-round
# income derived from snapshots (logged) because base+win+expert components
# are not yet fully reverse-engineered.
import json
import os
import re
from dataclasses import dataclass, field

from ..gamedata import GameData
from .model import SupplyEntry, SupplyLedger

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REINFORCE_JSON = os.path.join(_ROOT, "information", "增援卡牌-回放全量信息.json")

_LEVEL_PRICES = {1: 0, 2: 50, 3: 100, 4: 200}


def load_price_mods(path=_REINFORCE_JSON):
    """officer_id -> {'mech', 'buy', 'upgrade'} from 单位强化卡 descriptions.

    Corpus-verified description grammar (2026-08-26):
      招募价格减少/增加N -> buy price -/+N; 升级价格减少N -> upgrade -N.
    The mech resolves by longest gamedata card-name substring of the item
    name (量产堡垒 -> 堡垒, 长弓补贴 -> 长弓, 改进型钢球 -> 钢球)."""
    raw = json.load(open(path, encoding="utf8"))
    _gd = json.load(open(os.path.join(_ROOT, "data", "gamedata.json"),
                         encoding="utf8"))
    gd_names = {c.get("name"): int(c.get("mechID", 0))
                for c in _gd["cards"].values()}
    mods = {}
    for c in raw.get("cards", []):
        if c.get("类别") != "单位强化卡":
            continue
        name = str(c.get("名称") or "")
        desc = str(c.get("描述") or "")
        mech = None
        for cname, mid in sorted(gd_names.items(),
                                 key=lambda kv: -len(kv[0])):
            if cname and cname in name:
                mech = mid
                break
        if mech is None:
            continue
        entry = {"mech": mech, "buy": 0, "upgrade": 0}
        for m in re.finditer(r"(招募|升级)价格(减少|增加)(\d+)", desc):
            amount = int(m.group(3)) * (-1 if m.group(2) == "减少" else 1)
            entry["buy" if m.group(1) == "招募" else "upgrade"] += amount
        if entry["buy"] or entry["upgrade"]:
            mods[int(c["id"])] = entry
    return mods


def load_reinforce_items(path=_REINFORCE_JSON):
    """id -> {cost, kind, grant:{mech,count,level}} from the survey table.

    Cost rule (game reinforceItemPrices, survey-verified): explicit supply>=0
    wins; otherwise priced by card level (1=0, 2=50, 3=100, 4=200).
    Grant decoding: 单位获得卡 descriptions read 立即获得{count}队{level}级{name};
    the mech id resolves through gamedata card names."""
    raw = json.load(open(path, encoding="utf8"))
    gd_names = {}
    gamedata = os.path.join(_ROOT, "data", "gamedata.json")
    try:
        _gd = json.load(open(gamedata, encoding="utf8"))
        gd_names = {c.get("name"): int(c.get("mechID", 0))
                    for c in _gd["cards"].values()}
    except (OSError, KeyError, ValueError):
        gd_names = {}
    items = {}
    for c in raw.get("cards", []):
        cid = int(c["id"])
        cost = c.get("supply", c.get("费用"))
        if cost is None or cost < 0:
            cost = _LEVEL_PRICES.get(int(c.get("level", c.get("等级", 1)) or 1), 0)
        entry = {"cost": int(cost or 0), "kind": c.get("类别", "?"),
                 "name": c.get("名称", "")}
        params = str(c.get("描述参数") or "")
        if c.get("类别") == "单位获得卡" and ";" in params:
            try:
                count, level = params.split(";")[:2]
                mech = gd_names.get(c.get("名称"))
                if mech:
                    entry["grant"] = {"mech": mech, "count": int(count),
                                      "level": int(level)}
            except ValueError:
                pass
        items[cid] = entry
    return items


class Economy:
    """Price source shared by legality, deploy and the oracle tools."""

    def __init__(self, gd: GameData, items=None):
        self.gd = gd
        self.items = items if items is not None else load_reinforce_items()
        self.price_mods = load_price_mods()

    def buy_price(self, mech_id: int) -> int | None:
        c = self.gd.cards.get(int(mech_id))
        return int(c.base_money) if c else None

    def upgrade_price(self, mech_id: int) -> int | None:
        p = self.buy_price(mech_id)
        return None if p is None else p // 2

    def buy_price_mod(self, mech_id: int, officers) -> int:
        """Sum of active 单位强化卡 buy-price modifiers for this mech."""
        offs = set(officers or ())
        return sum(mod["buy"] for oid, mod in self.price_mods.items()
                   if mod["mech"] == int(mech_id) and oid in offs)

    def upgrade_price_mod(self, mech_id: int, officers) -> int:
        offs = set(officers or ())
        return sum(mod["upgrade"] for oid, mod in self.price_mods.items()
                   if mod["mech"] == int(mech_id) and oid in offs)

    def unlock_price(self, mech_id: int) -> int | None:
        c = self.gd.cards.get(int(mech_id))
        return int(c.unlock_price) if c else None

    def tech_price(self, mech_id: int, tech_id: int, owned_count: int) -> int | None:
        """Tech price rule `prices_v1_tech` (unbiased corpus refit
        2026-08-26 on price-known windows): supply + 200 * owned_active,
        where owned counts the mech's ACTIVE techs at purchase time (the
        snapshot techMap incl. card defaults — NOT this round's own buys,
        see the replay2json pre-round techMap fix)."""
        t = self.gd.techs.get(int(tech_id))
        if t is None:
            return None
        return int(t.supply) + 200 * int(owned_count)

    def item_cost(self, item_id: int) -> int | None:
        """0 for the skip choice (ID=0); None for unknown items."""
        if item_id == 0:
            return 0
        e = self.items.get(int(item_id))
        return int(e["cost"]) if e else None

    def item_grant(self, item_id: int):
        """(mech_id, count, level) for 单位获得卡, else None."""
        e = self.items.get(int(item_id)) if item_id else None
        g = (e or {}).get("grant")
        return (g["mech"], g["count"], g["level"]) if g else None

    def upgrade_exp_need(self, mech_id: int, current_level: int) -> int:
        """Exp consumed to go from canonical `current_level` to +1.

        Returns 0 when no threshold is known (free upgrade) or at the cap."""
        if current_level >= 9:
            return -1          # caller maps this to MAX_LEVEL
        e = self.gd.exps.get(int(mech_id))
        if e is None:
            return 0
        return int(e.upgrade_at[min(9, current_level + 1)] or 0)


@dataclass
class LedgerBuilder:
    supply_before: int
    entries: list = field(default_factory=list)

    def add(self, reason, amount, action_index=None, entity_id=None):
        self.entries.append(SupplyEntry(reason=reason, amount=int(amount),
                                        action_index=action_index,
                                        entity_id=entity_id))

    def build(self) -> SupplyLedger:
        led = SupplyLedger(supply_before=self.supply_before,
                           entries=tuple(self.entries))
        return led


class IncomePolicy:
    """Round income rule. The full real rule (base schedule + win/lose bonus +
    economy experts + loan side-effects) is not yet frozen; v0 ships two
    explicitly-named, versioned policies."""

    name = "income_policy_v0_base"

    def income(self, player_index: int, player_state, round_no: int,
               prev_result) -> int:
        raise NotImplementedError


class InjectedIncome(IncomePolicy):
    """Replay mode: per-(player, round) income supplied by the runner.

    Derived from replay snapshots + raw prices, so historical actions stay
    legal; every injected amount is recorded in the round ledger with reason
    'income_injected' and must be surfaced in trajectory logs."""

    name = "income_injected_v0"

    def __init__(self, table=None):
        # table[(player, round)] = income added at that round's deploy start
        self.table = dict(table or {})

    def set(self, player: int, round_no: int, amount: int):
        self.table[(int(player), int(round_no))] = int(amount)

    def income(self, player_index: int, player_state, round_no: int,
               prev_result) -> int:
        return self.table.get((int(player_index), int(round_no)), 0)


class FixedIncome(IncomePolicy):
    """Sandbox rule: constant income each round (default of the real game's
    early rounds; used by random rollouts only)."""

    name = "income_fixed_sandbox_v0"

    def __init__(self, amount=200):
        self.amount = int(amount)

    def income(self, player_index: int, player_state, round_no: int,
               prev_result) -> int:
        return self.amount if round_no >= 1 else 0


# ---------------------------------------------------------------- income v1
# income_rule_200r_v1 (frozen 2026-08-26 from the 1106-game corpus +
# user rulings Q8/Q10): income(r) = 200*r + expert bonuses - fast-supply
# debt; NO win/lose/draw difference (Win/Lose/Deuce income distributions
# are indistinguishable in the corpus).
EXPERT_INCOME_PER_ROUND = {
    10002: 50,     # 补给专家 (user Q10)
    10003: 150,    # 超级补给 (user Q10)
    20034: 100,    # 成本控制 (user Q10)
    20007: 50,     # 补给强化: +50 from the round AFTER acquisition; the
                   # officers snapshot already reflects that timing
}
FAST_SUPPLY_FIRST_ROUND_BONUS = 200   # 10010 快速补给专家, round 1 only
FAST_SUPPLY_DEBT = 300                # blueprint 1: -300 next round (Q8)
REINFORCE_SKIP_BONUS = 50             # skipping all 4 offers (Q4): +50


class Income200r(IncomePolicy):
    """Real-rule income: 200*r + experts - fast debts. The fast-supply
    debt for (player, round) is registered by the runner/env when the
    blueprint survives the round's undo folding (record_fast_supply)."""

    name = "income_200r_v1"

    def __init__(self):
        self.fast_debts = {}          # (player, round) -> activations

    def record_fast_supply(self, player: int, next_round: int, count: int = 1):
        key = (int(player), int(next_round))
        self.fast_debts[key] = self.fast_debts.get(key, 0) + int(count)

    def income(self, player_index: int, player_state, round_no: int,
               prev_result) -> int:
        inc = 200 * int(round_no)
        officers = set(player_state.officers or ())
        for oid, bonus in EXPERT_INCOME_PER_ROUND.items():
            if oid in officers:
                inc += bonus
        if 10010 in officers and int(round_no) == 1:
            inc += FAST_SUPPLY_FIRST_ROUND_BONUS
        inc -= FAST_SUPPLY_DEBT * self.fast_debts.get(
            (int(player_index), int(round_no)), 0)
        return inc
