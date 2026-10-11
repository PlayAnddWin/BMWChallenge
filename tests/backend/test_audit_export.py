from copy import deepcopy
import csv
import io
import json
import pytest
from services.audit import append_event, verify_chain, event_hash
from services.export import BMW_FIELDS, audit_bundle, export_freeze, export_requirements


def ledger():
    state = {}
    for action in ("CREATED", "EVIDENCE_REVIEWED", "APPROVED"):
        append_event(state, {"action": action, "actor": "PM Ada", "rationale": "Reviewed original source",
                             "before": {"score": 42}, "after": {"score": 55}, "versions": {"revision": 1}})
    return state


@pytest.mark.parametrize("field,value", [("actor", "AI_SYSTEM"), ("rationale", "changed"),
    ("before", {"score": 999}), ("after", {"description": "changed"}), ("versions", {"revision": 2})])
def test_every_decision_field_is_protected(field, value):
    state = ledger()
    assert verify_chain(state["events"], state["checkpoint"])
    state["events"][1][field] = value
    assert not verify_chain(state["events"], state["checkpoint"])


def test_deletion_reorder_and_full_rehash_detected_by_checkpoint():
    state = ledger()
    assert not verify_chain(state["events"][:-1], state["checkpoint"])
    assert not verify_chain(state["events"][::-1], state["checkpoint"])
    state["events"][0]["rationale"] = "forged"
    for index, event in enumerate(state["events"]):
        if index:
            event["previous_hash"] = state["events"][index - 1]["hash"]
        event["hash"] = event_hash(event)
    assert not verify_chain(state["events"], state["checkpoint"])


def test_failed_append_and_input_aliases_do_not_mutate_ledger():
    state = ledger()
    before = deepcopy(state)
    def failure():
        raise RuntimeError("injected audit failure")
    with pytest.raises(RuntimeError):
        append_event(state, {"action": "FREEZE"}, failure)
    assert state == before
    event = {"action": "EDIT", "after": {"title": "original"}}
    appended = append_event(state, event)
    event["after"]["title"] = "mutated input"
    appended["after"]["title"] = "mutated result"
    assert state["events"][-1]["after"]["title"] == "original"


def test_audit_bundle_uses_complete_frozen_prefix_after_later_edits():
    state = ledger()
    frozen_checkpoint = deepcopy(state["checkpoint"])
    append_event(state, {"action": "LATER_EDIT"})
    bundle = audit_bundle(state, frozen_checkpoint)
    assert len(bundle["events"]) == 3
    assert verify_chain(bundle["events"], frozen_checkpoint)
    state["events"][0]["actor"] = "changed"
    with pytest.raises(ValueError):
        audit_bundle(state, frozen_checkpoint)


def test_complete_250_record_export_retains_bmw_fields_and_extra_payload():
    records = [{field: [] if field in ("assumptions", "source_references", "linked_findings") else field
                for field in BMW_FIELDS} for _ in range(250)]
    for index, record in enumerate(records):
        record.update(id=f"REQ-{index}", smart={"milestone": "reviewed"},
                      source_references=[{"source_id": "synthetic", "locator": "page 4"}],
                      assumptions=[{"text": "validate packaging", "accepted": True}], extra={"nested": 1})
    parsed_json = json.loads(export_requirements(iter(records), "json"))
    assert parsed_json == records
    parsed_csv = list(csv.DictReader(io.StringIO(export_requirements(iter(records), "csv"))))
    assert len(parsed_csv) == 250
    assert set(BMW_FIELDS) <= parsed_csv[0].keys()
    for field in ("smart", "source_references", "assumptions", "extra"):
        assert json.loads(parsed_csv[-1][field]) == records[-1][field]


def test_freeze_export_preserves_snapshot_and_all_context():
    state = ledger()
    live = {"id": "REQ-1", "description": "approved content", "status": "APPROVED"}
    snapshot = {"id": "freeze-1", "requirements": [deepcopy(live)],
                "audit": audit_bundle(state), "source_versions": {"synthetic": 1}}
    expected = export_freeze(snapshot)
    live["description"] = "later edit"
    assert export_freeze(snapshot) == expected
    markdown = export_freeze(snapshot, "markdown")
    assert "approved content" in markdown
    assert "source_versions" in markdown and "checkpoint" in markdown


def test_unsupported_formats_fail_explicitly():
    with pytest.raises(ValueError):
        export_requirements([], "pdf")
    with pytest.raises(ValueError):
        export_freeze({}, "pdf")
