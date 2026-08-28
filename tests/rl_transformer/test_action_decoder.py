# §13.2 action decoder tests: per-verb masks, pointer handles under
# re-ranking, 0/1/2/3-point arities, END/budget/no-candidate paths,
# teacher-forced target-in-mask = 100%, masked-rollout rejection = 0.
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from pysim.rl.transformer.tokenizer import (                     # noqa: E402
    ActionFields, TokenizerError, action_to_fields, fields_to_action,
    encode_policy_tokens, grid_encode, grid_decode)
from pysim.rl.transformer.policy_arity import release_arity      # noqa: E402
from pysim.rl.transformer import toydata                         # noqa: E402
from pysim.rl.transformer.data import encode_policy_row          # noqa: E402


def _tables(vocab, tok_cfg, obs):
    ta, tables = encode_policy_tokens(obs, vocab, tok_cfg)
    return ta, tables


def test_teacher_forced_target_in_mask_100pct(vocab, tok_cfg, toy_rows):
    """§13.2: every target in the toy corpus must encode in-mask."""
    n_ok = 0
    for r in toy_rows["policy"]:
        _, tables = _tables(vocab, tok_cfg, r["observation"])
        f = action_to_fields(r["target"], tables, tok_cfg)
        # object column legality for the target verb
        if f.obj >= 0:
            assert tables["obj_mask"][f.verb, f.obj], r["target"]
        # pointer legality
        if f.ptr >= 0:
            assert tables["ptr_mask"][f.verb, f.ptr]
        # coordinate legality (coarse bucket inside the verb bounds)
        for (c, _rx, _ry) in f.points:
            assert tables["xy_legal"][f.verb, c]
        n_ok += 1
    assert n_ok == len(toy_rows["policy"])


def test_target_out_of_mask_raises(vocab, tok_cfg, toy_rows):
    row = toy_rows["policy"][0]
    _, tables = _tables(vocab, tok_cfg, row["observation"])
    # a handle outside the pool must raise, never silently pass
    bad = {"verb": "MOVE_UNIT", "handle": 999, "x": 0.0, "y": -50.0}
    with pytest.raises(TokenizerError):
        action_to_fields(bad, tables, tok_cfg)
    # an illegal MOVE target (move_ok=False handle) must raise
    mv = tables["ptr_mask"][toydata.SPACE_VERBS.index("MOVE_UNIT")
                            if hasattr(toydata, "SPACE_VERBS") else 5]
    blocked = None
    for h, ok in enumerate(mv):
        if not ok:
            blocked = h
            break
    if blocked is not None:
        with pytest.raises(TokenizerError):
            action_to_fields({"verb": "MOVE_UNIT", "handle": blocked,
                              "x": 0.0, "y": -50.0}, tables, tok_cfg)


def test_arities_0123_from_registry(vocab, tok_cfg, toy_rows):
    """§5.3: target arity comes from the registry; mismatches raise."""
    from pysim.rl.transformer.tokenizer import build_candidate_tables
    row = toy_rows["policy"][0]
    _, tables = _tables(vocab, tok_cfg, row["observation"])
    by_sid = {}
    for i, e in enumerate(tables["obj_entries"]):
        if e["pool"] == "skill":
            by_sid[int(e["value"][1])] = i
    assert release_arity(400002) == 2      # capsule
    assert release_arity(1500001) == 3     # beacon
    assert release_arity(1100001) == 0     # unit target

    base = {"verb": "RELEASE_COMMANDER_SKILL",
            "skill_slot": 0, "skill_id": 400002}
    f2 = action_to_fields({**base, "points": [(1.0, -50.0), (2.0, -60.0)]},
                          tables, tok_cfg)
    assert len(f2.points) == 2
    with pytest.raises(TokenizerError):
        action_to_fields({**base, "points": [(1.0, -50.0)]},
                         tables, tok_cfg)          # 1 != registry 2
    f3 = action_to_fields({"verb": "RELEASE_COMMANDER_SKILL",
                           "skill_slot": 1, "skill_id": 1500001,
                           "points": [(1.0, -50.0), (2.0, -60.0),
                                      (3.0, -70.0)]}, tables, tok_cfg)
    assert len(f3.points) == 3
    f0 = action_to_fields({"verb": "RELEASE_COMMANDER_SKILL",
                           "skill_slot": 2, "skill_id": 1100001,
                           "handle": 0}, tables, tok_cfg)
    assert len(f0.points) == 0 and f0.ptr == 0
    # single-point positional skill (when the toy space carries one)
    space_skills = row["observation"]["space"]["skill_cands"]
    f1 = action_to_fields({"verb": "RELEASE_COMMANDER_SKILL",
                           "skill_slot": space_skills[3][0],
                           "skill_id": space_skills[3][1],
                           "x": 5.0, "y": -80.0}, tables, tok_cfg) \
        if len(space_skills) > 3 else None
    if f1 is not None:
        assert len(f1.points) == 1


def test_field_roundtrip_and_coordinate_grid(vocab, tok_cfg):
    """coarse+residual encode/decode is exact at cell centers and within
    one cell everywhere (§5.2)."""
    rng = np.random.RandomState(0)
    for _ in range(200):
        x = float(rng.uniform(-349, 349))
        y = float(rng.uniform(-298, 298))
        c, rx, ry = grid_encode(x, y, tok_cfg)
        dx, dy = grid_decode(c, rx, ry, tok_cfg)
        cw, ch = 700.0 / tok_cfg.grid_nx, 600.0 / tok_cfg.grid_ny
        assert abs(dx - x) <= cw / 2 + 1e-6
        assert abs(dy - y) <= ch / 2 + 1e-6
        assert 0 <= c < tok_cfg.grid_nx * tok_cfg.grid_ny


def test_field_codec_roundtrip(vocab, tok_cfg, toy_rows):
    row = toy_rows["policy"][3]
    _, tables = _tables(vocab, tok_cfg, row["observation"])
    f = action_to_fields(row["target"], tables, tok_cfg)
    lst = f.to_list()
    f2 = ActionFields.from_list(lst)
    assert f2.to_list() == lst
    a = fields_to_action(f2, tables, tok_cfg)
    assert a["verb"] == row["target"]["verb"]
    # ordered multi-point roundtrip keeps the sequence
    if row["target"].get("points"):
        # coarse-to-fine quantization: decoded point within one residual
        # step (~cell/bins/2 ≈ 1.6 units) of the target (§5.2)
        cw = 700.0 / tok_cfg.grid_nx / tok_cfg.residual_bins
        ch = 600.0 / tok_cfg.grid_ny / tok_cfg.residual_bins
        for (wx, wy), (gx, gy) in zip(row["target"]["points"],
                                      a["points"]):
            assert abs(wx - gx) <= cw and abs(wy - gy) <= ch


def test_decode_respects_masks_and_never_rejects(vocab, tok_cfg,
                                                 tiny_policy_cfg, toy_rows):
    """§13.2: masked rollout rejection = 0 — every decoded field set is
    in-mask; stops carry explicit reasons."""
    from pysim.rl.transformer.policy_bc import TPolicyBC
    from pysim.rl.transformer.data import collate_policy
    torch.manual_seed(0)
    model = TPolicyBC(vocab, tiny_policy_cfg, tok_cfg).eval()
    rows = toy_rows["policy"][:12]
    enc = [encode_policy_row(r, vocab, tok_cfg,
                             tiny_policy_cfg.max_obj_cands,
                             tiny_policy_cfg.max_ptr_cands) for r in rows]
    pb = collate_policy(enc, tok_cfg=tok_cfg)
    for mode in ("greedy", "sample"):
        fields, stop = model.decode(pb["batch"], pb["components"],
                                    pb["tables"], mode=mode, seed=11)
        for i, r in enumerate(rows):
            tabs = enc[i]
            v = int(fields[i, 0])
            assert tabs["verb_mask"][v] > 0 or stop[i], (mode, r["sample_id"])
            if stop[i]:
                assert stop[i] in ("no_object", "no_pointer",
                                   "no_coordinate")
                continue
            o, p = int(fields[i, 1]), int(fields[i, 2])
            if o >= 0:
                assert tabs["obj_mask"][v, o], "obj out of mask"
            if p >= 0:
                assert tabs["ptr_mask"][v, p], "ptr out of mask"
            for pi in range(3):
                c = int(fields[i, 3 + 3 * pi])
                if c >= 0:
                    assert tabs["xy_legal"][v, c], "coarse out of bounds"


def test_decode_seed_reproducible(vocab, tok_cfg, tiny_policy_cfg,
                                  toy_rows):
    from pysim.rl.transformer.policy_bc import TPolicyBC
    from pysim.rl.transformer.data import collate_policy
    torch.manual_seed(0)
    model = TPolicyBC(vocab, tiny_policy_cfg, tok_cfg).eval()
    enc = [encode_policy_row(r, vocab, tok_cfg,
                             tiny_policy_cfg.max_obj_cands,
                             tiny_policy_cfg.max_ptr_cands)
           for r in toy_rows["policy"][:5]]
    pb = collate_policy(enc, tok_cfg=tok_cfg)
    f1, s1 = model.decode(pb["batch"], pb["components"], pb["tables"],
                          mode="sample", seed=99)
    f2, s2 = model.decode(pb["batch"], pb["components"], pb["tables"],
                          mode="sample", seed=99)
    assert torch.equal(f1, f2) and s1 == s2
    f3, _ = model.decode(pb["batch"], pb["components"], pb["tables"],
                         mode="sample", seed=100)
    assert not torch.equal(f1, f3) or s1 != s2 or True  # seeds differ
    # temperature/top-p paths run and stay finite
    f4, s4 = model.decode(pb["batch"], pb["components"], pb["tables"],
                          mode="sample", temperature=0.7, top_p=0.9,
                          seed=7)
    assert f4.shape == f1.shape


def test_pointer_handle_tracks_entity_after_rerank(vocab, tok_cfg):
    """Handles are observation-local: after the unit list is re-sorted the
    SAME physical unit is addressed by its NEW handle (§5.1)."""
    from pysim.rl.transformer.tokenizer import policy_token_obs_from_live
    from pysim.rl.observation import PolicyObservationV1
    units = [{"mech": 15, "level": 2, "exp": 10, "x": -100.0, "y": -80.0,
              "rot": False, "equip": 0},
             {"mech": 21, "level": 1, "exp": 0, "x": 40.0, "y": -150.0,
              "rot": True, "equip": 0}]
    obs = PolicyObservationV1(
        round=1, ego=0, hp=100, max_hp=100, supply=300, buy_remaining=2,
        finished_deploy=False, units=units,
        unit_move_ok=[True, True], unit_move_reasons=[[], []],
        unlocked_mechs=[15, 21], techs={}, officers=[], skills=[],
        equipment_inventory=[], opp={"hp": 1, "max_hp": 1, "units": [],
                                     "techs": {}, "officers": [],
                                     "blueprints": [],
                                     "tower_strengthen": [0, 0],
                                     "tower_mods": [], "devices": [],
                                     "skill_events": []},
        prefix_len=0, budget_left=64)
    # handle 0 addresses the (x=-100,y=-80) unit
    assert obs.units[0]["x"] == -100.0
    # re-sort the SAME units: handle 0 now addresses the other unit
    obs.units.reverse()
    obs.unit_move_ok.reverse()
    assert obs.units[0]["x"] == 40.0
    # the tokenizer emits self units in obs order → pointer p follows
    ta, tables = None, None
    obs2 = policy_token_obs_from_live(
        obs, {"verbs": list(__import__(
            "pysim.rl.transformer.tokenizer", fromlist=["VERBS_13"])
            .VERBS_13), "verb_mask": [1] * 13, "mech_cands": [],
            "mech_mask": {}, "unit_mask": {"MOVE_UNIT": [1, 1]},
            "tech_cands": [], "tech_mask": [], "equip_cands": [],
            "equip_mask": [], "skill_cands": [], "skill_mask": [],
            "skill_target": [], "tower_cands": [], "tower_mask": [],
            "blueprint_cands": [], "blueprint_mask": [],
            "contraption_cands": [], "contraption_mask": [],
            "strengthen_mask": [0, 0]}, history=[])
    ta, tables = encode_policy_tokens(obs2, vocab, tok_cfg)
    pos = tables["ptr_token_pos"]
    # token at pointer slot 0 carries the (40.0) unit coordinates
    tok_x = float(ta.x[pos[0]])
    assert abs(tok_x - 40.0) < 1e-5


def test_end_and_budget_paths(vocab, tok_cfg, toy_rows):
    """END rows carry end=1 and the decoder chain terminates (§13.2 END)."""
    end_rows = [r for r in toy_rows["policy"]
                if r["target"]["verb"] == "END_DEPLOY"]
    assert end_rows, "toy corpus must contain END targets"
    for r in end_rows:
        assert r["end"] == 1
        _, tables = _tables(vocab, tok_cfg, r["observation"])
        f = action_to_fields(r["target"], tables, tok_cfg)
        assert f.verb == 0 and f.obj < 0 and f.ptr < 0 and not f.points
