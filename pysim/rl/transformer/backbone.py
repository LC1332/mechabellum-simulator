# Shared entity-transformer backbone (任务书 §6.1/§6.2): typed token
# embedding + pre-LN self-attention blocks with the additive 2D relative
# bias. No ordinal positional embedding exists anywhere in this file —
# entity order is permutation-free by construction.
#
# SDPA note (§4.4): FlashAttention does not accept an additive float bias,
# so biased runs fall back to the efficient/math SDPA backends automatically
# (torch dispatches; the GPU probe tool records which backend ran and the
# throughput difference vs the unbiased/flash path).
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .relative_bias import RelativeBiasTable
from . import relative_bias as rb
from .tokenizer import N_TOKEN_TYPES, N_FEAT

NEG_INF = -1e9


class BiasedSelfAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.0):
        super().__init__()
        assert d_model % n_heads == 0
        self.h = n_heads
        self.dk = d_model // n_heads
        self.qkv = nn.Linear(d_model, 3 * d_model)
        self.proj = nn.Linear(d_model, d_model)
        self.drop = dropout

    def forward(self, x, attn_bias, pad_mask):
        # x [B,T,D], attn_bias [B,H,T,T] or None, pad_mask [B,T] (1=real)
        b, t, d = x.shape
        qkv = self.qkv(x).view(b, t, 3, self.h, self.dk)
        q, k, v = qkv.permute(2, 0, 3, 1, 4).unbind(0)     # [B,H,T,dk]
        mask = attn_bias
        if pad_mask is not None:
            keymask = (1.0 - pad_mask)[:, None, None, :] * NEG_INF
            mask = keymask if mask is None else mask + keymask
        out = F.scaled_dot_product_attention(
            q, k, v, attn_mask=mask,
            dropout_p=self.drop if self.training else 0.0)
        out = out.transpose(1, 2).reshape(b, t, d)
        return self.proj(out)


class EncoderBlock(nn.Module):
    def __init__(self, d_model: int, n_heads: int, d_ff: int,
                 dropout: float = 0.0):
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.attn = BiasedSelfAttention(d_model, n_heads, dropout)
        self.ln2 = nn.LayerNorm(d_model)
        self.ff = nn.Sequential(nn.Linear(d_model, d_ff), nn.SiLU(),
                                nn.Linear(d_ff, d_model))
        self.drop = nn.Dropout(dropout)

    def forward(self, x, attn_bias, pad_mask):
        x = x + self.drop(self.attn(self.ln1(x), attn_bias, pad_mask))
        x = x + self.drop(self.ff(self.ln2(x)))
        return x


class TokenBackbone(nn.Module):
    """[B,*] token arrays -> hidden states [B,T,D]."""

    def __init__(self, n_sem: int, d_model: int, n_layers: int, n_heads: int,
                 d_ff: int, dropout: float = 0.0, use_relbias: bool = True,
                 bias_edges: dict | None = None):
        super().__init__()
        self.d_model = d_model
        self.type_emb = nn.Embedding(N_TOKEN_TYPES, d_model)
        self.sem_emb = nn.Embedding(max(int(n_sem), 2), d_model)
        self.feat_proj = nn.Linear(N_FEAT, d_model)
        self.ln_in = nn.LayerNorm(d_model)
        self.use_relbias = use_relbias
        bb = bias_edges or {}
        self.bias = RelativeBiasTable(
            n_heads,
            dx_edges=tuple(bb.get("dx", rb.DEFAULT_DX_EDGES)),
            dy_edges=tuple(bb.get("dy", rb.DEFAULT_DY_EDGES)),
            dist_edges=tuple(bb.get("dist", rb.DEFAULT_DIST_EDGES)),
            zero=not use_relbias) if use_relbias else None
        self.blocks = nn.ModuleList(
            EncoderBlock(d_model, n_heads, d_ff, dropout)
            for _ in range(n_layers))
        self.ln_out = nn.LayerNorm(d_model)

    def forward(self, batch, components) -> torch.Tensor:
        # components: int64 [B,7,T,T] bias component indices (from
        # tokenizer.bias_components per sample, stacked+moved by the caller)
        x = self.type_emb(batch["type"]) + self.sem_emb(batch["sem"]) \
            + self.feat_proj(batch["feat"])
        x = self.ln_in(x)
        pad = batch["pad_mask"]
        bias = self.bias(components) if self.bias is not None else None
        for blk in self.blocks:
            x = blk(x, bias, pad)
        return self.ln_out(x)


def attention_pool(hidden: torch.Tensor, pad_mask: torch.Tensor,
                   query: nn.Parameter) -> torch.Tensor:
    """Permutation-invariant pooling: a learned query attends over the real
    tokens (padding excluded by -inf), softmax weights depend on values
    only (§4.1)."""
    scores = torch.einsum("btd,d->bt", hidden, query)
    scores = scores + (1.0 - pad_mask) * NEG_INF
    w = torch.softmax(scores, dim=-1)
    return torch.einsum("bt,btd->bd", w, hidden)
