from ash.config import Autonomy
from ash.gates import ApprovalGate, Decision


def test_human_required_by_default():
    gate = ApprovalGate(Autonomy())
    assert gate.requires_human("merge") is True
    assert gate.check("escalation") is Decision.wait_for_human


def test_autonomous_when_disabled():
    gate = ApprovalGate(Autonomy(require_human_for_merge=False, require_human_for_escalation=False))
    assert gate.requires_human("merge") is False
    assert gate.check("merge") is Decision.auto_approve
