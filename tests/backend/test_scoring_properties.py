"""Reproducible mixed-input invariant checks supplement explicit boundary cases."""
from math import fsum
import random

import pytest

from services.scoring import calculate_score


def test_seeded_mixed_inputs_preserve_score_range_and_override_invariants():
    rng = random.Random(73003)
    for case in range(1000):
        cohort = rng.randint(1, 100000)
        support = rng.randint(0, cohort)
        # Alternate fully known cases with unknown/discovery-subset cases.
        known = case % 2 == 0
        severity = rng.choice([1, 2, 3, 4] if known else [None, 1, 2, 3, 4])
        strategy = rng.choice([0, 1, 2, 3, 4] if known else [None, 0, 1, 2, 3, 4])
        complexity = rng.choice([1, 2, 3, 4, 5] if known else [None, 1, 2, 3, 4, 5])
        context = {
            'severity_level': severity,
            'strategic_fit_level': strategy,
            'complexity_level': complexity,
            'region_counts': {'US': {'support': support, 'cohort': cohort,
                                     'unassigned_candidates': 0 if known else rng.randint(0, cohort - support)}},
            'target_regions': ['US'],
            'target_vehicle_lines': ['G60'],
            'reference_volume': 100,
            'forecasts': [{'vehicle_line': 'G68', 'region': 'US', 'year': 2030,
                           'volume': rng.randint(0, 100), 'source_reference': 'synthetic-ref'}],
            'weights': {'customer': .4 + rng.choice([-1, 0, 1]) * 1e-10,
                        'reach': .25, 'strategy': .2, 'ease': .15},
        }
        score = calculate_score(context)
        assert 0 <= score['range']['lower'] <= score['calculated_score'] <= score['range']['upper'] <= 100, case
        assert score['calculated_score'] == pytest.approx(fsum(factor['contribution'] for factor in score['factors'].values())), case
        if known:
            assert score['range']['lower'] == score['calculated_score'] == score['range']['upper'], case
        override = rng.choice([0, 100, rng.uniform(0, 100)])
        overridden = calculate_score(context, override=override)
        assert overridden['effective_score'] == override, case
        assert overridden['override'] == override, case
        assert {key: value for key, value in overridden.items() if key not in {'effective_score', 'override'}} == {
            key: value for key, value in score.items() if key not in {'effective_score', 'override'}
        }, case
