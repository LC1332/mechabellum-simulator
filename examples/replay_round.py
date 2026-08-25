# 回放接入示例：加载 rounds*.json 语料, 单独重放一个回合并与回放标签对拍。
# 语料由 tools/replay2json.py 从 .grbr 官方回放生成:
#   python tools/replay2json.py <回放目录> local_data/rounds.json
# 用法:
#   python examples/replay_round.py local_data/rounds.json 0 3
#     (语料文件, 对局序号, 回合号; 省略回合号则重放该局所有回合)
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pysim.gamedata import GameData
from pysim.engine import battle_from_units
from pysim.replay_check import build_tech_map, build_tower_mods
from pysim.flank import pair_flank_delays, annotate_units

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def simulate_pair(gd, pair):
    """构建一个 round pair 的战斗 (replay_check 默认口径: units_fight 布阵 +
    mdefull 科技 + 绕后延迟 + 塔/军官), 返回 (battle, label)。
    注意 alive 检查要在 simulate() 之前做 (打完败方必然全灭)。"""
    u0 = pair["p0"].get("units_fight") or pair["p0"]["units"]
    u1 = pair["p1"].get("units_fight") or pair["p1"]["units"]
    d0, d1 = pair_flank_delays(pair, mode="card")   # 绕后空降延迟
    u0, u1 = annotate_units(u0, d0), annotate_units(u1, d1)
    b = battle_from_units(
        gd, u0, u1,
        tech_map0=build_tech_map(gd, pair["p0"], "mdefull", {int(u["id"]) for u in u0}),
        tech_map1=build_tech_map(gd, pair["p1"], "mdefull", {int(u["id"]) for u in u1}),
        towers0=[int(x) for x in (pair["p0"].get("towerStrengthen_raw") or [0, 0])][:2],
        towers1=[int(x) for x in (pair["p1"].get("towerStrengthen_raw") or [0, 0])][:2],
        officers0=pair["p0"].get("officers"), officers1=pair["p1"].get("officers"))
    return b, pair["label"]


def main():
    corpus = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "data", "samples", "rounds.json")
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rounds = json.load(open(corpus, encoding="utf8"))
    rec = rounds[idx]
    want = int(sys.argv[3]) if len(sys.argv) > 3 else None
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    print("对局 %s: %s VS %s (%d 个带标签回合)" % (
        rec["file"], rec["players"][0]["name"], rec["players"][1]["name"],
        len(rec["pairs"])))
    n = ok = skipped = 0
    for pair in rec["pairs"]:
        if want is not None and int(pair["round"]) != want:
            continue
        b, label = simulate_pair(gd, pair)
        if b.alive_count(0) == 0 or b.alive_count(1) == 0:
            skipped += 1   # 一方无兵可打 (replay_check 同样跳过)
            print("  回合 %2d: 跳过 (一方无部队)" % pair["round"])
            continue
        winner = b.simulate()
        hit = (winner == 0) == (label == "Win")
        n += 1
        ok += hit
        print("  回合 %2d: 回放=%s  模拟=team%s  %s | 幸存 %dv%d  %s" % (
            pair["round"], label, winner, "√" if hit else "×",
            b.alive_count(0), b.alive_count(1), b.result(winner)["end_time"]))
    if n:
        print("一致率: %d/%d = %.0f%% (另跳过 %d 个无兵回合)" % (
            ok, n, 100.0 * ok / n, skipped))


if __name__ == "__main__":
    main()
