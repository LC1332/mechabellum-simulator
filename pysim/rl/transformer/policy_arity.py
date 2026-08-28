# Action-side registry adapter (任务书 §5): target arity comes from the
# skill registry the ENGINE uses — never from the model or the data (§5.1).
# Also the verb vocabulary + its semantic ids shared by tokenizer/history.
from __future__ import annotations

from ...skills import RELEASE_POINT_COUNTS, commander_skill_target_kind

VERBS_13 = ("END_DEPLOY", "BUY_UNIT", "UNLOCK_UNIT", "UPGRADE_UNIT",
            "BUY_TECH", "MOVE_UNIT", "SELL_UNIT", "USE_EQUIPMENT",
            "RELEASE_COMMANDER_SKILL", "ACTIVATE_ENERGY_TOWER_SKILL",
            "STRENGTHEN_TOWER", "ACTIVE_BLUEPRINT", "RELEASE_CONTRAPTION")
VERB_INDEX = {v: i for i, v in enumerate(VERBS_13)}
# history/semantic id space for verbs (0 reserved for PAD/OOV)
VERB_SEM = {v: i + 1 for i, v in enumerate(VERBS_13)}
N_VERBS = len(VERBS_13)


def release_arity(sid: int) -> int:
    """Atomic-action point count for a commander-skill release:
    registry multi-point counts (capsule 2 / beacon 3), single-point for
    position-target skills, 0 for unit/no-target skills (§5.3)."""
    sid = int(sid)
    if sid in RELEASE_POINT_COUNTS:
        return int(RELEASE_POINT_COUNTS[sid])
    kind = commander_skill_target_kind(sid)
    return 1 if kind == "position" else 0
