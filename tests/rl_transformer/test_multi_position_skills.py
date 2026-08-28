# §13.2/§5.3 multi-position skill tests: ordered points kept, one [COMMIT]
# consumes one skill slot, point order affects the loss, mirror keeps order.
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from pysim.rl.transformer.tokenizer import (                     # noqa: E402
    action_to_fields, fields_to_action, encode_policy_tokens, grid_encode,
    grid_decode)
from pysim.rl.transformer.policy_arity import (release_arity,        # noqa: E402
                                               VERB_SEM, VERBS_13)
from pysim.rl.transformer import toydata                         # noqa: E402

CAPSULE, BEACON, UNIT_SKILL = 400002, 1500001, 1100001


def _tables(vocab, tok_cfg, obs):
    return encode_policy_tokens(obs, vocab, tok_cfg)


def _release_row(toy_rows):
    for r in toy_rows["policy"]:
        if r["target"].get("points") and \
                len(r["target"]["points"]) >= 2:
            return r
    pytest.skip("no multi-point row")


def test_ordered_points_survive_full_cycle(vocab, tok_cfg, toy_rows):
    row = _release_row(toy_rows)
    _, tables = _tables(vocab, tok_cfg, row["observation"])
    target = row["target"]
    f = action_to_fields(target, tables, tok_cfg)
    a = fields_to_action(f, tables, tok_cfg)
    # point ORDER verbatim (§5.3): decoding never re-sorts
    for (wx, wy), (gx, gy) in zip(target["points"], a["points"]):
        assert abs(wx - gx) < 5.0 and abs(wy - gy) < 5.0
    assert len(a["points"]) == len(target["points"])
    # one slot consumption: still ONE action with ONE skill slot
    assert a["skill_slot"] == target["skill_slot"]
    assert a["skill_id"] == target["skill_id"]


def test_point_order_changes_fields_and_loss(vocab, tok_cfg, tiny_policy_cfg,
                                             toy_rows):
    """Reversing the ordered points must change the encoded fields (the
    decoder is order-aware), not collapse into a set."""
    row = _release_row(toy_rows)
    _, tables = _tables(vocab, tok_cfg, row["observation"])
    target = dict(row["target"])
    if len(target["points"]) < 2:
        pytest.skip("need >=2 points")
    f1 = action_to_fields(target, tables, tok_cfg)
    rev = dict(target)
    rev["points"] = list(reversed(target["points"]))
    f2 = action_to_fields(rev, tables, tok_cfg)
    assert f1.points != f2.points
    assert len(f1.points) == len(f2.points)
    # grid-level: each point's coarse bucket differs for distinct points
    if target["points"][0] != target["points"][-1]:
        c1 = grid_encode(*target["points"][0], tok_cfg)
        c2 = grid_encode(*rev["points"][0], tok_cfg)
        assert c1 != c2

    # and the teacher-forced loss actually distinguishes the orders
    from pysim.rl.transformer.policy_bc import TPolicyBC
    from pysim.rl.transformer.data import (encode_policy_row,
                                           collate_policy)
    from pysim.rl.transformer.losses import (policy_stage_losses,
                                             build_stage_masks)
    torch.manual_seed(0)
    model = TPolicyBC(vocab, tiny_policy_cfg, tok_cfg)
    e1 = encode_policy_row({**row, "target": target}, vocab, tok_cfg,
                           tiny_policy_cfg.max_obj_cands,
                           tiny_policy_cfg.max_ptr_cands)
    e2 = encode_policy_row({**row, "target": rev}, vocab, tok_cfg,
                           tiny_policy_cfg.max_obj_cands,
                           tiny_policy_cfg.max_ptr_cands)
    pb = collate_policy([e1, e2], tok_cfg=tok_cfg)
    logits = model(pb["batch"], pb["components"], pb["tables"],
                   pb["fields"])
    sm = build_stage_masks(pb["fields"], pb["tables"],
                           pb["batch"]["type"].device)
    out = policy_stage_losses(logits, pb["fields"], sm)
    # per-sample stage CE must differ between the two orders: evaluate the
    # P2C head against each row's own target — swap the field rows
    fields_swapped = pb["fields"].flip(0)
    out_swapped = policy_stage_losses(logits, fields_swapped, sm)
    assert abs(float(out["total"]) - float(out_swapped["total"])) > 1e-6


def test_slot_consumed_once_semantics(vocab, tok_cfg, toy_rows):
    """A multi-point release is ONE atomic action (one slot), never
    expanded into per-point pseudo-actions (§5.3 / transition T0)."""
    from pysim.rl.masks import RLAction, to_engine_action
    from pysim.transition.model import (ActionKind,
                                        ReleaseCommanderSkillArgs)
    row = _release_row(toy_rows)
    _, tables = _tables(vocab, tok_cfg, row["observation"])
    a = fields_to_action(action_to_fields(row["target"], tables, tok_cfg),
                         tables, tok_cfg)
    kw = {}
    for k in ("mech", "handle", "equip", "skill_slot", "skill_id", "tower",
              "tower_index", "blueprint", "contraption", "x", "y", "rot"):
        if a.get(k) is not None:
            kw[k] = a[k]
    act = RLAction(a["verb"], **kw)
    if a.get("points"):
        act.points = tuple((float(x), float(y)) for x, y in a["points"])
    engine = to_engine_action(act, 0, _FakeHandleMap())
    assert engine.kind is ActionKind.RELEASE_COMMANDER_SKILL
    assert len(engine.args.positions) == len(a["points"])
    # engine coordinates: ego frame y<0 stays, no flip on side 0
    for (x, y), (ex, ey) in zip(a["points"], engine.args.positions):
        assert abs(ex - x) < 1e-6 and abs(ey - y) < 1e-6


class _FakeHandleMap:
    def resolve(self, handle):
        return 100 + int(handle)


def test_mirror_points_keeps_order(vocab, tok_cfg):
    from pysim.rl.transformer.tokenizer import mirror_points
    pts = [(1.0, -2.0), (3.0, -4.0), (5.0, 6.0)]
    m = mirror_points(pts)
    assert [(p[0], -p[1]) for p in pts] == m
    assert mirror_points(m) == pts


def test_multi_point_skills_present_in_toy_corpus(vocab, tok_cfg,
                                                  toy_rows):
    """coverage: the toy corpus exercises 2-point AND 3-point releases."""
    arities = set()
    for r in toy_rows["policy"]:
        p = r["target"].get("points") or []
        if p and r["target"]["verb"] == "RELEASE_COMMANDER_SKILL":
            arities.add(len(p))
    assert 2 in arities and 3 in arities
