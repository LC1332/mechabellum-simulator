# Shared fixtures for the rl_transformer matrix (§13) — auto-loaded.
import os
import sys

import pytest

torch = pytest.importorskip("torch")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pysim.gamedata import GameData                                    # noqa: E402
from pysim.rl.transformer.tokenizer import SemanticVocab, TokenizerConfig  # noqa: E402
from pysim.rl.transformer import toydata                               # noqa: E402

GD = GameData(os.path.join(ROOT, "data", "gamedata.json"))


@pytest.fixture(scope="session")
def vocab():
    return SemanticVocab.from_gamedata(GD)


@pytest.fixture(scope="session")
def tok_cfg():
    return TokenizerConfig(max_entity_tokens=320)


@pytest.fixture(scope="session")
def toy_rows():
    sim, real, pol = toydata.make_toy_rows(seed=0, n_games=6)
    return {"sim": sim, "real": real, "policy": pol}


@pytest.fixture(scope="session")
def big_toy_rows():
    sim, real, pol = toydata.make_toy_rows(seed=7, n_games=72)
    return {"sim": sim, "real": real, "policy": pol}


@pytest.fixture()
def tiny_value_cfg():
    from pysim.rl.transformer.battle_value import TValueConfig
    return TValueConfig(d_model=48, n_layers=2, n_heads=4, d_ff=96,
                        dropout=0.0)


@pytest.fixture()
def tiny_policy_cfg():
    from pysim.rl.transformer.policy_bc import TPolicyConfig
    return TPolicyConfig(d_model=48, n_layers_enc=2, n_layers_dec=2,
                         n_heads=4, d_ff=96, dropout=0.0,
                         max_obj_cands=256, max_ptr_cands=64)
