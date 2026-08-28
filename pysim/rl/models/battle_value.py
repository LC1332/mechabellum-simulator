# DeepSets-style dual-domain battle value model (task §7).
#
# Architecture: shared unit encoder (mech/equip embeddings + float features),
# per-side sum|mean|max pooling, swap-antisymmetric advantage path
# g(self,opp) - g(opp,self), global MLP; two INDEPENDENT domain heads:
#   SimHead  <- pysim labels only
#   RealHead <- FightReport labels only
# (label routing is enforced in the loss: a batch never updates the other
# domain's head, task §12.2). Each head outputs WDL logits + the two ego
# damage components (to_opp, to_self).
import torch
import torch.nn as nn
import torch.nn.functional as F

from ..contracts import MAX_UNITS_PAD


class UnitEncoder(nn.Module):
    def __init__(self, n_mech: int, n_equip: int, d_model: int = 96):
        super().__init__()
        self.mech_emb = nn.Embedding(n_mech, 32, padding_idx=0)
        self.equip_emb = nn.Embedding(n_equip, 16, padding_idx=0)
        self.proj = nn.Sequential(
            nn.Linear(32 + 16 + 6, d_model), nn.SiLU(),
            nn.Linear(d_model, d_model), nn.SiLU())

    def forward(self, f, mech, equip, mask):
        # f: [B, N, 6] float; mech/equip: [B, N]; mask: [B, N]
        m = self.mech_emb(mech)                      # sum over padding zeros
        e = self.equip_emb(equip)
        x = torch.cat([f, m, e], dim=-1)
        return self.proj(x) * mask.unsqueeze(-1)


class DomainHead(nn.Module):
    def __init__(self, d_pool: int, d_model: int = 96,
                 tech_dim: int = 24, off_dim: int = 4, glob_dim: int = 15):
        super().__init__()
        self.adv = nn.Sequential(nn.Linear(d_pool * 2, d_model), nn.SiLU(),
                                 nn.Linear(d_model, d_model), nn.SiLU())
        self.glob = nn.Sequential(
            nn.Linear(d_model + tech_dim * 2 + off_dim + glob_dim, d_model),
            nn.SiLU(), nn.Linear(d_model, d_model), nn.SiLU())
        self.wdl = nn.Linear(d_model, 3)
        self.dmg = nn.Linear(d_model, 2)

    def forward(self, self_pool, opp_pool, tech_feat, off_feat, glob):
        # swap-antisymmetric advantage path (task §7.1)
        a1 = self.adv(torch.cat([self_pool, opp_pool], dim=-1))
        a2 = self.adv(torch.cat([opp_pool, self_pool], dim=-1))
        adv = a1 - a2
        h = self.glob(torch.cat([adv, tech_feat, off_feat, glob], dim=-1))
        return self.wdl(h), self.dmg(h)


class BattleValueNet(nn.Module):
    def __init__(self, n_mech: int, n_equip: int, n_tech: int = 256,
                 d_model: int = 96):
        super().__init__()
        self.unit = UnitEncoder(n_mech, n_equip, d_model)
        self.tech_emb = nn.Embedding(n_tech, 16, padding_idx=0)
        self.owner_emb = nn.Embedding(n_mech, 8, padding_idx=0)
        d_pool = d_model * 3
        self.sim_head = DomainHead(d_pool, d_model)
        self.real_head = DomainHead(d_pool, d_model)

    def _pool(self, enc, mask):
        s = (enc * mask.unsqueeze(-1)).sum(1)
        c = mask.sum(1, keepdim=True).clamp(min=1)
        mean = s / c
        neg = enc.masked_fill(mask.unsqueeze(-1) == 0, -1e9)
        mx = neg.max(1).values
        mx = torch.where(torch.isinf(mx), torch.zeros_like(mx), mx)
        return torch.cat([s, mean, mx], dim=-1)

    def _tech_feat(self, tech_ids, tech_owners):
        t = self.tech_emb(tech_ids)                    # [B, T, 16]
        o = self.owner_emb(tech_owners)
        x = torch.cat([t, o], dim=-1)
        # masked mean over tech dim
        mask = (tech_ids > 0).float().unsqueeze(-1)
        s = (x * mask).sum(1)
        c = mask.sum(1).clamp(min=1)
        return s / c

    def _side(self, prefix, f, mech, equip, mask, tech):
        enc = self.unit(f, mech, equip, mask)
        pool = self._pool(enc, mask)
        tech_feat = self._tech_feat(tech["tech_ids"], tech["tech_owners"])
        return pool, tech_feat

    def forward(self, batch, domain: str):
        """domain: 'sim' | 'real' — selects the head (label routing)."""
        sp, stf = self._side("self", batch["self_f"], batch["self_mech"],
                             batch["self_equip"], batch["self_mask"],
                             batch["self_tech"])
        op, otf = self._side("opp", batch["opp_f"], batch["opp_mech"],
                             batch["opp_equip"], batch["opp_mask"],
                             batch["opp_tech"])
        off = torch.cat([batch["self_off"], batch["opp_off"]], dim=-1)
        head = self.sim_head if domain == "sim" else self.real_head
        return head(sp, op, torch.cat([stf, otf], dim=-1), off,
                    batch["global"])

    @torch.no_grad()
    def v_rank(self, batch, domain: str, lambda_terminal: float = 0.25):
        """Scalar ranking value: E[damage_diff] + λ(p_win - p_loss)."""
        wdl, dmg = self.forward(batch, domain)
        p = F.softmax(wdl, dim=-1)
        return (dmg[:, 0] - dmg[:, 1]) + lambda_terminal * (p[:, 2] - p[:, 0])
