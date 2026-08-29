# -*- coding: utf-8 -*-
"""step30 O1 v2: 解锁 census —— UID 字段修正 + pairs 区间归因。

norm 语料 pairs 不连续 (只含有内容的回合); 解锁动作挂在 deploy 回合的 actions
(PAD_UnlockUnit norm 化为 type=UnlockUnit, 目标 mech = UID 字段)。
unlocked_units 快照 = 该回合部署前状态 → 本回合动作的解锁应出现在
"下一 pair" 的快照; 同 pair 出现则说明快照在动作后 (实测裁决)。

输出: data/crawler_damage_oracle/crawler-damage-replay-v1/unlock_census.json
"""
import io, sys, os, json, argparse
from collections import Counter, defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8", errors="replace")
GITHUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GITHUB)

EXPERT_OFFICER_MECH = {20029: 2, 20033: 5, 20036: 21, 20037: 26, 20038: 20, 20039: 22}

gd = json.load(open(os.path.join(GITHUB, "data", "gamedata.json"), encoding="utf8"))
MECH_NAME = {int(k): (v.get("name") or "?") for k, v in gd["mechs"].items()}
NAME_MECH = {v: k for k, v in MECH_NAME.items()}

cards_info = json.load(open(os.path.join(GITHUB, "information", "增援卡牌-回放全量信息.json"),
                            encoding="utf8"))
ITEM_MECH = {}
ITEM_KIND = {}
for c in cards_info.get("cards", []):
    ITEM_KIND[c["id"]] = c.get("类别") or "?"
    tu = c.get("目标单位")
    if tu and tu in NAME_MECH:
        ITEM_MECH[c["id"]] = NAME_MECH[tu]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default=os.path.join(GITHUB, "local_data", "rounds_norm.json"))
    ap.add_argument("--out", default=os.path.join(
        GITHUB, "data", "crawler_damage_oracle", "crawler-damage-replay-v1",
        "unlock_census.json"))
    args = ap.parse_args()

    data = json.load(open(args.rounds, encoding="utf8"))
    act_count_dist = Counter()
    multi_rounds = []
    same_pair_success = 0
    next_pair_success = 0
    no_effect_actions = 0
    action_total = 0
    already_unlocked_actions = 0
    auto_events = []          # 无动作的增量 (区间归因后)
    expert_unlock_lag = defaultdict(list)   # officer -> [lag rounds]
    unlock_growth_events = 0

    for g in data:
        fname = g.get("file") or "?"
        for sk in ("p0", "p1"):
            pairs = g.get("pairs", [])
            prev_unlocked = set()
            prev_round = 0
            # 区间内 (prev_round, cur_round] 各回合的动作需要沿 pairs 找:
            # norm 语料动作只挂在出现于 pairs 的回合上; 中间回合若有动作必在
            # 某 pair 里 (pairs 即全部有内容回合)。逐 pair 顺序处理。
            pending_unlock_actions = []   # [(round, uid)] 尚未被快照确认的解锁
            for pair in pairs:
                cur = pair.get(sk) or {}
                rnd = cur.get("round")
                unlocked = set(int(x) for x in (cur.get("unlocked_units") or []))
                actions = cur.get("actions") or []
                unlock_acts = [int(a.get("UID")) for a in actions
                               if a.get("type") == "UnlockUnit"
                               and a.get("UID") is not None]
                item_ids = [int(a[k]) for a in actions
                            if a.get("type") == "ChooseReinforceItem"
                            for k in ("itemId", "item", "cardId", "id")
                            if k in a and str(a[k]).isdigit()]
                officers = [int(o) for o in (cur.get("officers") or [])]

                n_act = len(unlock_acts)
                act_count_dist[n_act] += 1
                if n_act >= 2:
                    multi_rounds.append({"file": fname, "round": rnd,
                                         "side": sk, "uids": unlock_acts})
                for uid in unlock_acts:
                    action_total += 1
                    if uid in unlocked:
                        same_pair_success += 1
                    elif uid in prev_unlocked:
                        already_unlocked_actions += 1
                    else:
                        pending_unlock_actions.append((rnd, uid))
                # 本 pair 快照确认 pending
                still_pending = []
                confirmed_this_pair = set()
                for (r0, uid) in pending_unlock_actions:
                    if uid in unlocked:
                        next_pair_success += 1
                        confirmed_this_pair.add(uid)
                    else:
                        still_pending.append((r0, uid))
                pending_unlock_actions = still_pending

                added = unlocked - prev_unlocked
                if added:
                    unlock_growth_events += 1
                    manual_uids = {uid for (_r, uid) in pending_unlock_actions} | \
                                  {uid for uid in unlock_acts if uid in unlocked} | \
                                  confirmed_this_pair
                    auto_added = added - manual_uids
                    # 本回合内动作已被同快照确认的算 manual
                    if auto_added:
                        auto_events.append({
                            "file": fname, "round": rnd, "side": sk,
                            "added": sorted(auto_added),
                            "officers": sorted(set(officers)),
                            "prev_items": item_ids})
                # 专家解锁时序: officers 出现后 mech 何时进 unlocked
                # (officers 首次出现 → 之后首个包含 mech 的 pair 的 round 差)
                prev_officers_key = "_off_%s_%s" % (fname, sk)
                # 简化: 仅统计同 pair 关系 (officer 出现 pair vs unlocked)
                # 完整时序在 oracle 受控局 (O12) 裁决, census 只给观察分布
                for oid in officers:
                    if oid in EXPERT_OFFICER_MECH:
                        mech = EXPERT_OFFICER_MECH[oid]
                        if mech in unlocked:
                            # 无法从此 pair 判定先后 → 记 pending 由后续 diff
                            pass
                prev_unlocked = unlocked
                prev_round = rnd

    # 专家时序观察 (独立轻量 pass): officer 首现 round → mech 首次入 unlocked 的 round 差 (游戏回合)
    expert_lag = defaultdict(list)
    for g in data:
        fname = g.get("file") or "?"
        for sk in ("p0", "p1"):
            first_officer = {}
            first_mech = {}
            for pair in g.get("pairs", []):
                cur = pair.get(sk) or {}
                rnd = cur.get("round")
                officers = [int(o) for o in (cur.get("officers") or [])]
                unlocked = set(int(x) for x in (cur.get("unlocked_units") or []))
                for oid in officers:
                    if oid in EXPERT_OFFICER_MECH and oid not in first_officer:
                        first_officer[oid] = rnd
                for oid, rnd0 in first_officer.items():
                    mech = EXPERT_OFFICER_MECH[oid]
                    if mech in unlocked and mech not in first_mech:
                        first_mech[mech] = rnd
            for oid, rnd0 in first_officer.items():
                mech = EXPERT_OFFICER_MECH[oid]
                if mech in first_mech:
                    expert_lag[oid].append(first_mech[mech] - rnd0)
                else:
                    expert_lag[oid].append(None)

    report = {
        "corpus": os.path.basename(args.rounds),
        "games": len(data),
        "unlock_action_round_dist": dict(act_count_dist),
        "unlock_action_total": action_total,
        "success_same_pair_snapshot": same_pair_success,
        "success_next_pair_snapshot": next_pair_success,
        "action_target_already_unlocked": already_unlocked_actions,
        "multi_unlock_rounds": multi_rounds,
        "multi_unlock_round_count": len(multi_rounds),
        "unlocked_growth_events": unlock_growth_events,
        "auto_growth_events_no_action": len(auto_events),
        "auto_growth_samples": auto_events[:80],
        "expert_officer_to_mech_unlock_lag": {
            oid: {"name": gd["officers"][str(oid)]["name"],
                  "mech": m, "activeRound": gd["officers"][str(oid)].get("activeRound"),
                  "lag_dist": dict(Counter(str(x) for x in expert_lag[oid]))}
            for oid, m in sorted(EXPERT_OFFICER_MECH.items())},
        "note": " UnlockUnit 目标字段=UID; pairs 非连续; pending 动作跨 pair 确认",
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(report, open(args.out, "w", encoding="utf8"),
              ensure_ascii=False, indent=1)
    print("games:", len(data), "| action-round dist:", dict(act_count_dist))
    print("action total:", action_total,
          "| same-pair ok:", same_pair_success,
          "| next-pair ok:", next_pair_success,
          "| target-already:", already_unlocked_actions)
    print("multi(>1) rounds:", len(multi_rounds))
    print("auto growth events:", len(auto_events))
    for k, v in report["expert_officer_to_mech_unlock_lag"].items():
        print(" expert", k, v["name"], "lag:", v["lag_dist"])
    print("saved:", args.out)


if __name__ == "__main__":
    main()
