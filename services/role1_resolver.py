"""Resolve backend drafts against imported Role 1 records, counts and forecasts.

No fixture counts or market volumes enter this adapter. Before Role 2 stores an
issue signal, submitted source-row selections are explicitly unreviewed,
non-exhaustive issue assignments. Their observed support is a lower bound.
"""

from collections import Counter
from copy import deepcopy
import hashlib
import json

from models import RequirementDraft
from services.audit import canonical_json
from services.decisions import DomainError
from services.providers import GraphSourceProvider
from services.scoring import calculate_score


class _TransactionSources(GraphSourceProvider):
    def __init__(self, tx):
        self.tx = tx

    def _read(self, query, **params):
        return self.tx.run(query, **params).data()


class Role1EvidenceResolver:
    def __init__(self, source_provider):
        self.sources = source_provider

    @staticmethod
    def _normalized_draft(draft):
        draft = deepcopy(draft)
        for item in draft.get("assumptions", []) + draft.get("smart", {}).get("metrics", []) + [draft.get("smart", {}).get("milestone", {})]:
            item.pop("review_state", None)
        return draft

    @staticmethod
    def _reference(entity, claim="scoring"):
        return {"source_id": entity["source_file_id"], "record_id": entity["source_record_id"],
                "source_version": entity["source_version"], "locator": deepcopy(entity["source_locator"]),
                "claim_key": claim, "relation": "context", "validation_state": "verified"}

    def resolve(self, draft):
        return self._resolve(draft, self.sources)

    def _resolve(self, draft, sources):
        draft = RequirementDraft.model_validate(draft).model_dump()
        draft = self._normalized_draft(draft)
        cache, snapshots = {}, {}

        def get(kind, identity):
            return sources._get(kind, identity, cache)

        manifests = sources._read("MATCH (m:EvidenceManifest {id:'role1'}) RETURN properties(m) AS manifest")
        active = json.loads(manifests[0]["manifest"].get("active_source_ids_json", "[]")) if manifests else []
        source_rows = sources._read("MATCH (s:SourceFile) WHERE size($active)=0 OR s.id IN $active RETURN properties(s) AS source ORDER BY s.id", active=active)
        if not source_rows:
            raise DomainError(503, "No imported evidence is available; run preprocessing and import first")
        source_manifest = [{key: row["source"].get(key) for key in ("id", "filename", "kind", "sha256", "source_version", "conversion_version")} for row in source_rows]
        active_ids = [row["id"] for row in source_manifest]

        def pin(reference, entity=None):
            entity = entity or get("record", reference["record_id"])
            parent = entity.get("source_record", entity)
            raw = parent.get("raw_payload")
            if not isinstance(raw, dict) or not raw:
                raise DomainError(422, "Referenced evidence has no original source payload")
            source_file = entity.get("source_file") or get("source", reference["source_id"])
            source_file = {k: deepcopy(v) for k, v in source_file.items() if not k.startswith(("first_import", "last_import", "import_snapshot"))}
            snapshot = {"source_id": reference["source_id"], "record_id": reference["record_id"],
                        "source_version": reference["source_version"], "locator": deepcopy(reference["locator"]),
                        "raw_payload": deepcopy(raw), "source_file": source_file}
            if entity.get("kind"):
                snapshot["fact_id"] = entity["id"]
                snapshot["fact"] = {k: deepcopy(v) for k, v in entity.items() if k not in {"source_record", "source_file"}}
            elif entity.get("document_kind"):
                snapshot["chunk_id"] = entity["id"]
                snapshot["chunk"] = {k: deepcopy(v) for k, v in entity.items() if k not in {"source_record", "source_file"}}
            snapshots[canonical_json({k: snapshot[k] for k in ("source_id", "record_id", "source_version", "locator")})] = snapshot

        selected_comments, citation_refs, surveys, strategy_refs = {}, [], [], []
        for link in draft["evidence_links"]:
            if link["source_id"] not in active_ids or not sources.verify_reference(link):
                raise DomainError(422, "Evidence citation does not resolve to the active original source version")
            entity = None
            for kind in ("record", "fact", "chunk"):
                try:
                    entity = get(kind, link["record_id"])
                    break
                except DomainError as exc:
                    if exc.status != 404:
                        raise
            pin(link, entity)
            citation_refs.append(deepcopy(link))
            comment_id = entity.get("feedback_comment_id")
            if comment_id:
                comment = get("feedback", comment_id)
                scope = draft["source_scope"]
                if (comment.get("model") not in scope["derivatives"] or comment.get("region") not in scope["regions"]
                        or comment.get("powertrain") not in scope["powertrains"]):
                    raise DomainError(422, "Feedback citation is outside the draft's source scope")
                selected = selected_comments.setdefault(comment_id, {"comment": comment, "relations": set(), "references": []})
                selected["relations"].add(link["relation"])
                selected["references"].append(deepcopy(link))
            if entity.get("kind", "").startswith("survey"):
                surveys.append(entity)
            if entity.get("document_kind") == "corporate_report":
                strategy_refs.append(deepcopy(link))

        support = {identity: value for identity, value in selected_comments.items() if "supports" in value["relations"]}
        signal_snapshots = []
        for finding in draft["finding_links"]:
            try:
                stored = get("signal", finding["signal_id"])
            except DomainError as exc:
                if exc.status != 404:
                    raise
                # These are submitted, cited issue assignments, not imported or
                # model-verified findings. PM source review remains mandatory.
                stored = {"id": finding["signal_id"], "revision": finding["revision"], "payload": {
                    "assignment_origin": "submitted_draft_evidence_selection", "assignment_review": "unreviewed",
                    "source_references": citation_refs, "support_ids": sorted(support), "discovery_complete": False}}
            if stored.get("revision") != finding["revision"]:
                raise DomainError(409, "Signal revision changed; refresh the draft")
            signal_snapshots.append(stored)
            signal_payload = stored.get("payload", {})
            for span in signal_payload.get("evidence", []):
                if not isinstance(span, dict) or "feedback_id" not in span or "source" not in span:
                    raise DomainError(422, "Stored signal contains malformed issue membership")
                source = span["source"]
                comment = get("feedback", span["feedback_id"])
                row = get("record", source["id"])
                start, end = span.get("start"), span.get("end")
                if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(comment["original_text"])
                        or comment["original_text"][start:end] != span.get("text")
                        or row.get("feedback_comment_id") != comment["id"]
                        or comment["source_file_id"] != source["source_file_id"]
                        or comment["source_version"] != source["source_version"]
                        or comment["source_file_id"] not in active_ids):
                    raise DomainError(422, "Signal membership does not resolve to the exact active feedback span")
                if (comment.get("model") not in draft["source_scope"]["derivatives"]
                        or comment.get("region") not in draft["source_scope"]["regions"]
                        or comment.get("powertrain") not in draft["source_scope"]["powertrains"]):
                    raise DomainError(422, "Stored issue membership is outside the draft source scope")
                relation = {"supports": "supports", "opposes": "contradicts", "context": "context"}.get(span.get("polarity"))
                if relation is None:
                    raise DomainError(422, "Stored issue membership has invalid polarity")
                reference = {"source_id": source["source_file_id"], "record_id": source["id"], "source_version": source["source_version"],
                    "locator": row["locator"], "claim_key": span["id"], "relation": relation, "validation_state": "verified"}
                pin(reference, row)
                selected = selected_comments.setdefault(comment["id"], {"comment": comment, "relations": set(), "references": []})
                selected["relations"].add(relation)
                if reference not in selected["references"]:
                    selected["references"].append(reference)
        support = {identity: value for identity, value in selected_comments.items() if "supports" in value["relations"]}

        cohort_rows = sources._read("""
            MATCH (fb:CustomerFeedback)
            WHERE fb.source_file_id IN $source_ids AND fb.model IN $models
              AND fb.region IN $regions AND fb.powertrain IN $powertrains
            RETURN fb.id AS id, fb.region AS region, fb.source_version AS source_version,
                   fb.source_file_id AS source_file_id ORDER BY id
        """, source_ids=active_ids, models=draft["source_scope"]["derivatives"],
            regions=draft["source_scope"]["regions"], powertrains=draft["source_scope"]["powertrains"])
        counts, coverage = {}, set()
        for region in draft["target_scope"]["regions"]:
            ids = {row["id"] for row in cohort_rows if row["region"] == region}
            supporting = {identity for identity, item in support.items() if item["comment"]["region"] == region}
            assigned = {identity for identity, item in selected_comments.items() if item["comment"]["region"] == region}
            if not supporting <= ids:
                raise DomainError(422, "Issue support does not belong to the authoritative customer cohort")
            counts[region] = {"support": len(supporting) if ids else None, "cohort": len(ids) if ids else None,
                              "unassigned_candidates": len(ids - assigned), "support_ids": sorted(supporting),
                              "cohort_ids": sorted(ids), "discovery_complete": False}
            if supporting:
                coverage.add(region)

        rows = sources._read("MATCH (f:Fact {kind:'sales',year:2030}) WHERE f.source_file_id IN $source_ids RETURN f.id AS id ORDER BY id", source_ids=active_ids)
        forecasts, keys = [], {}
        for row in rows:
            native = get("fact", row["id"])
            native = {k: deepcopy(v) for k, v in native.items() if k not in {"source_record", "source_file"}}
            native["source_reference"] = self._reference(native, "2030-sales-baseline")
            pin(native["source_reference"], get("fact", row["id"]))
            key = (native["vehicle_line"], native["region"], native["year"])
            if key in keys and keys[key]["id"] != native["id"]:
                raise DomainError(422, "Forecast baseline contains multiple source versions for a sales scope")
            keys[key] = native
            forecasts.append(native)
        reference_volume = sum(fact["volume"] for fact in keys.values())
        if not reference_volume:
            raise DomainError(503, "No complete sourced 2030 forecast baseline is available")

        # Explicit proxy assumptions come from the draft and are reviewed by the
        # PM. A whole-line forecast is never asserted to be a BEV volume.
        assumptions = deepcopy(draft["assumptions"])
        proxy_ids = [a["id"] for a in assumptions if a.get("kind") in {"whole_line_proxy", "powertrain_proxy", "successor_proxy"}]
        mapping = {m["target_derivative"]: m["source_derivative"] for m in draft["target_scope"].get("successor_mappings", [])}
        reach = {"kind": "successor_proxy" if mapping else "whole_line_proxy", "permitted": bool(proxy_ids),
                 "assumption_ids": proxy_ids, "reason": "Sourced whole-line 2030 sales are an upper-bound proxy; derivative/powertrain splits are unknown"}
        if mapping:
            reach["vehicle_line_map"] = mapping
        proposals = draft["factor_proposals"]
        score_context = {"input_kind": "role1", "policy_version": "v1",
                         "target_regions": draft["target_scope"]["regions"], "target_vehicle_lines": draft["target_scope"]["derivatives"],
                         "target_powertrains": draft["target_scope"]["powertrains"], "region_counts": counts,
                         "forecasts": forecasts, "reference_volume": reference_volume,
                         "reference_policy": {"year": 2030, "total": reference_volume, "fact_ids": [f["id"] for f in forecasts],
                                              "source_versions": sorted({f["source_version"] for f in forecasts})},
                         "reach_assessment": reach, "assumptions": assumptions,
                         "severity_level": proposals.get("severity_level"), "severity_provisional": True,
                         "strategic_fit_level": proposals.get("strategic_fit_level") if strategy_refs else None,
                         "strategy_references": strategy_refs, "strategy_rationale": proposals.get("strategy_rationale", ""),
                         "complexity_level": proposals.get("complexity_level"),
                         "customer_references": [ref for item in support.values() for ref in item["references"]],
                         "warnings": ["Issue discovery is partial; cited canonical comments give observed lower-bound support"]}
        for factor, field in (("customer", "severity_level"), ("strategy", "strategic_fit_level"), ("ease", "complexity_level")):
            score_context.setdefault("factor_assessments", {})[factor] = {
                "kind": "proposed_assessment" if score_context[field] is not None else "unknown",
                "reason": "Submitted assessment requires human review" if score_context[field] is not None else "No source-verified assessment is available",
                "provenance": "AIProposed", "assumption_ids": []}

        prepared_surveys = []
        for native in surveys:
            fact = {k: deepcopy(v) for k, v in native.items() if k not in {"source_record", "source_file"}}
            fact["source_reference"] = self._reference(fact, "native-study-context")
            pin(fact["source_reference"], native)
            prepared_surveys.append(fact)
        source_counts = Counter(item["comment"]["source_file_id"] for item in support.values())
        evidence_context = {"input_kind": "role1", "support_ids": sorted(support),
                            "target_regions": draft["target_scope"]["regions"], "covered_regions": sorted(coverage),
                            "assignment_reviews": [.5 for _ in support], "survey_facts": prepared_surveys,
                            "support_source_counts": dict(source_counts),
                            "source_count": len(set(source_counts) | {f["source_file_id"] for f in prepared_surveys}),
                            "proposed_targets": any(m["provenance"] != "SourceBacked" for m in draft["smart"]["metrics"]),
                            "coverage_gaps": sorted(set(draft["target_scope"]["regions"]) - coverage)}
        score = calculate_score(score_context)
        return {"traceable": True, "verified": all(link["validation_state"] == "verified" for link in citation_refs),
                "evidence_closure_required": True, "source_snapshots": list(snapshots.values()),
                "source_references": citation_refs, "signal_snapshots": signal_snapshots,
                "source_manifest": source_manifest, "cohort_snapshots": cohort_rows,
                "score_context": score_context, "evidence_context": evidence_context,
                "required_assumption_ids": score["required_assumption_ids"], "resolver_draft": draft,
                "prioritization_rationale": "v1 inputs resolved from exact imported records and distinct scoped comments; reach and assessments remain explicitly provisional"}

    def assert_current(self, context, tx):
        if tx is None:
            raise DomainError(503, "Real evidence mutations require the Neo4j transaction")
        tx.run("MATCH (m:EvidenceManifest {id:'role1'}) SET m.lock_version=coalesce(m.lock_version,0)+1 RETURN m.id AS id").consume()
        current = self._resolve(context["resolver_draft"], _TransactionSources(tx))
        if canonical_json(current) != canonical_json(context):
            raise DomainError(409, "Imported evidence changed; refresh and review the draft")
        return True
