# Shared metric functions (task §11 metrics.py): WDL/damage metrics used by
# the non-neural baselines, the neural trainers and the final report.
import numpy as np
import torch


def wdl_metrics(y_true, proba):
    eps = 1e-9
    y_true = np.asarray(y_true)
    nll = -np.log(np.clip(proba[np.arange(len(y_true)), y_true], eps, 1))
    onehot = np.eye(proba.shape[1])[y_true]
    brier = ((proba - onehot) ** 2).sum(axis=1)
    acc = float((proba.argmax(axis=1) == y_true).mean())
    accs = []
    for c in np.unique(y_true):
        m = y_true == c
        accs.append(float((proba[m].argmax(axis=1) == c).mean()))
    conf = np.zeros((proba.shape[1], proba.shape[1]), dtype=int)
    for t, p in zip(y_true, proba.argmax(axis=1)):
        conf[t, p] += 1
    return {"nll": float(nll.mean()), "brier": float(brier.mean()),
            "acc": acc, "balanced_acc": float(np.mean(accs)),
            "n": int(len(y_true)),
            "confusion": conf.tolist(),
            "class_dist": np.bincount(y_true,
                                      minlength=proba.shape[1]).tolist()}


def damage_metrics(y_true, y_pred):
    y_true = np.asarray(y_true)
    err = np.asarray(y_pred) - y_true
    return {"mae": float(np.abs(err).mean()),
            "rmse": float(np.sqrt((err ** 2).mean())),
            "bias": float(err.mean()),
            "q90": float(np.quantile(np.abs(err), 0.9))}


def ece(proba, y_true, n_bins=15):
    """Expected Calibration Error + reliability diagram data."""
    conf = proba.max(axis=1)
    pred = proba.argmax(axis=1)
    correct = (pred == np.asarray(y_true)).astype(float)
    bins = np.linspace(0, 1, n_bins + 1)
    ece_total, diagram = 0.0, []
    for i in range(n_bins):
        m = (conf > bins[i]) & (conf <= bins[i + 1])
        if not m.any():
            diagram.append({"bin": [float(bins[i]), float(bins[i + 1])],
                            "n": 0, "acc": None, "conf": None})
            continue
        acc, avg_conf = correct[m].mean(), conf[m].mean()
        ece_total += m.mean() * abs(acc - avg_conf)
        diagram.append({"bin": [float(bins[i]), float(bins[i + 1])],
                        "n": int(m.sum()), "acc": float(acc),
                        "conf": float(avg_conf)})
    return float(ece_total), diagram


def temperature_scale(logits_val, y_val, iters=200):
    """Fit temperature on validation only (task §7.2)."""
    t = torch.nn.Parameter(torch.ones(1))
    opt = torch.optim.LBFGS([t], lr=0.1, max_iter=iters)
    logits = torch.as_tensor(np.asarray(logits_val), dtype=torch.float32)
    y = torch.as_tensor(np.asarray(y_val), dtype=torch.long)

    def closure():
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(logits / t.clamp(min=1e-3),
                                                 y)
        loss.backward()
        return loss

    opt.step(closure)
    return float(t.detach())
