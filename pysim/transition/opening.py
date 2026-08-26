# Opening (round 0 ChooseAdvanceTeam) for the audit game: catalog-driven
# package execution + deterministic candidate generation.
#
# The raw replay records ONLY the chosen {Index, ID}; the other three
# candidates are lost. Recorded packages are induced offline from
# ChooseAdvanceTeam -> round-1 snapshot evidence (tools/build_opening_catalog.py);
# session runtime reads the catalog, never the live round-1 snapshot.
# Generated candidates come from opening_offer_generator_v1 (stable seed,
# catalog packages, simulator-generated badge) — they do NOT claim to
# reconstruct the game's unrecorded RNG.
import hashlib
import json
import os

from .model import (EnvironmentState, PlayerState, UnitCard, Phase,
                    SCHEMA_VERSION, RULESET_VERSION, ENGINE_VERSION)

CATALOG_SCHEMA = "opening_catalog_v1"
GENERATOR_VERSION = "opening_offer_generator_v1"


class OpeningError(Exception):
    pass


def load_catalog(path):
    data = json.load(open(path, encoding="utf8"))
    if data.get("schema_version") != CATALOG_SCHEMA:
        raise OpeningError("catalog schema %s != %s"
                           % (data.get("schema_version"), CATALOG_SCHEMA))
    return data


def package_of(catalog, team_id):
    pkg = (catalog.get("packages") or {}).get(str(int(team_id)))
    if pkg is None:
        raise OpeningError("team %s not in opening catalog" % team_id)
    return pkg


def generator_seed(ruleset_version, system_seed, player_seed, replay_id,
                   player_index):
    """Stable seed per 任务书 G3: hash(ruleset, systemSeed, playerSeed,
    replay_id, player_index)."""
    h = hashlib.sha256("|".join(str(x) for x in (
        ruleset_version, system_seed, player_seed, replay_id,
        player_index)).encode("utf8"))
    return int(h.hexdigest()[:16], 16)


def generate_offers(catalog, recorded_team_id, recorded_index, seed):
    """4 opening offers: the recorded package pinned at its original Index,
    three others drawn without replacement from the catalog.

    Deterministic for the same seed; avoids duplicate team ids. Returns a
    list of (index, team_id, source) sorted by index."""
    teams = [int(t) for t in (catalog.get("packages") or {})
             if int(t) != int(recorded_team_id)]
    # decimated reproducible shuffle (no random module state)
    def prng():
        s = seed & 0xFFFFFFFF
        while True:
            s = (1103515245 * s + 12345) & 0x7FFFFFFF
            yield s >> 8
    g = prng()
    shuffled = []
    pool = list(teams)
    while pool:
        shuffled.append(pool.pop(next(g) % len(pool)))
    n = 4
    idxs = [i for i in range(n) if i != int(recorded_index)]
    out = [(int(recorded_index), int(recorded_team_id), "replay_recorded")]
    for slot, team in zip(idxs, shuffled):
        out.append((slot, team, "generated_v1"))
    return sorted(out)


def _units_of(pkg, start_entity_id, eco):
    units = []
    eid = start_entity_id
    idx = 0
    for grp in pkg.get("units", []):
        mech = int(grp["mech"])
        level = int(grp.get("level", 1))          # canonical 1-based
        for (x, y) in grp.get("formation", []):
            price = eco.buy_price(mech) if eco else 0
            units.append(UnitCard(
                entity_id=eid, mech_id=mech, level=max(1, level), exp=0,
                x=float(x), y=float(y),
                is_rotate=bool(grp.get("is_rotate", False)),
                sell_supply=int(price or 0), replay_index=idx))
            eid += 1
            idx += 1
    return units, eid


def player_state_from_package(pkg, start_entity_id=1, eco=None) -> PlayerState:
    units, next_id = _units_of(pkg, start_entity_id, eco)
    unlocked = {int(m) for m in pkg.get("unlocked", [])}
    unlocked |= {u.mech_id for u in units}
    cons = tuple(tuple(str(x) for x in c)
                 for c in pkg.get("constructions", []))
    hp = int(pkg.get("hp", 4500))
    cs = tuple(tuple(str(x) for x in s)
               for s in pkg.get("commander_skills", []))
    return PlayerState(
        hp=hp, max_hp=int(pkg.get("max_hp", max(hp, 4500))),
        supply=int(pkg.get("supply", 0)), pre_round_fight_result=None,
        units=tuple(units), unlocked_mechs=frozenset(unlocked),
        tech_map=tuple(sorted(
            (int(m), tuple(int(t) for t in lst))
            for m, lst in (pkg.get("tech_map") or {}).items())),
        officers=tuple(int(o) for o in pkg.get("officers", [])),
        blueprints=tuple(int(b) for b in pkg.get("blueprints", [])),
        commander_skills_raw=cs,
        tower_strengthen=(0, 0), constructions_raw=cons), next_id


def build_initial_state(pkg0, pkg1, provenance=(), eco=None) -> EnvironmentState:
    """Round-1 DEPLOYMENT state from two opening packages (rule execution,
    NOT round-1 snapshot backfill). Round-1 income is added separately by
    the session (income policy), matching the replay's snapshot timing."""
    p0, next_id = player_state_from_package(pkg0, start_entity_id=1, eco=eco)
    p1, _ = player_state_from_package(pkg1, start_entity_id=next_id, eco=eco)
    top = max([u.entity_id for u in p1.units], default=next_id) + 1
    st = EnvironmentState(
        schema_version=SCHEMA_VERSION, ruleset_version=RULESET_VERSION,
        engine_version=ENGINE_VERSION, round=1, phase=Phase.DEPLOYMENT,
        players=(p0, p1), finished_deploy=(False, False), next_entity_id=top,
        provenance=tuple(provenance))
    from .state_tools import assert_state_invariants
    assert_state_invariants(st)
    return st


def recorded_team_of(game, side):
    """The historical ChooseAdvanceTeam id/index of one player side."""
    recs = game["players"][side]["rounds"]
    if not recs:
        return None, None
    for a in recs[0].get("actions") or []:
        if a.get("type") == "ChooseAdvanceTeam":
            try:
                return int(a.get("ID")), int(a.get("Index", 0) or 0)
            except (TypeError, ValueError):
                return None, None
    return None, None
