"""Compute topic and issue statistics using distinct canonical comments."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings  # noqa: E402


TOPIC_QUERY = """
MATCH (fb:CustomerFeedback)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
WHERE a.vfc2_original IS NOT NULL
WITH fb.model AS model, fb.region AS region, fb.powertrain AS powertrain,
     a.vfc2_original AS topic, fb.id AS feedback_id,
     collect(DISTINCT a) AS annotations
WITH model, region, powertrain, topic,
     count(DISTINCT feedback_id) AS distinct_comments,
     collect(DISTINCT feedback_id) AS feedback_ids,
     sum(size(annotations)) AS annotation_count,
     count(DISTINCT CASE WHEN any(a IN annotations WHERE a.polarity = 'positive') THEN feedback_id END) AS positive_comments,
     count(DISTINCT CASE WHEN any(a IN annotations WHERE a.polarity = 'negative') THEN feedback_id END) AS negative_comments,
     count(DISTINCT CASE WHEN any(a IN annotations WHERE a.polarity = 'request') THEN feedback_id END) AS request_comments
RETURN model, region, powertrain, topic, distinct_comments, annotation_count,
       positive_comments, negative_comments, request_comments, feedback_ids
ORDER BY model, region, powertrain, topic
"""

ISSUE_QUERY = """
MATCH (fb:CustomerFeedback)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
WHERE a.vfc3_original IS NOT NULL
WITH fb.model AS model, fb.region AS region, fb.powertrain AS powertrain,
     a.vfc2_original AS topic, a.vfc3_original AS issue, fb.id AS feedback_id,
     collect(DISTINCT a) AS annotations
WITH model, region, powertrain, topic, issue,
     count(DISTINCT feedback_id) AS distinct_comments,
     collect(DISTINCT feedback_id) AS feedback_ids,
     sum(size(annotations)) AS annotation_count,
     count(DISTINCT CASE WHEN any(a IN annotations WHERE a.polarity = 'positive') THEN feedback_id END) AS positive_comments,
     count(DISTINCT CASE WHEN any(a IN annotations WHERE a.polarity = 'negative') THEN feedback_id END) AS negative_comments,
     count(DISTINCT CASE WHEN any(a IN annotations WHERE a.polarity = 'request') THEN feedback_id END) AS request_comments
RETURN model, region, powertrain, topic, issue, distinct_comments, annotation_count,
       positive_comments, negative_comments, request_comments, feedback_ids
ORDER BY model, region, powertrain, topic, issue
"""


def _data(result: Any) -> list[dict[str, Any]]:
    return [row.data() if hasattr(row, "data") else dict(row) for row in result]


def _persist(session: Any, rows: list[dict[str, Any]], level: str) -> None:
    records = []
    for row in rows:
        identity = [level, row.get("model"), row.get("region"), row.get("powertrain"),
                    row.get("topic"), row.get("issue")]
        key = "|".join("" if part is None else str(part) for part in identity)
        node_id = "VFC-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
        properties = {key: value for key, value in row.items() if key != "feedback_ids"}
        record = {**properties, "id": node_id, "level": level,
                  "rates_json": json.dumps({
                      "positive": row["positive_comments"] / row["distinct_comments"] if row["distinct_comments"] else None,
                      "negative": row["negative_comments"] / row["distinct_comments"] if row["distinct_comments"] else None,
                      "request": row["request_comments"] / row["distinct_comments"] if row["distinct_comments"] else None,
                  }, sort_keys=True),
                  "interpretation": "within_dataset_mention_counts_not_population_prevalence"}
        records.append({"id": node_id, "properties": record, "feedback_ids": row["feedback_ids"]})

    # Reuse the membership obtained by aggregation instead of rescanning the
    # whole annotation graph in a separate transaction for every group.
    for offset in range(0, len(records), 250):
        session.run("""
            UNWIND $rows AS row
            MERGE (stats:VFCAnalysis {id: row.id})
            SET stats += row.properties
            WITH stats, row
            UNWIND row.feedback_ids AS feedback_id
            MATCH (fb:CustomerFeedback {id: feedback_id})
            MERGE (stats)-[:SUMMARIZES]->(fb)
        """, rows=records[offset:offset + 250]).consume()


def analyze(session: Any) -> dict[str, Any]:
    topic_rows = _data(session.run(TOPIC_QUERY))
    issue_rows = _data(session.run(ISSUE_QUERY))
    _persist(session, topic_rows, "topic")
    _persist(session, issue_rows, "issue")
    return {"topics": len(topic_rows), "issues": len(issue_rows),
            "counting_rule": "DISTINCT canonical feedback IDs; annotation count is separate.",
            "rate_interpretation": "Within-dataset annotations, not population preference or prevalence."}


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
        with driver.session() as session:
            summary = analyze(session)
    except Exception as exc:
        print(f"VFC analysis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
