"""Bounded evidence retrieval; study context never chooses a confidence tier."""
from contracts.intelligence import EvidencePack, EvidenceSpan, Scope, Signal, StudyContext
from services.common import EngineError, scope_matches
from services.interfaces import EvidenceProvider

RETRIEVAL_VERSION = "retrieval-v1"


def diverse_spans(spans: list[EvidenceSpan], limit: int) -> list[EvidenceSpan]:
    groups: dict[tuple, list[EvidenceSpan]] = {}
    for span in sorted(spans, key=lambda item: item.id):
        key = (tuple(span.scope.regions), span.source.source_file_id)
        groups.setdefault(key, []).append(span)
    result = []
    while groups and len(result) < limit:
        for key in list(groups):
            result.append(groups[key].pop(0))
            if not groups[key]:
                del groups[key]
            if len(result) == limit:
                break
    return result


def scoped_studies(studies: list[StudyContext], scope: Scope) -> list[StudyContext]:
    result = []
    for study in studies:
        relation, reason = study.relation, study.reason
        if not study.mapping_reviewed or not scope_matches(study.fact.scope, scope):
            relation = "unmapped"
            reason += "; not reviewed or outside the requested derivative/region/powertrain scope"
        result.append(study.model_copy(update={"relation": relation, "reason": reason}))
    return result


def build_evidence_pack(signal: Signal, scope: Scope, *, evidence_provider: EvidenceProvider) -> EvidencePack:
    # Successor proposals retrieve from the source derivative, while preserving target applicability gaps.
    source_query = Scope(derivatives=signal.source_scope.derivatives, regions=scope.regions,
                         powertrains=scope.powertrains)
    gaps = list(signal.gaps)
    in_scope = [span for span in signal.evidence if scope_matches(span.scope, source_query)]
    supporting = diverse_spans([span for span in in_scope if span.polarity == "supports"], 8)
    opposing = diverse_spans([span for span in in_scope if span.polarity == "opposes"], 4)
    studies = scoped_studies(evidence_provider.get_survey_facts(signal.category, source_query)[:24], source_query)
    sales = [fact for fact in evidence_provider.get_sales_facts(scope, year=2030)[:24]
             if fact.kind == "sales" and fact.year == 2030 and scope_matches(fact.scope, scope)]
    query = signal.category + " " + signal.title
    equipment = [passage for passage in evidence_provider.search_document_chunks(query, source_query, "option", 3)
                 if passage.kind == "option" and scope_matches(passage.scope, source_query)][:3]
    strategy = [passage for passage in evidence_provider.search_document_chunks(query, source_query, "strategy", 3)
                if passage.kind == "strategy" and scope_matches(passage.scope, source_query)][:3]
    for region in scope.regions:
        region_support = any(region in span.scope.regions for span in supporting)
        direct_study = any(region in study.fact.scope.regions and study.relation == "direct" for study in studies)
        if not region_support and not direct_study:
            gaps.append(f"No direct issue evidence for target region {region}; absence is not zero demand.")
    if set(scope.derivatives) != set(signal.source_scope.derivatives):
        gaps.append("Source-to-target derivative carryover is a proposed assumption, not architecture compatibility.")
    if any(study.relation == "contradictory" for study in studies) or opposing:
        gaps.append("Opposing evidence is present; inspect its scope and interpretation separately.")
    if any(study.fact.respondent_count is None for study in studies):
        gaps.append("Study respondent counts are unknown; distributions do not establish sample size.")
    if scope.powertrains and any(not fact.scope.powertrains for fact in sales):
        gaps.append("Sales have no powertrain split; whole-line volume is only an upper-bound proxy.")
    if not sales:
        gaps.append("No applicable 2030 forecast facts retrieved for the target scope.")
    if not strategy:
        gaps.append("No applicable strategy passage retrieved; strategic fit remains provisional.")
    gaps.append("Document market applicability is limited to its supplied scope; translations are not original quotes.")
    pack = EvidencePack(signal_id=signal.id, signal_revision=signal.revision, target_scope=scope,
                        supporting=supporting, opposing=opposing, studies=studies, sales=sales,
                        equipment=equipment, strategy=strategy, gaps=list(dict.fromkeys(gaps)),
                        retrieval_version=RETRIEVAL_VERSION)
    ids = [item.id for item in supporting + opposing + sales + equipment + strategy] + [item.fact.id for item in studies]
    if len(ids) != len(set(ids)):
        raise EngineError("duplicate retrieved evidence identifiers")
    return pack
