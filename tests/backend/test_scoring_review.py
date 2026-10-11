"""Independent adversarial checks against Role 3 scoring/export contracts."""
import csv
import io
import json
from copy import deepcopy

import pytest

from services.audit import append_event, verify_chain
from services.export import BMW_FIELDS, export_requirements
from services.scoring import calculate_evidence, calculate_score
from tests.backend.test_scoring import golden


@pytest.mark.parametrize('bad', [-1, 1.5, float('nan'), float('inf'), True, '2'])
@pytest.mark.parametrize('field', ['support', 'cohort', 'unassigned_candidates'])
def test_invalid_counts_cannot_hide_behind_unknown_partner(field, bad):
    context = golden()
    context['region_counts'] = {'EU': {field: bad}}
    with pytest.raises(ValueError):
        calculate_score(context)


@pytest.mark.parametrize('field', ['target_regions', 'target_vehicle_lines'])
@pytest.mark.parametrize('bad', ['EU', None, [''], [True], ['  ']])
def test_scope_requires_explicit_nonblank_identifiers(field, bad):
    context = golden()
    context[field] = bad
    with pytest.raises(ValueError):
        calculate_score(context)


def test_whitespace_scopes_and_shared_line_are_normalized_before_matching():
    context = golden()
    context.update(target_regions=[' EU ', 'EU'], target_vehicle_lines=[' G60 ', 'G68'])
    context['region_counts'] = {' EU ': {'support': 2, 'cohort': 3}}
    for fact in context['forecasts']:
        fact['region'] = ' EU '
    assert calculate_score(context)['calculated_score'] == pytest.approx(55.0639452948)


def test_ambiguous_duplicate_normalized_cohorts_fail():
    context = golden()
    context['region_counts'][' EU '] = {'support': 0, 'cohort': 3}
    with pytest.raises(ValueError, match='duplicate'):
        calculate_score(context)


def test_unknown_severity_keeps_observed_mention_information():
    context = golden()
    context.pop('severity_level')
    factor = calculate_score(context)['factors']['customer']
    assert factor['lower'] == pytest.approx(.4 * (2 / 3) ** .5)
    assert factor['upper'] - factor['lower'] == pytest.approx(.6)
    assert factor['value'] == pytest.approx(.3 + factor['lower'])
    assert factor['provisional']


def test_unknown_cohort_keeps_known_severity_information():
    context = golden()
    context['region_counts'] = {}
    factor = calculate_score(context)['factors']['customer']
    assert factor['lower'] == pytest.approx(.3)
    assert factor['upper'] == pytest.approx(.7)
    assert factor['value'] == pytest.approx(.5)


@pytest.mark.parametrize('bad', [None, 'forecast', [None], [3]])
def test_malformed_forecast_shapes_fail(bad):
    context = golden()
    context['forecasts'] = bad
    with pytest.raises(ValueError):
        calculate_score(context)


@pytest.mark.parametrize('bad', [-1, 1.5, float('nan'), float('inf'), True])
def test_evidence_source_counts_are_validated(bad):
    with pytest.raises(ValueError):
        calculate_evidence({'source_count': bad})


@pytest.mark.parametrize('bad', ['abc', [''], [' '], [None], [1]])
def test_distinct_support_requires_traceable_nonblank_ids(bad):
    with pytest.raises(ValueError):
        calculate_evidence({'support_ids': bad})


def test_evidence_normalizes_regions_without_counting_duplicate_coverage():
    result = calculate_evidence({'target_regions': [' EU ', 'US', 'EU'],
                                 'covered_regions': ['EU', ' EU ', 'CN']})
    assert result['components']['coverage'] == .5
    assert any('US' in warning for warning in result['warnings'])


def test_opposition_does_not_add_support_or_corroboration():
    context = {'support_ids': ['a'], 'corroboration': 'none'}
    baseline = calculate_evidence(context)
    context['contradiction'] = True
    context['proposed_targets'] = True
    result = calculate_evidence(context)
    assert result['value'] == baseline['value']
    assert any('Contradictory' in warning for warning in result['warnings'])
    assert any('Proposed' in warning for warning in result['warnings'])


def test_actual_contract_csv_exports_values_not_blank_alias_columns(draft):
    from services.decisions import GovernanceService
    from services.repository import MemoryRepository
    from tests.backend.conftest import FakeEvidence
    service = GovernanceService(MemoryRepository(), FakeEvidence())
    record = service.create_requirement(draft, 'review-export')
    original = deepcopy(record)
    parsed = list(csv.DictReader(io.StringIO(export_requirements([record], 'csv'))))[0]
    assert parsed['problem'] == record['problem_statement']
    assert json.loads(parsed['linked_findings']) == record['finding_links']
    assert json.loads(parsed['source_references']) == record['evidence_links']
    assert json.loads(parsed['score_factors']) == record['score']['factors']
    assert float(parsed['evidence_index']) == record['evidence']['value']
    assert parsed['evidence_level'] == record['evidence']['level']
    assert record == original
    assert json.loads(export_requirements([record])) == [record]


def test_empty_export_has_complete_header_and_empty_json():
    assert json.loads(export_requirements([])) == []
    reader = csv.DictReader(io.StringIO(export_requirements([], 'csv')))
    assert set(BMW_FIELDS) <= set(reader.fieldnames)
    assert list(reader) == []


@pytest.mark.parametrize('events', [[None], ['bad'], 'bad', None, {}])
def test_malformed_ledger_is_false_not_crash(events):
    assert not verify_chain(events, {'sequence': 1, 'hash': '0' * 64})


def test_nonfinite_event_cannot_partially_mutate_ledger():
    state = {}
    append_event(state, {'action': 'CREATED'})
    before = deepcopy(state)
    with pytest.raises(ValueError):
        append_event(state, {'after': {'score': float('nan')}})
    assert state == before


@pytest.mark.parametrize('bad', [True, 1, [], {}, {'source_id': ' ', 'locator': 'page 1'}])
def test_forecast_reference_must_be_traceable_not_merely_truthy(bad):
    context = golden()
    context['forecasts'][0]['source_reference'] = bad
    with pytest.raises(ValueError, match='source_reference'):
        calculate_score(context)


@pytest.mark.parametrize('bad', [None, [], True, 1])
def test_malformed_weight_container_fails_with_validation_error(bad):
    with pytest.raises(ValueError):
        calculate_score({'weights': bad})


@pytest.mark.parametrize('function', [calculate_score, calculate_evidence])
@pytest.mark.parametrize('bad', [None, [], 'bad'])
def test_context_must_be_mapping(function, bad):
    with pytest.raises(ValueError):
        function(bad)


@pytest.mark.parametrize('bad', [None, '1', True, {}])
def test_review_values_require_collection(bad):
    with pytest.raises(ValueError):
        calculate_evidence({'assignment_reviews': bad})


@pytest.mark.parametrize('function', [calculate_score, calculate_evidence])
@pytest.mark.parametrize('field,bad', [('warnings', None), ('warnings', 'warning'), ('warnings', [{}]),
                                       ('assumptions', None), ('assumptions', 'assume'), ('assumptions', [True])])
def test_annotations_require_structured_collections(function, field, bad):
    with pytest.raises(ValueError):
        function({field: bad})


@pytest.mark.parametrize('locator', [True, 'page 1', {}, {'page': True}, {'page': ' '}])
def test_forecast_locator_requires_structured_nonempty_values(locator):
    context = golden()
    context['forecasts'][0]['source_reference'] = {'source_id': 'sales', 'locator': locator}
    with pytest.raises(ValueError, match='source_reference'):
        calculate_score(context)


def test_structured_reference_and_proxy_assumptions_survive_scoring():
    context = golden()
    reference = {'source_id': 'sales', 'locator': {'sheet': 'Forecast', 'row': 3}}
    for fact in context['forecasts']:
        fact['source_reference'] = reference
    context['assumptions'] = [{'kind': 'powertrain_proxy', 'description': 'Whole-line volume is an upper bound'}]
    result = calculate_score(context)
    assert result['factors']['reach']['references'] == [reference]
    assert result['assumptions'] == context['assumptions']
    result['assumptions'][0]['description'] = 'changed'
    assert context['assumptions'][0]['description'] != 'changed'


@pytest.mark.parametrize('context', [{'policy_version': 'other'}, {'policy_version': 'legacy-v0'},
                                    {'region_counts': {'EU': []}}])
def test_policy_missing_legacy_inputs_and_bad_cohort_rejected(context):
    with pytest.raises(ValueError):
        calculate_score(context)


def test_zero_forecast_is_known_zero_with_warning():
    context = golden()
    for fact in context['forecasts']:
        fact['volume'] = 0
    result = calculate_score(context)
    assert result['factors']['reach']['value'] == 0
    assert not result['factors']['reach']['provisional']
    assert any('zero-volume' in warning for warning in result['warnings'])


@pytest.mark.parametrize('value', ['opposing', .25, True, float('nan')])
def test_invalid_corroboration_cannot_inflate_index(value):
    with pytest.raises(ValueError):
        calculate_evidence({'corroboration': value})


def test_source_concentration_warning_independent_of_score():
    result = calculate_evidence({'source_count': 1})
    assert any('one source' in warning for warning in result['warnings'])
    assert result['value'] == calculate_evidence({})['value']


@pytest.mark.parametrize('bad', [None, True, 'row', {}, [{}], [{'id': ''}], [{'id': ' A '}],
                                [{'id': 'A', 'score': None}], [{'id': 'A', 'status': 'WHAT'}],
                                [{'id': 'A', 'policy_version': 'unknown'}],
                                [{'id': 'A'}, {'id': 'A'}]])
def test_bad_ranking_baseline_is_validation_error(bad):
    from services.scoring import rank_requirements
    with pytest.raises(ValueError):
        rank_requirements(bad)


def test_huge_unrepresentable_number_returns_validation_error():
    context = golden()
    context['reference_volume'] = 10 ** 1000
    with pytest.raises(ValueError):
        calculate_score(context)


def test_large_integer_counts_preserve_exact_original():
    result = calculate_evidence({'distinct_support_comments': 2 ** 53 + 1})
    assert result['distinct_support_comments'] == 2 ** 53 + 1
    assert result['components']['support'] == 1


@pytest.mark.parametrize('checkpoint', [{}, {'sequence': True, 'hash': '0' * 64},
                                      {'sequence': 0.0, 'hash': '0' * 64},
                                      {'sequence': 0, 'hash': '0' * 64, 'extra': 1},
                                      {'sequence': -1, 'hash': '0' * 64},
                                      {'sequence': 0, 'hash': 'short'}])
def test_bad_checkpoint_shape_and_sequence_type_rejected(checkpoint):
    assert not verify_chain([], checkpoint)


@pytest.mark.parametrize('field', ['sequence', 'previous_hash', 'hash'])
def test_caller_cannot_supply_reserved_ledger_metadata(field):
    state = {}
    append_event(state, {'action': 'CREATED'})
    before = deepcopy(state)
    with pytest.raises(ValueError, match='server-owned'):
        append_event(state, {'action': 'EDIT', field: 'forged'})
    assert state == before


@pytest.mark.parametrize('bad', [None, [], 'event'])
def test_nonmapping_event_fails_without_write(bad):
    state = {}
    with pytest.raises(ValueError):
        append_event(state, bad)
    assert state == {}


@pytest.mark.parametrize('bad', [float('inf'), object(), {1: 'number', 'a': 'string'}])
def test_unserializable_payload_verification_fails_safely(bad):
    state = {}
    append_event(state, {'action': 'CREATED'})
    state['events'][0]['after'] = bad
    assert not verify_chain(state['events'], state['checkpoint'])


def test_actual_pydantic_contract_export_preserves_structured_values(backend):
    from models import RequirementDetail
    record = backend[3]
    contract = RequirementDetail.model_validate(record)
    expected = contract.model_dump(mode='json')
    assert json.loads(export_requirements([contract])) == [expected]
    row = next(csv.DictReader(io.StringIO(export_requirements([contract], 'csv'))))
    assert json.loads(row['source_references']) == expected['evidence_links']
    assert json.loads(row['smart']) == expected['smart']


def test_authoritative_support_ids_prevent_broad_count_from_inflating_evidence():
    scoped = {'support_ids': ['issue-a', 'issue-b'], 'distinct_support_comments': 500,
              'target_regions': ['EU'], 'covered_regions': ['EU'], 'corroboration': 'broad'}
    result = calculate_evidence(scoped)
    assert result['distinct_support_comments'] == 2
    assert result['components']['support'] < .3
    scoped['distinct_support_comments'] = 100000
    assert calculate_evidence(scoped)['value'] == result['value']


def test_customer_cohorts_use_regional_proportions_not_pooled_counts():
    context = golden()
    context['target_regions'] = ['EU', 'US']
    context['region_counts'] = {'EU': {'support': 1, 'cohort': 2},
                                'US': {'support': 9, 'cohort': 100}}
    customer = calculate_score(context)['factors']['customer']['value']
    assert customer == pytest.approx(.3 + .4 * ((.5 + .09) / 2) ** .5)
    assert customer != pytest.approx(.3 + .4 * (10 / 102) ** .5)
