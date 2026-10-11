"""Complete server-side exports; callers supply unpaginated authoritative records."""
from copy import deepcopy
import csv
import io
import json
import re
from services.audit import canonical_json, verify_or_raise

BMW_FIELDS = (
    "id", "revision", "title", "description", "problem", "smart", "target_scope",
    "linked_findings", "source_references", "evidence", "score", "score_factors",
    "prioritization_rationale", "evidence_index", "evidence_level", "assumptions",
    "uncertainties", "status", "decision", "history",
)


def _record(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return deepcopy(dict(value))


def export_requirements(records, format="json"):
    rows = [_record(record) for record in records]
    if format == "json":
        return json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    if format != "csv":
        raise ValueError("Requirement export format must be csv or json")
    columns = list(BMW_FIELDS) + sorted(set().union(*(row.keys() for row in rows)) - set(BMW_FIELDS))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        row = deepcopy(row)
        aliases = {"problem": "problem_statement", "linked_findings": "finding_links",
                   "source_references": "evidence_links"}
        for column, field in aliases.items():
            if column not in row and field in row:
                row[column] = row[field]
        score, evidence = row.get("score", {}), row.get("evidence", {})
        if isinstance(score, dict):
            row.setdefault("score_factors", score.get("factors", {}))
        if isinstance(evidence, dict):
            row.setdefault("evidence_index", evidence.get("value"))
            row.setdefault("evidence_level", evidence.get("level"))
        writer.writerow({key: canonical_json(value) if isinstance(value, (dict, list, tuple)) else value for key, value in row.items()})
    return output.getvalue()


def audit_bundle(state, checkpoint=None):
    """Include the complete prefix, never a filtered requirement history."""
    checkpoint = deepcopy(checkpoint if checkpoint is not None else state["checkpoint"])
    events = deepcopy(state.get("events", []))
    sequence = checkpoint["sequence"]
    prefix = events[:sequence]
    verify_or_raise(prefix, checkpoint)
    return {"events": prefix, "checkpoint": checkpoint,
            "verification": "tamper-evident relative to retained checkpoint"}


def export_freeze(snapshot, format="json"):
    snapshot = _record(snapshot)
    if format == "json":
        return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    if format != "markdown":
        raise ValueError("Freeze export format must be markdown or json")
    # Render decision information for PMs, with one full manifest for verification.
    requirements = snapshot.get("requirements", [])
    ranks = snapshot.get("ranking", {}).get("ranks", {})
    ordered = sorted(requirements, key=lambda r: (ranks.get(r["id"]) if ranks.get(r["id"]) is not None else float("inf"), r["id"]))
    lines = [f"# Approved plan {snapshot.get('id', '')}", "", "## Executive summary", "",
             f"{len(requirements)} approved requirements frozen at {snapshot.get('timestamp', 'unspecified time')} by {snapshot.get('actor_id', 'unspecified actor')}.",
             str(snapshot.get("rationale", "")), "", "## Prioritized approved list", ""]
    for requirement in ordered:
        score = requirement.get("score", {})
        lines.append(f"- Rank {ranks.get(requirement['id'], 'unavailable')}: {requirement['id']} — {requirement.get('title', '')}; priority {score.get('effective_score', 'unavailable')} ({score.get('policy_version', 'unavailable')}).")
    lines.append("")
    for requirement in ordered:
        lines.extend([f"## {requirement.get('id', '')}: {requirement.get('title', '')}",
                      str(requirement.get("description", "")), ""])
        smart = requirement.get("smart", {})
        lines.extend(["### SMART description", "", str(smart.get("specific", "")), ""])
        for metric in smart.get("metrics", []):
            lines.append(f"- {metric.get('id', '')}: {metric.get('operator', '')} {metric.get('target', '')} {metric.get('unit', '')}; conditions: {metric.get('conditions', '')}; verification: {metric.get('verification_method', '')}; provenance: {metric.get('provenance', '')}.")
        lines.extend(["", f"Milestone: {smart.get('milestone', {}).get('value', 'unavailable')}.",
                      f"Attainability: {smart.get('attainability', '')}", f"Relevance: {smart.get('relevance', '')}", "", "### Source references", ""])
        for reference in requirement.get("evidence_links", []):
            lines.append(f"- {reference.get('relation', '')}: {reference.get('source_id', '')} / {reference.get('record_id', '')}, version {reference.get('source_version', '')}; locator {canonical_json(reference.get('locator', {}))}.")
        score, evidence = requirement.get("score", {}), requirement.get("evidence", {})
        lines.extend(["", "### Scoring rationale and evidence coverage", "", str(requirement.get("prioritization_rationale", "")),
                      f"Calculated priority: {score.get('calculated_score', 'unavailable')}; effective priority: {score.get('effective_score', 'unavailable')}; override: {score.get('override')}.",
                      f"Evidence index: {evidence.get('value', 'unavailable')} ({evidence.get('level', 'unavailable')}); rubric, not probability.",
                      f"Coverage: {canonical_json(evidence.get('components', {}).get('coverage'))}; target scope: {canonical_json(requirement.get('target_scope', {}))}.", "", "### Open validations and assumptions", ""])
        validations = [f"{item.get('id', '')}: {item.get('description', '')} ({item.get('review_state', 'pending')})" for item in requirement.get("assumptions", [])]
        validations.extend(value if isinstance(value := item.get("description"), str) else canonical_json(item) for item in requirement.get("uncertainties", []))
        validations.extend(evidence.get("warnings", []))
        validations.extend(score.get("warnings", []))
        lines.extend(f"- {item}" for item in dict.fromkeys(validations))
        if not validations:
            lines.append("No open validations recorded.")
        lines.extend(["", "### Decision history", ""])
        history = [event for event in snapshot.get("audit", {}).get("events", []) if event.get("entity_id") == requirement["id"] and event.get("action") in {"STATUS_CHANGED", "CONTENT_CHANGED", "SUGGESTION_APPLIED", "LEGACY_REQUIREMENT_IMPORTED", "REQUIREMENT_DERIVED"}]
        for event in history:
            after = event.get("after") or {}
            lines.append(f"- {event.get('timestamp', '')}: {event.get('action', '')}, revision {after.get('revision', '')}, status {after.get('status', '')}; {event.get('actor_id', '')}: {event.get('rationale', '')}")
        if not history:
            lines.append("Decision history unavailable in this snapshot.")
        lines.append("")
    manifest = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", manifest)), default=0))
    lines.extend(["## Complete manifest and audit checkpoint", "", fence + "json", manifest, fence, ""])
    return "\n".join(lines)
