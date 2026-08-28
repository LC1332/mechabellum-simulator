# TValue (任务书 §6.1): encoder-only entity Transformer battle value with
# STRICTLY independent SimHead / RealHead. The shared-backbone question is a
# first-class ablation (§6.4-6): `shared_backbone=False` builds one encoder
# per domain; a domain's loss never touches the other domain's head OR
# private backbone either way.
#
# Side-swap Gate (§6.1/§10.2): formal inference uses the symmetrized
#   pred(s) = 0.5 * (f(s) + inverse_swap(f(swap(s))))
# with swap implemented as the exact token-level mirror (tokenizer
# .swap_token_arrays) and the bias components RE-DERIVED from the swapped
# geometry (exact, including dy==0 pairs). Training still uses the swap
# consistency loss; reports carry BOTH the raw f(s) asymmetry and the
# symmetrized residuals (≤1e-5 target).
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch
import torch.nn as nn

from .backbone import TokenBackbone, attention_pool
from .tokenizer import (SemanticVocab, TokenizerConfig, TokenArrays,
                        swap_token_arrays)
from . import relative_bias as rb


@dataclass
class TValueConfig:
    d_model: int = 192
    n_layers: int = 6
    n_heads: int = 6
    d_ff: int = 768
    dropout: float = 0.1
    use_relbias: bool = True
    shared_backbone: bool = True
    use_ranking_loss: bool = True      # §6.4-7 ablation switch
    use_swap_loss: bool = True
    lambda_damage: float = 1.0
    lambda_rank: float = 0.5
    lambda_sym: float = 0.05
    lambda_cal: float = 0.0            # optional calibration regularizer
    uncertainty: bool = True           # heteroscedastic damage logvars
    # tokenizer binding (frozen into checkpoints)
    tokenizer: dict = field(default_factory=lambda: TokenizerConfig().to_dict())

    @staticmethod
    def from_dict(d: dict) -> "TValueConfig":
        d = dict(d)
        tk = d.pop("tokenizer", None)
        c = TValueConfig(**{k: v for k, v in d.items()
                            if k in TValueConfig.__dataclass_fields__})
        if tk:
            c.tokenizer = tk
        return c

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__
             if k != "tokenizer"}
        d["tokenizer"] = self.tokenizer
        return d


class DomainHead(nn.Module):
    """Independent per-domain output head: WDL logits + ego damage
    (to_opp, to_self) [+ heteroscedastic logvars]."""

    def __init__(self, d_model: int, d_ff: int, uncertainty: bool = True):
        super().__init__()
        self.mlp = nn.Sequential(
            nn.Linear(d_model, d_ff), nn.SiLU(), nn.Linear(d_ff, d_ff),
            nn.SiLU(), nn.Linear(d_ff, d_model), nn.SiLU())
        self.wdl = nn.Linear(d_model, 3)
        self.dmg = nn.Linear(d_model, 2)
        self.logvar = nn.Linear(d_model, 2) if uncertainty else None

    def forward(self, pooled):
        h = self.mlp(pooled)
        return self.wdl(h), self.dmg(h), \
            (self.logvar(h) if self.logvar is not None else None)


class TValue(nn.Module):
    def __init__(self, vocab: SemanticVocab, cfg: TValueConfig,
                 tok_cfg: TokenizerConfig | None = None):
        super().__init__()
        self.cfg = cfg
        self.tok_cfg = tok_cfg or TokenizerConfig.from_dict(cfg.tokenizer)
        edges = self.tok_cfg.to_dict()["bias_buckets"]
        n_sem = max(vocab.sizes().values())
        if cfg.shared_backbone:
            self.backbone = TokenBackbone(
                n_sem, cfg.d_model, cfg.n_layers, cfg.n_heads, cfg.d_ff,
                cfg.dropout, cfg.use_relbias, edges)
            self.backbone_sim = self.backbone_real = None
        else:
            self.backbone = None
            self.backbone_sim = TokenBackbone(
                n_sem, cfg.d_model, cfg.n_layers, cfg.n_heads, cfg.d_ff,
                cfg.dropout, cfg.use_relbias, edges)
            self.backbone_real = TokenBackbone(
                n_sem, cfg.d_model, cfg.n_layers, cfg.n_heads, cfg.d_ff,
                cfg.dropout, cfg.use_relbias, edges)
        self.pool_query = nn.Parameter(torch.zeros(cfg.d_model))
        nn.init.normal_(self.pool_query, std=0.02)
        self.sim_head = DomainHead(cfg.d_model, cfg.d_ff, cfg.uncertainty)
        self.real_head = DomainHead(cfg.d_model, cfg.d_ff, cfg.uncertainty)

    # ------------------------------------------------------------ encode
    def _encoder(self, domain: str) -> TokenBackbone:
        if self.cfg.shared_backbone:
            return self.backbone
        return self.backbone_sim if domain == "sim" else self.backbone_real

    def forward(self, batch, components, domain: str):
        if domain not in ("sim", "real"):
            raise ValueError("domain must be sim|real, got %r" % domain)
        h = self._encoder(domain)(batch, components)
        pooled = attention_pool(h, batch["pad_mask"], self.pool_query)
        head = self.sim_head if domain == "sim" else self.real_head
        return head(pooled)

    # ------------------------------------------------- symmetrized (§6.1)
    def predict_symmetric(self, batch, components, domain: str):
        """0.5*(f(s) + inverse_swap(f(swap(s)))) — the FORMAL inference
        path for the side-swap Gate. Returns (wdl_probs, dmg)."""
        wdl, dmg, _ = self.forward(batch, components, domain)
        sw_batch, sw_comp = swapped_inputs(batch, components, self.tok_cfg)
        wdl2, dmg2, _ = self.forward(sw_batch, sw_comp, domain)
        # inverse_swap of a swapped prediction: WDL classes mirror
        # (loss, draw, win) -> (win, draw, loss); damage components swap
        wdl2 = torch.stack([wdl2[:, 2], wdl2[:, 1], wdl2[:, 0]], dim=-1)
        dmg2 = torch.stack([dmg2[:, 1], dmg2[:, 0]], dim=-1)
        wdl_s = torch.softmax(wdl, dim=-1) + torch.softmax(wdl2, dim=-1)
        return 0.5 * wdl_s, 0.5 * (dmg + dmg2)

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ------------------------------------------------------- batch plumbing
def batch_components(batch: dict, tok_cfg: TokenizerConfig,
                     device=None) -> torch.Tensor:
    """Vectorized torch bias components for a collated batch [B,7,T,T]
    (mirrors tokenizer.bias_components exactly; both paths are tested
    against relative_bias.pair_components)."""
    dev = device or batch["type"].device
    x = batch["x"].to(dev)
    y = batch["y"].to(dev)
    side = batch["side"].to(dev)
    air = batch["air"].to(dev)
    area = batch["area"].to(dev)
    grp = batch["group"].to(dev)
    dx = x[:, :, None] - x[:, None, :]
    dy = y[:, :, None] - y[:, None, :]
    dist = torch.hypot(dx, dy)

    def buck(values, edges):
        e = torch.as_tensor(edges, dtype=values.dtype, device=dev)
        return torch.searchsorted(e, values, right=True).to(torch.int64)

    n_grp = len(rb.TYPE_GROUPS)
    both_pos = (side[:, :, None] >= 0) & (side[:, None, :] >= 0)
    comp_side = torch.where(both_pos,
                            (side[:, :, None] != side[:, None, :]).long(), 2)
    same_air = air[:, :, None] == air[:, None, :]
    comp_air = torch.where(same_air,
                           (air[:, :, None] == 1).long(), 2)
    both_area = (area[:, :, None] >= 0) & (area[:, None, :] >= 0)
    comp_area = torch.where(both_area,
                            (area[:, :, None] != area[:, None, :]).long(), 2)
    comp = torch.stack([
        buck(dx, tok_cfg.dx_edges), buck(dy, tok_cfg.dy_edges),
        buck(dist, tok_cfg.dist_edges), comp_side,
        grp[:, :, None] * n_grp + grp[:, None, :], comp_air, comp_area,
    ], dim=1)
    # neutral sentinels land in fixed buckets (pad-independent bias)
    neutral_keys = (side < 0)[:, None, :]
    zdx = int(np.searchsorted(tok_cfg.dx_edges, 0.0, "right"))
    zdy = int(np.searchsorted(tok_cfg.dy_edges, 0.0, "right"))
    zd = int(np.searchsorted(tok_cfg.dist_edges, 0.0, "right"))
    comp[:, 0] = torch.where(neutral_keys, zdx, comp[:, 0])
    comp[:, 1] = torch.where(neutral_keys, zdy, comp[:, 1])
    comp[:, 2] = torch.where(neutral_keys, zd, comp[:, 2])
    neutral_q = (side[:, :, None] < 0) | (side[:, None, :] < 0)
    comp[:, 3] = torch.where(neutral_q, 2, comp[:, 3])
    neutral_air = (air[:, :, None] < 0) | (air[:, None, :] < 0)
    comp[:, 5] = torch.where(neutral_air, 2, comp[:, 5])
    neutral_area = (area[:, :, None] < 0) | (area[:, None, :] < 0)
    comp[:, 6] = torch.where(neutral_area, 2, comp[:, 6])
    return comp


def swapped_inputs(batch: dict, components: torch.Tensor,
                   tok_cfg: TokenizerConfig):
    """Exact side swap of a collated batch: mirror the token arrays, then
    RE-DERIVE the bias components from the mirrored geometry (exact for
    dy==0 pairs — no bucket reflection arithmetic)."""
    from .tokenizer import TT, FEAT_SLICES
    out = dict(batch)
    t = batch["type"]
    swap = torch.zeros_like(t)
    for a, b in ((TT["SELF_UNIT"], TT["OPP_UNIT"]),
                 (TT["SELF_TECH"], TT["OPP_TECH"]),
                 (TT["SELF_TOWER"], TT["OPP_TOWER"])):
        swap = torch.where(t == a, torch.full_like(t, b), swap)
        swap = torch.where(t == b, torch.full_like(t, a), swap)
    out["type"] = torch.where(swap > 0, swap, t)
    f = batch["feat"].clone()
    f[..., FEAT_SLICES["y"]] = -f[..., FEAT_SLICES["y"]]
    unitish = torch.isin(t, torch.as_tensor(
        [TT["SELF_UNIT"], TT["OPP_UNIT"]], device=t.device))
    f[..., FEAT_SLICES["rot"]] = torch.where(
        unitish, 1.0 - f[..., FEAT_SLICES["rot"]], f[..., FEAT_SLICES["rot"]])
    sided = torch.isin(t, torch.as_tensor(
        [TT["SELF_UNIT"], TT["OPP_UNIT"], TT["SELF_TECH"], TT["OPP_TECH"],
         TT["SELF_TOWER"], TT["OPP_TOWER"]], device=t.device))
    f[..., FEAT_SLICES["side"]] = torch.where(
        sided, 1.0 - f[..., FEAT_SLICES["side"]], f[..., FEAT_SLICES["side"]])
    glob = t == TT["GLOBAL"]
    for a, b in (("hp", "flag"), ("value", "cd")):
        ia, ib = FEAT_SLICES[a], FEAT_SLICES[b]
        f[..., ia] = torch.where(glob, batch["feat"][..., ib], f[..., ia])
        f[..., ib] = torch.where(glob, batch["feat"][..., ia], f[..., ib])
    out["feat"] = f
    pm = batch["pad_mask"] > 0
    y2 = batch["y"].clone()
    y2[pm] = -batch["y"][pm]
    out["y"] = y2
    side = batch["side"].clone()
    m = side >= 0
    side[m] = 1 - side[m]
    out["side"] = side
    return out, batch_components(out, tok_cfg)


def collate_components(arrays: list, tok_cfg: TokenizerConfig,
                       device=None) -> torch.Tensor:
    """Per-sample numpy components (tokenizer.bias_components) stacked —
    the CPU/test path; batch_components is the on-device equivalent."""
    comps = [tokenizer_bias_components(a, tok_cfg) for a in arrays]
    t = max(c.shape[-1] for c in comps)
    out = np.full((len(comps), 7, t, t), 0, dtype=np.int64)
    # neutral fill for padding: fixed buckets
    z = (np.searchsorted(tok_cfg.dx_edges, 0.0, "right"),
         np.searchsorted(tok_cfg.dy_edges, 0.0, "right"),
         np.searchsorted(tok_cfg.dist_edges, 0.0, "right"))
    for c_i, c in enumerate(comps):
        n = c.shape[-1]
        out[c_i, 0, :, :] = z[0]
        out[c_i, 1, :, :] = z[1]
        out[c_i, 2, :, :] = z[2]
        out[c_i, 3, :, :] = 2
        out[c_i, 5, :, :] = 2
        out[c_i, 6, :, :] = 2
        out[c_i, :, :n, :n] = c
    return torch.as_tensor(out, device=device)


def tokenizer_bias_components(ta: TokenArrays,
                              tok_cfg: TokenizerConfig) -> np.ndarray:
    from .tokenizer import bias_components
    return bias_components(ta, tok_cfg)
