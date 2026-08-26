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
from dataclasses import dataclass, field

from ..gamedata import GameData
from .model import SupplyEntry, SupplyLedger

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REINFORCE_JSON = os.path.join(_ROOT, "information", "增援卡牌-回放全量信息.json")

_LEVEL_PRICES = {1: 0, 2: 50, 3: 100, 4: 200}


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

    def buy_price(self, mech_id: int) -> int | None:
        c = self.gd.cards.get(int(mech_id))
        return int(c.base_money) if c else None

    def upgrade_price(self, mech_id: int) -> int | None:
        p = self.buy_price(mech_id)
        return None if p is None else p // 2

    def unlock_price(self, mech_id: int) -> int | None:
        c = self.gd.cards.get(int(mech_id))
        return int(c.unlock_price) if c else None

    def tech_price(self, mech_id: int, tech_id: int, owned_count: int) -> int | None:
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
