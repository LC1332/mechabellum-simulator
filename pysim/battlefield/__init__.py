# Battlefield layer (重构计划 §2.2): versioned compile contracts between the
# transition (match) layer and the battle engine.
#
# Layering (hard rules, 重构计划 §2.1):
#   1. transition never touches Battle private arrays - it compiles an
#      EnvironmentState into the frozen BattleInput below;
#   2. the engine never reads replay/XML/raw actions - it only consumes
#      BattleInput (today through legacy_engine, later natively);
#   3. the compiler never mutates persistent state;
#   4. settlement only consumes the versioned BattleOutcome/V2.
#
# This package must stay import-cycle-free at module level: keep __init__
# empty and import submodules directly.
