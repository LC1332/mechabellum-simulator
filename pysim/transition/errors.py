# transition reason codes + TransitionError.
# Receipts carry these codes; exceptions are reserved for schema/engine bugs,
# never for ordinary illegal policy actions (rejected receipts instead).
OK = "OK"
WRONG_PHASE = "WRONG_PHASE"
UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
UNSUPPORTED_RULE_DATA = "UNSUPPORTED_RULE_DATA"
UNKNOWN_MECH = "UNKNOWN_MECH"
UNKNOWN_TECH = "UNKNOWN_TECH"
UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
UNKNOWN_ITEM = "UNKNOWN_ITEM"
FUTURE_LOCAL_REF = "FUTURE_LOCAL_REF"
DUPLICATE_LOCAL_REF = "DUPLICATE_LOCAL_REF"
INSUFFICIENT_SUPPLY = "INSUFFICIENT_SUPPLY"
MECH_NOT_UNLOCKED = "MECH_NOT_UNLOCKED"
TECH_ALREADY_OWNED = "TECH_ALREADY_OWNED"
TECH_PREREQUISITE_MISSING = "TECH_PREREQUISITE_MISSING"
EXP_NOT_ENOUGH = "EXP_NOT_ENOUGH"
MAX_LEVEL = "MAX_LEVEL"
POSITION_OUT_OF_BOUNDS = "POSITION_OUT_OF_BOUNDS"
POSITION_OUT_OF_DEPLOY_ZONE = "POSITION_OUT_OF_DEPLOY_ZONE"
PLAYER_ALREADY_FINISHED = "PLAYER_ALREADY_FINISHED"
ACTION_AFTER_END_DEPLOY = "ACTION_AFTER_END_DEPLOY"
UNDO_EMPTY = "UNDO_EMPTY"
BUY_LIMIT_REACHED = "BUY_LIMIT_REACHED"
UNIT_NOT_MOVABLE = "UNIT_NOT_MOVABLE"
# step3 任务书 §5.2/§6.2 precise blockers (never a wrong approximation)
MISSING_RULE_DATA = "MISSING_RULE_DATA"
SKILL_TARGET_INVALID = "SKILL_TARGET_INVALID"
EQUIPMENT_NOT_IN_INVENTORY = "EQUIPMENT_NOT_IN_INVENTORY"
EQUIPMENT_TARGET_NOT_ALLOWED = "EQUIPMENT_TARGET_NOT_ALLOWED"
EQUIPMENT_RESTRICTION_MISMATCH = "EQUIPMENT_RESTRICTION_MISMATCH"
TECH_MECH_NOT_ON_FIELD = "TECH_MECH_NOT_ON_FIELD"


class TransitionError(Exception):
    """Schema corruption / unknown ruleset / engine-internal error.

    Ordinary illegal actions are NOT exceptions: apply_action() returns a
    rejected receipt and leaves the state untouched (strict mode raises this
    only as a wrapper carrying the receipt context).
    """

    def __init__(self, reason_code, detail=""):
        super().__init__("%s: %s" % (reason_code, detail))
        self.reason_code = reason_code
        self.detail = detail
