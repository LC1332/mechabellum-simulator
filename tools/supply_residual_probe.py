"""Supply residual attribution: run the CURRENT deploy model per round,
residual = modeled_supply_end - next_snapshot_supply; correlate with the
presence of each unpriced mechanism (blueprint/contraption/tower/...)."""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pysim.gamedata import GameData
from pysim.transition import (ReplayAdapter, Economy, Income200r,
                              canonicalize_plan, deploy_transition,
                              EnvironmentState)
from pysim.transition.model import PlayerState

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
gd = GameData(os.path.join(ROOT, 'data', 'gamedata.json'))
eco = Economy(gd)
adapter = ReplayAdapter(os.path.join(ROOT, 'local_data', 'rounds_norm.json'))
d = adapter.games()
policy = Income200r()

resid_by_mech = defaultdict(Counter)
overall = Counter()
for g in d:
    if g.get('info', {}).get('matchMode') != 'VS_1_1':
        continue
    gid = adapter.game_index_of(g)
    for side in (0, 1):
        rs = g['players'][side]['rounds']
        for i, r in enumerate(rs[:-1]):
            rnd = int(r['round'])
            if rnd < 1:
                continue
            nxt = rs[i + 1]
            base = adapter.environment_state(gid, rnd, economy=eco)
            inc = policy.income(side, base.players[side], rnd,
                                base.players[side].pre_round_fight_result)
            players = list(base.players)
            players[side] = PlayerState(
                **{**base.players[side].__dict__,
                   'supply': base.players[side].supply + inc})
            state = EnvironmentState(
                schema_version=base.schema_version,
                ruleset_version=base.ruleset_version,
                engine_version=base.engine_version, round=base.round,
                phase=base.phase, players=tuple(players),
                finished_deploy=base.finished_deploy,
                next_entity_id=base.next_entity_id,
                terminal_reason=base.terminal_reason,
                provenance=base.provenance)
            acts, nrep = adapter.norm_actions(g, side, rnd)
            plan, _ = canonicalize_plan(side, acts, state.players[side],
                                        economy=eco, norm_report=nrep)
            try:
                dep = deploy_transition(state, (plan,), eco)
            except Exception:
                continue
            got = dep.state.players[side].supply
            want = int(nxt['supply'])
            resid = got - want
            overall[resid] += 1
            # attribute: what mechanisms appear in this round's norm stream?
            mechs = set()
            for e in acts:
                if e.get('t') == 'passthrough':
                    rr = e.get('raw_rec') or {}
                    if e['raw_type'] == 'ActiveBlueprint':
                        mechs.add('bp%s' % rr.get('ID'))
                    elif e['raw_type'] == 'ReleaseContraption':
                        mechs.add('con%s' % rr.get('ContraptionID'))
                    elif e['raw_type'] == 'StrengthenTower':
                        mechs.add('tower')
            for m in mechs:
                resid_by_mech[m][resid] += 1
            if not mechs:
                resid_by_mech['none'][resid] += 1
            for e in acts:
                if e.get('t') == 'passthrough' and \
                        e.get('raw_type') == 'ActiveBlueprint' and \
                        str((e.get('raw_rec') or {}).get('ID')) == '1':
                    policy.record_fast_supply(side, rnd + 1)

print('overall residual:', overall.most_common(12))
print()
for m in sorted(resid_by_mech):
    print('%-8s n=%-5d %s' % (m, sum(resid_by_mech[m].values()),
                              resid_by_mech[m].most_common(6)))
