# TPolicy-BC (任务书 §6.2): entity encoder + structured autoregressive
# decoder for one atomic action at a time (§5). The decoder chain is
#   BOS -> VERB -> OBJ -> PTR -> P1C->P1X->P1Y -> ... -> ORI -> COMMIT
# with legality masks applied BEFORE softmax at every stage (§5.1/§6.2):
# verb <- space.verb_mask; object <- per-verb candidate mask; pointer <-
# per-verb unit mask; coarse xy <- per-verb bounds mask. Pointer stages
# score ENCODER TOKENS of own units (observation-local handles — never
# permanent ids, §5.1). AUX: P(end now) + remaining-action bucket at BOS
# (§6.2, Phase 1 END failure counter-measure).
from __future__ import annotations

import math
from dataclasses import dataclass, field

import torch
import torch.nn as nn
import torch.nn.functional as F

from .backbone import TokenBackbone, NEG_INF
from .tokenizer import (SemanticVocab, TokenizerConfig, DECODE_SLOTS,
                        N_SLOTS, SLOT_INDEX, MAX_POINTS, ActionFields,
                        VERBS_13)
from .policy_arity import N_VERBS, VERB_INDEX
from .battle_value import batch_components

# which slot's hidden state feeds which head, and the head sizes
VERB_HEAD_SLOTS = {"verb": "BOS", "end": "BOS", "rem": "BOS",
                   "obj": "VERB", "ptr": "OBJ",
                   "P1C": "PTR", "P1X": "P1C", "P1Y": "P1X",
                   "P2C": "P1Y", "P2X": "P2C", "P2Y": "P2X",
                   "P3C": "P2Y", "P3X": "P3C", "P3Y": "P3X",
                   "ori": "ORI"}
# where the ORIENTATION condition comes from when fewer points exist
ORI_COND_FALLBACK = {0: "PTR", 1: "P1Y", 2: "P2Y", 3: "P3Y"}


@dataclass
class TPolicyConfig:
    d_model: int = 192
    n_layers_enc: int = 6
    n_layers_dec: int = 3
    n_heads: int = 6
    d_ff: int = 768
    dropout: float = 0.1
    use_relbias: bool = True
    use_history: bool = True           # §6.4-4 ablation: strip history tokens
    use_end_aux: bool = True           # §6.4-5 ablation
    lambda_end: float = 0.25
    max_obj_cands: int = 512
    max_ptr_cands: int = 64
    n_rem_buckets: int = 9
    tokenizer: dict = field(default_factory=lambda: TokenizerConfig().to_dict())

    @staticmethod
    def from_dict(d: dict) -> "TPolicyConfig":
        d = dict(d)
        tk = d.pop("tokenizer", None)
        c = TPolicyConfig(**{k: v for k, v in d.items()
                             if k in TPolicyConfig.__dataclass_fields__})
        if tk:
            c.tokenizer = tk
        return c

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__
             if k != "tokenizer"}
        d["tokenizer"] = self.tokenizer
        return d


class CrossAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.0):
        super().__init__()
        self.h = n_heads
        self.dk = d_model // n_heads
        self.q = nn.Linear(d_model, d_model)
        self.kv = nn.Linear(d_model, 2 * d_model)
        self.proj = nn.Linear(d_model, d_model)

    def forward(self, x, mem, mem_mask):
        # x [B,S,D], mem [B,T,D], mem_mask [B,T] (1=real)
        b, s, d = x.shape
        t = mem.shape[1]
        q = self.q(x).view(b, s, self.h, self.dk).transpose(1, 2)
        kv = self.kv(mem).view(b, t, 2, self.h, self.dk).permute(2, 0, 3, 1, 4)
        k, v = kv[0], kv[1]
        mask = (1.0 - mem_mask)[:, None, None, :] * NEG_INF
        out = F.scaled_dot_product_attention(q, k, v, attn_mask=mask)
        out = out.transpose(1, 2).reshape(b, s, d)
        return self.proj(out)


class DecoderBlock(nn.Module):
    def __init__(self, d_model: int, n_heads: int, d_ff: int,
                 dropout: float = 0.0):
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.self_attn = nn.MultiheadAttention(d_model, n_heads,
                                               dropout=dropout,
                                               batch_first=True)
        self.ln2 = nn.LayerNorm(d_model)
        self.cross = CrossAttention(d_model, n_heads, dropout)
        self.ln3 = nn.LayerNorm(d_model)
        self.ff = nn.Sequential(nn.Linear(d_model, d_ff), nn.SiLU(),
                                nn.Linear(d_ff, d_model))
        self.drop = nn.Dropout(dropout)

    def forward(self, x, mem, mem_mask):
        b, s, _ = x.shape
        causal = torch.triu(
            torch.ones(s, s, dtype=torch.bool, device=x.device), 1)
        h = self.ln1(x)
        a, _ = self.self_attn(h, h, h, attn_mask=causal, need_weights=False)
        x = x + self.drop(a)
        x = x + self.drop(self.cross(self.ln2(x), mem, mem_mask))
        x = x + self.drop(self.ff(self.ln3(x)))
        return x


class TPolicyBC(nn.Module):
    def __init__(self, vocab: SemanticVocab, cfg: TPolicyConfig,
                 tok_cfg: TokenizerConfig | None = None):
        super().__init__()
        self.cfg = cfg
        self.tok_cfg = tok_cfg or TokenizerConfig.from_dict(cfg.tokenizer)
        edges = self.tok_cfg.to_dict()["bias_buckets"]
        n_sem = max(vocab.sizes().values())
        self.encoder = TokenBackbone(
            n_sem, cfg.d_model, cfg.n_layers_enc, cfg.n_heads, cfg.d_ff,
            cfg.dropout, cfg.use_relbias, edges)
        d = cfg.d_model
        self.slot_emb = nn.Embedding(N_SLOTS, d)
        nx, ny, r = self.tok_cfg.grid_nx, self.tok_cfg.grid_ny, \
            self.tok_cfg.residual_bins
        self.n_coarse = nx * ny
        self.field_emb = nn.ModuleDict({
            "verb": nn.Embedding(N_VERBS + 1, d),
            "obj": nn.Embedding(cfg.max_obj_cands + 1, d),
            "ptr": nn.Embedding(cfg.max_ptr_cands + 1, d),
            "coarse": nn.Embedding(self.n_coarse, d),
            "res": nn.Embedding(max(r, 2), d),
            "ori": nn.Embedding(4, d),
            "none": nn.Embedding(1, d),
        })
        self.dec = nn.ModuleList(
            DecoderBlock(d, cfg.n_heads, cfg.d_ff, cfg.dropout)
            for _ in range(cfg.n_layers_dec))
        self.ln_dec = nn.LayerNorm(d)
        self.head_verb = nn.Linear(d, N_VERBS)
        self.head_obj = nn.Linear(d, cfg.max_obj_cands)
        self.head_ptr = nn.Linear(d, cfg.max_ptr_cands)
        self.head_coarse = nn.Linear(d, self.n_coarse)
        self.head_rx = nn.Linear(d, r)
        self.head_ry = nn.Linear(d, r)
        self.head_ori = nn.Linear(d, 3)
        self.head_end = nn.Linear(d, 2)
        self.head_rem = nn.Linear(d, cfg.n_rem_buckets)

    # ------------------------------------------------------------ encode
    def encode(self, batch, components) -> torch.Tensor:
        return self.encoder(batch, components)

    # --------------------------------------------- teacher-forced (§8.2)
    def forward(self, batch, components, tables, fields):
        """fields: int64 [B, N_FIELD=3+3*3+1] (ActionFields.to_list).
        Returns per-stage logits dict (masking is applied by the loss)."""
        mem = self.encode(batch, components)
        b = mem.shape[0]
        dev = mem.device
        prev = self._field_embeddings(fields, tables, dev)   # [B, N_SLOTS, D]
        slots = torch.arange(N_SLOTS, device=dev)
        x = self.slot_emb(slots)[None].expand(b, -1, -1) + prev
        for blk in self.dec:
            x = blk(x, mem, batch["pad_mask"])
        h = self.ln_dec(x)
        return self._stage_logits(h, tables)

    def _field_embeddings(self, fields, tables, dev) -> torch.Tensor:
        """prev-field embedding at each slot: the value PREDICTED by the
        previous slot (teacher forcing = ground truth here)."""
        b = fields.shape[0]
        d = self.cfg.d_model
        out = torch.zeros(b, N_SLOTS, d, device=dev)

        def e(kind, idx):
            return self.field_emb[kind](idx.clamp(min=0))

        out[:, SLOT_INDEX["VERB"]] = self._prev_of_verb(fields, dev)
        out[:, SLOT_INDEX["OBJ"]] = self._prev_of(fields, 1, "obj", dev)
        out[:, SLOT_INDEX["PTR"]] = self._prev_of(fields, 2, "ptr", dev)
        # point chain: each coarse/rx/ry slot consumes the previous field
        for i in range(MAX_POINTS):
            c_slot = SLOT_INDEX["P%dC" % (i + 1)]
            x_slot = SLOT_INDEX["P%dX" % (i + 1)]
            y_slot = SLOT_INDEX["P%dY" % (i + 1)]
            prev_slot = "PTR" if i == 0 else "P%dY" % i
            src = {"PTR": 2, "P1Y": 5, "P2Y": 8}[prev_slot]
            has_c = fields[:, 3 + 3 * i] != -100
            out[:, c_slot] = torch.where(
                has_c[:, None],
                e("coarse", fields[:, src].clamp(min=0)),
                self.field_emb["none"](torch.zeros(b, dtype=torch.long,
                                                   device=dev)))
            has_x = fields[:, 4 + 3 * i] != -100
            out[:, x_slot] = torch.where(
                has_x[:, None],
                e("coarse", fields[:, 3 + 3 * i].clamp(min=0)),
                self.field_emb["none"](torch.zeros(b, dtype=torch.long,
                                                   device=dev)))
            has_y = fields[:, 5 + 3 * i] != -100
            out[:, y_slot] = torch.where(
                has_y[:, None],
                e("res", fields[:, 4 + 3 * i].clamp(min=0)),
                self.field_emb["none"](torch.zeros(b, dtype=torch.long,
                                                   device=dev)))
        has_o = fields[:, -1] != -100
        out[:, SLOT_INDEX["ORI"]] = torch.where(
            has_o[:, None], e("ori", fields[:, -1].clamp(min=0)),
            self.field_emb["none"](torch.zeros(b, dtype=torch.long,
                                               device=dev)))
        return out

    def _prev_of_verb(self, fields, dev):
        b = fields.shape[0]
        return self.field_emb["none"](
            torch.zeros(b, dtype=torch.long, device=dev))

    def _prev_of(self, fields, col, kind, dev):
        b = fields.shape[0]
        ok = fields[:, col] != -100
        idx = fields[:, col].clamp(min=0, max=self._field_cap(kind) - 1)
        emb = self.field_emb[kind](idx)
        none = self.field_emb["none"](torch.zeros(b, dtype=torch.long,
                                                  device=dev))
        return torch.where(ok[:, None], emb, none)

    def _field_cap(self, kind: str) -> int:
        return {"obj": self.cfg.max_obj_cands + 1,
                "ptr": self.cfg.max_ptr_cands + 1}[kind]

    # -------------------------------------------------------- heads
    def _stage_logits(self, h, tables):
        out = {
            "verb": self.head_verb(h[:, SLOT_INDEX["BOS"]]),
            "obj": self.head_obj(h[:, SLOT_INDEX["VERB"]]),
            "ptr": self.head_ptr(h[:, SLOT_INDEX["OBJ"]]),
            "P1C": self.head_coarse(h[:, SLOT_INDEX["PTR"]]),
            "P1X": self.head_rx(h[:, SLOT_INDEX["P1C"]]),
            "P1Y": self.head_ry(h[:, SLOT_INDEX["P1X"]]),
            "P2C": self.head_coarse(h[:, SLOT_INDEX["P1Y"]]),
            "P2X": self.head_rx(h[:, SLOT_INDEX["P2C"]]),
            "P2Y": self.head_ry(h[:, SLOT_INDEX["P2X"]]),
            "P3C": self.head_coarse(h[:, SLOT_INDEX["P2Y"]]),
            "P3X": self.head_rx(h[:, SLOT_INDEX["P3C"]]),
            "P3Y": self.head_ry(h[:, SLOT_INDEX["P3X"]]),
            "ori": self.head_ori(h[:, SLOT_INDEX["P3Y"]]),
        }
        if self.cfg.use_end_aux:
            out["end"] = self.head_end(h[:, SLOT_INDEX["BOS"]])
            out["rem"] = self.head_rem(h[:, SLOT_INDEX["BOS"]])
        return out

    # ------------------------------------------------- free decode (§10.4)
    @torch.no_grad()
    def decode(self, batch, components, tables, mode: str = "greedy",
               temperature: float = 1.0, top_p: float = 1.0,
               seed: int | None = None, diverse: bool = False):
        """Structured masked decode of ONE atomic action for each batch row.

        Returns (fields [B,N_FIELD], stop_reasons list[str]). stop_reason is
        "" on success, else one of: no_verb / no_object / no_pointer /
        no_coordinate — an EXPLICIT reason, never a silent skip (§5.1).
        Every returned field set is guaranteed in-mask, so a masked rollout
        has rejection 0 by construction (§13.2)."""
        was_training = self.training
        self.eval()
        gen = None
        if seed is not None or diverse or mode != "greedy":
            gen = torch.Generator(device="cpu")
            gen = gen.manual_seed(int(seed or 0))
        mem = self.encode(batch, components)
        b = mem.shape[0]
        dev = mem.device
        verb_mask = tables["verb_mask"].to(dev).to(torch.get_default_dtype())
        obj_mask = tables["obj_mask"].to(dev).to(torch.get_default_dtype())
        ptr_mask = tables["ptr_mask"].to(dev).to(torch.get_default_dtype())
        xy_legal = tables["xy_legal"].to(dev).to(torch.get_default_dtype())
        arities = tables["arities"].to(dev)                  # [B,O]
        O = obj_mask.shape[-1]
        P = ptr_mask.shape[-1]

        chosen = {s: torch.full((b,), -100, dtype=torch.long, device=dev)
                  for s in DECODE_SLOTS}
        stop = [""] * b
        emb_prev = self.field_emb["none"](
            torch.zeros(b, dtype=torch.long, device=dev)
        ).unsqueeze(1)                                        # [B,1,D]
        prev_kind = None

        def sample(logits, mask_row):
            lg = logits / max(temperature, 1e-3)
            lg = lg + (1.0 - mask_row.to(lg.dtype)) * NEG_INF
            if mode == "greedy":
                return lg.argmax(dim=-1)
            probs = F.softmax(lg, dim=-1)
            if top_p < 1.0:
                sp, si = torch.sort(probs, descending=True, dim=-1)
                cum = sp.cumsum(-1)
                keep = cum - sp < top_p
                keep[..., 0] = True
                sp = sp * keep
                sp = sp / sp.sum(-1, keepdim=True)
                probs = torch.zeros_like(probs).scatter(-1, si, sp)
            if diverse:
                gumb = -torch.log(-torch.log(
                    torch.rand(probs.shape, generator=gen).to(dev) + 1e-9)
                    + 1e-9)
                return (probs.clamp(min=1e-9).log() + gumb).argmax(-1)
            if gen is None:
                return torch.multinomial(probs, 1).squeeze(-1)
            # deterministic CPU-side sampling for reproducible seeds
            p = probs.cpu()
            u = torch.rand(p.shape, generator=gen)
            cdf = p.cumsum(-1)
            pick = (u < cdf).long().argmax(-1).to(dev)
            return pick

        needs_pointer = {"UPGRADE_UNIT", "SELL_UNIT", "MOVE_UNIT",
                         "USE_EQUIPMENT"}
        n_points_of = torch.zeros(b, dtype=torch.long, device=dev)
        use_ori = torch.zeros(b, dtype=torch.long, device=dev)

        for step, slot in enumerate(DECODE_SLOTS):
            if slot == "COMMIT":
                break
            # build decoder input from the embeddings of previous choices
            x = self.slot_emb(
                torch.as_tensor([step], device=dev))[None].expand(b, -1, -1)
            x = x + emb_prev
            for blk in self.dec:
                x = blk(x, mem, batch["pad_mask"])
            h = self.ln_dec(x[:, 0])                          # [B,D]

            def head(kind):
                return {"verb": self.head_verb, "obj": self.head_obj,
                        "ptr": self.head_ptr, "coarse": self.head_coarse,
                        "rx": self.head_rx, "ry": self.head_ry,
                        "ori": self.head_ori}[kind](h)

            if slot == "BOS":
                lg = head("verb") + (1.0 - verb_mask) * NEG_INF
                v = sample(lg, torch.ones_like(verb_mask))
                bad = verb_mask.gather(1, v[:, None]).squeeze(1) < 0.5
                # rows with an illegal argmax (all-masked) fall to END_DEPLOY
                endv = VERB_INDEX["END_DEPLOY"]
                v = torch.where(bad, torch.full_like(v, endv), v)
                chosen["VERB"] = v
                emb_prev = self.field_emb["verb"](v).unsqueeze(1)
                if self.cfg.use_end_aux:
                    self._last_aux = (self.head_end(h), self.head_rem(h))
                continue
            verb = chosen["VERB"]
            if slot == "VERB":
                lg = head("obj")[:, :O] \
                    + (1.0 - obj_mask.gather(
                        1, verb[:, None, None].expand(-1, 1, O)).squeeze(1)
                       .to(lg.dtype)) * NEG_INF
                any_legal = obj_mask.gather(
                    1, verb[:, None, None].expand(-1, 1, O)).squeeze(1).sum(-1)
                o = sample(lg, torch.ones(b, O, device=dev))
                need_obj = ~torch.isin(
                    verb, torch.as_tensor(
                        [VERB_INDEX["END_DEPLOY"]], device=dev))
                bad = (any_legal <= 0) & need_obj
                for i in range(b):
                    if bool(bad[i]):
                        stop[i] = "no_object"
                o = torch.where(bad, torch.full_like(o, 0), o)
                chosen["OBJ"] = torch.where(need_obj, o,
                                            torch.full_like(o, -100))
                emb_prev = torch.where(
                    need_obj[:, None],
                    self.field_emb["obj"](o.clamp(min=0)).unsqueeze(1),
                    self.field_emb["none"](torch.zeros(b, dtype=torch.long,
                                                       device=dev)
                                           ).unsqueeze(1))
                # points & pointer requirements are decided BY THE VERB
                # (§5): 位置参数只属于 BUY/MOVE/CONTRAPTION 和按 registry
                # arity 的技能释放; UNLOCK/TECH/EQUIP/TOWER/BP 等 0 点
                n_points_of = torch.where(
                    verb == VERB_INDEX["RELEASE_COMMANDER_SKILL"],
                    arities.gather(1, o[:, None]).squeeze(1),
                    torch.zeros_like(o)).long()
                for v_need in ("BUY_UNIT", "MOVE_UNIT", "RELEASE_CONTRAPTION"):
                    n_points_of = torch.where(
                        verb == VERB_INDEX[v_need],
                        torch.ones_like(n_points_of), n_points_of)
                use_ori = torch.isin(
                    verb, torch.as_tensor([VERB_INDEX["MOVE_UNIT"],
                                           VERB_INDEX["BUY_UNIT"]],
                                          device=dev)).long()
                continue
            if slot == "PTR":
                pmask = ptr_mask.gather(
                    1, verb[:, None, None].expand(-1, 1, P)).squeeze(1)
                required = torch.isin(
                    verb, torch.as_tensor(
                        [VERB_INDEX[v] for v in needs_pointer],
                        device=dev))
                rel_skill = (verb == VERB_INDEX["RELEASE_COMMANDER_SKILL"]) \
                    & (chosen["OBJ"] >= 0) & (n_points_of == 0)
                required = required | rel_skill
                lg = head("ptr")[:, :P] + (1.0 - pmask) * NEG_INF
                p_pick = sample(lg, torch.ones(b, P, device=dev))
                have = pmask.sum(-1) > 0
                take = required & have
                chosen["PTR"] = torch.where(
                    take, p_pick, torch.full_like(p_pick, -100))
                for i in range(b):
                    if bool(required[i]) and not bool(have[i]):
                        stop[i] = "no_pointer"
                emb_prev = torch.where(
                    take[:, None],
                    self.field_emb["ptr"](p_pick.clamp(min=0)).unsqueeze(1),
                    self.field_emb["none"](torch.zeros(
                        b, dtype=torch.long, device=dev)).unsqueeze(1))
                self._ptr_take = take
                continue
            # point slots + orientation: presence decided by n_points_of
            if slot.startswith("P") and slot[1] in "123":
                pi = int(slot[1]) - 1
                kind = slot[2]            # C / X / Y
                active = n_points_of > pi
                if kind == "C":
                    vm = xy_legal.gather(
                        1, verb[:, None, None].expand(
                            -1, 1, self.n_coarse)).squeeze(1)
                    lg = head("coarse") + (1.0 - vm) * NEG_INF
                    val = sample(lg, torch.ones(b, self.n_coarse,
                                                device=dev))
                    emb = self.field_emb["coarse"](val.clamp(min=0))
                elif kind == "X":
                    lg = head("rx")
                    val = sample(lg, torch.ones(
                        b, lg.shape[-1], device=dev))
                    emb = self.field_emb["res"](val.clamp(min=0))
                else:
                    lg = head("ry")
                    val = sample(lg, torch.ones(
                        b, lg.shape[-1], device=dev))
                    emb = self.field_emb["res"](val.clamp(min=0))
                chosen[slot] = torch.where(
                    active, val, torch.full_like(val, -100))
                emb_prev = torch.where(
                    active[:, None], emb.unsqueeze(1),
                    self.field_emb["none"](torch.zeros(
                        b, dtype=torch.long, device=dev)).unsqueeze(1))
                for i in range(b):
                    if bool(active[i]) and kind == "C" and \
                            stop[i] == "" and \
                            float(vm[i].sum()) <= 0.0:
                        stop[i] = "no_coordinate"
                continue
            if slot == "ORI":
                active = use_ori > 0
                lg = head("ori")
                val = sample(lg, torch.ones(b, 3, device=dev))
                chosen[slot] = torch.where(
                    active, val, torch.full_like(val, -100))
                emb_prev = self.field_emb["ori"](val.clamp(min=0)) \
                    .unsqueeze(1)
                continue
        if was_training:
            self.train()
        fields = torch.stack([
            chosen["VERB"], chosen["OBJ"], chosen["PTR"],
            chosen["P1C"], chosen["P1X"], chosen["P1Y"],
            chosen["P2C"], chosen["P2X"], chosen["P2Y"],
            chosen["P3C"], chosen["P3X"], chosen["P3Y"],
            chosen["ORI"]], dim=-1)
        return fields, stop


