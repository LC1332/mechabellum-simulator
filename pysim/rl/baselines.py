# T1 non-neural baselines + health checks (task §6).
#
# Value: constant prior, logistic regression, HistGradientBoosting on
#        structured features.
# Policy: END-only, global frequent verb, conditional frequent verb (per
#         round bucket), random-legal — all evaluated teacher-forced.
# Health: label-shuffle sanity, tiny-set overfit probe, duplicate detector.
from __future__ import annotations

import gzip
import json
import numpy as np

from .features import Vocab, battle_features, UNIT_FLOATS
from .contracts import MAX_UNITS_PAD
from .metrics import wdl_metrics, damage_metrics, ece, temperature_scale  # noqa: F401


# ---------------------------------------------------------------- loaders
def load_rows(path, split=None, tier=None, corpus=None, limit=0):
    rows = []
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf8") as f:
        for line in f:
            r = json.loads(line)
            if split is not None and r.get("split") != split:
                continue
            if tier is not None and r.get("tier") != tier:
                continue
            if corpus is not None and r.get("corpus") != corpus:
                continue
            rows.append(r)
            if limit and len(rows) >= limit:
                break
    return rows


def value_matrix(rows, vocab: Vocab):
    """Flat feature matrix for tree/linear baselines: pooled unit stats +
    global block (order-free so permutation invariance is trivial)."""
    X = []
    for r in rows:
        f = battle_features(r["observation"], vocab)
        xf = []
        for side in ("self", "opp"):
            m = f[side + "_mask"]
            n = max(m.sum(), 1)
            xf.extend([
                (f[side + "_f"][:, 0] * m).sum() / n,       # mean level
                (f[side + "_f"][:, 1] * m).sum() / n,       # mean exp
                (f[side + "_f"][:, 2] * m).sum(),           # x centroid
                (f[side + "_f"][:, 3] * m).sum(),           # y centroid
                float(m.sum()),                             # unit count
                (f[side + "_f"][:, 5] * m).sum(),           # rotated count
            ])
        mech_hist = np.bincount(
            f["self_mech"][f["self_mask"] > 0],
            minlength=vocab.n_mech)[:60] / max(f["self_mask"].sum(), 1)
        xf.extend(0.5 * mech_hist)
        mech_hist_o = np.bincount(
            f["opp_mech"][f["opp_mask"] > 0],
            minlength=vocab.n_mech)[:60] / max(f["opp_mask"].sum(), 1)
        xf.extend(0.5 * mech_hist_o)
        xf.extend(f["global"].tolist())
        X.append(xf)
    return np.asarray(X, dtype=np.float32)


def value_targets(rows):
    y_wdl = np.asarray([r["y_wdl"] for r in rows], dtype=np.int64)
    y_dmg = np.asarray([[r["y_damage_to_opp"], r["y_damage_to_self"]]
                        for r in rows], dtype=np.float32)
    return y_wdl, y_dmg


# ---------------------------------------------------------------- value
def constant_prior(y_wdl, y_dmg):
    p = np.bincount(y_wdl, minlength=3) / max(len(y_wdl), 1)
    return {"probs": p.tolist(),
            "damage_mean": y_dmg.mean(axis=0).tolist()}


def fit_value_baselines(X_tr, y_wdl_tr, y_dmg_tr, seed=0):
    """Returns predictors {name: (proba_fn, damage_fn)}."""
    out = {}
    prior = np.bincount(y_wdl_tr, minlength=3) / max(len(y_wdl_tr), 1)
    dmg_mean = y_dmg_tr.mean(axis=0)
    out["constant_prior"] = (lambda X: np.tile(prior, (len(X), 1)),
                             lambda X: np.tile(dmg_mean, (len(X), 1)))
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier

    def _full3(proba, classes):
        out = np.zeros((len(proba), 3))
        out[:, classes] = proba
        out = out / out.sum(axis=1, keepdims=True)
        return out

    lr = LogisticRegression(max_iter=2000, C=1.0,
                            random_state=seed).fit(X_tr, y_wdl_tr)
    hgb = HistGradientBoostingClassifier(max_iter=300, max_depth=6,
                                         learning_rate=0.08,
                                         random_state=seed,
                                         early_stopping=False)\
        .fit(X_tr, y_wdl_tr)
    lr_classes, hgb_classes = lr.classes_, hgb.classes_
    out["logistic"] = (lambda X: _full3(lr.predict_proba(X), lr_classes),
                       lambda X: np.tile(dmg_mean, (len(X), 1)))
    out["hgb"] = (lambda X: _full3(hgb.predict_proba(X), hgb_classes),
                  lambda X: np.tile(dmg_mean, (len(X), 1)))
    return out


# ---------------------------------------------------------------- policy
VERBS = ("END_DEPLOY", "BUY_UNIT", "UNLOCK_UNIT", "UPGRADE_UNIT", "BUY_TECH",
         "MOVE_UNIT", "SELL_UNIT", "USE_EQUIPMENT", "RELEASE_COMMANDER_SKILL",
         "ACTIVATE_ENERGY_TOWER_SKILL", "STRENGTHEN_TOWER",
         "ACTIVE_BLUEPRINT", "RELEASE_CONTRAPTION")
VERB_ID = {v: i for i, v in enumerate(VERBS)}


def verb_stats(rows):
    """(global verb distribution, per-round-bucket conditional)."""
    glob = np.zeros(len(VERBS))
    by_bucket = {}
    for r in rows:
        v = r["target"]["verb"]
        name = VERBS[v] if v < len(VERBS) else "END_DEPLOY"
        glob[VERB_ID[name]] += 1
        b = "r1-2" if r["round"] <= 2 else ("r3-5" if r["round"] <= 5
                                            else "r6+")
        by_bucket.setdefault(b, np.zeros(len(VERBS)))
        by_bucket[b][VERB_ID[name]] += 1
    return glob / max(glob.sum(), 1), {
        k: v / max(v.sum(), 1) for k, v in by_bucket.items()}


def policy_baseline_predict(name, row, stats):
    """Return (verb probs, chosen verb id) under a baseline policy."""
    vmask = np.asarray(row["space"]["verb_mask"], dtype=bool)
    verbs_row = row["space"]["verbs"]
    glob, by_bucket = stats
    if name == "end_only":
        prob = np.zeros(len(glob))
        prob[VERB_ID["END_DEPLOY"]] = 1.0
    elif name == "freq_global":
        prob = glob
    elif name == "freq_cond":
        b = "r1-2" if row["round"] <= 2 else ("r3-5" if row["round"] <= 5
                                              else "r6+")
        prob = by_bucket.get(b, glob)
    elif name == "random_legal":
        prob = vmask.astype(float)
    else:
        raise ValueError(name)
    prob = prob * vmask
    s = prob.sum()
    choice = VERB_ID["END_DEPLOY"] if s <= 0 else \
        int(np.argmax(prob))
    return prob, choice


# ---------------------------------------------------------------- health
def label_shuffle_check(rows, seed=0):
    """Shuffle WDL across replay groups; downstream value metrics must drop
    to prior level (executed by the trainer via --shuffle-labels)."""
    import random
    rng = random.Random(seed)
    ys = [r["y_wdl"] for r in rows]
    rng.shuffle(ys)
    return ys


def duplicate_detector(rows):
    """Same (match_id_hash, round, ego_side) must appear once per split —
    leaks across splits are the failure mode."""
    seen = {}
    leaks = []
    for r in rows:
        key = (r["match_id_hash"], r["round"], r.get("ego_side", 0))
        sp = r["split"]
        if key in seen and seen[key] != sp:
            leaks.append(key)
        seen[key] = sp
    return leaks


def observation_leakage_scan(rows, n=2000):
    """Label fields must not appear inside the observation payload."""
    bad = []
    for r in rows[:n]:
        blob = json.dumps(r.get("observation") or r.get("obs"))
        for label in ("y_wdl", "y_damage", "winner", "preRoundFightResult"):
            if label in blob:
                bad.append((r["sample_id"], label))
    return bad
