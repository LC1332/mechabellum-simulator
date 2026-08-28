# Mechanic registry (重构计划 §3.1/M1/B1): the SINGLE rule source for
# mechanism support. capability.py, the battlefield compiler and the battle
# warnings all query this module - nobody keeps a second table.
#
# Six-stage closure (重构计划 §2.1): a mechanism is `effect_complete` only
# when decode / legality / economy / persistent_state / battle / settlement
# are all complete AND confidence == "verified". "Code has an event" never
# implies verified: provisional values (cal/assumed numbers awaiting the
# real-game oracle) and implementation-path completeness are separate axes.
#
#   stage values: "complete" | "partial" | "missing"
#   confidence:   "verified" (corpus/official evidence + full closure)
#               | "provisional" (runnable, numbers not oracle-checked)
#               | "unsupported"
from dataclasses import dataclass

REGISTRY_VERSION = "mechanic_registry_v1"

_COMPLETE = "complete"
_PARTIAL = "partial"
_MISSING = "missing"


@dataclass(frozen=True)
class MechanicSupport:
    mechanism: str
    ident: int
    decode: str                  # raw -> typed action resolution
    legality: str                # target/resource/zone checks
    economy: str                 # cost/refund/income effects
    persistent_state: str        # inventory/binding/persistence
    battle: str                  # battlefield effect consumed by the engine
    settlement: str              # post-fight writeback implications
    confidence: str              # verified | provisional | unsupported
    evidence: tuple

    @property
    def transition_complete(self) -> bool:
        return all(s == _COMPLETE for s in (self.decode, self.legality,
                                            self.economy,
                                            self.persistent_state))

    @property
    def battle_fidelity(self) -> str:
        """exact = implementation path complete (does NOT prove numbers;
        see confidence). approximate = runnable but partially modeled.
        unsupported = no modeled effect."""
        if self.battle == _COMPLETE:
            return "exact"
        if self.transition_complete:
            return "approximate"
        return "unsupported"

    @property
    def effect_complete(self) -> bool:
        return self.transition_complete and self.battle == _COMPLETE \
            and self.settlement == _COMPLETE and self.confidence == "verified"

    def two_axis(self) -> dict:
        """The step3 two-axis view + the confidence/effect_complete axes
        (重构计划 §10.1). Kept as a plain dict for JSON views."""
        return {"transition_complete": self.transition_complete,
                "battle_fidelity": self.battle_fidelity,
                "confidence": self.confidence,
                "effect_complete": self.effect_complete}

    def as_dict(self) -> dict:
        d = {"mechanism": self.mechanism, "ident": self.ident,
             "decode": self.decode, "legality": self.legality,
             "economy": self.economy,
             "persistent_state": self.persistent_state,
             "battle": self.battle, "settlement": self.settlement,
             "confidence": self.confidence, "evidence": list(self.evidence)}
        d.update(self.two_axis())
        return d


def _full(ident, mechanism, confidence, evidence) -> MechanicSupport:
    return MechanicSupport(mechanism, int(ident), _COMPLETE, _COMPLETE,
                           _COMPLETE, _COMPLETE, _COMPLETE, _COMPLETE,
                           confidence, tuple(evidence))


def _unmodeled(ident, mechanism) -> MechanicSupport:
    return MechanicSupport(mechanism, int(ident), _MISSING, _MISSING,
                           _MISSING, _MISSING, _MISSING, _MISSING,
                           "unsupported", ())


# ---------------------------------------------------------------- equipment
# Transition stages complete for every registered id (step3: charge, stock,
# bind, replace, persist - acquisition economics and binding only). Battle
# stage completes per EQUIPMENT_BATTLE_SPECS. Cross-round effect gaps
# (统御核心 round income +50 / death-wipe, 部署模块 redeploy rights, 生产线
# periodic summons, 深渊信标) report on the settlement stage - they never
# block runtime playability (step3 E6: known equipment stays playable).
# step32: 生产线 periodic summons ARE modeled now (EquipmentRuntimeSpec
# summon schedule; battle-transient, no cross-round state -> settlement
# complete). 统御核心/部署模块/深渊信标 keep their cross-round gaps.
_EQ_CROSS_ROUND = {
    13030010: "统御核心 round income +50 / death-wipe unmodeled (E5)",
    13040001: "部署模块 per-round redeploy rights unmodeled (E5)",
    1306004: "深渊信标 unmodeled (E4)",
}


def _equipment_support(equipment_id: int) -> MechanicSupport:
    from .effects.equipment import (EQUIPMENT_DEFS, EQUIPMENT_BATTLE_SPECS,
                                    EQUIPMENT_RUNTIME_SPECS,
                                    EQUIPMENT_RUNTIME_VERSION)
    eid = int(equipment_id)
    d = EQUIPMENT_DEFS.get(eid)
    if d is None:
        return _unmodeled(eid, "equipment")
    spec = EQUIPMENT_BATTLE_SPECS.get(eid)
    rt = EQUIPMENT_RUNTIME_SPECS.get(eid)
    # battle numbers carry the survey description + stacking assumption:
    # provisional until the per-id oracle A/B lands (任务书 §4/§8.5)
    conf = "provisional" \
        if (spec is not None or rt is not None) else "unsupported"
    ev = ["step3:transition chain (charge/stock/bind/persist)"]
    if spec is not None:
        ev += list(spec.evidence)
    if rt is not None:
        ev.append("runtime:" + EQUIPMENT_RUNTIME_VERSION +
                  ":" + rt.digest())
        ev += list(rt.evidence)
    if spec is None and rt is None:
        note = _EQ_CROSS_ROUND.get(eid)
        ev.append("battle effect not implemented (battle_approximate)"
                  + ("; " + note if note else ""))
    settlement = _PARTIAL if eid in _EQ_CROSS_ROUND else _COMPLETE
    return MechanicSupport("equipment", eid, _COMPLETE, _COMPLETE,
                           _COMPLETE, _COMPLETE,
                           _COMPLETE if (spec is not None or rt is not None)
                           else _MISSING,
                           settlement, conf, tuple(ev))


# ------------------------------------------------------------- commander skill
# Cooldown lifecycle is corpus-verified (1106 games, cd-after-release counts)
# and ticks in advance_round. Battle numbers stay provisional wherever the
# def's provenance carries unresolved values (see evidence per id).
_SKILL_CD = {300001: (2, "cd=2: 800/800 corpus releases"),
             800001: (2, "cd=2: 526/526 corpus releases"),
             100002: (3, "cd=3: 200/200 corpus releases"),
             1200001: (3, "cd=3: 222/222 corpus releases"),
             1200003: (3, "cd=3: 363/363 corpus releases"),
             1100001: (1, "cd=1: 1084/1084 corpus releases"),
             # step4 任务书 §1.3/QA#3: 再部署 cooldown = next round (user
             # ruling); corpus cd counts for the new P1 ids stay unmeasured
             # (cal) — 0 re-arms next round
             1000001: (1, "cd=1: step4 QA#3 user ruling (next round)")}

_SKILL_CONFIDENCE = {
    # strike damage is wiki-backed but splash 20 has no source yet
    300001: ("provisional", ("wiki:导弹打击 3000/missile (damage verified)",
                             "splash=20 unsourced -> provisional",
                             "corpus:" + _SKILL_CD[300001][1])),
    800001: ("provisional", ("wiki:空投护盾 50000 HP (hp verified)",
                             "radius=30 cal -> provisional",
                             "corpus:" + _SKILL_CD[800001][1])),
    100002: ("provisional", ("reddit:燃烧弹 88dmg/0.25s tick (dps verified)",
                             "radius/duration cal -> provisional",
                             "corpus:" + _SKILL_CD[100002][1])),
    1200001: ("provisional", ("summon mech/count provisional (crawler card 24x)",
                              "corpus:" + _SKILL_CD[1200001][1])),
    1200003: ("provisional", ("summon mech/count provisional (wasp card 12x)",
                              "corpus:" + _SKILL_CD[1200003][1])),
    # transition-layer skill: exp jump rule frozen by the step3 任务书 §5
    1100001: ("verified", ("step3任务书§5:强化训练 exp->next threshold "
                           "(user-frozen rule)",
                           "corpus:" + _SKILL_CD[1100001][1])),
    # step4 任务书 §1.3/QA#3: redeploy rule frozen by the user (transition
    # only, per-slot once per round, cd=1) — no battle numbers involved
    1000001: ("verified", ("step4任务书§1.3+QA#3:再部署 unlock move right "
                           "(user-frozen rule, transition-only)",
                           "corpus:" + _SKILL_CD[1000001][1])),
    # step4 任务书 §7.1 P1 additions: numbers user-frozen 2026-08-27, but
    # spread/timing/splash stay cal — confidence provisional (never verified
    # without an oracle calibration game)
    300003: ("provisional", ("step4 P1: 15x2500 multi-strike (user table)",
                             "spread pattern cal (sunflower), splash cal",
                             "ff per QA#6 user ruling")),
    300004: ("provisional", ("step4 P1: 70000 at t=15s (user table)",
                             "splash cal (40)",
                             "ff per QA#6 user ruling")),
    300007: ("provisional", ("step4 P1: r30 70000 (user table)",
                             "barrier bypass per QA#6 user ruling")),
    1200002: ("provisional", ("step4 P1: airdrop 1 犀牛 mech 5 (user table)",)),
    1200004: ("provisional", ("step4 P1: airdrop 1 霸主 mech 11 (user table)",)),
    1200005: ("provisional", ("step4 P1: airdrop 1 火神 mech 3 (user table)",)),
    # ---- step5 任务书 §2.1 frozen rules + §12 QA answers (user 2026-08-28).
    # Frozen numbers are auditable user rulings; every field still marked
    # cal keeps the id OUT of verified. The oracle A/B (§4) upgrades these
    # one by one — never the code path alone.
    200001: ("provisional", ("step5§2.1 user-frozen: r60, 20000 shield "
                             "damage, 25s disable, speed x0.60, "
                             "barrier-covered ground immune",
                             "air/building/device interaction cal")),
    200002: ("provisional", ("step5 QA-2 user-frozen: r130, same effect "
                             "spec as 200001 (radius-only variant)",
                             "cd/stacking cal")),
    200003: ("provisional", ("step5§2.1+QA-4 user-frozen: 20s photon, "
                             "damage taken x0.70, immune+clears EMP/引燃/"
                             "酸液/退化光束",
                             "shape assumed capsule r30 (cal)")),
    400002: ("provisional", ("step5§2.1 user-frozen: capsule(A,B,30), "
                             "ground speed x0.45, unlit oil 2 battles, "
                             "ignite->flame, shield-clipped generation",
                             "air/友伤/tick cal")),
    500002: ("provisional", ("step5 QA-2 user-frozen: 3% maxHP/s + damage "
                             "taken x2.5", "tick/duration/air cal")),
    600002: ("provisional", ("step5 QA-2 user-frozen: enemy range x0.65, "
                             "capsule r30, shield-clipped generation",
                             "duration/air/stacking cal")),
    300005: ("provisional", ("step5 QA-2 user-frozen: r130 ONLY",
                             "strike distribution/damage/duration cal -> "
                             "seeded provisional; strict-effect stays "
                             "blocked until the storm oracle")),
    300006: ("provisional", ("step5§2.1 user-frozen: moving circle r20 "
                             "A->B", "speed/dps/tick/ff cal")),
    1500001: ("provisional", ("step5§2.1+QA-6 user-frozen: 3 ordered "
                              "points, r40 member selection, relative "
                              "offsets, stop-to-attack; walls/cannons "
                              "unaffected",
                              "collision/转向/midline edges cal")),
    1500002: ("provisional", ("step5 QA-2/QA-6 user ruling: identical "
                              "effect to 1500001 (增援卡 variant)")),
    # 战地回收 900001 (transition-only: unit sell + construction refund):
    # refund table user-frozen, unit path corpus-verified, cd=0 corpus law
    900001: ("verified", ("step5 QA-2 user-frozen: wall (cid1) 50, "
                          "反装甲炮 (cid2) / 速射炮 (cid3) 100, 磁力路障 "
                          "(cid4) 50 (user ruling 2026-08-28; corpus 270 "
                          "recycled cid4 rows)",
                          "corpus:cd=0 2479/2479 releases (unit path)")),
}


def _commander_skill_support(skill_id: int) -> MechanicSupport:
    from ..skills import COMMANDER_SKILLS, TRANSITION_SKILLS
    sid = int(skill_id)
    if sid in COMMANDER_SKILLS:
        conf, ev = _SKILL_CONFIDENCE.get(
            sid, ("provisional", ("provisional by default",)))
        ev = list(ev) + ["def:" + str(COMMANDER_SKILLS[sid].get("conf", ""))]
        return _full(sid, "commander_skill", conf, ev)
    if sid in TRANSITION_SKILLS:
        conf, ev = _SKILL_CONFIDENCE.get(sid, ("provisional", ("?",)))
        return _full(sid, "commander_skill", conf, ev)
    return _unmodeled(sid, "commander_skill")


# ---------------------------------------------------------------- contraption
def _contraption_support(cid: int) -> MechanicSupport:
    from ..skills import CONTRAPTIONS
    cid = int(cid)
    d = CONTRAPTIONS.get(cid)
    if not d:
        return _unmodeled(cid, "contraption")
    # 10001: damage wiki-verified but range/cd/hp cal; 20001: hp wiki + r7+ A/B
    # mapping but radius cal -> both stay provisional
    return _full(cid, "contraption", "provisional",
                 ("def:" + str(d.get("conf", "")),))


# ---------------------------------------------------------------- tower skill
def _tower_skill_support(sid: int) -> MechanicSupport:
    sid = int(sid)
    if sid == 3:
        return _full(sid, "tower_skill", "verified",
                     ("step4 user ruling 2026-08-27: 批量征召 cost 50, this "
                      "round's buy limit +1 (前置)",
                      "corpus wall: 0/16,512 buy-rounds exceed "
                      "2+tower3+10004; 6,953 rounds show it between buy#2 "
                      "and buy#3"))
    if sid == 1:
        return _full(sid, "tower_skill", "verified",
                     ("step4 user ruling 2026-08-27: 快速补给 cost 0, +200 "
                      "now / -300 next round income"))
    if sid == 4:
        return _full(sid, "tower_skill", "verified",
                     ("step4 user ruling 2026-08-27 + doc order examples: "
                      "精英征召 cost 100, buys AFTER it spawn at level+1"))
    if sid in (5, 6):
        return _full(sid, "tower_skill", "verified",
                     ("step3任务书§3:5=射程+15/6=移速+3, fees frozen "
                      "(user ruling 2026-08-27)",
                      "step4 QA#4: single purchase per id per round"))
    return _unmodeled(sid, "tower_skill")


# ----------------------------------------------------------------- blueprint
# step4 FINAL (user ruling 2026-08-27 + corpus unlock probes): blueprint
# 1/2/3 = commander-skill RESEARCH (one-time; slot granted next round):
#   1 黏油弹 150 -> skill 400002, 2 战地回收 100 -> 900001,
#   3 移动信标 100 -> 1500001
# (unlock correlation: 470/470 and 1837/1860 researched->seen, ZERO
# unresearched sightings; slot lag = +1 round ~100%)
_BLUEPRINT_EVIDENCE = {
    1: ("step4 user ruling: 黏油弹 research 150 -> unlock 400002 "
        "(corpus correlation 470/470)", "verified"),
    2: ("step4 user ruling: 战地回收 research 100 -> unlock 900001 "
        "(corpus correlation 1837/1860, never unresearched)", "verified"),
    3: ("step4 user ruling: 移动信标 research 100 -> unlock 1500001 "
        "(corpus correlation 548, lag=+1)", "verified"),
    4: ("corpus:_probe9/_probe14 officer 20310 mapping", "verified"),
    5: ("corpus:_probe9/_probe14 officer 20300 mapping", "verified"),
    401: ("corpus:_probe14 II tier replaces I", "verified"),
    501: ("corpus:_probe14 II tier replaces I", "verified"),
}


def _blueprint_support(bid: int) -> MechanicSupport:
    bid = int(bid)
    if bid not in _BLUEPRINT_EVIDENCE:
        return _unmodeled(bid, "blueprint")
    ev, conf = _BLUEPRINT_EVIDENCE[bid]
    return _full(bid, "blueprint", conf, (ev,))


# ------------------------------------------------------------------- officer
# Reinforcement-card officers with battle/deploy effects (重构计划 §3.1 P0):
# descriptions are survey-verbatim; implementation status per id.
# 10004 额外部署位: buy limit +1 per copy (可重复), deploy reads it.
# 10007/10008: device params multiplied at compile time (battlefield
#   compiler applies them to barrier hp / turret damage).
# 10009 快速传送: flank spawn delay halved, applied by the compiler.
# 20003 高效科技研发: already in the unified tech quotes (step3).
_OFFICER_IMPL = {
    10004: (_COMPLETE, "deploy:buy_limit +1 per held copy (BASE_BUY_LIMIT)"),
    10007: (_COMPLETE, "compiler:barrier device hp x1.4"),
    10008: (_COMPLETE, "compiler:turret device damage x3.0"),
    10009: (_COMPLETE, "compiler:flank spawn_at halved"),
    20003: (_COMPLETE, "economy:tech quotes -50 (step3)"),
}


def _officer_support(oid: int) -> MechanicSupport:
    oid = int(oid)
    if oid not in _OFFICER_IMPL:
        return _unmodeled(oid, "officer")
    stage, note = _OFFICER_IMPL[oid]
    return MechanicSupport("officer", oid, _COMPLETE, _COMPLETE, _COMPLETE,
                           _COMPLETE, stage, _COMPLETE, "provisional",
                           ("survey:增援卡牌-回放全量信息.md 描述 verbatim",
                            note,
                            "provisional: no targeted replay probe frozen yet"))


# ------------------------------------------------------------------ dispatch
def mechanism_support(mechanism: str, ident) -> MechanicSupport:
    """The single support lookup. Returns an unsupported record for unknown
    (mechanism, id) pairs - never a guess."""
    try:
        ident = int(ident)
    except (TypeError, ValueError):
        return _unmodeled(0, str(mechanism))
    if mechanism == "equipment":
        return _equipment_support(ident)
    if mechanism == "commander_skill":
        return _commander_skill_support(ident)
    if mechanism == "contraption":
        return _contraption_support(ident)
    if mechanism == "tower_skill":
        return _tower_skill_support(ident)
    if mechanism == "blueprint":
        return _blueprint_support(ident)
    if mechanism == "officer":
        return _officer_support(ident)
    return _unmodeled(ident, str(mechanism))


def skill_cooldown_rounds(skill_id: int) -> int:
    """Corpus-verified cooldown (rounds) applied when a slot is consumed.
    0 = re-arms next round without a cooling counter."""
    return _SKILL_CD.get(int(skill_id), (0, ""))[0]


def equipment_battle_warning(equipment_id: int) -> str | None:
    """Per-id approximation warning for battle outcomes; None when the id
    has a battle spec or a runtime spec (no longer approximate)."""
    from .effects.equipment import (EQUIPMENT_BATTLE_SPECS,
                                    EQUIPMENT_RUNTIME_SPECS)
    eid = int(equipment_id)
    if eid and eid not in EQUIPMENT_BATTLE_SPECS \
            and eid not in EQUIPMENT_RUNTIME_SPECS:
        return ("equipment:%d battle effect not simulated "
                "(battle_approximate)" % eid)
    return None


def registry_dump() -> dict:
    """Full registry view for reports/metrics regeneration (single source)."""
    from .effects.equipment import (EQUIPMENT_DEFS, EQUIPMENT_RUNTIME_SPECS,
                                    EQUIPMENT_RUNTIME_VERSION)
    from ..skills import COMMANDER_SKILLS, TRANSITION_SKILLS, CONTRAPTIONS
    out = {"registry_version": REGISTRY_VERSION,
           "equipment": [], "commander_skill": [], "contraption": [],
           "tower_skill": [], "blueprint": [], "officer": []}
    for eid in sorted(EQUIPMENT_DEFS):
        s = mechanism_support("equipment", eid)
        d = s.as_dict()
        d["name"] = EQUIPMENT_DEFS[eid].name
        # step32: runtime spec params ride the dump (digest-stable view)
        rt = EQUIPMENT_RUNTIME_SPECS.get(eid)
        if rt is not None:
            d["runtime_version"] = EQUIPMENT_RUNTIME_VERSION
            d["runtime"] = rt.params()
            d["runtime_digest"] = rt.digest()
        out["equipment"].append(d)
    for sid in sorted(set(COMMANDER_SKILLS) | set(TRANSITION_SKILLS)):
        d = mechanism_support("commander_skill", sid).as_dict()
        d["cooldown_rounds"] = skill_cooldown_rounds(sid)
        out["commander_skill"].append(d)
    for cid in sorted(k for k, v in CONTRAPTIONS.items() if v):
        out["contraption"].append(mechanism_support("contraption", cid)
                                  .as_dict())
    for sid in (1, 3, 4, 5, 6):
        out["tower_skill"].append(mechanism_support("tower_skill", sid)
                                  .as_dict())
    for bid in sorted(_BLUEPRINT_EVIDENCE):
        out["blueprint"].append(mechanism_support("blueprint", bid).as_dict())
    for oid in sorted(_OFFICER_IMPL):
        d = mechanism_support("officer", oid).as_dict()
        d["name"] = {"10004": "额外部署位", "10007": "先进护盾装置",
                     "10008": "先进飞弹装置", "10009": "快速传送",
                     "20003": "高效科技研发"}.get(str(oid), str(oid))
        out["officer"].append(d)
    out["summary"] = {
        "equipment_total": len(out["equipment"]),
        "equipment_battle_implemented": sum(
            1 for e in out["equipment"] if e["battle"] == _COMPLETE),
        "equipment_runtime_version": EQUIPMENT_RUNTIME_VERSION,
        "equipment_runtime_implemented": sum(
            1 for e in out["equipment"] if e.get("runtime")),
        "equipment_selection_coverage_top4": "1168/2318 = 50.4%",
        "verified_mechanisms": sum(
            1 for grp in ("commander_skill", "contraption", "tower_skill",
                          "blueprint", "officer")
            for e in out[grp] if e["confidence"] == "verified"),
        "provisional_mechanisms": sum(
            1 for grp in ("commander_skill", "contraption", "tower_skill",
                          "blueprint", "officer")
            for e in out[grp] if e["confidence"] == "provisional"),
    }
    return out
