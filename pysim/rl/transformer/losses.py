# Losses (任务书 §8): strict domain routing for TValue (§8.1) + the
# structured masked-stage policy loss (§8.2). Every stage CE is a MASKED CE
# over legal candidates only (§5.1); the unmasked illegal probability mass
# is REPORTED, never trained on. Ranking only compares candidates INSIDE
# one candidate group; the swap-consistency term accompanies (never
# replaces) the honest raw-asymmetry report.
from __future__ import annotations

import math

import torch
import torch.nn.functional as F

from .battle_value import swapped_inputs

NEG_INF = -1e9


# ---------------------------------------------------------------- value
def value_loss(model, batch, components, targets, cfg, tok_cfg, domain: str):
    """targets keys: wdl_soft [B,3] | wdl [B]; dmg [B,2] (normalized);
    group_id [B] long (-1 = no group)."""
    wdl, dmg, logvar = model(batch, components, domain)
    parts = {}
    if targets.get("wdl_soft") is not None:
        ce = -(targets["wdl_soft"] * F.log_softmax(wdl, dim=-1)).sum(-1).mean()
    else:
        ce = F.cross_entropy(wdl, targets["wdl"])
    parts["ce"] = float(ce.detach())
    loss = ce

    dv = targets["dmg"]
    if cfg.uncertainty and logvar is not None:
        nll = 0.5 * (logvar + (dv - dmg) ** 2 / torch.exp(logvar)
                     + math.log(2 * math.pi))
        hub = nll.mean()
    else:
        hub = F.huber_loss(dmg, dv, delta=0.1)
    parts["damage"] = float(hub.detach())
    loss = loss + cfg.lambda_damage * hub

    if cfg.use_ranking_loss and targets.get("group_id") is not None:
        rank = ranking_loss(wdl, dmg, targets["group_id"])
        parts["rank"] = float(rank.detach())
        loss = loss + cfg.lambda_rank * rank

    if cfg.use_swap_loss:
        sw_b, sw_c = swapped_inputs(batch, components, tok_cfg)
        wdl2, dmg2, _ = model(sw_b, sw_c, domain)
        wdl2_inv = torch.stack([wdl2[:, 2], wdl2[:, 1], wdl2[:, 0]], dim=-1)
        dmg2_inv = torch.stack([dmg2[:, 1], dmg2[:, 0]], dim=-1)
        sym = (F.softmax(wdl, -1) - F.softmax(wdl2_inv, -1)).abs().mean() \
            + (dmg - dmg2_inv).abs().mean()
        parts["sym"] = float(sym.detach())
        loss = loss + cfg.lambda_sym * sym

    if cfg.lambda_cal > 0:
        # overconfidence penalty: (top prob - P(true)) gap; the proper
        # temperature scaling stays a validation-only fit (§8.1)
        with torch.no_grad():
            if targets.get("wdl") is not None:
                y = targets["wdl"]
            else:
                y = targets["wdl_soft"].argmax(-1)
        p = F.softmax(wdl, -1)
        correct = p.gather(1, y[:, None]).squeeze(1).detach()
        cal = ((p.max(-1).values - correct) ** 2).mean()
        loss = loss + cfg.lambda_cal * cal

    return loss, parts


def ranking_loss(wdl_logits, dmg, group_id):
    """Pairwise logistic ranking INSIDE candidate groups only (§8.1):
    score = normalized damage diff + 0.25 * (p_win - p_loss)."""
    p = F.softmax(wdl_logits, dim=-1)
    score = (dmg[:, 0] - dmg[:, 1]) + 0.25 * (p[:, 2] - p[:, 0])
    loss, n = wdl_logits.new_zeros(()), 0
    for g in torch.unique(group_id):
        if int(g) < 0:
            continue
        idx = (group_id == g).nonzero(as_tuple=True)[0]
        if idx.numel() < 2:
            continue
        s = score[idx]
        y = (dmg[idx, 0] - dmg[idx, 1]).detach()
        for i in range(idx.numel()):
            for j in range(i + 1, idx.numel()):
                if abs(float(y[i] - y[j])) < 1e-6:
                    continue
                sign = 1.0 if float(y[i]) > float(y[j]) else -1.0
                loss = loss + F.softplus(-sign * (s[i] - s[j]))
                n += 1
    return loss / max(n, 1)


# ---------------------------------------------------------------- policy
# stage table: (logit key, target column in the fields vector); the
# candidate-legality mask for each stage is supplied per row by the caller
# (gathered from the space tables BY THE TARGET VERB)
STAGES = (
    ("verb", 0), ("obj", 1), ("ptr", 2),
    ("P1C", 3), ("P1X", 4), ("P1Y", 5),
    ("P2C", 6), ("P2X", 7), ("P2Y", 8),
    ("P3C", 9), ("P3X", 10), ("P3Y", 11),
    ("ori", 12),
)


def policy_stage_losses(logits, fields, stage_masks=None, end_flag=None,
                        rem_bucket=None):
    """Masked CE per stage (§8.2): -100 targets are ABSENT stages and drop
    out — a parameter head trains only under its verb/arity. `stage_masks`
    maps stage -> [B, width] float legality (rows gathered by the target
    verb; rx/ry/ori default to all-legal)."""
    out, illegal = {}, {}
    total = None
    for key, col in STAGES:
        if key not in logits:
            continue
        tgt = fields[:, col]
        active = tgt != -100
        if int(active.sum()) == 0:
            continue
        lg = logits[key][active]
        t = tgt[active].clamp(min=0)
        mask = None
        if stage_masks and key in stage_masks:
            m = stage_masks[key][active].to(lg.dtype)
            width = lg.shape[1]
            m = m[:, :width] if m.shape[1] >= width else \
                F.pad(m, (0, width - m.shape[1]))
            lg = lg + (1.0 - m) * NEG_INF
            mask = m
        ce = F.cross_entropy(lg, t)
        out["loss_" + key] = float(ce.detach())
        total = ce if total is None else total + ce
        with torch.no_grad():
            p = F.softmax(logits[key][active], dim=-1)   # UNMASKED mass
            legal = torch.zeros_like(p)
            if mask is not None:
                legal = mask
            legal = legal.scatter(1, t[:, None].clamp(max=p.shape[1] - 1),
                                  1.0)
            illegal[key] = float((p * (1.0 - legal)).sum(-1).mean())
    if "end" in logits and end_flag is not None:
        ce_end = F.cross_entropy(logits["end"], end_flag)
        out["loss_end"] = float(ce_end.detach())
        total = ce_end if total is None else total + ce_end
    if "rem" in logits and rem_bucket is not None:
        ce_rem = F.cross_entropy(logits["rem"], rem_bucket)
        out["loss_rem"] = float(ce_rem.detach())
        total = ce_rem if total is None else total + ce_rem
    out["illegal"] = illegal
    out["total"] = total
    return out


def build_stage_masks(fields, tables, device):
    """Per-row stage legality gathered BY THE TARGET VERB (§5.1):
    verb <- verb_mask; obj/ptr/coarse <- that verb's candidate column."""
    verb = fields[:, 0].clamp(min=0)
    obj_mask = tables["obj_mask"].to(device)         # [B,13,O]
    ptr_mask = tables["ptr_mask"].to(device)         # [B,13,P]
    xy_legal = tables["xy_legal"].to(device)         # [B,13,G]
    O, P, G = obj_mask.shape[-1], ptr_mask.shape[-1], xy_legal.shape[-1]
    row_v = verb[:, None]
    return {
        "verb": tables["verb_mask"].to(device),
        "obj": obj_mask.gather(1, row_v[:, :, None].expand(-1, 1, O)
                               ).squeeze(1),
        "ptr": ptr_mask.gather(1, row_v[:, :, None].expand(-1, 1, P)
                               ).squeeze(1),
        "P1C": xy_legal.gather(1, row_v[:, :, None].expand(-1, 1, G)
                               ).squeeze(1),
        "P2C": xy_legal.gather(1, row_v[:, :, None].expand(-1, 1, G)
                               ).squeeze(1),
        "P3C": xy_legal.gather(1, row_v[:, :, None].expand(-1, 1, G)
                               ).squeeze(1),
    }
