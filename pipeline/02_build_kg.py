"""Build descriptive source-rate and mixed-opinion evidence relationships.

Differences between opaque source labels are descriptive rates, not preference
conflicts. Opposite feedback polarity on the same imported VFC3 is surfaced as
a review candidate; it is not promoted to a formal conflict without a reviewed
preference-metric mapping.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings  # noqa: E402


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join("" if value is None else str(value) for value in parts)
    return prefix + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in ("model", "region", "powertrain", "vfc2", "vfc3"))


def collect_annotation_groups(session: Any) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Return distinct commenter sets; repeated annotations never inflate support."""
    rows = session.run("""
        MATCH (fb:CustomerFeedback)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
        WHERE a.vfc3_original IS NOT NULL
        RETURN fb.id AS feedback_id, fb.model AS model, fb.region AS region,
               fb.powertrain AS powertrain, fb.source_label AS source_label,
               a.vfc2_original AS vfc2, a.vfc3_original AS vfc3,
               a.feedback_type_original AS feedback_type, a.polarity AS polarity
        ORDER BY model, region, powertrain, vfc2, vfc3, source_label, feedback_id
    """)
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw in rows:
        row = raw.data() if hasattr(raw, "data") else dict(raw)
        key = _key(row)
        group = groups.setdefault(key, {
            "model": row.get("model"), "region": row.get("region"),
            "powertrain": row.get("powertrain"), "vfc2": row.get("vfc2"),
            "vfc3": row.get("vfc3"), "comments": set(),
            "positive": set(), "negative": set(), "request": set(),
            "source_labels": defaultdict(lambda: {"comments": set(), "negative": set()}),
            "annotation_types": defaultdict(set),
        })
        feedback_id = row.get("feedback_id")
        if not feedback_id:
            continue
        group["comments"].add(feedback_id)
        if row.get("polarity") in {"positive", "negative", "request"}:
            group[row["polarity"]].add(feedback_id)
        if row.get("source_label"):
            source = group["source_labels"][row["source_label"]]
            source["comments"].add(feedback_id)
            if row.get("polarity") == "negative":
                source["negative"].add(feedback_id)
        if row.get("feedback_type"):
            group["annotation_types"][row["feedback_type"]].add(feedback_id)
    return groups


def _save_source_rate_differences(tx: Any, groups: dict[tuple[Any, ...], dict[str, Any]]) -> int:
    pairs = []
    for key, group in groups.items():
        labels = sorted(group["source_labels"].items(), key=lambda item: item[0])
        for index, (label_a, stats_a) in enumerate(labels):
            for label_b, stats_b in labels[index + 1:]:
                count_a, count_b = len(stats_a["comments"]), len(stats_b["comments"])
                if not count_a or not count_b:
                    continue
                rate_a = len(stats_a["negative"]) / count_a
                rate_b = len(stats_b["negative"]) / count_b
                identity = (*key, label_a, label_b)
                pairs.append({
                    "id": _stable_id("SRD", *identity),
                    "model": group["model"], "region": group["region"],
                    "powertrain": group["powertrain"], "vfc2": group["vfc2"], "vfc3": group["vfc3"],
                    "source_label_a": label_a, "source_label_b": label_b,
                    "comments_a": count_a, "comments_b": count_b,
                    "negative_comments_a": len(stats_a["negative"]),
                    "negative_comments_b": len(stats_b["negative"]),
                    "negative_rate_a": rate_a, "negative_rate_b": rate_b,
                    "absolute_rate_difference": abs(rate_a - rate_b),
                    "interpretation": "descriptive_source_rate_difference",
                    "source_label_semantics": "opaque",
                    "is_preference_conflict": False,
                })
    if pairs:
        tx.run("""
            UNWIND $rows AS row
            MERGE (d:SourceRateDifference {id: row.id})
            SET d += row
            MERGE (issue:VFCTag3 {id: row.issue_id})
            SET issue.vfc2 = row.vfc2, issue.vfc3 = row.vfc3
            MERGE (issue)-[:HAS_SOURCE_RATE_DIFFERENCE]->(d)
        """, rows=[{**row, "issue_id": _stable_id("VFC3", row["vfc2"], row["vfc3"])} for row in pairs])
    return len(pairs)


def _save_mixed_opinions(tx: Any, groups: dict[tuple[Any, ...], dict[str, Any]]) -> int:
    rows = []
    for key, group in groups.items():
        if not group["positive"] or not group["negative"]:
            continue
        mix_id = _stable_id("MIX", *key)
        rows.append({
            "id": mix_id,
            "issue_id": _stable_id("VFC3", group["vfc2"], group["vfc3"]),
            "model": group["model"], "region": group["region"],
            "powertrain": group["powertrain"], "vfc2": group["vfc2"], "vfc3": group["vfc3"],
            "positive_comment_count": len(group["positive"]),
            "negative_comment_count": len(group["negative"]),
            "overlapping_comment_count": len(group["positive"] & group["negative"]),
            "interpretation": "candidate_mixed_opinions_for_review",
            "is_preference_conflict": False,
            "reason": "Opposite imported annotation polarity is on the same VFC3; a reviewed preference metric is required to call it a conflict.",
        })
    if rows:
        tx.run("""
            UNWIND $rows AS row
            MERGE (mix:OpinionMix {id: row.id})
            SET mix += row
            MERGE (issue:VFCTag3 {id: row.issue_id})
            SET issue.vfc2 = row.vfc2, issue.vfc3 = row.vfc3
            MERGE (issue)-[:HAS_MIXED_OPINIONS]->(mix)
        """, rows=rows)
    return len(rows)


def build_evidence_relationships(session: Any) -> dict[str, int]:
    groups = collect_annotation_groups(session)
    with session.begin_transaction() as tx:
        source_differences = _save_source_rate_differences(tx, groups)
        mixed_opinions = _save_mixed_opinions(tx, groups)
        tx.commit()
    return {"vfc3_groups": len(groups), "source_rate_differences": source_differences,
            "mixed_opinion_candidates": mixed_opinions, "formal_conflicts_created": 0}


def main() -> int:
    try:
        settings = get_settings()
    except ValueError as exc:
        print(f"Invalid runtime configuration: {exc}", file=sys.stderr)
        return 2
    uri, user, password = settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password.get_secret_value()
    if not all((uri, user, password)):
        print("Set NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD before running.")
        return 2
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("Neo4j driver is missing; install pipeline/requirements-data.txt")
        return 2
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=settings.neo4j_database) as session:
            summary = build_evidence_relationships(session)
    except Exception as exc:
        print(f"Evidence relationship build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
