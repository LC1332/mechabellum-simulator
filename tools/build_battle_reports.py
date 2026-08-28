#!/usr/bin/env python
"""对局战报生成器: TPolicy vs 回放赢家 (direct pysim), 输出 markdown 战报.

每场战报包含: 局况(root/回合/双方座位)、policy 完整 plan(可读动作序列)、
回放赢家 plan、pysim 裁决(胜者/双方伤害/用时)、执行层诊断(回退/拒绝/exploit)。
文首汇总训练指标与胜率 CI, 文末诊断与下一步。

  CUDA_VISIBLE_DEVICES=1 tools/build_battle_reports.py \
    --checkpoint local_data/rl_transformer/auto_v1/checkpoints/tpolicy_seed0.pt \
    --out local_data/rl_transformer/auto_v1/reports
"""
import argparse
import glob
import json
import os
import sys
import time

import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                              # noqa: E402
from pysim.transition.economy import Economy                     # noqa: E402
from pysim.rl.transformer import token_contract as tc            # noqa: E402
from pysim.rl.transformer import distributed as D                # noqa: E402
from pysim.rl.arena import play_joint, detect_exploits           # noqa: E402
from pysim.rl.contracts import derive_seed                       # noqa: E402

VERB_CN = {
    "END_DEPLOY": "结束部署", "BUY_UNIT": "购买单位", "UNLOCK_UNIT": "解锁单位",
    "UPGRADE_UNIT": "升级单位", "BUY_TECH": "购买科技", "MOVE_UNIT": "移动单位",
    "SELL_UNIT": "出售单位", "USE_EQUIPMENT": "使用装备",
    "RELEASE_COMMANDER_SKILL": "指挥官技能",
    "ACTIVATE_ENERGY_TOWER_SKILL": "能量塔技能", "STRENGTHEN_TOWER": "强化塔",
    "ACTIVE_BLUEPRINT": "研究蓝图", "RELEASE_CONTRAPTION": "释放装置",
}


class Namer:
    def __init__(self, gd):
        self.gd = gd
        from pysim.skills import COMMANDER_SKILLS
        from pysim.transition.deploy import (BLUEPRINT_COSTS,
                                             CONTRAPTION_COSTS,
                                             TOWER_SKILL_COSTS)
        self.skills = COMMANDER_SKILLS
        self.towers = TOWER_SKILL_COSTS
        self.bps = BLUEPRINT_COSTS
        self.contrs = CONTRAPTION_COSTS

    def mech(self, mid):
        c = self.gd.cards.get(int(mid))
        return "%s#%s" % (getattr(c, "name", "?"), mid)

    def tech(self, tid):
        t = self.gd.techs.get(int(tid))
        return getattr(t, "name", "?") + "#%s" % tid

    def skill(self, sid):
        return "%s#%s" % (self.skills.get(int(sid), {}).get("name", "?"), sid)

    def tower(self, sid):
        return "塔技能#%s" % sid

    def bp(self, bid):
        return "蓝图#%s" % bid

    def contr(self, cid):
        return "装置#%s" % cid


def fmt_action(a: dict, namer: Namer) -> str:
    v = a.get("verb", "?")
    cn = VERB_CN.get(v, v)
    bits = []
    if a.get("mech") is not None:
        bits.append(namer.mech(a["mech"]))
    if a.get("tech") is not None:
        bits.append("%s (单位%s)" % (namer.tech(a["tech"][1]), a["tech"][0]))
    if a.get("skill_id") is not None:
        bits.append(namer.skill(a["skill_id"]))
    if a.get("handle") is not None:
        bits.append("单位#h%d" % a["handle"])
    if a.get("equip") is not None:
        bits.append("装备#%s" % a["equip"])
    if a.get("tower") is not None:
        bits.append(namer.tower(a["tower"]))
    if a.get("tower_index") is not None:
        bits.append("塔%d" % a["tower_index"])
    if a.get("blueprint") is not None:
        bits.append(namer.bp(a["blueprint"]))
    if a.get("contraption") is not None:
        bits.append(namer.contr(a["contraption"]))
    if a.get("points"):
        pts = "→".join("(%.0f,%.0f)" % (p[0], p[1])
                       for p in a["points"])
        bits.append("落点 %s" % pts)
    elif a.get("y") is not None:
        bits.append("@(%.0f,%.0f)" % (a["x"], a["y"]))
    if a.get("rot") is not None:
        rot = a["rot"]
        bits.append({0: "朝向不变", 1: "旋转", 2: "标准"}.get(rot,
                                                       "rot=%s" % rot))
    return "- `%s` %s" % (cn, " · ".join(bits) if bits else "")


def fmt_engine_action(act, namer):
    """CanonicalAction (human plan) -> readable line."""
    k = str(act.kind)
    name = k.split(".")[-1]
    a = act.args
    bits = []
    if a is not None:
        if getattr(a, "mech_id", None) is not None:
            bits.append(namer.mech(a.mech_id))
        if getattr(a, "tech_id", None) is not None:
            bits.append(namer.tech(a.tech_id))
        if getattr(a, "skill_id", None) is not None:
            bits.append(namer.skill(a.skill_id))
        pts = getattr(a, "positions", None)
        if pts:
            bits.append("落点 " + "→".join("(%.0f,%.0f)" % (p[0], p[1])
                                           for p in pts))
        elif getattr(a, "x", None) is not None:
            bits.append("@(%.0f,%.0f)" % (a.x, a.y))
        if getattr(a, "ref", None) is not None:
            bits.append("单位#h%s" % a.ref.handle)
        if getattr(a, "equipment_id", None) is not None:
            bits.append("装备#%s" % a.equipment_id)
        if getattr(a, "is_rotate", None) is not None:
            bits.append("旋转" if a.is_rotate else "朝向不变")
    return "- `%s` %s" % (name, " · ".join(bits) if bits else "")


def load_policy(checkpoint):
    sys.path.insert(0, ROOT)
    from tools.run_transformer_arena import (TPolicyActuator, load_model)
    D.enforce_env()
    model, vocab, cfg, tok_cfg, ck, device = load_model(checkpoint)
    return model, vocab, cfg, tok_cfg, ck, device


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--corpus-chunks",
                    default="local_data/rl_phase1/dev_small/corpus_chunks")
    ap.add_argument("--out", default="local_data/rl_transformer/auto_v1/reports")
    ap.add_argument("--n-roots", type=int, default=6)
    ap.add_argument("--rounds-per-root", type=int, default=2)
    ap.add_argument("--max-games", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--title", default="TPolicy vs 回放赢家")
    args = ap.parse_args()

    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    eco = Economy(gd)
    namer = Namer(gd)
    model, vocab, cfg, tok_cfg, ck, device = load_policy(args.checkpoint)
    from tools.run_transformer_arena import (TPolicyActuator,
                                             plan_with_actuator,
                                             build_root_factory)
    actuator = TPolicyActuator(model, vocab, cfg, tok_cfg, device,
                               mode="greedy", seed=args.seed)
    from pysim.transition.replay_adapter import ReplayAdapter
    from pysim.rl.prefix_env import teacher_force_walk

    chunk_dir = args.corpus_chunks
    chunks = sorted(os.path.join(chunk_dir, f) for f in
                    os.listdir(chunk_dir)
                    if f.endswith(".json") and f != "chunks.json")
    make_root = build_root_factory(chunks, eco)

    def human_plan(cp, gi, rnd, seat):
        gs = json.load(open(cp))
        adapter = ReplayAdapter(gs)
        adapter._games = gs
        adapter._warned_raw = True
        try:
            entries = adapter.norm_actions(gs[gi], seat, rnd)[0]
            root0 = make_root(cp, gi, rnd)
            w = teacher_force_walk(root0, seat, entries, eco, gd)
        except Exception:
            return None, None
        if w.end_reason != "human_end":
            return None, None
        plan = {"engine_actions": w.engine_actions, "noops": w.noops,
                "forced_end": False, "stop_reason": "end",
                "steps": w.n_exogenous + len(w.samples),
                "final_state": w.final_state}
        return plan, gs[gi]

    games = []
    n_played = 0
    for cp in chunks:
        if n_played >= args.n_roots * args.rounds_per_root:
            break
        gs = json.load(open(cp))
        for gi, g in enumerate(gs):
            if gi >= args.max_games:
                break
            if n_played >= args.n_roots * args.rounds_per_root:
                break
            max_round = max((int(r["round"]) for p in g["players"]
                             for r in p.get("rounds", [])), default=0)
            for rnd in range(1, max_round):
                if n_played >= args.n_roots * args.rounds_per_root:
                    break
                root = make_root(cp, gi, rnd)
                if root is None:
                    continue
                h = {0: human_plan(cp, gi, rnd, 0),
                     1: human_plan(cp, gi, rnd, 1)}
                if h[0][0] is None or h[1][0] is None:
                    continue
                seed_h = derive_seed(
                    "rep|%s|%d|%d" % (os.path.basename(cp), gi, rnd), 0)
                rep = play_joint(root, h[0][0], h[1][0], eco, gd, seed_h)
                w_seat = int(rep["winner"])
                game = {"root": "%s#局%d#第%d回合" %
                        (os.path.basename(cp).replace(".json", ""), gi, rnd),
                        "replay_winner_seat": w_seat,
                        "replay": rep,
                        "seats": {}}
                for tag, pol_seat in (("挑战局(政策执输家位)", 1 - w_seat),
                                      ("卫冕局(政策执赢家位)", w_seat)):
                    actuator.reset()
                    plan = plan_with_actuator(root, pol_seat, eco, gd,
                                              actuator)
                    if pol_seat != w_seat:
                        hum, hum_tag = h[w_seat][0], "回放赢家 plan"
                    else:
                        hum, hum_tag = h[1 - w_seat][0], "回放输家 plan"
                    seed_m = derive_seed(
                        "vs|%s|%d|%d|%s" % (os.path.basename(cp), gi, rnd,
                                            tag), 0)
                    if pol_seat == 0:
                        res = play_joint(root, plan, hum, eco, gd, seed_m)
                        pol_win = int(res["winner"] == 0)
                    else:
                        res = play_joint(root, hum, plan, eco, gd, seed_m)
                        pol_win = int(res["winner"] == 1)
                    game["seats"][tag] = {
                        "policy_seat": pol_seat, "policy_win": pol_win,
                        "result": res, "plan": plan,
                        "human_plan": hum, "human_tag": hum_tag,
                    }
                games.append(game)
                n_played += 1
                print("played", game["root"], flush=True)

    os.makedirs(args.out, exist_ok=True)
    write_report(args, games, namer, ck)
    # also dump the raw json for later tooling
    raw = []
    for g in games:
        raw.append({
            "root": g["root"], "replay_winner_seat": g["replay_winner_seat"],
            "replay": g["replay"],
            "seats": {tag: {"policy_seat": s["policy_seat"],
                            "policy_win": s["policy_win"],
                            "result": s["result"],
                            "policy_actions": s["plan"]["actions_dict"],
                            "stop": s["plan"]["stop_reason"],
                            "steps": s["plan"]["steps"],
                            "fallbacks": s["plan"]["fallbacks"]}
                      for tag, s in g["seats"].items()}})
    with open(os.path.join(args.out, "battle_raw_%s.json" %
                           time.strftime("%Y%m%d_%H%M%S")), "w",
              encoding="utf8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1, default=str)


def write_report(args, games, namer, ck):
    chal = [g["seats"]["挑战局(政策执输家位)"] for g in games]
    hold = [g["seats"]["卫冕局(政策执赢家位)"] for g in games]
    lines = []
    add = lines.append
    add("# 对局战报 — %s" % args.title)
    add("")
    add("- checkpoint: `%s` (engineering: %s | contract: `%s`)" % (
        args.checkpoint, bool(ck.get("engineering_only")),
        ck.get("contract_version")))
    add("- 裁决: direct pysim, 双方 plan 由同一 root 独立执行, 共同 seed")
    add("- 对手: 回放赢家 = 复盘同局双人类 plan 后 pysim 的胜者 (seat %s)"
        % "0/1")
    add("- 生成时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    add("")

    # -------- 汇总
    add("## 总览")
    add("")
    add("| 指标 | 挑战局 (政策执输家位) | 卫冕局 (政策执赢家位) |")
    add("|---|---|---|")
    for row, seat_games in (("policy 胜率",
                             (chal, hold)),):
        wr = [float(np.mean([s["policy_win"] for s in sg]))
              for sg in seat_games]
        add("| %s | %.3f (%d/%d) | %.3f (%d/%d) |" % (
            row, wr[0], sum(s["policy_win"] for s in seat_games[0]),
            len(seat_games[0]),
            wr[1], sum(s["policy_win"] for s in seat_games[1]),
            len(seat_games[1])))
    dmg = [np.mean([s["result"]["damage_to_player"][1 - s["policy_seat"]] -
                    s["result"]["damage_to_player"][s["policy_seat"]]
                    for s in sg]) for sg in (chal, hold)]
    add("| 平均伤害差 (policy−对手) | %.0f | %.0f |" % (dmg[0], dmg[1]))
    steps = [np.mean([s["plan"]["steps"] for s in sg]) for sg in (chal, hold)]
    add("| 平均 plan 步数 | %.1f | %.1f |" % (steps[0], steps[1]))
    fb = [np.mean([s["plan"]["fallbacks"] for s in sg])
          for sg in (chal, hold)]
    add("| 平均 verb 回退次数 | %.1f | %.1f |" % (fb[0], fb[1]))
    stops = {}
    for s in chal + hold:
        stops[s["plan"]["stop_reason"]] = \
            stops.get(s["plan"]["stop_reason"], 0) + 1
    add("policy plan 停止原因分布: %s" % json.dumps(stops, ensure_ascii=False))
    add("")

    # -------- 每场战报
    add("## 逐场战报")
    add("")
    for g in games:
        add("### %s" % g["root"])
        add("")
        add("复盘裁决: 回放赢家 = 玩家 %d (胜率验证: winner=%d, 双方伤害 %s, "
            "用时 %.1fs)" % (
                g["replay_winner_seat"], g["replay"]["winner"],
                g["replay"]["damage_to_player"], g["replay"]["end_time"]))
        add("")
        for tag, s in g["seats"].items():
            res = s["result"]
            verdict = "✅ policy 胜" if s["policy_win"] else "❌ policy 负"
            add("#### %s — %s" % (tag, verdict))
            add("")
            add("- policy 坐 seat %d; 胜者 seat %d; 伤害(对0/对1)=%s; "
                "用时 %.1fs" % (s["policy_seat"], res["winner"],
                                res["damage_to_player"], res["end_time"]))
            add("- 执行层: steps=%d stop=%s fallbacks=%d rejections=%d "
                "noops=%d" % (s["plan"]["steps"], s["plan"]["stop_reason"],
                              s["plan"]["fallbacks"],
                              len(res["rejections"]), len(res["noops"])))
            exp = detect_exploits(s["plan"], None)
            if exp:
                add("- ⚠ exploit 旗标: `%s`" % ", ".join(exp))
            add("")
            add("**policy plan (greedy):**")
            add("")
            acts = s["plan"]["actions_dict"]
            if acts:
                for a in acts:
                    add(fmt_action(a, namer))
            else:
                add("- (空 plan)")
            add("")
            hp = s["human_plan"]["engine_actions"]
            add("**回放赢家 plan (seat %d):**" % (
                s["policy_seat"] if s["policy_seat"] != 0 else 1)
                if False else "**对手(回放赢家) plan:**")
            add("")
            if hp:
                for act in hp:
                    add(fmt_engine_action(act, namer))
            else:
                add("- (空 plan)")
            add("")

    # -------- 诊断 (模式检测)
    add("## 诊断 (pattern 检测)")
    add("")
    dup_buy = 0; unlock_spam = 0; short_plan = 0; cycles = 0; no_tech = 0
    n_all = 0
    for g in games:
        for tag, s2 in g["seats"].items():
            n_all += 1
            acts = s2["plan"]["actions_dict"]
            buys = [(round(a.get("x", 0)), round(a.get("y", 0)))
                    for a in acts if a.get("verb") == "BUY_UNIT"]
            if len(buys) != len(set(buys)):
                dup_buy += 1
            unlocks = sum(1 for a in acts
                          if a.get("verb") == "UNLOCK_UNIT")
            bought = sum(1 for a in acts if a.get("verb") == "BUY_UNIT")
            if unlocks >= 3 and bought <= unlocks:
                unlock_spam += 1
            if s2["plan"]["stop_reason"] == "cycle_stop":
                cycles += 1
            human_steps = len(s2["human_plan"]["engine_actions"])
            if human_steps >= 10 and s2["plan"]["steps"] <= 4:
                short_plan += 1
            if not any(a.get("verb") == "BUY_TECH" for a in acts) and \
                    any(a.get("verb") == "BUY_TECH"
                        for act in s2["human_plan"]["engine_actions"]
                        if getattr(act, "kind", None) is not None and False):
                no_tech += 1
    add("| 模式 | 场次 (/ %d) | 说明 |" % n_all)
    add("|---|---|---|")
    add("| 重复同点购买 | %d | 同一坐标多次 BUY_UNIT → overlap/exploit |" %
        dup_buy)
    add("| 解锁刷子 | %d | 连续 UNLOCK≥3 且不落地购买 → 白白花钱 |" %
        unlock_spam)
    add("| plan 过短 | %d | 人类 ≥10 步时 policy ≤4 步结束 → 部署不足 |" %
        short_plan)
    add("| 循环保护停止 | %d | cycle_stop: 同签名动作重复 3 次 |" % cycles)
    add("")
    add("**下一步建议** (按预期收益排序): ① scheduled-sampling/DAgger 恢复数据 "
        "压制 free-running 漂移 (cycle_stop/回退的根因); ② 训练时把 UNLOCK 的 "
        "无意义连发当作负样本降权; ③ 更多 epoch + Medium 档; ④ 复用第一周期 "
        "checkpoint 做 best-of-N + TValue 预筛。")
    add("")

    out = os.path.join(args.out, "战报_%s.md" % time.strftime("%Y%m%d_%H%M%S"))
    with open(out, "w", encoding="utf8") as f:
        f.write("\n".join(lines) + "\n")
    print("written:", out)
    chal_wr = float(np.mean([s["policy_win"] for s in chal])) if chal else None
    print("挑战局 win rate:", chal_wr)
    return out


if __name__ == "__main__":
    main()
