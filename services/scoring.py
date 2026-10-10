"""Deterministic priority and evidence rubrics; no model or database dependency.

v1 context: severity_level (1..4), strategic_fit_level (0..4),
complexity_level (1..5), target_regions, region_counts mapping each region to
support/cohort/unassigned_candidates, forecasts with vehicle_line/region/year/
volume/source_reference, fixed reference_volume and optional target_vehicle_lines.
G60 and G68 share the G60_G68 sales line. Missing inputs are provisional.
Evidence additionally accepts distinct_support_comments (or support_ids),
covered_regions, corroboration (none/broad/direct or 0/.5/1), assignment_reviews
(.5 traceable unreviewed, 1 confirmed), study_only, warnings and assumptions.
Legacy contexts explicitly select policy_version='legacy-v0' and provide impact,
market_reach_norm and effort; their 0..10 scale is never relabeled as v1.
"""

from math import fsum, isfinite, log1p, sqrt
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


def _source_reference(value):
    return ((isinstance(value, str) and bool(value.strip())) or
            (isinstance(value, dict) and isinstance(value.get("source_id"), str)
             and bool(value["source_id"].strip()) and isinstance(value.get("locator"), dict)
             and bool(value["locator"]) and all(isinstance(key, str) and key.strip()
                 and not isinstance(item, bool) and isinstance(item, (str, int))
                 and (not isinstance(item, str) or item.strip())
                 and (key not in ("row", "page") or type(item) is int and item > 0)
                 for key, item in value["locator"].items())))


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
    weights = context.get("weights", WEIGHTS)
    if not isinstance(weights, dict) or set(weights) != set(WEIGHTS):
        raise ValueError("weights must specify the four policy factors")
    weights = {key: _number(value, key, 0, 1) for key, value in weights.items()}
    weight_total = fsum(weights.values())
    if abs(weight_total - 1) > 1e-9:
        raise ValueError("weights must sum to one")
    weights = {key: value / weight_total for key, value in weights.items()}
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
    if not regions:
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
    reference = context.get("reference_volume")
    if reference is not None:
        reference = _number(reference, "reference_volume")
        if reference == 0:
            raise ValueError("reference_volume must be positive")
    lines = {_line(line) for line in _names(context.get("target_vehicle_lines", []), "target_vehicle_lines")}
    unique = {}
    forecasts = context.get("forecasts", [])
    if not isinstance(forecasts, (list, tuple)):
        raise ValueError("forecasts must be a collection")
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
            "warnings": warnings, "assumptions": assumptions}


def calculate_evidence(context: dict) -> dict:
    """ECI is an evidence rubric, independent of priority and target validity."""
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
    corroboration = context.get("corroboration", 0)
    if isinstance(corroboration, str):
        if corroboration not in {"none", "broad", "direct"}:
            raise ValueError("invalid corroboration")
        corroboration = {"none": 0, "broad": .5, "direct": 1}[corroboration]
    corroboration = _number(corroboration, "corroboration", 0, 1)
    if corroboration not in (0, .5, 1):
        raise ValueError("corroboration must be 0, .5, or 1")
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
        warnings.append("Unknown respondent or customer support count")
    if targets - covered:
        warnings.append("Regional coverage gaps: " + ", ".join(sorted(targets - covered)))
    if context.get("contradiction"):
        warnings.append("Contradictory evidence requires separate review")
    source_count = context.get("source_count")
    if source_count is not None:
        source_count = _count(source_count, "source_count")
    if source_count == 1:
        warnings.append("Evidence concentrated in one source")
    if context.get("proposed_targets"):
        warnings.append("Proposed targets require human review")
    return {"policy_version": "eci-v1", "value": value, "level": "Low" if value < 40 else "Moderate" if value < 70 else "High",
            "components": components, "distinct_support_comments": None if unknown else count,
            "range": {"lower": value, "upper": min(100, value + (35 if unknown else 0)), "kind": "rubric_unknown_inputs"},
            "unknown_components": ["support"] if unknown else [], "warnings": warnings,
            "assumptions": assumptions, "is_probability": False}


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
