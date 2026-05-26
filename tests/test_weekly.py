# tests/test_weekly.py
import math
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from secure_score_graph import compute_weekly_stats


def _make_json(current_score, max_score, last_score, last_max=None, tenant_name='Contoso', avg_all=None):
    last_max = last_max or max_score
    avg_comparative = []
    if avg_all is not None:
        avg_comparative = [{'basis': 'AllTenants', 'averageScore': avg_all}]
    return {
        'tenant_name': tenant_name,
        'result': [
            {
                'createdDateTime': '2024-01-14T00:00:00Z',
                'currentScore': last_score,
                'maxScore': last_max,
                'averageComparativeScores': [],
                'azureTenantId': 'tenant-abc',
            },
            {
                'createdDateTime': '2024-01-21T00:00:00Z',
                'currentScore': current_score,
                'maxScore': max_score,
                'averageComparativeScores': avg_comparative,
                'azureTenantId': 'tenant-abc',
            },
        ],
    }


def test_score_current_pct():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300))
    assert stats['score_current_pct'] == round(312 / 445 * 100, 2)


def test_score_last_pct():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300))
    assert stats['score_last_pct'] == round(300 / 445 * 100, 2)


def test_score_movement_positive():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300))
    expected = round(312 / 445 * 100 - 300 / 445 * 100, 2)
    assert stats['score_movement'] == expected
    assert stats['score_movement'] > 0


def test_score_movement_negative():
    stats = compute_weekly_stats(_make_json(current_score=290, max_score=445, last_score=312))
    assert stats['score_movement'] < 0


def test_week_of_label():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300))
    assert stats['week_of_label'] == '21 Jan 2024'


def test_tenant_name():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300, tenant_name='Fabrikam'))
    assert stats['tenant_name'] == 'Fabrikam'


def test_tenant_id():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300))
    assert stats['tenant_id'] == 'tenant-abc'


def test_band_cyber_resilient():
    # 400/445 = ~89.9% → Cyber Resilient
    stats = compute_weekly_stats(_make_json(current_score=400, max_score=445, last_score=390))
    assert stats['score_band'] == 'Cyber Resilient'


def test_band_near_ready():
    # 300/445 = ~67.4% → Near Ready
    stats = compute_weekly_stats(_make_json(current_score=300, max_score=445, last_score=290))
    assert stats['score_band'] == 'Near Ready'


def test_band_significant_gaps():
    # 230/445 = ~51.7% → Significant Gaps
    stats = compute_weekly_stats(_make_json(current_score=230, max_score=445, last_score=220))
    assert stats['score_band'] == 'Significant Gaps'


def test_band_critical_risk():
    # 150/445 = ~33.7% → Critical Risk
    stats = compute_weekly_stats(_make_json(current_score=150, max_score=445, last_score=140))
    assert stats['score_band'] == 'Critical Risk'


def test_avg_all_tenants_present():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300, avg_all=65.5))
    assert stats['avg_score_all_tenants'] == 65.5


def test_avg_all_tenants_absent():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300))
    assert stats['avg_score_all_tenants'] is None


def test_gauge_dashoffset_format():
    stats = compute_weekly_stats(_make_json(current_score=312, max_score=445, last_score=300))
    # Must be a string with a decimal point
    assert isinstance(stats['gauge_dashoffset'], str)
    assert '.' in stats['gauge_dashoffset']


def test_sorts_result_by_date_and_takes_last_two():
    # Pass 3 entries — function must sort and pick the last two
    data = {
        'tenant_name': 'Contoso',
        'result': [
            {'createdDateTime': '2024-01-07T00:00:00Z', 'currentScore': 280, 'maxScore': 445, 'averageComparativeScores': [], 'azureTenantId': 't1'},
            {'createdDateTime': '2024-01-21T00:00:00Z', 'currentScore': 312, 'maxScore': 445, 'averageComparativeScores': [], 'azureTenantId': 't1'},
            {'createdDateTime': '2024-01-14T00:00:00Z', 'currentScore': 300, 'maxScore': 445, 'averageComparativeScores': [], 'azureTenantId': 't1'},
        ],
    }
    stats = compute_weekly_stats(data)
    assert stats['score_current_pct'] == round(312 / 445 * 100, 2)
    assert stats['score_last_pct'] == round(300 / 445 * 100, 2)
