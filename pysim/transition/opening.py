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
import copy
import hashlib
import json
import os

from .model import (EnvironmentState, PlayerState, UnitCard, Phase,
                    SCHEMA_VERSION, RULESET_VERSION, ENGINE_VERSION)

CATALOG_SCHEMA = "opening_catalog_v2"
CATALOG_SCHEMA_V1 = "opening_catalog_v1"
GENERATOR_VERSION = "opening_offer_generator_v1"

# step2 任务书 G1: catalog v2 freezes every package in player-0 world
# orientation (formation y < 0; player 1 owns y > 0). The runtime mirrors Y
# when building player 1's initial units and NOTHING else — x, mech ids,
# levels and is_rotate stay identical (is_rotate is an orientation flag of
# the package itself, not a function of which side deploys it).
FORMATION_SPACE = {
    "coordinate_space": "world_v1",
    "orientation": "player0",          # formation y < 0
    "player1_rule": "mirror_y",        # y -> -y when side 1 deploys it
}


class OpeningError(Exception):
    pass


def load_catalog(path):
    data = json.load(open(path, encoding="utf8"))
    sv = data.get("schema_version")
    if sv == CATALOG_SCHEMA:
        return data
    if sv == CATALOG_SCHEMA_V1:
        # explicit v1 adapter (step2 G1): v1 stored formations mirrored to
        # POSITIVE y regardless of the evidence side, i.e. player-1
        # orientation. Negating y converts to the v2 player-0 convention.
        adapted = _adapt_v1_catalog(data)
        adapted["adapted_from"] = CATALOG_SCHEMA_V1
        return adapted
    raise OpeningError("catalog schema %s != %s"
                       % (sv, CATALOG_SCHEMA))


def _adapt_v1_catalog(data):
    out = copy.deepcopy(data)
    out["schema_version"] = CATALOG_SCHEMA
    out["formation_space"] = dict(FORMATION_SPACE,
                                  note="adapted from opening_catalog_v1 "
                                       "(positive-y orientation, y negated)")
    for pkg in (out.get("packages") or {}).values():
        for grp in pkg.get("units", []):
            grp["formation"] = [[float(x), -float(y)] for (x, y) in
                                grp.get("formation", [])]
    return out


def mirror_package_y(pkg):
    """Same package deployed across the midline: formation y -> -y only."""
    out = copy.deepcopy(pkg)
    for grp in out.get("units", []):
        grp["formation"] = [[float(x), -float(y)] for (x, y) in
                            grp.get("formation", [])]
    return out


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


def player_state_from_package(pkg, start_entity_id=1, eco=None,
                              gd=None) -> PlayerState:
    units, next_id = _units_of(pkg, start_entity_id, eco)
    unlocked = {int(m) for m in pkg.get("unlocked", [])}
    unlocked |= {u.mech_id for u in units}
    cons = tuple(tuple(str(x) for x in c)
                 for c in pkg.get("constructions", []))
    hp = int(pkg.get("hp", 4500))
    officers = tuple(int(o) for o in pkg.get("officers", []))
    cs = tuple(tuple(str(x) for x in s)
               for s in pkg.get("commander_skills", []))
    equipment = tuple(sorted(int(e) for e in pkg.get("equipment_inventory",
                                                     []) or []))
    # shared round event at round 1 (step3 任务书 §5.3): officer cmdSkills
    # (e.g. 训练专家 1100001) and equipment grants (增幅专家 3x 13030009).
    # Top-up semantics keep snapshot-derived slots/equipment from doubling.
    if gd is not None:
        from .equipment import (round_officer_skills,
                                round_officer_equipment, top_up_skill_slots)
        grants = round_officer_skills(gd, officers, 1)
        if grants:
            cs = tuple(top_up_skill_slots(cs, grants))
        eq = round_officer_equipment(officers, 1)
        if eq:
            # top up to the grant multiplicity (never below the evidence
            # multiset; 增幅专家's 3x 13030009 must keep its copies)
            out = list(equipment)
            grant_counts = {}
            for e in eq:
                grant_counts[e] = grant_counts.get(e, 0) + 1
            for e, n in grant_counts.items():
                have = sum(1 for x in out if x == e)
                if n > have:
                    out.extend([e] * (n - have))
            equipment = tuple(sorted(out))
    return PlayerState(
        hp=hp, max_hp=int(pkg.get("max_hp", max(hp, 4500))),
        supply=int(pkg.get("supply", 0)), pre_round_fight_result=None,
        units=tuple(units), unlocked_mechs=frozenset(unlocked),
        tech_map=tuple(sorted(
            (int(m), tuple(int(t) for t in lst))
            for m, lst in (pkg.get("tech_map") or {}).items())),
        officers=officers,
        blueprints=tuple(int(b) for b in pkg.get("blueprints", [])),
        commander_skills_raw=cs,
        equipment_inventory=equipment,
        # step4 任务书 §1.2/QA#1: round-1 opening units have not fought a
        # battle yet -> every one of them is movable in round 1
        spawned_this_round=tuple(sorted(u.entity_id for u in units)),
        tower_strengthen=(0, 0), constructions_raw=cons), next_id


def build_initial_state(pkg0, pkg1, provenance=(), eco=None,
                        gd=None) -> EnvironmentState:
    """Round-1 DEPLOYMENT state from two opening packages (rule execution,
    NOT round-1 snapshot backfill). Round-1 income is added separately by
    the session (income policy), matching the replay's snapshot timing.

    Both packages come from the catalog in player-0 world orientation
    (formation y < 0): pkg0 deploys as stored, pkg1 mirrors Y only
    (任务书 G1 — x/mech/level/is_rotate identical, no double rotation).

    gd (given by the runtime) applies the round-1 officer grants (skills +
    equipment) with the same code path as advance_round."""
    p0, next_id = player_state_from_package(pkg0, start_entity_id=1, eco=eco,
                                            gd=gd)
    p1_pkg = mirror_package_y(pkg1)
    p1, _ = player_state_from_package(p1_pkg, start_entity_id=next_id,
                                      eco=eco, gd=gd)
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
