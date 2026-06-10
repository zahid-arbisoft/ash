from ash.schemas import CodeChange, EditAction, Spec


def _sample_spec_dict():
    return {
        "epic": {
            "title": "t",
            "summary": "s",
            "business_goal": "b",
            "acceptance_criteria": ["a"],
            "edge_cases": [],
        },
        "technical_spec": {
            "approach": "x",
            "affected_areas": [],
            "data_model_changes": [],
            "api_changes": [],
            "testing_strategy": "t",
        },
        "tickets": [
            {
                "id": "T1",
                "title": "t",
                "description": "d",
                "type": "bug",
                "acceptance_criteria": [],
                "dependencies": [],
                "estimate": "S",
            }
        ],
        "risk_assessment": [{"description": "r", "severity": "low", "mitigation": "m"}],
    }


def test_spec_roundtrip():
    spec = Spec.model_validate(_sample_spec_dict())
    assert spec.tickets[0].type.value == "bug"
    assert Spec.model_validate_json(spec.model_dump_json()) == spec


def test_codechange():
    change = CodeChange.model_validate(
        {
            "summary": "do x",
            "edits": [{"path": "a/b.py", "action": "create", "content": "print(1)\n"}],
            "tests_note": "",
        }
    )
    assert change.edits[0].action == EditAction.create
