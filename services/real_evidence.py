"""Authoritative governance context backed by Role 01's imported source graph.

This adapter verifies supplied assignments; it does not infer issue relevance,
SMART targets, survey mappings, severity, strategy or engineering feasibility.
Those assessments remain unknown until a separate reviewed policy supplies them.
"""
from itertools import product
from copy import deepcopy
from math import isfinite
from threading import Lock

from services.decisions import DomainError
from services.evidence import EvidenceScope


class RealEvidenceResolver:
    def __init__(self, sources, evidence, fixed_reference_volume=None):
        self.sources = sources
        self.evidence = evidence
        self.fixed_reference_volume = fixed_reference_volume
        self._baseline = None
        self._baseline_lock = Lock()

    def _reference_baseline(self):
        """Pin the full sourced 2030 corpus once; never renormalize by scope."""
        facts = self.evidence.get_sales_facts(None, year=2030)["facts"]
        unique = {}
        for fact in facts:
            self.sources.validate_original_fact(fact)
            line = "G60_G68" if fact["vehicle_line"] in {"G60", "G68", "G60_G68"} else fact["vehicle_line"]
            key = (line, fact["region"])
            volume = fact["volume"]
            if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not isfinite(volume) or volume < 0:
                raise DomainError(422, "Sales baseline requires finite nonnegative original volumes")
            if key in unique and unique[key]["volume"] != volume:
                raise DomainError(422, "Conflicting volumes for the fixed sales baseline")
            unique.setdefault(key, {"vehicle_line": line, "region": fact["region"], "volume": volume, "facts": []})["facts"].append(fact)
        candidate = {"year": 2030, "counting_rule": "Unique supplied vehicle-line/region keys; G60_G68 counted once",
                     "total_volume": sum(v["volume"] for v in unique.values()),
                     "entries": [unique[k] for k in sorted(unique)]}
        with self._baseline_lock:
            if self._baseline is None and candidate["entries"]:
                if self.fixed_reference_volume is not None and self.fixed_reference_volume != candidate["total_volume"]:
                    raise DomainError(422, "Declared baseline must equal all supplied 2030 unique line/region forecasts")
                self._baseline = deepcopy(candidate)
                self.fixed_reference_volume = candidate["total_volume"] or None
            elif self._baseline is not None and candidate != self._baseline:
                raise DomainError(409, "Original sales baseline changed; explicit reviewed policy migration required")
            return deepcopy(self._baseline)

    def _forecast_proxies(self, draft):
        """Explicit PM-authored whole-line policies; acceptance occurs at approval."""
        target = draft["target_scope"]
        proxies = {}
        for assumption in draft.get("assumptions", []):
            policy = assumption.get("forecast_proxy")
            if policy is None:
                continue
            required = {"policy_version", "actor_id", "provenance", "rationale", "target_derivative",
                        "source_derivative", "vehicle_line", "regions", "powertrains"}
            if not isinstance(policy, dict) or set(policy) != required:
                raise DomainError(422, "Forecast proxy requires a complete explicit policy")
            if (policy["policy_version"] != "whole-line-proxy-v1" or policy["provenance"] != "PMEntered"
                    or not isinstance(policy["actor_id"], str) or not policy["actor_id"].strip()
                    or policy["actor_id"].strip().upper() == "AI_SYSTEM"
                    or not isinstance(policy["rationale"], str) or not policy["rationale"].strip()):
                raise DomainError(422, "Forecast proxy requires named PM authorship and rationale")
            source = policy["source_derivative"]
            model = policy["target_derivative"]
            line = "G60_G68" if source in {"G60", "G68"} else source
            if source not in draft["source_scope"]["derivatives"] or model not in target["derivatives"] or policy["vehicle_line"] != line:
                raise DomainError(422, "Forecast proxy must match the supplied source and target derivatives")
            for field in ("regions", "powertrains"):
                values = policy[field]
                if not isinstance(values, list) or not values or len(set(values)) != len(values) or set(values) != set(target[field]):
                    raise DomainError(422, "Forecast proxy must explicitly cover the exact target region and powertrain scope")
            if model != source and not any(mapping.get("source_derivative") == source
                    and mapping.get("target_derivative") == model and mapping.get("assumption_id") == assumption["id"]
                    for mapping in target.get("successor_mappings", [])):
                raise DomainError(422, "Successor forecast proxy requires its explicit reviewed scope mapping")
            if model in proxies:
                raise DomainError(422, "Ambiguous forecast proxy policies for target derivative")
            proxies[model] = {"assumption_id": assumption["id"], "policy": deepcopy(policy)}
        return proxies

    def assert_current(self, context, tx):
        from services.evidence_transaction import validate_context
        validate_context(self, tx, context, self.sources._originals())
        return True

    def resolve(self, draft):
        from services.evidence_transaction import generation
        before = generation(self)
        with self.sources.integrity_session():
            context = self._resolve_verified(draft)
        if generation(self) != before:
            raise DomainError(409, "Source corpus changed during evidence resolution")
        context["_source_generation"] = before
        return context

    def _resolve_verified(self, draft):
        links = draft.get("evidence_links", [])
        if not links or not all(self.sources.verify_reference(link) for link in links):
            raise DomainError(422, "Evidence must match an original imported source version and locator")
        snapshots, feedback = [], {}
        for link in links:
            try:
                record = self.sources.get("record", link["record_id"])
            except DomainError as exc:
                if exc.status != 404:
                    raise
                chunk = self.sources.get("chunk", link["record_id"])
                record = self.sources.get("record", chunk["source_record_id"])
                raw = {"text": chunk["original_text"], "page_payload": record["raw_payload"]}
            else:
                raw = record["raw_payload"]
            snapshots.append({**{k: link[k] for k in ("source_id", "record_id", "source_version", "locator")},
                              "raw_payload": raw})
            if link["relation"] == "supports":
                # The loader's canonical DESCRIBES relationship prevents repeated
                # annotations/source rows from inflating distinct comment support.
                for fb in self.evidence.get_feedback_for_source_record(link["record_id"]):
                    self.sources.validate_original_feedback(fb)
                    feedback[fb["id"]] = fb
        proxies = self._forecast_proxies(draft)
        baseline = self._reference_baseline()
        scope = draft["target_scope"]
        models, regions = sorted(set(scope["derivatives"])), sorted(set(scope["regions"]))
        powertrains = sorted(set(scope.get("powertrains", [])))
        # Empty powertrain means the whole derivative cohort, not a fabricated split.
        region_counts, support_ids, forecasts = {}, set(), {}
        for region in regions:
            cohort = 0
            for model, powertrain in product(sorted({proxies[m]["policy"]["source_derivative"] if m in proxies else m for m in models}), powertrains or [None]):
                counts = self.evidence.get_comment_cohort_counts(EvidenceScope(
                    model=model, region=region, powertrain=powertrain))
                cohort += counts["cohort_distinct_comment_count"]
            supported = {fid for fid, fb in feedback.items() if fb.get("region") == region
                         and fb.get("model") in {proxies[m]["policy"]["source_derivative"] if m in proxies else m for m in models}
                         and (not powertrains or fb.get("powertrain") in powertrains)}
            support_ids.update(supported)
            region_counts[region] = {"support": len(supported), "cohort": cohort}
            sales = self.evidence.get_sales_facts(EvidenceScope(region=region), year=2030)
            for fact in sales["facts"]:
                # Combined G60_G68 volumes require an explicit proxy policy; do
                # not silently treat them as direct derivative/powertrain volumes.
                direct = fact["vehicle_line"] in models and not powertrains
                proxy = any(p["policy"]["vehicle_line"] == fact["vehicle_line"] for p in proxies.values())
                if not direct and not proxy:
                    continue
                forecasts[fact["id"]] = {"vehicle_line": fact["vehicle_line"], "region": fact["region"],
                    "year": fact["year"], "volume": fact["volume"], "source_reference": {
                        "source_id": fact["source_file_id"], "record_id": fact["source_record_id"],
                        "source_version": fact["source_version"], "locator": fact["source_locator"]}}
        warnings = ["Source assignment is traceable but relevance is subject to human PM review",
                    "No reviewed survey corroboration or strategy assessment supplied"]
        if powertrains:
            warnings.append("Whole-line sales are not powertrain-specific forecasts")
        if any(model != p["policy"]["source_derivative"] for model, p in proxies.items()):
            warnings.append("Customer mention fractions describe the mapped source cohort; target derivative applicability remains an explicit assumption")
        if proxies:
            warnings.append("Explicit whole-line proxy: volumes are neither derivative nor powertrain splits or successor forecasts")
        return {"traceable": True, "verified": True, "source_snapshots": snapshots,
            "required_assumption_ids": sorted(p["assumption_id"] for p in proxies.values()),
            "score_context": {"target_regions": regions, "target_vehicle_lines": sorted({proxies[m]["policy"]["vehicle_line"] if m in proxies else m for m in models}),
                "forecast_proxy_policies": deepcopy(proxies), "reach_provisional": bool(proxies),
                "assumptions": [{"id": p["assumption_id"], "forecast_proxy": deepcopy(p["policy"])} for p in proxies.values()],
                "region_counts": region_counts, "customer_references": sorted(support_ids),
                "reference_volume": self.fixed_reference_volume, "reference_baseline": baseline,
                "forecasts": list(forecasts.values()),
                "warnings": warnings},
            "evidence_context": {"support_ids": sorted(support_ids), "target_regions": regions,
                "covered_regions": [r for r in regions if region_counts[r]["support"]],
                "corroboration": "none", "assignment_reviews": [.5] * len(support_ids),
                "contradiction": any(link["relation"] == "contradicts" for link in links),
                "proposed_targets": any(item.get("provenance") != "SourceBacked" for item in
                    draft["smart"]["metrics"] + [draft["smart"]["milestone"]]),
                "source_count": len({link["source_id"] for link in links}), "warnings": warnings}}
