"""Deterministic priority and evidence rubrics; no model or database dependency.

Bare v1 inputs retain the synthetic arithmetic contract: severity_level (1..4),
strategic_fit_level (0..4), complexity_level (1..5), target_regions, region_counts
(support/cohort/unassigned_candidates), forecasts, and fixed reference_volume.
G60 and G68 share the G60_G68 sales line. Missing inputs are provisional.

Role 1 contexts opt in with input_kind='role1', or are recognized by native fact
metadata/reference_policy/reach_assessment. They require nonempty target_regions,
target_vehicle_lines and target_powertrains ('ALL' explicitly means whole-line).
Sales facts carry id, kind='sales', unit='vehicles', vehicle_line, region, year,
volume, source_file_id, source_record_id, source_version, source_reference,
applicability='whole_line', and powertrain_split='unknown'. Structured references
pin the same source/record/version and a workbook cell locator. Locators preserve
nested objects/lists (including distribution cells), with null allowed only for
printed_page. Coordinate names row/page/column/chunk_number require positive
integers; start_offset/end_offset require nonnegative integers. reference_policy
contains year=2030, fact_ids, source_versions and a derived total. Its facts must
be in forecasts or reference_forecasts (the full, target-independent baseline).
Optional fact_snapshots/source_snapshots are cross-checked against these facts.

reach_assessment contains kind (direct/whole_line_proxy/successor_proxy/unavailable),
permitted, reason and assumption_ids. Successor mappings additionally supply
vehicle_line_map, mapping target lines to sourced sales lines. Permitted proxies
require described assumptions with those IDs. Their headline is the sourced
upper-bound scenario, not a BEV estimate; reach bounds are [0, volume/reference].

Optional factor_assessments maps customer/strategy/ease to kind
('proposed_assessment'/'unknown'), reason, assumption_ids and provenance='AIProposed'.
Proposed levels retain the standard arithmetic/bounds but are always provisional.
Explicit unknown assessments supply no corresponding level and use midpoint .5
with [0, 1] bounds. Required assumption IDs include every factor assessment.

Evidence accepts distinct_support_comments (or authoritative support_ids),
covered_regions, assignment_reviews (.5 traceable unreviewed, 1 confirmed),
source_count, optional support_source_counts, coverage_gaps and survey_facts.
Native survey facts retain respondent_n/scale and reviewed topic_mapping
metadata (relation, review_state='reviewed', reason). An optional reviewed
corroboration_assessment (kind, fact_ids, reason, review_state) can express the
resolver's scoped assessment. Scalar corroboration/contradiction is arithmetic
compatibility only; it cannot establish corroboration in a Role 1 context.

Legacy contexts explicitly select policy_version='legacy-v0' and provide impact,
market_reach_norm and effort; their 0..10 scale is never relabeled as v1.
"""

from math import fsum, isclose, isfinite, log1p, sqrt
from copy import deepcopy
import json

WEIGHTS = {"customer": .40, "reach": .25, "strategy": .20, "ease": .15}


def _number(value, name, low=0, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = isfinite(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite representable number") from error
    if not finite:
        raise ValueError(f"{name} must be a finite number")
    if value < low or (high is not None and value > high):
        raise ValueError(f"{name} outside allowed range")
    return float(value)


def _count(value, name):
    number = _number(value, name)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return value if isinstance(value, int) else int(number)


def _level(value, name, low, high):
    number = _number(value, name, low, high)
    if not number.is_integer():
        raise ValueError(f"{name} must be a discrete policy level")
    return number


def _factor(value=None, lower=None, upper=None, references=None, reason=None):
    unknown = value is None
    return {"value": .5 if unknown else value,
            "lower": 0 if unknown else value if lower is None else lower,
            "upper": 1 if unknown else value if upper is None else upper,
            "provisional": unknown or lower != upper and lower is not None,
            "references": references or [], "reason": reason}


def _names(values, name):
    if not isinstance(values, (list, tuple, set)):
        raise ValueError(f"{name} must be a collection of identifiers")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{name} must contain nonblank identifiers")
    return {value.strip() for value in values}


def _line(value):
    return "G60_G68" if value in {"G60", "G68", "G60_G68"} else value


def _annotations(context):
    warnings, assumptions = context.get("warnings", []), context.get("assumptions", [])
    if not isinstance(warnings, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in warnings):
        raise ValueError("warnings must be a collection of nonblank strings")
    if not isinstance(assumptions, (list, tuple)) or any(not isinstance(item, (str, dict)) or not item or (isinstance(item, str) and not item.strip()) for item in assumptions):
        raise ValueError("assumptions must be a collection of descriptions or objects")
    try:
        json.dumps({"warnings": warnings, "assumptions": assumptions}, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("annotations must contain finite JSON metadata") from exc
    return list(warnings), deepcopy(list(assumptions))


def _flags(context, names):
    for name in names:
        if name in context and type(context[name]) is not bool:
            raise ValueError(f"{name} must be boolean")


def _valid_locator(locator):
    """Validate native nested coordinates without normalizing their identities."""
    active = set()

    def valid(value, key=None):
        if key in {"row", "page", "column", "chunk_number"}:
            return type(value) is int and value > 0
        if key in {"start_offset", "end_offset"}:
            return type(value) is int and value >= 0
        if key == "cells" and (not isinstance(value, list) or any(not isinstance(item, dict) for item in value)):
            return False
        if value is None:
            return key == "printed_page"
        if isinstance(value, bool):
            return False
        if isinstance(value, (dict, list)):
            identity = id(value)
            if not value or identity in active:
                return False
            active.add(identity)
            try:
                if isinstance(value, dict):
                    return all(isinstance(name, str) and bool(name.strip()) and valid(item, name)
                               for name, item in value.items())
                return all(valid(item) for item in value)
            finally:
                active.remove(identity)
        if isinstance(value, str):
            return bool(value.strip())
        return type(value) is int or type(value) is float and isfinite(value)

    return isinstance(locator, dict) and valid(locator)


def _source_reference(value):
    if isinstance(value, str):
        return bool(value.strip())
    if not isinstance(value, dict):
        return False
    source_id = value.get("source_id", value.get("source_file_id"))
    locator = value.get("locator", value.get("source_locator"))
    return (isinstance(source_id, str) and bool(source_id.strip())
            and _valid_locator(locator))


def _references(values, name):
    if values is None:
        return []
    if not isinstance(values, (list, tuple)) or any(not _source_reference(value) for value in values):
        raise ValueError(f"{name} must be a collection of traceable source references")
    try:
        unique = {json.dumps(value, sort_keys=True, allow_nan=False): deepcopy(value) for value in values}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain serializable source references") from exc
    return [unique[key] for key in sorted(unique)]


def _identifier(value, name):
    return next(iter(_names([value], name)))


def _metadata(value, name):
    try:
        json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain finite JSON metadata") from exc


def _same_snapshot(left, right):
    """Compare exact JSON values/types; nested cell order is part of identity."""
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _role1_input(context, fields, records=()):
    kind = context.get("input_kind")
    if kind is not None and kind not in ("role1", "synthetic"):
        raise ValueError("input_kind must be role1 or synthetic")
    native = any(field in context for field in fields) or any(
        isinstance(record, dict) and any(field in record for field in
            ("id", "kind", "unit", "source_file_id", "source_record_id", "source_version", "applicability", "powertrain_split"))
        for record in records)
    if native and kind == "synthetic":
        raise ValueError("native metadata cannot be labeled as synthetic arithmetic")
    return kind == "role1" or native


def _native_reference(fact, name, required=True):
    source_id = _identifier(fact.get("source_file_id"), f"{name} source_file_id")
    record_id = _identifier(fact.get("source_record_id"), f"{name} source_record_id")
    version = _identifier(fact.get("source_version"), f"{name} source_version")
    reference = fact.get("source_reference")
    if reference is None and not required:
        reference = {"source_id": source_id, "record_id": record_id,
                     "source_version": version, "locator": fact.get("source_locator")}
    if not isinstance(reference, dict) or not _source_reference(reference):
        raise ValueError(f"{name} must have a structured source_reference")
    for keys, expected in ((("source_id", "source_file_id"), source_id),
                           (("record_id", "source_record_id"), record_id),
                           (("source_version",), version)):
        supplied = [reference[key] for key in keys if key in reference]
        if not supplied or any(_identifier(value, f"{name} source_reference {keys[0]}") != expected for value in supplied):
            raise ValueError(f"{name} source_reference disagrees with native provenance")
    locator = reference.get("locator", reference.get("source_locator"))
    if "locator" in reference and "source_locator" in reference and not _same_snapshot(reference["source_locator"], locator):
        raise ValueError(f"{name} source_reference contains conflicting locators")
    if "source_locator" in fact and not _same_snapshot(fact["source_locator"], locator):
        raise ValueError(f"{name} source_reference disagrees with native source_locator")
    return _references([reference], f"{name} source_reference")[0]


def _sales_facts(values, name):
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a collection")
    facts = {}
    for original in values:
        if not isinstance(original, dict):
            raise ValueError(f"{name} fact must be a mapping")
        _metadata(original, name)
        fact = deepcopy(original)
        for field in ("id", "vehicle_line", "region", "source_file_id", "source_record_id", "source_version"):
            fact[field] = _identifier(fact.get(field), f"{name} {field}")
        if fact.get("kind") != "sales" or fact.get("unit") != "vehicles":
            raise ValueError(f"{name} must contain native sales facts in vehicles")
        if fact.get("applicability") != "whole_line" or fact.get("powertrain_split") != "unknown":
            raise ValueError(f"{name} must retain native whole_line applicability and unknown powertrain_split")
        fact["year"] = _count(fact.get("year"), f"{name} year")
        fact["volume"] = _number(fact.get("volume"), f"{name} volume")
        fact["source_reference"] = _native_reference(fact, name)
        locator = fact["source_reference"].get("locator", fact["source_reference"].get("source_locator"))
        if (not isinstance(locator.get("sheet"), str) or not locator["sheet"].strip()
                or type(locator.get("row")) is not int or locator["row"] <= 0
                or type(locator.get("column")) is not int or locator["column"] <= 0):
            raise ValueError(f"{name} source_reference must locate the native sales cell (sheet/row/column)")
        if fact["id"] in facts and not _same_snapshot(_sales_snapshot(facts[fact["id"]]), _sales_snapshot(fact)):
            raise ValueError("conflicting forecasts for the same fact snapshot ID")
        facts[fact["id"]] = fact
    return facts


def _sales_snapshot(fact):
    fields = ("id", "kind", "unit", "vehicle_line", "region", "year", "volume",
              "source_file_id", "source_record_id", "source_version", "source_reference",
              "applicability", "powertrain_split")
    return {field: fact.get(field) for field in fields}


def _unique_sales(facts):
    unique, versions = {}, {}
    for fact in sorted(facts, key=lambda item: item["id"]):
        source_id, version = fact["source_file_id"], fact["source_version"]
        if source_id in versions and versions[source_id] != version:
            raise ValueError("mixed source versions in the sales snapshot")
        versions[source_id] = version
        key = (_line(fact["vehicle_line"]), fact["region"], fact["year"])
        if key in unique and unique[key]["volume"] != fact["volume"]:
            raise ValueError("conflicting forecasts for a shared sales-line key")
        if key in unique and unique[key]["snapshot"] != (source_id, version):
            raise ValueError("conflicting source snapshots for a shared sales-line key")
        record = unique.setdefault(key, {"volume": fact["volume"], "snapshot": (source_id, version),
                                        "references": [], "fact_ids": []})
        record["references"] = _references([*record["references"], fact["source_reference"]], "forecast source_reference")
        record["fact_ids"].append(fact["id"])
    return unique


def _check_sales_snapshots(context, facts):
    if "fact_snapshots" in context:
        snapshots = _sales_facts(context["fact_snapshots"], "fact_snapshots")
        for identity, fact in facts.items():
            if identity not in snapshots or not _same_snapshot(_sales_snapshot(snapshots[identity]), _sales_snapshot(fact)):
                raise ValueError("forecast disagrees with its native fact snapshot")
    if "source_snapshots" not in context:
        return
    snapshots = context["source_snapshots"]
    if not isinstance(snapshots, (list, tuple)) or any(not isinstance(item, dict) for item in snapshots):
        raise ValueError("source_snapshots must be a collection of source record mappings")
    _metadata(snapshots, "source_snapshots")
    for fact in facts.values():
        locator = fact["source_reference"].get("locator", fact["source_reference"].get("source_locator"))
        candidates = [item for item in snapshots
                      if item.get("source_id", item.get("source_file_id")) == fact["source_file_id"]
                      and item.get("record_id", item.get("source_record_id", item.get("id"))) == fact["source_record_id"]
                      and item.get("source_version") == fact["source_version"]]
        if not any(_source_reference({"source_id": fact["source_file_id"], "locator": item.get("locator", item.get("source_locator"))})
                   and all(key in locator and _same_snapshot(locator[key], value)
                           for key, value in item.get("locator", item.get("source_locator", {})).items())
                   for item in candidates):
            raise ValueError("forecast provenance disagrees with its source record snapshot")


def _reference_baseline(context, forecasts):
    reference_facts = _sales_facts(context.get("reference_forecasts", list(forecasts.values())), "reference_forecasts")
    all_facts = dict(reference_facts)
    for identity, fact in forecasts.items():
        if identity in all_facts and not _same_snapshot(_sales_snapshot(all_facts[identity]), _sales_snapshot(fact)):
            raise ValueError("forecast disagrees with the fixed reference snapshot")
        all_facts[identity] = fact
    _unique_sales(all_facts.values())
    _check_sales_snapshots(context, all_facts)
    policy = context.get("reference_policy")
    if not isinstance(policy, dict) or type(policy.get("year")) is not int or policy["year"] != 2030:
        raise ValueError("reference_policy must pin the 2030 sales baseline")
    _metadata(policy, "reference_policy")
    fact_ids = _names(policy.get("fact_ids"), "reference_policy fact_ids")
    versions = _names(policy.get("source_versions"), "reference_policy source_versions")
    if not fact_ids or not versions or not fact_ids <= set(reference_facts):
        raise ValueError("reference_policy must identify available native reference facts and source versions")
    if fact_ids != {identity for identity, fact in reference_facts.items() if fact["year"] == policy["year"]}:
        raise ValueError("reference_policy fact_ids must retain the complete fixed reference_forecasts baseline")
    selected = [reference_facts[identity] for identity in sorted(fact_ids)]
    if any(fact["year"] != policy["year"] for fact in selected):
        raise ValueError("reference_policy fact year disagrees with its fixed year")
    if versions != {fact["source_version"] for fact in selected}:
        raise ValueError("reference_policy source_versions disagree with its fact snapshots")
    unique = _unique_sales(selected)
    try:
        derived = _number(fsum(item["volume"] for item in unique.values()), "derived reference volume")
    except OverflowError as exc:
        raise ValueError("derived reference volume must be finite") from exc
    total = _number(policy.get("total"), "reference_policy total")
    if total == 0 or not isclose(total, derived, rel_tol=1e-12, abs_tol=1e-9):
        raise ValueError("reference_policy total must equal the positive deduplicated native baseline")
    if context.get("reference_volume") is not None:
        reference = _number(context["reference_volume"], "reference_volume")
        if not isclose(reference, total, rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError("reference_volume disagrees with the fixed reference_policy total")
    normalized = dict(deepcopy(policy), fact_ids=sorted(fact_ids), source_versions=sorted(versions), total=total)
    return total, normalized, fact_ids


def _reach_assessment(context):
    assessment = context.get("reach_assessment")
    if assessment is None:
        return {"kind": "unavailable", "permitted": False, "assumption_ids": [],
                "reason": "No resolver reach assessment"}
    if not isinstance(assessment, dict):
        raise ValueError("reach_assessment must be a mapping")
    _metadata(assessment, "reach_assessment")
    if assessment.get("kind") not in ("direct", "whole_line_proxy", "successor_proxy", "unavailable"):
        raise ValueError("invalid reach_assessment kind")
    if type(assessment.get("permitted")) is not bool:
        raise ValueError("reach_assessment permitted must be boolean")
    if assessment["kind"] == "unavailable" and assessment["permitted"]:
        raise ValueError("unavailable reach_assessment cannot be permitted")
    result = deepcopy(assessment)
    result["reason"] = _identifier(assessment.get("reason"), "reach_assessment reason")
    result["assumption_ids"] = sorted(_names(assessment.get("assumption_ids", []), "reach_assessment assumption_ids"))
    return result


def _role1_reach(context, regions, target_lines, warnings, assumptions):
    powertrains = {value.upper() for value in _names(context.get("target_powertrains", []), "target_powertrains")}
    powertrains = {"BEV" if value == "BEVE" else value for value in powertrains}
    if not regions or not target_lines or not powertrains:
        raise ValueError("Role 1 scoring requires meaningful target_regions, target_vehicle_lines and target_powertrains")
    if "ALL" in powertrains and len(powertrains) != 1:
        raise ValueError("target_powertrains ALL cannot be combined with specific powertrains")
    if powertrains - {"ALL", "BEV", "ICE", "PHEV", "HEV", "MHEV"}:
        raise ValueError("target_powertrains must identify meaningful powertrains or explicit ALL")
    forecasts = _sales_facts(context.get("forecasts", []), "forecasts")
    reference, policy, reference_ids = _reference_baseline(context, forecasts)
    assessment = _reach_assessment(context)
    required = _names(context.get("required_assumption_ids", []), "required_assumption_ids") | set(assessment["assumption_ids"])
    mapping = assessment.get("vehicle_line_map", {})
    if not isinstance(mapping, dict):
        raise ValueError("reach_assessment vehicle_line_map must be a mapping")
    normalized_mapping = {}
    for key, value in mapping.items():
        key = _identifier(key, "vehicle_line_map target")
        if key in normalized_mapping:
            raise ValueError("duplicate normalized vehicle_line_map target")
        normalized_mapping[key] = _identifier(value, "vehicle_line_map sales line")
    mapping = normalized_mapping
    if set(mapping) - target_lines:
        raise ValueError("vehicle_line_map must refer to target_vehicle_lines")
    mapped_lines = {_line(mapping.get(line, line)) for line in target_lines}
    candidates = {identity: fact for identity, fact in forecasts.items()
                  if fact["year"] == policy["year"] and fact["region"] in regions
                  and _line(fact["vehicle_line"]) in mapped_lines}
    if set(candidates) - reference_ids:
        raise ValueError("applicable forecasts must belong to the fixed reference_policy fact_ids")
    unique = _unique_sales(candidates.values())
    expected = {(line, region, policy["year"]) for line in mapped_lines for region in regions}
    missing = expected - set(unique)
    if missing:
        warnings.append("Missing forecast scopes: " + ", ".join(f"{line}/{region}" for line, region, _ in sorted(missing)))
    references = _references([source for item in unique.values() for source in item["references"]], "forecast source_reference")
    unavailable = None
    kind = assessment["kind"]
    successor_mapping = any(_line(line) != _line(sales_line) for line, sales_line in mapping.items())
    if not assessment["permitted"] or kind == "unavailable":
        unavailable = assessment["reason"]
    elif missing or not unique:
        unavailable = "Missing comparable sourced forecast for the target scope"
    elif kind == "successor_proxy" and not successor_mapping:
        unavailable = "Successor proxy requires an explicit vehicle_line_map"
    elif successor_mapping and kind != "successor_proxy":
        unavailable = "A successor sales line requires a successor_proxy assessment"
    elif kind == "direct" and (powertrains != {"ALL"} or
            (target_lines & {"G60", "G68"} and not ({"G60", "G68"} <= target_lines or "G60_G68" in target_lines))):
        unavailable = "Whole-line sales with unknown powertrain split are not direct derivative/powertrain-specific reach"
    elif kind in ("whole_line_proxy", "successor_proxy"):
        described = {_identifier(item["id"], "assumption id") for item in assumptions
                     if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
                     and isinstance(item.get("description"), str) and item["description"].strip()}
        missing_assumptions = set(assessment["assumption_ids"]) - described
        if not assessment["assumption_ids"] or missing_assumptions:
            unavailable = "Explicit proxy assumptions required"
            if missing_assumptions:
                unavailable += ": " + ", ".join(sorted(missing_assumptions))
    if unavailable:
        reach = _factor(references=references, reason=unavailable)
        reach["availability"] = "unavailable"
        warnings.append("Reach unavailable: " + unavailable)
    else:
        volume = fsum(item["volume"] for item in unique.values())
        ratio = _number(volume / reference, "reach", 0, 1)
        proxy = kind != "direct"
        reach = _factor(ratio, 0 if proxy else ratio, ratio, references=references, reason=assessment["reason"])
        reach.update(availability="provisional" if proxy else "available", provisional=proxy,
                     target_volume=volume, volume_interpretation="sourced_whole_line_upper_bound" if proxy else "whole_line_volume",
                     headline_convention="sourced upper-bound proxy scenario" if proxy else "direct applicable volume/reference")
        if proxy:
            warnings.append(f"Provisional {kind} reach; whole-line volume is an upper bound, not a BEV estimate")
        if any(item["volume"] == 0 for item in unique.values()):
            warnings.append("Target includes an explicit zero-volume forecast")
    reach.update(kind=kind, assessment=assessment, fact_ids=sorted(candidates),
                 source_versions=sorted({fact["source_version"] for fact in candidates.values()}),
                 reference_volume=reference, reference_policy=policy, required_assumption_ids=sorted(required))
    return reach


def _factor_assessments(context):
    values = context.get("factor_assessments", {})
    levels = {"customer": "severity_level", "strategy": "strategic_fit_level", "ease": "complexity_level"}
    if not isinstance(values, dict) or set(values) - set(levels):
        raise ValueError("factor_assessments must map customer/strategy/ease to assessment metadata")
    _metadata(values, "factor_assessments")
    assessments = {}
    for name, original in values.items():
        if not isinstance(original, dict) or original.get("kind") not in ("proposed_assessment", "unknown"):
            raise ValueError(f"factor_assessments {name} kind must be proposed_assessment or unknown")
        if original.get("provenance") != "AIProposed":
            raise ValueError(f"factor_assessments {name} provenance must be AIProposed")
        assessment = deepcopy(original)
        assessment["reason"] = _identifier(original.get("reason"), f"factor_assessments {name} reason")
        assessment["assumption_ids"] = sorted(_names(original.get("assumption_ids", []), f"factor_assessments {name} assumption_ids"))
        if assessment["kind"] == "unknown" and context.get(levels[name]) is not None:
            raise ValueError(f"factor_assessments {name} unknown assessment cannot supply {levels[name]}")
        assessments[name] = assessment
    return assessments


def calculate_score(context: dict, override=None) -> dict:
    """Compute a reproducible score, sensitivity bounds, and explicit override."""
    if not isinstance(context, dict):
        raise ValueError("score context must be a mapping")
    warnings, assumptions = _annotations(context)
    policy = context.get("policy_version", "v1")
    if policy == "legacy-v0":
        impact = _number(context.get("impact"), "impact", 0, 10)
        reach = _number(context.get("market_reach_norm"), "market_reach_norm", 0, 10)
        effort = _number(context.get("effort"), "effort", 0, 10)
        calculated = round(.4 * impact + .3 * reach + .3 * (10 - effort), 2)
        effective = calculated if override is None else _number(override, "override", 0, 10)
        return {"policy_version": policy, "scale": [0, 10], "calculated_score": calculated,
                "effective_score": effective, "override": override,
                "factors": {"impact": impact, "market_reach_norm": reach, "effort": effort},
                "range": {"lower": calculated, "upper": calculated, "kind": "legacy_arithmetic"}}
    if policy != "v1":
        raise ValueError("unsupported score policy")
    _flags(context, ("severity_provisional", "study_only"))
    customer_references = _references(context.get("customer_references"), "customer_references")
    strategy_references = _references(context.get("strategy_references"), "strategy_references")
    assessments = _factor_assessments(context)
    weights = context.get("weights", WEIGHTS)
    if not isinstance(weights, dict) or set(weights) != set(WEIGHTS):
        raise ValueError("weights must specify the four policy factors")
    weights = {key: _number(value, key, 0, 1) for key, value in weights.items()}
    weight_total = fsum(weights.values())
    if abs(weight_total - 1) > 1e-9:
        raise ValueError("weights must sum to one")
    weights = {key: value / weight_total for key, value in weights.items()}
    forecasts = context.get("forecasts", [])
    if not isinstance(forecasts, (list, tuple)):
        raise ValueError("forecasts must be a collection")
    role1 = _role1_input(context, ("reference_policy", "reach_assessment", "reference_forecasts",
                                  "target_powertrains", "fact_snapshots", "source_snapshots", "factor_assessments"), forecasts)
    regions = _names(context.get("target_regions", []), "target_regions")
    counts = context.get("region_counts", {})
    if not isinstance(counts, dict):
        raise ValueError("region_counts must be a mapping")
    normalized_counts = {}
    for region, record in counts.items():
        normalized = next(iter(_names([region], "region_counts keys")))
        if normalized in normalized_counts:
            raise ValueError("duplicate normalized customer region")
        if not isinstance(record, dict):
            raise ValueError("customer cohort must be a mapping")
        for key in ("support", "cohort", "unassigned_candidates"):
            if record.get(key) is not None:
                _count(record[key], key)
        normalized_counts[normalized] = record
    counts = normalized_counts
    if not regions and not role1:
        regions = set(counts)
    proportions, upper_proportions = [], []
    for region in sorted(regions):
        record = counts.get(region)
        if record is None:
            warnings.append(f"Missing customer cohort: {region}")
            continue
        if record.get("support") is None or record.get("cohort") is None:
            warnings.append(f"Unavailable support or cohort count: {region}")
            continue
        support = _count(record.get("support", 0), "support")
        cohort = _count(record.get("cohort", 0), "cohort")
        candidates = _count(record.get("unassigned_candidates", 0), "unassigned_candidates")
        if support + candidates > cohort:
            raise ValueError("support plus unassigned candidates exceeds cohort")
        if cohort == 0:
            warnings.append(f"Unavailable customer cohort: {region}")
            continue
        proportions.append(support / cohort)
        upper_proportions.append((support + candidates) / cohort)
    severity = context.get("severity_level")
    if severity is not None:
        severity = _level(severity, "severity_level", 1, 4) / 4
    if severity is None and not proportions:
        customer = _factor(reason="Unknown severity and comparable customer cohort")
    else:
        mention_lower = sqrt(sum(proportions) / len(proportions)) if proportions else 0
        mention_upper = sqrt(sum(upper_proportions) / len(upper_proportions)) if proportions else 1
        lower = .6 * (severity if severity is not None else 0) + .4 * mention_lower
        upper = .6 * (severity if severity is not None else 1) + .4 * mention_upper
        value = .6 * (severity if severity is not None else .5) + .4 * (mention_lower if proportions else .5)
        customer = _factor(value, lower, upper, references=customer_references,
                           reason="Within-dataset mention signal; unknown inputs and discovery subset give bounds")
        if len(proportions) < len(regions) or context.get("severity_provisional") or context.get("study_only"):
            customer["provisional"] = True
    target_lines = _names(context.get("target_vehicle_lines", []), "target_vehicle_lines")
    if role1:
        reach = _role1_reach(context, regions, target_lines, warnings, assumptions)
    else:
        reference = context.get("reference_volume")
        if reference is not None:
            reference = _number(reference, "reference_volume")
            if reference == 0:
                raise ValueError("reference_volume must be positive")
        lines = {_line(line) for line in target_lines}
        unique = {}
        for fact in forecasts:
            if not isinstance(fact, dict):
                raise ValueError("forecast must be a mapping")
            if fact.get("year") != 2030:
                continue
            key = (_line(next(iter(_names([fact.get("vehicle_line")], "forecast vehicle_line")))),
                   next(iter(_names([fact.get("region")], "forecast region"))))
            if (regions and key[1] not in regions) or (lines and key[0] not in lines):
                continue
            volume = _number(fact.get("volume"), "forecast volume")
            source = fact.get("source_reference")
            if not _source_reference(source):
                raise ValueError("forecast must have a source_reference")
            if key in unique and unique[key][0] != volume:
                raise ValueError("conflicting forecasts for a shared sales-line key")
            references = unique[key][1] if key in unique else []
            unique[key] = (volume, _references([*references, source], "forecast source_reference"))
        expected = {(line, region) for line in lines for region in regions}
        missing = expected - set(unique)
        if missing:
            warnings.append("Missing forecast scopes: " + ", ".join(f"{a}/{b}" for a, b in sorted(missing)))
        if reference is None or not unique or missing:
            reach = _factor(reason="Missing comparable sourced forecast or fixed reference")
        else:
            volume = sum(unique[key][0] for key in sorted(unique))
            reach = _factor(_number(volume / reference, "reach", 0, 1),
                            references=_references([reference for item in unique.values() for reference in item[1]], "forecast source_reference"))
            reach["target_volume"] = volume
            reach["reference_volume"] = reference
            if any(item[0] == 0 for item in unique.values()):
                warnings.append("Target includes an explicit zero-volume forecast")
    strategy = context.get("strategic_fit_level")
    complexity = context.get("complexity_level")
    factors = {"customer": customer, "reach": reach,
               "strategy": _factor(None if strategy is None else _level(strategy, "strategic_fit_level", 0, 4) / 4,
                                   references=strategy_references),
               "ease": _factor(None if complexity is None else (5 - _level(complexity, "complexity_level", 1, 5)) / 4)}
    if strategy is not None and (not strategy_references or not isinstance(context.get("strategy_rationale"), str) or not context["strategy_rationale"].strip()):
        factors["strategy"]["provisional"] = True
        factors["strategy"]["reason"] = "Uncited strategic assessment requires passage and rationale review"
    required = set(reach.get("required_assumption_ids", []))
    for name, assessment in assessments.items():
        factor = factors[name]
        unknown = assessment["kind"] == "unknown"
        if unknown:
            factor = factors[name] = _factor(references=factor["references"])
        factor.update(assessment=deepcopy(assessment), reason=assessment["reason"], provisional=True,
                      availability="unknown" if unknown else "provisional",
                      required_assumption_ids=list(assessment["assumption_ids"]))
        required.update(assessment["assumption_ids"])
    for name, factor in factors.items():
        factor["weight"] = weights[name]
        factor["contribution"] = 100 * weights[name] * factor["value"]
    calculated = min(100.0, max(0.0, fsum(f["contribution"] for f in factors.values())))
    effective = calculated if override is None else _number(override, "override", 0, 100)
    return {"policy_version": "v1", "scale": [0, 100], "calculated_score": calculated,
            "effective_score": effective, "override": override, "factors": factors,
            "range": {"lower": min(100.0, max(0.0, fsum(100 * weights[k] * f["lower"] for k, f in factors.items()))),
                      "upper": min(100.0, max(0.0, fsum(100 * weights[k] * f["upper"] for k, f in factors.items()))),
                      "kind": "provisional_sensitivity", "statistical_confidence_interval": False},
             "warnings": warnings, "assumptions": assumptions,
             "input_basis": "role1_metadata" if role1 else "synthetic_arithmetic",
             "required_assumption_ids": sorted(required)}


def _reviewed_relation(mapping, name, warnings):
    if mapping is None:
        return {"kind": "unmapped", "review_state": "unreviewed", "reason": "No reviewed mapping"}
    if not isinstance(mapping, dict):
        raise ValueError(f"{name} must be reviewed mapping metadata")
    _metadata(mapping, name)
    relation = mapping.get("kind", mapping.get("relation", "unmapped"))
    if relation not in ("unmapped", "broad", "direct", "contradictory"):
        raise ValueError(f"{name} has an invalid corroboration relation")
    if mapping.get("review_state") != "reviewed":
        warnings.append(f"Unreviewed corroboration mapping: {name}")
        return {"kind": "unmapped", "claimed_kind": relation, "review_state": "unreviewed",
                "reason": "No resolver-reviewed issue-to-study mapping", "mapping": deepcopy(mapping)}
    return {"kind": relation, "review_state": "reviewed",
            "reason": _identifier(mapping.get("reason"), f"{name} reason"), "mapping": deepcopy(mapping)}


def _survey_evidence(context, targets, warnings):
    values = context.get("survey_facts", [])
    if not isinstance(values, (list, tuple)):
        raise ValueError("survey_facts must be a collection")
    facts, unknowns, versions = {}, {}, {}
    for original in values:
        if not isinstance(original, dict):
            raise ValueError("survey fact must be a mapping")
        _metadata(original, "survey_facts")
        fact = deepcopy(original)
        identity = _identifier(fact.get("id"), "survey fact id")
        if not isinstance(fact.get("kind"), str) or not fact["kind"].startswith("survey"):
            raise ValueError("survey_facts must contain native survey facts")
        fact["id"] = identity
        for field in ("source_file_id", "source_record_id", "source_version"):
            fact[field] = _identifier(fact.get(field), f"survey fact {field}")
        if fact.get("region") is not None:
            fact["region"] = _identifier(fact["region"], "survey fact region")
        source_id, version = fact["source_file_id"], fact["source_version"]
        if source_id in versions and versions[source_id] != version:
            raise ValueError("mixed source versions in the native survey snapshot")
        versions[source_id] = version
        fact["source_reference"] = _native_reference(fact, "survey fact", required=False)
        locator = fact["source_reference"].get("locator", fact["source_reference"].get("source_locator"))
        if "cell_locators" in fact and not _same_snapshot(fact["cell_locators"], locator.get("cells")):
            raise ValueError("survey fact source_reference disagrees with native distribution cell_locators")
        respondent_n, scale = fact.get("respondent_n"), fact.get("scale")
        if respondent_n is not None:
            _count(respondent_n, "survey respondent_n")
        if scale is not None and not isinstance(scale, str):
            raise ValueError("survey scale must retain its native description or be unknown")
        fields = []
        if respondent_n is None:
            fields.append("respondent_n")
        elif respondent_n == 0:
            warnings.append(f"Zero survey respondent N: {identity}")
        if scale is None or not scale.strip():
            fields.append("scale")
        if identity in facts and not _same_snapshot(facts[identity], fact):
            raise ValueError("conflicting native survey fact snapshots")
        facts[identity] = fact
        if fields:
            unknowns[identity] = fields
    for identity, fields in sorted(unknowns.items()):
        if "respondent_n" in fields:
            warnings.append(f"Unknown survey respondent N: {identity}; comment support is a separate count")
        if "scale" in fields:
            warnings.append(f"Unknown native survey scale: {identity}; no cross-scale averaging")
    fact_details = []
    for identity, fact in sorted(facts.items()):
        detail = _reviewed_relation(fact.get("topic_mapping"), identity, warnings)
        detail["fact_ids"] = [identity]
        fact_details.append(detail)
    if "corroboration_assessment" in context:
        assessment = context["corroboration_assessment"]
        if not isinstance(assessment, dict):
            raise ValueError("corroboration_assessment must be a mapping")
        detail = _reviewed_relation(assessment, "corroboration_assessment", warnings)
        identities = _names(assessment.get("fact_ids", []), "corroboration_assessment fact_ids")
        if identities - set(facts) or (detail["kind"] != "unmapped" and not identities):
            raise ValueError("reviewed corroboration must identify available native survey facts")
        detail["fact_ids"] = sorted(identities)
        details = [detail, *[item for item in fact_details if item["kind"] == "contradictory"]]
    else:
        details = fact_details
    for detail in details:
        scoped = [facts[identity] for identity in detail["fact_ids"]]
        if any(fact.get("region") not in targets for fact in scoped):
            detail["kind"] = "unmapped"
            detail["reason"] = "Survey fact region is unknown or outside the target scope"
            warnings.append(detail["reason"] + ": " + ", ".join(detail["fact_ids"]))
        detail["references"] = _references([fact["source_reference"] for fact in scoped], "survey source_reference")
    kinds = {detail["kind"] for detail in details}
    kind = "direct" if "direct" in kinds else "broad" if "broad" in kinds else "contradictory" if "contradictory" in kinds else "unmapped"
    if kind == "unmapped":
        warnings.append("No reviewed survey corroboration in target scope (unmapped)")
    return {"value": {"direct": 1, "broad": .5}.get(kind, 0), "kind": kind,
            "contradiction": "contradictory" in kinds, "details": details,
            "unknowns": {identity: unknowns[identity] for identity in sorted(unknowns)},
            "source_ids": {fact["source_file_id"] for fact in facts.values()}}


def _support_sources(context, count, unknown, survey_source_ids, warnings):
    values = context.get("support_source_counts", {})
    if not isinstance(values, dict):
        raise ValueError("support_source_counts must be a mapping of source-file IDs to distinct comment counts")
    counts = {}
    for identity, value in values.items():
        identity = _identifier(identity, "support_source_counts source-file ID")
        if identity in counts:
            raise ValueError("duplicate normalized support source ID")
        counts[identity] = _count(value, "support_source_counts count")
    attributed = sum(counts.values())
    if not unknown and attributed > count:
        raise ValueError("source-attributed comments exceed distinct support comments")
    unattributed = None if unknown else count - attributed
    if unattributed and context.get("source_count") != 1:
        warnings.append("Comment source concentration incomplete: unattributed support comments")
    if count and not unknown and len([value for value in counts.values() if value]) == 1 and attributed == count:
        warnings.append("Customer comment support concentrated in one source")
    return {"support_source_counts": dict(sorted(counts.items())),
            "largest_support_source_share": max(counts.values()) / count if counts and count and not unknown else None,
            "unattributed_support_comments": unattributed,
            "known_source_file_ids": sorted(set(counts) | survey_source_ids)}


def calculate_evidence(context: dict) -> dict:
    """ECI is independent of priority; real corroboration needs reviewed metadata."""
    if not isinstance(context, dict):
        raise ValueError("evidence context must be a mapping")
    _flags(context, ("contradiction", "proposed_targets", "study_only"))
    warnings, assumptions = _annotations(context)
    count = context.get("distinct_support_comments")
    if "support_ids" in context:
        count = len(_names(context["support_ids"], "support_ids"))
    unknown = count is None
    count = 0 if unknown else _count(count, "distinct_support_comments")
    support = min(1, log1p(count) / log1p(50))
    targets = _names(context.get("target_regions", []), "target_regions")
    covered = _names(context.get("covered_regions", []), "covered_regions")
    coverage = len(covered & targets) / len(targets) if targets else 0
    role1 = _role1_input(context, ("survey_facts", "corroboration_assessment", "support_source_counts"))
    survey = None
    if role1:
        if not targets:
            raise ValueError("Role 1 evidence requires meaningful target_regions")
        survey = _survey_evidence(context, targets, warnings)
        corroboration = survey["value"]
        contradiction = survey["contradiction"]
    else:
        corroboration = context.get("corroboration", 0)
        if isinstance(corroboration, str):
            if corroboration not in {"none", "broad", "direct"}:
                raise ValueError("invalid corroboration")
            corroboration = {"none": 0, "broad": .5, "direct": 1}[corroboration]
        corroboration = _number(corroboration, "corroboration", 0, 1)
        if corroboration not in (0, .5, 1):
            raise ValueError("corroboration must be 0, .5, or 1")
        contradiction = context.get("contradiction", False)
    reviews = context.get("assignment_reviews", [])
    if not isinstance(reviews, (list, tuple)):
        raise ValueError("assignment_reviews must be a collection")
    for review in reviews:
        if _number(review, "assignment_review", 0, 1) not in (.5, 1):
            raise ValueError("untraceable assignment or invalid review value")
    validation = sum(reviews) / len(reviews) if reviews else 0
    components = {"support": support, "coverage": coverage, "corroboration": corroboration,
                  "assignment_validation": validation}
    value = 100 * (.35 * support + .25 * coverage + .25 * corroboration + .15 * validation)
    if count < 5:
        warnings.append("Low distinct customer support")
    if unknown:
        warnings.append("Unknown distinct customer comment support count" if role1 else "Unknown respondent or customer support count")
    if targets - covered:
        warnings.append("Regional coverage gaps: " + ", ".join(sorted(targets - covered)))
    if contradiction:
        warnings.append("Contradictory evidence requires separate review")
    source_count = context.get("source_count")
    if source_count is not None:
        source_count = _count(source_count, "source_count")
    if source_count == 1:
        warnings.append("Evidence concentrated in one source")
    concentration = _support_sources(context, count, unknown, survey["source_ids"], warnings) if role1 else None
    if role1 and source_count is not None and source_count < len(concentration["known_source_file_ids"]):
        raise ValueError("source_count is smaller than the native evidence source-file count")
    gaps = sorted((targets - covered) | _names(context.get("coverage_gaps", []), "coverage_gaps"))
    if context.get("coverage_gaps"):
        warnings.append("Evidence coverage gaps: " + ", ".join(sorted(_names(context["coverage_gaps"], "coverage_gaps"))))
    if context.get("proposed_targets"):
        warnings.append("Proposed targets require human review")
    result = {"policy_version": "eci-v1", "value": value, "level": "Low" if value < 40 else "Moderate" if value < 70 else "High",
             "components": components, "distinct_support_comments": None if unknown else count,
             "range": {"lower": value, "upper": min(100, value + (35 if unknown else 0)), "kind": "rubric_unknown_inputs"},
             "unknown_components": ["support"] if unknown else [], "warnings": warnings,
             "assumptions": assumptions, "is_probability": False,
             "input_basis": "role1_metadata" if role1 else "synthetic_arithmetic",
             "source_count": source_count, "coverage_gaps": gaps}
    if survey is not None:
        result.update(corroboration_kind=survey["kind"], contradiction=contradiction,
                      corroboration_details=survey["details"], survey_unknowns=survey["unknowns"],
                      source_concentration=concentration)
    return result


def rank_requirements(requirements):
    """Return baseline ID-to-rank map, with inactive decisions explicitly unranked.

    Rows supply id, status and score.effective_score (or effective_score).
    Mixed score policies must be migrated or ranked in separate baselines.
    """
    if isinstance(requirements, (str, dict)) or requirements is None:
        raise ValueError("ranking baseline must be a collection of records")
    try:
        rows = list(requirements)
    except TypeError as error:
        raise ValueError("ranking baseline must be a collection of records") from error
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ValueError("ranking records must have a nonblank ID")
        if row["id"] != row["id"].strip():
            raise ValueError("ranking ID must be normalized")
        if not isinstance(row.get("score", {}), dict):
            raise ValueError("ranking score must be a mapping")
        if row.get("status", "DRAFT") not in {"DRAFT", "PM_REVIEW", "APPROVED", "REJECTED", "POSTPONED"}:
            raise ValueError("unsupported ranking decision status")
        if row.get("score", {}).get("policy_version", row.get("policy_version", "v1")) not in {"v1", "legacy-v0"}:
            raise ValueError("unsupported ranking score policy")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate requirement ID in ranking baseline")
    policies = {row.get("score", {}).get("policy_version", row.get("policy_version", "v1"))
                for row in rows if row.get("status", "DRAFT") in {"DRAFT", "PM_REVIEW", "APPROVED"}}
    if len(policies) > 1:
        raise ValueError("cannot mix legacy and v1 scores in a ranking baseline")
    active = []
    result = {row["id"]: None for row in rows}
    for row in rows:
        if row.get("status", "DRAFT") in {"DRAFT", "PM_REVIEW", "APPROVED"}:
            score = row.get("score", {}).get("effective_score", row.get("effective_score"))
            high = 10 if policies == {"legacy-v0"} else 100
            active.append((_number(score, "effective_score", 0, high), row["id"]))
    for rank, (_, identity) in enumerate(sorted(active, key=lambda item: (-item[0], item[1])), 1):
        result[identity] = rank
    return result
