"""SMART comparison syntax is structural validation, never semantic AI proof."""
import copy
import pytest
from pydantic import ValidationError
from models import RequirementDraft, DecisionRequest
from conftest import fixture_draft, FakeEvidence, decision
from test_review import identity


@pytest.mark.parametrize('operator,target', [(None, '2'), (None, 'quick'), ('about', 2), ('≤', 2), ('<=', 'quick'), ('<', 'NaN'), ('>=', 'Infinity'), ('>', '-Infinity')])
def test_metric_rejects_missing_invalid_or_unmeasurable_ordered_comparison(operator, target):
    draft = fixture_draft()
    metric = draft['smart']['metrics'][0]
    metric['target'] = target
    if operator is None:
        metric.pop('operator')
    else:
        metric['operator'] = operator
    with pytest.raises(ValidationError):
        RequirementDraft.model_validate(draft)


@pytest.mark.parametrize('operator,target', [('<=', '2'), ('<', 2), ('>=', 0), ('>', -1.5), ('==', 'available'), ('!=', 'unavailable'), ('<=', '2e2')])
def test_metric_operator_preserves_original_numeric_and_categorical_target_types(operator, target):
    draft = fixture_draft()
    draft['smart']['metrics'][0].update(operator=operator, target=target)
    metric = RequirementDraft.model_validate(draft).smart['metrics'][0]
    assert metric['operator'] == operator
    assert metric['target'] == target and type(metric['target']) is type(target)
    assert 'operator' not in draft['smart']['milestone']


@pytest.mark.parametrize('operation', ['edit', 'preview', 'approve'])
def test_metric_comparator_guard_blocks_every_mutation_boundary_without_writes(backend, operation):
    client, repo, service, record = backend
    smart = copy.deepcopy(record['smart'])
    smart['metrics'][0].pop('operator')
    if operation == 'approve':
        # Simulate a historical draft predating the comparator contract.
        repo.state['requirements'][record['id']]['smart'] = smart
        repo.state['revisions'][record['id']][-1]['smart'] = copy.deepcopy(smart)
    before = repo.read()
    if operation == 'edit':
        response = client.patch('/api/requirements/' + record['id'], json=dict(identity(), patch={'smart': smart}))
    elif operation == 'preview':
        response = client.post('/api/scoring/preview', json={'requirement_id': record['id'], 'expected_revision': 1, 'patch': {'smart': smart}})
    else:
        # Reading historical content stays possible, but it cannot be approved.
        assert client.get('/api/requirements/' + record['id']).status_code == 200
        response = client.post('/api/requirements/' + record['id'] + '/decisions', json=decision())
    assert response.status_code == 422, response.text
    assert repo.read() == before


def test_categorical_equality_metric_remains_subject_to_separate_human_approval(backend):
    from models import EditRequest
    client, repo, service, record = backend
    smart = copy.deepcopy(record['smart'])
    smart['metrics'][0].update(operator='==', target='available', unit='availability state', verification_method='Verify accessible control in stationary-vehicle acceptance test')
    edited = service.edit_requirement(record['id'], EditRequest(**dict(identity(), patch={'smart': smart})))
    assert edited['status'] == 'PM_REVIEW' and edited['approved_revision'] is None
    approved = service.decide_requirement(record['id'], DecisionRequest(**decision(2)))
    assert approved['status'] == 'APPROVED'
    assert approved['smart']['metrics'][0]['target'] == 'available'
