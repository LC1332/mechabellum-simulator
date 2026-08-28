# pi_BC: prefix behaviour-cloning policy (task §4.5/§8.2).
#
# Hierarchical heads over a shared board encoder — no giant tokenizer:
#   verb head (masked CE over the profile verbs)
#     -> mech / unit / tech / equip / skill / tower / bp / contraption
#        pointer heads (masked over the observation's candidate pools)
#     -> bounded (x, y) Gaussian head (ego coords squashed to [-1, 1])
#     -> orientation head (MOVE: keep/rotate/standard; BUY: rot/standard)
# Loss terms apply ONLY for the heads the target verb parameterizes
# (task §4.5: 参数 head 只在对应 verb 下计算 loss).
import torch
import torch.nn as nn
import torch.nn.functional as F

from ..masks import ALL_VERBS, VERB_INDEX
from ..features import N_POLICY_GLOBAL


class BoardEncoder(nn.Module):
    """Same DeepSets core as the value model (verified unit encoder)."""

    def __init__(self, n_mech: int, n_equip: int, n_tech: int,
                 d_model: int = 96):
        super().__init__()
        self.mech_emb = nn.Embedding(n_mech, 32, padding_idx=0)
        self.equip_emb = nn.Embedding(n_equip, 16, padding_idx=0)
        self.unit_proj = nn.Sequential(
            nn.Linear(32 + 16 + 6, d_model), nn.SiLU(),
            nn.Linear(d_model, d_model), nn.SiLU())
        self.tech_emb = nn.Embedding(n_tech, 16, padding_idx=0)
        self.owner_emb = nn.Embedding(n_mech, 8, padding_idx=0)
        self.glob_proj = nn.Sequential(
            nn.Linear(N_POLICY_GLOBAL + 4 * d_model + 48, d_model), nn.SiLU(),
            nn.Linear(d_model, d_model), nn.SiLU())
        self.d_model = d_model

    def forward(self, b):
        enc = []
        for side in ("self", "opp"):
            x = torch.cat([b[side + "_f"],
                           self.mech_emb(b[side + "_mech"]),
                           self.equip_emb(b[side + "_equip"])], dim=-1)
            e = self.unit_proj(x) * b[side + "_mask"].unsqueeze(-1)
            enc.append(e)
        own, opp = enc
        m_own = b["self_mask"].sum(1, keepdim=True).clamp(min=1)
        m_opp = b["opp_mask"].sum(1, keepdim=True).clamp(min=1)
        stats = torch.cat([
            (own * b["self_mask"].unsqueeze(-1)).sum(1) / m_own,
            (opp * b["opp_mask"].unsqueeze(-1)).sum(1) / m_opp,
            own.max(1).values, opp.max(1).values,
        ], dim=-1)
        techs = []
        for side in ("self", "opp"):
            ids, ownr = b[side + "_tech"]["tech_ids"], \
                b[side + "_tech"]["tech_owners"]
            mask = (ids > 0).float().unsqueeze(-1)
            x = torch.cat([self.tech_emb(ids), self.owner_emb(ownr)],
                          dim=-1)
            techs.append((x * mask).sum(1) / mask.sum(1).clamp(min=1))
        g = self.glob_proj(torch.cat([b["global"], stats,
                                      techs[0], techs[1]], dim=-1))
        return g, own, b["self_mask"]


def _pointer(emb, ctx, proj):
    """Score candidates against the context (MLP over concat)."""
    T = emb.shape[1]
    ctx_exp = ctx.unsqueeze(1).expand(-1, T, -1)
    return proj(torch.cat([emb, ctx_exp], dim=-1)).squeeze(-1)


class PolicyBC(nn.Module):
    def __init__(self, n_mech: int, n_equip: int, n_tech: int,
                 d_model: int = 96, max_units: int = 64):
        super().__init__()
        self.encoder = BoardEncoder(n_mech, n_equip, n_tech, d_model)
        d = d_model
        self.verb_head = nn.Sequential(nn.Linear(d, d), nn.SiLU(),
                                       nn.Linear(d, len(ALL_VERBS)))
        E = 32
        self.mech_emb = nn.Embedding(n_mech, E, padding_idx=0)
        # pointer-head id tables index hashed candidate ids (mod 64;
        # collisions are harmless for ranking)
        self.equip_emb2 = nn.Embedding(64, E, padding_idx=0)
        self.tech_emb2 = nn.Embedding(n_tech, E, padding_idx=0)
        self.skill_emb = nn.Embedding(1024, E, padding_idx=0)
        self.tower_emb = nn.Embedding(64, E, padding_idx=0)
        self.bp_emb = nn.Embedding(64, E, padding_idx=0)
        self.contr_emb = nn.Embedding(64, E, padding_idx=0)
        self.tgt_emb = nn.Embedding(3, 8, padding_idx=0)   # skill target kind
        self.mech_proj = nn.Sequential(nn.Linear(E + d, 64), nn.SiLU(),
                                       nn.Linear(64, 1))
        self.unit_proj = nn.Sequential(nn.Linear(d + d, 64), nn.SiLU(),
                                       nn.Linear(64, 1))
        self.tech_proj = nn.Sequential(nn.Linear(E * 2 + d, 64), nn.SiLU(),
                                       nn.Linear(64, 1))
        self.equip_proj = nn.Sequential(nn.Linear(E + d, 64), nn.SiLU(),
                                        nn.Linear(64, 1))
        self.skill_proj = nn.Sequential(nn.Linear(E + 8 + d, 64), nn.SiLU(),
                                        nn.Linear(64, 1))
        self.tower_proj = nn.Sequential(nn.Linear(E + d, 32), nn.SiLU(),
                                        nn.Linear(32, 1))
        self.bp_proj = nn.Sequential(nn.Linear(E + d, 32), nn.SiLU(),
                                     nn.Linear(32, 1))
        self.contr_proj = nn.Sequential(nn.Linear(E + d, 32), nn.SiLU(),
                                        nn.Linear(32, 1))
        self.strengthen_proj = nn.Sequential(nn.Linear(d, 32), nn.SiLU(),
                                             nn.Linear(32, 1))
        # bounded continuous (x, y): Gaussian over tanh-squashed params
        self.xy_head = nn.Sequential(nn.Linear(d, 64), nn.SiLU(),
                                     nn.Linear(64, 4))   # mu_x, mu_y, log_s_x, log_s_y
        self.rot_move = nn.Linear(d, 3)                    # keep/rot/standard
        self.rot_buy = nn.Linear(d, 2)

    def forward(self, b, space):
        """Returns dict of head outputs. `space` carries the variable
        candidate pools as padded tensors + masks (built by collate)."""
        ctx, unit_enc, unit_mask = self.encoder(b)
        out = {"verb_logits": self.verb_head(ctx), "ctx": ctx}
        if space.get("mech_ids") is not None:
            emb = self.mech_emb(space["mech_ids"])
            out["mech_scores"] = _pointer(emb, ctx, self.mech_proj)
        if space.get("tech_ids") is not None:
            emb = torch.cat([self.tech_emb2(space["tech_ids"][:, :, 0]),
                             self.mech_emb(space["tech_ids"][:, :, 1])],
                            dim=-1)
            out["tech_scores"] = self.tech_proj(
                torch.cat([emb, ctx.unsqueeze(1).expand(
                    -1, emb.shape[1], -1)], dim=-1)).squeeze(-1)
        if space.get("equip_ids") is not None:
            out["equip_scores"] = _pointer(self.equip_emb2(space["equip_ids"]),
                                           ctx, self.equip_proj)
        if space.get("skill_ids") is not None:
            kinds = space.get("skill_kinds")
            if kinds is None:
                kinds = torch.ones_like(space["skill_ids"])
            emb = torch.cat([self.skill_emb(space["skill_ids"].clamp(
                max=1023)), self.tgt_emb(kinds)], dim=-1)
            out["skill_scores"] = _pointer(emb, ctx, self.skill_proj)
        if space.get("tower_ids") is not None:
            out["tower_scores"] = _pointer(self.tower_emb(space["tower_ids"]),
                                           ctx, self.tower_proj)
        if space.get("bp_ids") is not None:
            out["bp_scores"] = _pointer(self.bp_emb(space["bp_ids"]),
                                        ctx, self.bp_proj)
        if space.get("contr_ids") is not None:
            out["contr_scores"] = _pointer(self.contr_emb(space["contr_ids"]),
                                           ctx, self.contr_proj)
        # unit pointer scores the live unit encodings
        out["unit_scores"] = self.unit_proj(torch.cat(
            [unit_enc, ctx.unsqueeze(1).expand(-1, unit_enc.shape[1], -1)],
            dim=-1)).squeeze(-1)
        out["unit_mask"] = unit_mask
        xy = self.xy_head(ctx)
        out["xy_mu"] = torch.tanh(xy[:, :2])          # [-1, 1]
        out["xy_logscale"] = xy[:, 2:].clamp(-4, 1)
        out["rot_move_logits"] = self.rot_move(ctx)
        out["rot_buy_logits"] = self.rot_buy(ctx)
        if space.get("strengthen") is not None:
            ctx2 = ctx.unsqueeze(1).expand(-1, 2, -1)
            out["strengthen_scores"] = self.strengthen_proj(
                torch.cat([ctx2, space["strengthen"].unsqueeze(-1)],
                          dim=-1)).squeeze(-1)
        return out
