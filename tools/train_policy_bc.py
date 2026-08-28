#!/usr/bin/env python
"""T3: train pi_BC on policy_prefix_real_v1 (task §8).

Teacher-forced masked multi-head training; reports verb/pointer/xy/END
metrics + baseline comparison + 3-seed stability. The checkpoint bundles
the vocab so the arena decoder needs no corpus access.
"""
import argparse
import gzip
import json
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                                  # noqa: E402
from pysim.rl.features import Vocab, policy_features                 # noqa: E402
from pysim.rl.masks import ALL_VERBS, VERB_INDEX                 # noqa: E402
from pysim.rl.models.policy_bc import PolicyBC                       # noqa: E402

HEAD_FOR_VERB = {
    "BUY_UNIT": ("mech", "xy", "rot_buy"),
    "UNLOCK_UNIT": ("mech",),
    "UPGRADE_UNIT": ("unit",),
    "SELL_UNIT": ("unit",),
    "MOVE_UNIT": ("unit", "xy", "rot_move"),
    "BUY_TECH": ("tech",),
    "USE_EQUIPMENT": ("equip", "unit"),
    "RELEASE_COMMANDER_SKILL": ("skill",),   # + optional xy/unit by kind
    "ACTIVATE_ENERGY_TOWER_SKILL": ("tower",),
    "STRENGTHEN_TOWER": ("strengthen",),
    "ACTIVE_BLUEPRINT": ("bp",),
    "RELEASE_CONTRAPTION": ("contr", "xy"),
    "END_DEPLOY": (),
}

POINTER_SPECS = (
    ("mech", "mech_ids", "mech_cands"),
    ("tech", "tech_ids", "tech_cands"),
    ("equip", "equip_ids", "equip_cands"),
    ("skill", "skill_ids", "skill_cands"),
    ("tower", "tower_ids", "tower_cands"),
    ("bp", "bp_ids", "blueprint_cands"),
    ("contr", "contr_ids", "contraption_cands"))


def featurize_row(r, vocab, gd):
    f = policy_features(r["obs"], r["space"], vocab)
    f["sample_id"] = r["sample_id"]
    f["split"] = r["split"]
    f["tier"] = r["tier"]
    f["round"] = r["round"]
    f["match_id_hash"] = r["match_id_hash"]
    tgt = dict(r["target"])
    tv = tgt["verb"]
    if isinstance(tv, str):
        tv = VERB_INDEX[tv]
    tgt["verb"] = int(tv)
    f["target"] = tgt
    f["target_verb_name"] = ALL_VERBS[tv] if tv < len(ALL_VERBS) \
        else "END_DEPLOY"
    f["space"] = r["space"]
    return f


def collate(rows_feats, vocab, device, max_units=64):
    B = {}
    n = len(rows_feats)
    def stack(key):
        return torch.as_tensor(np.stack([f[key] for f in rows_feats]),
                               device=device)
    for k in ("self_f", "self_mech", "self_equip", "self_mask", "opp_f",
              "opp_mech", "opp_equip", "opp_mask", "global", "self_off"):
        B[k] = stack(k)
    for side in ("self", "opp"):
        T = max(f[side + "_tech"]["tech_ids"].shape[0] for f in rows_feats)
        ids = np.zeros((n, T), dtype=np.int64)
        own = np.zeros((n, T), dtype=np.int64)
        for i, f in enumerate(rows_feats):
            a = f[side + "_tech"]["tech_ids"]
            b = f[side + "_tech"]["tech_owners"]
            ids[i, :len(a)] = a
            own[i, :len(b)] = b
        B[side + "_tech"] = {
            "tech_ids": torch.as_tensor(ids, device=device),
            "tech_owners": torch.as_tensor(own, device=device)}
    # candidate pools
    space = {}
    for head, key, pool_key in POINTER_SPECS:
        lists = [f["space"].get(pool_key) or [] for f in rows_feats]
        T = max(len(x) for x in lists)
        width = 2 if head == "tech" else 1
        ids = np.zeros((n, T, width), dtype=np.int64)
        mask = np.zeros((n, T), dtype=np.float32)
        for i, lst in enumerate(lists):
            for j, cand in enumerate(lst):
                pair = list(cand) if isinstance(cand, (list, tuple))                     else [cand]
                if head == "tech":
                    # candidate = (mech, tech) -> (tech vocab, mech vocab)
                    ids[i, j, 0] = vocab.tech(pair[1])
                    ids[i, j, 1] = vocab.mech(pair[0])
                else:
                    # pointer tables index hashed ids (bp 501, equip
                    # 13030009, contraption 30001 ...): mod-64 mapping
                    ids[i, j, 0] = int(pair[0]) % 64
            mask[i, :len(lst)] = 1.0
        t = torch.as_tensor(ids, device=device)
        space[key] = t.squeeze(-1) if width == 1 else t
        space[key + "_mask"] = torch.as_tensor(mask, device=device)
    if all("skill_target" in f["space"] for f in rows_feats):
        T = space["skill_ids"].shape[1] if "skill_ids" in space else 0
        kinds = np.zeros((n, T), dtype=np.int64)
        for i, f in enumerate(rows_feats):
            st = f["space"].get("skill_target") or []
            for j, k in enumerate(st[:T]):
                kinds[i, j] = {"none": 1, "position": 2, "unit": 0}.get(k, 1)
        space["skill_kinds"] = torch.as_tensor(kinds, device=device)
    if "skill_ids" in space:
        space["skill_ids"] = space["skill_ids"] % 1024
    B["space"] = space
    return B


END_CLASS_WEIGHT = 5.0      # END is ~1/plan-len of samples; the T3 gate
                            # (正常 END >= 99%) needs the model to stop


def loss_fn(model, batch, rows_feats, device, lam_xy=0.3):
    out = model(batch, batch["space"])
    n = len(rows_feats)
    verbs = torch.as_tensor([f["target"]["verb"] for f in rows_feats],
                            device=device)
    verb_logits = out["verb_logits"]
    vmask = torch.as_tensor(np.asarray([f["space"]["verb_mask"]
                                        for f in rows_feats]),
                            dtype=torch.float32, device=device)
    w = torch.where(verbs == VERB_INDEX["END_DEPLOY"],
                    torch.full_like(verbs, END_CLASS_WEIGHT,
                                    dtype=torch.float32),
                    torch.ones_like(verbs, dtype=torch.float32))
    per_ex = F.cross_entropy(verb_logits + (1.0 - vmask) * -1e9, verbs,
                             reduction="none")
    verb_ce = (per_ex * w / w.sum()).sum() * n

    total = verb_ce
    parts = {"verb_ce": float(verb_ce)}
    ctx = out["ctx"]
    tverb = [f["target_verb_name"] for f in rows_feats]

    for head, key, _pool_key in POINTER_SPECS:
        t_idx = torch.as_tensor([f["target"].get(head, -1)
                                 for f in rows_feats], device=device)
        use = torch.as_tensor([head in HEAD_FOR_VERB.get(v, ())
                               and f["target"].get(head, -1) >= 0
                               for f, v in zip(rows_feats, tverb)],
                              dtype=torch.bool, device=device)
        if not use.any():
            continue
        scores = out.get(head + "_scores")
        if scores is None:
            continue
        scores = scores + (1.0 - batch["space"][key + "_mask"]) * -1e9
        ce = F.cross_entropy(scores[use], t_idx[use])
        total = total + ce
        parts[head + "_ce"] = float(ce)

    # unit pointer
    t_unit = torch.as_tensor([f["target"].get("unit", -1)
                              for f in rows_feats], device=device)
    use = torch.as_tensor(["unit" in HEAD_FOR_VERB.get(v, ())
                           and f["target"].get("unit", -1) >= 0
                           for f, v in zip(rows_feats, tverb)],
                          dtype=torch.bool, device=device)
    if use.any():
        scores = out["unit_scores"] + \
            (1.0 - out["unit_mask"]) * -1e9
        total = total + F.cross_entropy(scores[use], t_unit[use])
        parts["unit_ce"] = float(F.cross_entropy(scores[use], t_unit[use]))

    # strengthen pointer (2 fixed slots)
    t_ti = torch.as_tensor([f["target"].get("tower_index", -1)
                            for f in rows_feats], device=device)
    use = torch.as_tensor(["strengthen" in HEAD_FOR_VERB.get(v, ())
                           and f["target"].get("tower_index", -1) >= 0
                           for f, v in zip(rows_feats, tverb)],
                          dtype=torch.bool, device=device)
    if use.any() and "strengthen_scores" in out:
        total = total + F.cross_entropy(out["strengthen_scores"][use],
                                        t_ti[use])

    # xy: bounded Gaussian NLL in normalized ego coords
    use = torch.as_tensor(["xy" in HEAD_FOR_VERB.get(v, ())
                           and f["target"].get("x") is not None
                           for f, v in zip(rows_feats, tverb)],
                          dtype=torch.bool, device=device)
    if use.any():
        mu, ls = out["xy_mu"][use], out["xy_logscale"][use]
        xy = torch.as_tensor(np.asarray(
            [[f["target"]["x"] / 350.0, f["target"]["y"] / 300.0]
             for f, v in zip(rows_feats, tverb)
             if "xy" in HEAD_FOR_VERB.get(v, ())
             and f["target"].get("x") is not None]),
            dtype=torch.float32, device=device).clamp(-1, 1)
        nll = (0.5 * ((xy - mu) / ls.exp()) ** 2 + ls).sum(-1).mean()
        total = total + lam_xy * nll
        parts["xy_nll"] = float(nll)

    # orientation
    use_m = torch.as_tensor([v == "MOVE_UNIT" and f["target"].get("rot", -1)
                             >= 0 for f, v in zip(rows_feats, tverb)],
                            dtype=torch.bool, device=device)
    if use_m.any():
        t_rot = torch.as_tensor([f["target"]["rot"] for f, v in
                                 zip(rows_feats, tverb)
                                 if v == "MOVE_UNIT"], device=device)
        total = total + F.cross_entropy(out["rot_move_logits"][use_m], t_rot)
    use_b = torch.as_tensor([v == "BUY_UNIT" and f["target"].get("rot", -1)
                             >= 0 for f, v in zip(rows_feats, tverb)],
                            dtype=torch.bool, device=device)
    if use_b.any():
        t_rot = torch.as_tensor(
            [1 if f["target"]["rot"] == 1 else 0 for f, v in
             zip(rows_feats, tverb) if v == "BUY_UNIT"], device=device)
        total = total + F.cross_entropy(out["rot_buy_logits"][use_b], t_rot)
    return total, parts, out, verbs


@torch.no_grad()
def eval_model(model, rows_feats, vocab, device, batch_size=512):
    model.eval()
    stats = {"verb_top1": 0, "verb_top3": 0, "n": 0, "xy_l1": 0.0,
             "xy_n": 0, "unit_top1": 0, "unit_n": 0, "head_top1": 0,
             "head_n": 0, "end_pred": 0, "end_tp": 0, "end_true": 0,
             "illegal_mass": 0.0}
    for i in range(0, len(rows_feats), batch_size):
        chunk = rows_feats[i:i + batch_size]
        batch = collate(chunk, vocab, device)
        _, _, out, _ = loss_fn(model, batch, chunk, device)
        vmask = torch.as_tensor(np.asarray([f["space"]["verb_mask"]
                                            for f in chunk]),
                                dtype=torch.float32, device=device)
        probs = F.softmax(out["verb_logits"] + (1 - vmask) * -1e9, dim=-1)
        preds = probs.argmax(-1)
        top3 = probs.topk(3, dim=-1).indices
        for j, f in enumerate(chunk):
            tv = f["target"]["verb"]
            vname = f["target_verb_name"]
            stats["n"] += 1
            stats["verb_top1"] += int(preds[j].item() == tv)
            stats["verb_top3"] += int(tv in top3[j].tolist())
            stats["illegal_mass"] += float(
                probs[j][torch.as_tensor(
                    [VERB_INDEX[v] for v, ok in zip(
                        ALL_VERBS, f["space"]["verb_mask"]) if not ok],
                    device=probs.device)].sum()) \
                if not all(f["space"]["verb_mask"]) else 0.0
            if vname == "END_DEPLOY":
                stats["end_true"] += 1
            if preds[j].item() == VERB_INDEX["END_DEPLOY"]:
                stats["end_pred"] += 1
                stats["end_tp"] += int(vname == "END_DEPLOY")
            for head, key, _pool_key in POINTER_SPECS:
                if f["target"].get(head, -1) >= 0 and head in \
                        HEAD_FOR_VERB.get(vname, ()):
                    sc = out.get(head + "_scores")
                    if sc is None:
                        continue
                    stats["head_n"] += 1
                    stats["head_top1"] += int(sc[j].argmax().item() ==
                                              f["target"][head])
            if f["target"].get("unit", -1) >= 0 and "unit" in \
                    HEAD_FOR_VERB.get(vname, ()):
                sc = out["unit_scores"] + (1 - out["unit_mask"]) * -1e9
                stats["unit_n"] += 1
                stats["unit_top1"] += int(sc[j].argmax().item() ==
                                          f["target"]["unit"])
            if "xy" in HEAD_FOR_VERB.get(vname, ()) and \
                    f["target"].get("x") is not None:
                mu = out["xy_mu"][j]
                stats["xy_l1"] += float(
                    (mu - torch.tensor([f["target"]["x"] / 350.0,
                                        f["target"]["y"] / 300.0],
                                       device=device)).abs().sum())
                stats["xy_n"] += 1
    model.train()
    n = max(stats["n"], 1)
    return {"verb_top1": stats["verb_top1"] / n,
            "verb_top3": stats["verb_top3"] / n,
            "unit_ptr_top1": stats["unit_top1"] / max(stats["unit_n"], 1),
            "ptr_top1": stats["head_top1"] / max(stats["head_n"], 1),
            "xy_l1_norm": stats["xy_l1"] / max(stats["xy_n"], 1),
            "end_precision": stats["end_tp"] / max(stats["end_pred"], 1),
            "end_recall": stats["end_tp"] / max(stats["end_true"], 1),
            "illegal_prob_mass": stats["illegal_mass"] / n,
            "n": stats["n"]}


def load_split(path, splits, vocab, gd, limit=0):
    op = gzip.open if path.endswith(".gz") else open
    feats = []
    with op(path, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r["split"] not in splits:
                continue
            feats.append(featurize_row(r, vocab, gd))
            if limit and len(feats) >= limit:
                break
    return feats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available()
                    else "cpu")
    ap.add_argument("--limit-train", type=int, default=150000)
    ap.add_argument("--tiny", action="store_true")
    args = ap.parse_args()
    ds = os.path.join(args.run_dir, "datasets")
    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    gd = GameData(os.path.join(ROOT, "data", "gamedata.json"))
    vocab = Vocab(gd)

    t0 = time.time()
    path = os.path.join(ds, "policy_prefix_real_v1.jsonl.gz")
    tr = load_split(path, {"train"}, vocab, gd,
                    limit=64 if args.tiny else args.limit_train)
    va = load_split(path, {"validation"}, vocab, gd)
    print("train %d val %d (%.0fs load)" % (len(tr), len(va),
                                            time.time() - t0))

    model = PolicyBC(vocab.n_mech, vocab.n_equip, vocab.n_tech).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    rng = np.random.RandomState(args.seed)
    model.train()
    for epoch in range(args.epochs):
        order = rng.permutation(len(tr))
        ep_loss, ep_parts, nb = 0.0, {}, 0
        for i in range(0, len(order), args.batch_size):
            idx = order[i:i + args.batch_size]
            chunk = [tr[j] for j in idx]
            batch = collate(chunk, vocab, device)
            loss, parts, _o, _v = loss_fn(model, batch, chunk, device)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            ep_loss += float(loss) * len(chunk)
            for k, v in parts.items():
                ep_parts[k] = ep_parts.get(k, 0.0) + v * len(chunk)
            nb += len(chunk)
        msg = {k: round(v / nb, 4) for k, v in ep_parts.items()}
        print("epoch %d loss=%.4f %s (%.0fs)" % (epoch, ep_loss / nb, msg,
                                                 time.time() - t0), flush=True)

    report = {"args": vars(args), "train_metrics": {}, "epochs": args.epochs}
    for name, rows in (("train_sub", tr[:5000]), ("validation", va)):
        if rows:
            report["train_metrics"][name] = eval_model(model, rows, vocab,
                                                       device)
    ck_dir = os.path.join(args.run_dir, "checkpoints")
    os.makedirs(ck_dir, exist_ok=True)
    ck = os.path.join(ck_dir, "policy_bc_seed%d.pt" % args.seed)
    torch.save({"model": model.state_dict(), "vocab": vocab.to_dict(),
                "args": vars(args)}, ck)
    report["checkpoint"] = ck
    with open(os.path.join(args.run_dir,
                           "policy_report_seed%d.json" % args.seed),
              "w") as f:
        json.dump(report, f, indent=1, default=str)
    print(json.dumps(report["train_metrics"], indent=1))


if __name__ == "__main__":
    main()
