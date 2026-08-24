# Build a synthetic rounds.json (1 pair, ~170 units as cards) to benchmark
# the OLD C# engine v1 (1 unit per card) at multi-module scale.
import json, io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def units(cards_):
    out = []
    for i, (mid, lvl, (x, y)) in enumerate(cards_):
        out.append({"id": mid, "index": i, "roundCount": 0, "durability": 0, "exp": 0,
                    "level": lvl, "x": x, "y": y, "equipment": 0,
                    "isRotate": False, "sellSupply": 0})
    return out

# ~85 cards per side, mixed mechs, mirrored deployment
mix = [10, 9, 28, 7, 1, 3, 2, 20, 22, 13, 16]
p0u, p1u = [], []
for i in range(85):
    mid = mix[i % len(mix)]
    x = -300 + (i % 17) * 37
    y = -280 + (i // 17) * 40
    p0u.append((mid, 1 + (i % 3), (x, y)))
    p1u.append((mid, 1 + (i % 3), (x, 300 - 0 - y)))

pair = {"round": 1,
        "p0": {"round": 1, "reactorCore": 0, "supply": 0, "preRoundFightResult": None,
               "rng": [], "units": units(p0u), "techs": []},
        "p1": {"round": 1, "reactorCore": 0, "supply": 0, "preRoundFightResult": None,
               "rng": [], "units": units(p1u), "techs": []},
        "match": None, "label": "Win"}
rec = {"file": "benchmark_synthetic_170units_battle.grbr",
       "info": {"systemSeed": 0, "mapID": 0, "prepareTime": 30, "deployTime": 100,
                "fightTime": 120, "maxRound": 40, "gameMode": "Normal", "matchMode": "VS_1_1"},
       "players": [], "pairs": [pair]}
_dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "bench_rounds.json")
json.dump([rec], open(_dst, "w", encoding="utf8"),
          ensure_ascii=False)
print("wrote bench_rounds.json: 85 v 85 cards")
