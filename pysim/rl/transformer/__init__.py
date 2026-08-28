# RL Phase 1.5: Transformer baseline package (Transformer基线任务书 2026-08-28).
#
# Engineering-complete skeleton built BEFORE the T0 (1000-replay backtest)
# freeze, per 任务书 §3.2: model skeleton, toy data, token round-trip, DDP
# smoke, throughput probes and unit tests are allowed now; FORMAL sim label
# generation / training / test / arena verdicts stay gated on
# token_contract.t0_gate_allows().
from . import token_contract  # noqa: F401
