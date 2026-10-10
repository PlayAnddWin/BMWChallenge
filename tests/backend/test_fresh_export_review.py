import json

from services.export import export_freeze


def test_markdown_contains_one_complete_manifest_without_duplicate_payloads():
    snapshot = {"id": "freeze", "requirements": [{"id": "REQ-1", "title": "Accessible controls", "description": "Customer-facing approved plan", "evidence_links": [{"record_id": "unique-source-payload"}], "assumptions": [{"id": "assumption", "description": "Recorded uncertainty"}]}], "checkpoint": {"sequence": 0, "hash": "0" * 64}}
    markdown = export_freeze(snapshot, "markdown")
    assert "## REQ-1: Accessible controls" in markdown
    assert markdown.count("```json") == 1
    assert markdown.count("unique-source-payload") == 2  # readable citation and exact manifest
    assert json.loads(markdown.split("```json\n")[1].split("\n```", 1)[0]) == snapshot


def test_markdown_readable_sections_follow_frozen_priority_and_decision_history(backend, draft):
    from conftest import decision
    from models import DecisionRequest, FreezeRequest
    _, repo, service, first = backend
    draft["derivation_key"] = "higher-priority"
    draft["title"] = "Higher priority"
    draft["factor_proposals"]["severity_level"] = 4
    second = service.create_requirement(draft, "synthetic-run")
    for record in (first, second):
        service.decide_requirement(record["id"], DecisionRequest(**decision()))
    snapshot = service.freeze(FreezeRequest(selections={first["id"]: 2, second["id"]: 2},
        expected_ranking_id=repo.read()["rankings"][-1]["id"], actor_id="pm", actor_role="PM_USER", rationale="Freeze prioritized synthetic plan"))
    markdown = export_freeze(snapshot, "markdown")
    readable = markdown.split("## Complete manifest", 1)[0]
    assert readable.index(f"## {second['id']}") < readable.index(f"## {first['id']}")
    for expected in ("Executive summary", "Prioritized approved list", "SMART description", "Source references", "Scoring rationale and evidence coverage", "Open validations and assumptions", "Decision history", "Usability test", "G1 planning", "hash-1", "Reviewed original synthetic evidence", "STATUS_CHANGED", "APPROVED", "UI feasibility requires validation"):
        assert expected in readable
    assert markdown.count("```json") == 1
    assert json.loads(markdown.split("```json\n")[1].split("\n```", 1)[0]) == snapshot


def test_http_markdown_handles_structured_uncertainty_and_embedded_code_fences(backend):
    from conftest import decision
    client, repo, _, record = backend
    rid = record["id"]
    body = {"expected_revision": 1, "actor_id": "pm", "actor_role": "PM_USER", "rationale": "Record uncertainty",
            "patch": {"uncertainties": [{"description": {"open": ["```nested```"]}}]}}
    assert client.patch(f"/api/requirements/{rid}", json=body).status_code == 200
    assert client.post(f"/api/requirements/{rid}/decisions", json=decision(2)).status_code == 200
    frozen = client.post("/api/freezes", json={"selections": {rid: 3}, "expected_ranking_id": repo.read()["rankings"][-1]["id"],
        "actor_id": "pm", "actor_role": "PM_USER", "rationale": "Freeze uncertainty"})
    assert frozen.status_code == 200, frozen.text
    response = client.get(f"/api/freezes/{frozen.json()['id']}/export?format=markdown")
    assert response.status_code == 200, response.text
    manifest = response.text.split("````json\n")[1].split("\n````", 1)[0]
    assert json.loads(manifest) == frozen.json()
