# Weekly Secure Score Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `report_type: "weekly"` mode to the Azure Function that produces a 2-page PDF with score snapshot, regression alert, and top 3 controls to action — leaving the existing quarterly path completely untouched.

**Architecture:** A new `compute_weekly_stats()` helper extracts the last two score entries from the POST body and computes all template variables using plain arithmetic. A new `render_weekly_html_report()` renders `weekly_template.html` via the same Jinja2 pattern as the quarterly renderer. The `main()` function branches on `report_type` before any statistical analysis runs.

**Tech Stack:** Python 3, Jinja2, Playwright (PDF), Azure Functions — all already installed.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `secure_score_graph/weekly_template.html` | 2-page Jinja2 HTML template for the weekly PDF |
| Modify | `secure_score_graph/__init__.py` | Add `compute_weekly_stats`, `render_weekly_html_report`, routing in `main()` |
| Create | `tests/test_weekly.py` | Unit tests for the new Python functions |

---

## Task 1: Write failing tests for `compute_weekly_stats`

**Files:**
- Create: `tests/test_weekly.py`

- [ ] **Step 1: Create the test file**

```python
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
```

- [ ] **Step 2: Run tests to confirm they all fail (function doesn't exist yet)**

```
pytest tests/test_weekly.py -v
```

Expected: `ImportError` or `AttributeError: module has no attribute 'compute_weekly_stats'`

---

## Task 2: Implement `compute_weekly_stats` in `__init__.py`

**Files:**
- Modify: `secure_score_graph/__init__.py`

- [ ] **Step 1: Add `compute_weekly_stats` after `build_trend_stats` (around line 218)**

Insert this function:

```python
def compute_weekly_stats(json_data):
    """Extract stats from the last two score entries for the weekly report."""
    from datetime import datetime as _dt

    data = json_data.get('result', [])
    # Parse dates and sort ascending
    for item in data:
        if not isinstance(item.get('date'), _dt):
            item['date'] = _dt.strptime(
                item['createdDateTime'].split('T')[0], '%Y-%m-%d'
            )
    data_sorted = sorted(data, key=lambda x: x['date'])

    last_week = data_sorted[-2]
    this_week = data_sorted[-1]

    score_current_pct = round(this_week['currentScore'] / this_week['maxScore'] * 100, 2)
    score_last_pct    = round(last_week['currentScore'] / last_week['maxScore'] * 100, 2)
    score_movement    = round(score_current_pct - score_last_pct, 2)

    if score_current_pct >= 80:
        score_band = 'Cyber Resilient'
    elif score_current_pct >= 65:
        score_band = 'Near Ready'
    elif score_current_pct >= 45:
        score_band = 'Significant Gaps'
    else:
        score_band = 'Critical Risk'

    avg_all_tenants = None
    for entry in (this_week.get('averageComparativeScores') or []):
        if entry.get('basis') == 'AllTenants':
            avg_all_tenants = float(entry['averageScore'])
            break

    circumference = 2 * math.pi * 42
    dashoffset    = circumference * (1 - score_current_pct / 100)

    tenant_id = this_week.get('azureTenantId') or json_data.get('tenant_id', '')

    return {
        'tenant_name':        json_data.get('tenant_name', 'Customer Tenant'),
        'tenant_id':          tenant_id,
        'generated_date':     datetime.now().strftime('%d %b %Y'),
        'generated_iso':      datetime.now().strftime('%Y-%m-%d'),
        'week_of_label':      this_week['date'].strftime('%-d %b %Y') if os.name != 'nt' else this_week['date'].strftime('%d %b %Y').lstrip('0'),
        'score_current_pct':  score_current_pct,
        'score_last_pct':     score_last_pct,
        'score_movement':     score_movement,
        'current_score_pts':  int(this_week['currentScore']),
        'max_score_pts':      int(this_week['maxScore']),
        'score_band':         score_band,
        'band_css_class':     _BAND_CSS_MAP.get(score_band, 'significant_gaps'),
        'band_description':   _BAND_DESC.get(score_band, ''),
        'gauge_color':        _BAND_GAUGE_COLOR.get(score_band, '#ef4444'),
        'gauge_dashoffset':   f'{dashoffset:.2f}',
        'avg_score_all_tenants': avg_all_tenants,
    }
```

- [ ] **Step 2: Run the tests**

```
pytest tests/test_weekly.py -v
```

Expected: all tests PASS. If `test_week_of_label` fails on Windows date stripping, adjust the `week_of_label` line — the cross-platform lstrip approach above handles it, but verify output matches `'21 Jan 2024'` exactly.

- [ ] **Step 3: Commit**

```bash
git add secure_score_graph/__init__.py tests/test_weekly.py
git commit -m "feat: add compute_weekly_stats helper and tests"
```

---

## Task 3: Write failing tests for `render_weekly_html_report`

**Files:**
- Modify: `tests/test_weekly.py`

- [ ] **Step 1: Append these tests to `tests/test_weekly.py`**

```python
from secure_score_graph import render_weekly_html_report


def _make_controls():
    return [
        {
            'rank': 1, 'title': 'Enable MFA', 'controlCategory': 'Identity',
            'maxScore': 9, 'implementationCost': 'low', 'userImpact': 'moderate',
            'threats': ['accountBreach', 'phishingOrWhaling'],
            'remediation': 'Require MFA for all users via Conditional Access.',
            'remediationImpact': 'Users prompted on first sign-in.',
            'actionUrl': 'https://portal.azure.com/#blade/example',
        },
        {
            'rank': 2, 'title': 'Block Legacy Auth', 'controlCategory': 'Identity',
            'maxScore': 8, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': ['accountBreach'],
            'remediation': 'Create Conditional Access policy to block legacy auth protocols.',
            'remediationImpact': 'Older clients without modern auth will be blocked.',
            'actionUrl': 'https://portal.azure.com/#blade/example2',
        },
        {
            'rank': 3, 'title': 'Enable SSPR', 'controlCategory': 'Identity',
            'maxScore': 4, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': ['accountBreach'],
            'remediation': 'Enable Self-Service Password Reset for all users.',
            'remediationImpact': 'Users can reset passwords without helpdesk.',
            'actionUrl': 'https://portal.azure.com/#blade/example3',
        },
    ]


def test_render_weekly_returns_html_string():
    json_data = _make_json(current_score=312, max_score=445, last_score=300)
    html = render_weekly_html_report(json_data, _make_controls())
    assert isinstance(html, str)
    assert '<!doctype html>' in html.lower()


def test_render_weekly_contains_tenant_name():
    json_data = _make_json(current_score=312, max_score=445, last_score=300, tenant_name='Fabrikam')
    html = render_weekly_html_report(json_data, _make_controls())
    assert 'Fabrikam' in html


def test_render_weekly_contains_weekly_title():
    json_data = _make_json(current_score=312, max_score=445, last_score=300)
    html = render_weekly_html_report(json_data, _make_controls())
    assert 'Weekly Secure Score Update' in html


def test_render_weekly_contains_top_3_control_titles():
    json_data = _make_json(current_score=312, max_score=445, last_score=300)
    html = render_weekly_html_report(json_data, _make_controls())
    assert 'Enable MFA' in html
    assert 'Block Legacy Auth' in html
    assert 'Enable SSPR' in html


def test_render_weekly_regression_mode_when_score_dropped():
    json_data = _make_json(current_score=290, max_score=445, last_score=312)
    html = render_weekly_html_report(json_data, _make_controls())
    assert 'Score Regression This Week' in html


def test_render_weekly_stable_mode_when_score_rose():
    json_data = _make_json(current_score=312, max_score=445, last_score=300)
    html = render_weekly_html_report(json_data, _make_controls())
    assert 'Score Stable' in html or 'Improving' in html


def test_render_weekly_no_controls_shows_placeholder():
    json_data = _make_json(current_score=312, max_score=445, last_score=300)
    html = render_weekly_html_report(json_data, [])
    assert 'Microsoft 365 Security Centre' in html


def test_render_weekly_only_top_3_controls_shown():
    # Pass 5 controls — only top 3 by rank should appear
    controls = _make_controls() + [
        {
            'rank': 4, 'title': 'Control Four', 'controlCategory': 'Data',
            'maxScore': 3, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': [], 'remediation': 'Do thing four.', 'remediationImpact': '',
        },
        {
            'rank': 5, 'title': 'Control Five', 'controlCategory': 'Apps',
            'maxScore': 2, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': [], 'remediation': 'Do thing five.', 'remediationImpact': '',
        },
    ]
    json_data = _make_json(current_score=312, max_score=445, last_score=300)
    html = render_weekly_html_report(json_data, controls)
    assert 'Control Four' not in html
    assert 'Control Five' not in html
```

- [ ] **Step 2: Run tests to confirm new ones fail**

```
pytest tests/test_weekly.py -v -k "render_weekly"
```

Expected: `ImportError` — `render_weekly_html_report` not yet defined.

---

## Task 4: Create `weekly_template.html`

**Files:**
- Create: `secure_score_graph/weekly_template.html`

- [ ] **Step 1: Create the template file**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Weekly Secure Score Update — {{ tenant_name }}</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Lato:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap");

    :root {
      --hero-bg: #1b4d62;
      --cta: #3abfcf;
      --cta-hover: #2ea8b8;
      --brand-pink: #e4007c;
      --white: #ffffff;
      --light-grey: #f2f4f5;
      --border: #e5e7eb;
      --text-primary: #2d2d2d;
      --text-muted: #6b7280;
      --text-on-dark: #ffffff;
      --pass-bg: #dcfce7;
      --pass-text: #15803d;
      --pass-border: #86efac;
      --fail-bg: #fee2e2;
      --fail-text: #dc2626;
      --fail-border: #fca5a5;
      --skip-bg: #f3f4f6;
      --skip-text: #6b7280;
      --skip-border: #d1d5db;
      --sev-critical-bg: #7f1d1d;
      --sev-critical-text: #ffffff;
      --sev-high-bg: #f97316;
      --sev-high-text: #ffffff;
      --sev-medium-bg: #f59e0b;
      --sev-medium-text: #1a1a1a;
      --sev-low-bg: #3b82f6;
      --sev-low-text: #ffffff;
      --sev-info-bg: #e5e7eb;
      --sev-info-text: #374151;
      --font-heading: "Poppins", Arial, sans-serif;
      --font-body: "Lato", Arial, sans-serif;
      --font-mono: "JetBrains Mono", Consolas, monospace;
      --page-w: 210mm;
      --page-h: 297mm;
      --gutter: 14mm;
    }

    @page { size: A4; margin: 0; }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }

    html, body {
      font-family: var(--font-body);
      color: var(--text-primary);
      background: var(--white);
      font-size: 10pt;
      line-height: 1.55;
    }

    .page {
      width: var(--page-w);
      min-height: var(--page-h);
      position: relative;
      overflow: hidden;
      background: var(--white);
      page-break-before: always;
      page-break-inside: avoid;
    }

    .page:first-child { page-break-before: auto; }

    /* ── Cover ── */
    .cover {
      background: var(--hero-bg);
      display: flex;
      flex-direction: column;
      width: 100%;
      height: var(--page-h);
      color: var(--text-on-dark);
    }

    .cover-header {
      padding: 12mm var(--gutter) 8mm;
      border-bottom: 1px solid rgba(255,255,255,0.12);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .cover-logo-wordmark { display: flex; align-items: baseline; }

    .cover-logo-intuity {
      font-family: var(--font-heading);
      font-weight: 700;
      font-size: 20pt;
      color: var(--brand-pink);
      letter-spacing: -0.5px;
    }

    .cover-logo-tech {
      font-family: var(--font-heading);
      font-weight: 600;
      font-size: 8pt;
      color: rgba(255,255,255,0.7);
      letter-spacing: 3px;
      text-transform: uppercase;
      margin-left: 4px;
      align-self: center;
    }

    .cover-confidential-badge {
      font-family: var(--font-body);
      font-size: 7pt;
      color: rgba(255,255,255,0.5);
      letter-spacing: 1.5px;
      text-transform: uppercase;
      border: 1px solid rgba(255,255,255,0.25);
      padding: 3px 8px;
      border-radius: 3px;
    }

    .cover-stripe {
      height: 3px;
      background: linear-gradient(90deg, var(--cta) 0%, var(--brand-pink) 100%);
    }

    .cover-body {
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 var(--gutter);
    }

    .cover-eyebrow {
      font-family: var(--font-body);
      font-size: 8pt;
      font-weight: 700;
      letter-spacing: 3px;
      text-transform: uppercase;
      color: var(--cta);
      margin-bottom: 6mm;
    }

    .cover-title {
      font-family: var(--font-heading);
      font-weight: 700;
      font-size: 30pt;
      line-height: 1.15;
      color: var(--white);
      margin-bottom: 3mm;
    }

    .cover-subtitle {
      font-family: var(--font-heading);
      font-weight: 600;
      font-size: 16pt;
      color: var(--cta);
      margin-bottom: 10mm;
    }

    .cover-meta {
      display: flex;
      flex-direction: column;
      gap: 2mm;
      margin-bottom: 10mm;
    }

    .cover-meta-row {
      display: flex;
      align-items: baseline;
      gap: 6px;
    }

    .cover-meta-label {
      font-family: var(--font-body);
      font-size: 8pt;
      color: rgba(255,255,255,0.5);
      text-transform: uppercase;
      letter-spacing: 1px;
      width: 30mm;
      flex-shrink: 0;
    }

    .cover-meta-value {
      font-family: var(--font-body);
      font-size: 10pt;
      color: rgba(255,255,255,0.9);
      font-weight: 500;
    }

    .cover-score-area {
      display: flex;
      align-items: center;
      gap: 10mm;
      margin-top: 4mm;
    }

    .score-gauge {
      position: relative;
      width: 44mm;
      height: 44mm;
      flex-shrink: 0;
    }

    .score-gauge svg {
      width: 100%;
      height: 100%;
      transform: rotate(-90deg);
    }

    .score-gauge-track {
      fill: none;
      stroke: rgba(255,255,255,0.1);
      stroke-width: 8;
    }

    .score-gauge-fill {
      fill: none;
      stroke-width: 8;
      stroke-linecap: round;
    }

    .score-gauge-inner {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
    }

    .score-number {
      font-family: var(--font-heading);
      font-size: 20pt;
      font-weight: 700;
      color: var(--white);
      line-height: 1;
    }

    .score-denom {
      font-family: var(--font-body);
      font-size: 7pt;
      color: rgba(255,255,255,0.5);
    }

    .score-band-info {
      display: flex;
      flex-direction: column;
      gap: 2mm;
    }

    .score-band-label {
      font-family: var(--font-heading);
      font-weight: 600;
      font-size: 14pt;
    }

    .score-band-desc {
      font-family: var(--font-body);
      font-size: 9pt;
      color: rgba(255,255,255,0.65);
      max-width: 80mm;
      line-height: 1.45;
    }

    .delta-display {
      margin-top: 4mm;
      font-family: var(--font-heading);
      font-size: 22pt;
      font-weight: 700;
      line-height: 1;
    }

    .delta-display .delta-label {
      display: block;
      font-family: var(--font-body);
      font-size: 8pt;
      font-weight: 400;
      color: rgba(255,255,255,0.5);
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-top: 1.5mm;
    }

    .delta-positive { color: #22c55e; }
    .delta-negative { color: #f87171; }
    .delta-neutral  { color: rgba(255,255,255,0.7); }

    .band-ready        { color: #22c55e; }
    .band-near_ready   { color: #f59e0b; }
    .band-significant_gaps { color: #ef4444; }
    .band-not_ready    { color: #fca5a5; }
    .band-critical_risk    { color: #fca5a5; }
    .band-cyber_resilient  { color: #22c55e; }

    .cover-footer {
      padding: 6mm var(--gutter);
      border-top: 1px solid rgba(255,255,255,0.1);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .cover-footer-center {
      font-family: var(--font-mono);
      font-size: 7pt;
      color: rgba(255,255,255,0.3);
      letter-spacing: 0.5px;
    }

    .cover-footer-right {
      font-family: var(--font-mono);
      font-size: 7pt;
      color: rgba(255,255,255,0.3);
      letter-spacing: 0.5px;
    }

    /* ── Summary page ── */
    .page-header-bar {
      background: var(--hero-bg);
      padding: 5mm var(--gutter) 4mm;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8mm;
    }

    .page-header-bar h1 {
      font-family: var(--font-heading);
      font-weight: 700;
      font-size: 13pt;
      color: var(--white);
    }

    .page-header-right { text-align: right; }

    .page-header-tenant {
      font-family: var(--font-body);
      font-size: 8pt;
      color: rgba(255,255,255,0.7);
      display: block;
    }

    .page-header-date {
      font-family: var(--font-mono);
      font-size: 7.5pt;
      color: var(--cta);
      display: block;
    }

    .page-inner { padding: 0 var(--gutter) 10mm; }

    .page-footer {
      position: absolute;
      bottom: 0;
      left: 0;
      right: 0;
      padding: 3mm var(--gutter);
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: var(--white);
    }

    .page-footer-left {
      font-family: var(--font-body);
      font-size: 7pt;
      color: var(--text-muted);
    }

    .page-footer-right {
      font-family: var(--font-mono);
      font-size: 7pt;
      color: var(--text-muted);
    }

    .section-title {
      font-family: var(--font-heading);
      font-weight: 700;
      font-size: 11pt;
      color: var(--hero-bg);
      margin-bottom: 4mm;
      padding-bottom: 2mm;
      border-bottom: 2px solid var(--cta);
    }

    /* Metric strip */
    .metric-strip {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 3mm;
      margin: 0 0 7mm;
    }

    .metric-card {
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 3.5mm;
      background: var(--white);
    }

    .metric-label {
      display: block;
      font-size: 6.8pt;
      font-weight: 700;
      letter-spacing: 0.7px;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-bottom: 1.5mm;
    }

    .metric-value {
      display: block;
      font-family: var(--font-heading);
      font-size: 17pt;
      font-weight: 700;
      color: var(--hero-bg);
      line-height: 1;
    }

    .metric-value.positive { color: #15803d; }
    .metric-value.negative { color: #dc2626; }

    .metric-sub {
      display: block;
      font-size: 7.3pt;
      color: var(--text-muted);
      margin-top: 1.5mm;
      line-height: 1.35;
    }

    /* Regression / stable callout */
    .alert-callout {
      border-radius: 0 4px 4px 0;
      padding: 4mm 5mm;
      margin-bottom: 7mm;
    }

    .alert-callout.regression {
      background: var(--fail-bg);
      border-left: 4px solid var(--fail-text);
    }

    .alert-callout.stable {
      background: var(--pass-bg);
      border-left: 4px solid var(--pass-text);
    }

    .alert-callout-title {
      font-family: var(--font-heading);
      font-weight: 700;
      font-size: 10pt;
      margin-bottom: 1.5mm;
    }

    .alert-callout.regression .alert-callout-title { color: var(--fail-text); }
    .alert-callout.stable .alert-callout-title     { color: var(--pass-text); }

    .alert-callout-text {
      font-family: var(--font-body);
      font-size: 8.5pt;
      color: var(--text-primary);
      line-height: 1.55;
    }

    /* Control cards */
    .control-cards {
      display: flex;
      flex-direction: column;
      gap: 3mm;
    }

    .control-card {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--white);
      overflow: hidden;
    }

    .control-card-header {
      background: var(--light-grey);
      border-bottom: 1px solid var(--border);
      padding: 3mm 3.5mm;
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 4mm;
    }

    .control-rank {
      font-family: var(--font-heading);
      font-weight: 700;
      font-size: 11pt;
      color: var(--cta);
      flex-shrink: 0;
      min-width: 6mm;
    }

    .control-title {
      font-family: var(--font-heading);
      font-size: 9pt;
      font-weight: 700;
      color: var(--hero-bg);
      flex: 1;
      line-height: 1.3;
    }

    .control-badges {
      display: flex;
      gap: 1.5mm;
      align-items: flex-start;
      flex-shrink: 0;
    }

    .control-card-body {
      padding: 3mm 3.5mm;
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 3mm;
    }

    .control-block-title {
      font-family: var(--font-heading);
      font-size: 7pt;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.6px;
      margin-bottom: 1mm;
    }

    .control-block-text {
      font-size: 7.5pt;
      line-height: 1.45;
      color: var(--text-primary);
    }

    .badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 3px;
      font-family: var(--font-heading);
      font-size: 7pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      white-space: nowrap;
    }

    .badge-critical { background: var(--sev-critical-bg); color: var(--sev-critical-text); }
    .badge-high     { background: var(--sev-high-bg);     color: var(--sev-high-text); }
    .badge-medium   { background: var(--sev-medium-bg);   color: var(--sev-medium-text); }
    .badge-low      { background: var(--sev-low-bg);      color: var(--sev-low-text); }
    .badge-info     { background: var(--sev-info-bg);     color: var(--sev-info-text); }

    .badge-category {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 3px;
      font-family: var(--font-body);
      font-size: 7pt;
      font-weight: 700;
      background: rgba(27,77,98,0.1);
      color: var(--hero-bg);
      border: 1px solid rgba(27,77,98,0.2);
      white-space: nowrap;
    }

    .no-controls-note {
      background: var(--light-grey);
      border-left: 4px solid var(--cta);
      border-radius: 0 4px 4px 0;
      padding: 4mm 5mm;
      font-size: 8.5pt;
      color: var(--text-muted);
      line-height: 1.55;
    }

    @media print {
      .page { page-break-before: always; page-break-inside: avoid; }
      .page:first-child { page-break-before: auto; }
    }

    @media screen {
      body { background: #888; padding: 10mm; }
      .page { display: block; margin: 0 auto 10mm; box-shadow: 0 4px 24px rgba(0,0,0,0.35); }
    }
  </style>
</head>
<body>

  <!-- PAGE 1 — COVER -->
  <section class="page cover" id="cover">
    <div class="cover-header">
      <div class="cover-logo-wordmark">
        <span class="cover-logo-intuity">intuity</span>
        <span class="cover-logo-tech">Technologies</span>
      </div>
      <span class="cover-confidential-badge">Confidential</span>
    </div>

    <div class="cover-stripe"></div>

    <div class="cover-body">
      <div class="cover-eyebrow">Microsoft Secure Score</div>
      <div class="cover-title">Weekly Secure Score<br />Update</div>
      <div class="cover-subtitle">{{ tenant_name }}</div>

      <div class="cover-meta">
        <div class="cover-meta-row">
          <span class="cover-meta-label">Tenant ID</span>
          <span class="cover-meta-value" style="font-family: var(--font-mono); font-size: 8.5pt">{{ tenant_id if tenant_id else 'N/A' }}</span>
        </div>
        <div class="cover-meta-row">
          <span class="cover-meta-label">Generated</span>
          <span class="cover-meta-value">{{ generated_date }}</span>
        </div>
        <div class="cover-meta-row">
          <span class="cover-meta-label">Week of</span>
          <span class="cover-meta-value">{{ week_of_label }}</span>
        </div>
        <div class="cover-meta-row">
          <span class="cover-meta-label">Prepared by</span>
          <span class="cover-meta-value">Intuity Technologies</span>
        </div>
      </div>

      <div class="cover-score-area">
        <div class="score-gauge">
          <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
            <circle class="score-gauge-track" cx="50" cy="50" r="42" />
            <circle class="score-gauge-fill" cx="50" cy="50" r="42"
              stroke="{{ gauge_color }}"
              stroke-dasharray="263.9"
              stroke-dashoffset="{{ gauge_dashoffset }}" />
          </svg>
          <div class="score-gauge-inner">
            <span class="score-number">{{ score_current_pct }}</span>
            <span class="score-denom">/ 100</span>
          </div>
        </div>

        <div class="score-band-info">
          <span class="score-band-label band-{{ band_css_class }}">{{ score_band }}</span>
          <span class="score-band-desc">{{ band_description }}</span>
          <div class="delta-display {{ 'delta-positive' if score_movement >= 0 else 'delta-negative' }}">
            {{ '+' if score_movement >= 0 else '' }}{{ score_movement }}pp
            <span class="delta-label">vs last week</span>
          </div>
        </div>
      </div>
    </div>

    <div class="cover-footer">
      <span class="cover-footer-center">
        This report is confidential and intended solely for the named recipient organisation.
        Distribution or reproduction requires written consent from Intuity Technologies.
      </span>
      <span class="cover-footer-right">intuity.ie</span>
    </div>
  </section>

  <!-- PAGE 2 — WEEKLY SUMMARY -->
  <section class="page" id="weekly-summary">
    <div class="page-header-bar">
      <h1>Weekly Summary — Week of {{ week_of_label }}</h1>
      <div class="page-header-right">
        <span class="page-header-tenant">{{ tenant_name }}</span>
        <span class="page-header-date">{{ generated_iso }}</span>
      </div>
    </div>

    <div class="page-inner">

      <!-- Section 1: Score Snapshot -->
      <div class="section-title">Score Snapshot</div>
      <div class="metric-strip">
        <div class="metric-card">
          <span class="metric-label">Current Score</span>
          <span class="metric-value">{{ score_current_pct }}%</span>
          <span class="metric-sub">{{ current_score_pts }} / {{ max_score_pts }} pts</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">Last Week</span>
          <span class="metric-value">{{ score_last_pct }}%</span>
          <span class="metric-sub">previous snapshot</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">Week Movement</span>
          <span class="metric-value {{ 'positive' if score_movement >= 0 else 'negative' }}">
            {{ '+' if score_movement >= 0 else '' }}{{ score_movement }}pp
          </span>
          <span class="metric-sub">percentage points</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">vs All Tenants</span>
          <span class="metric-value">
            {% if avg_score_all_tenants is not none %}
              {{ avg_score_all_tenants }}%
            {% else %}
              N/A
            {% endif %}
          </span>
          <span class="metric-sub">Microsoft benchmark avg</span>
        </div>
      </div>

      <!-- Section 2: Regression Alert -->
      <div class="section-title">Weekly Assessment</div>
      {% if regression_mode %}
      <div class="alert-callout regression">
        <div class="alert-callout-title">Score Regression This Week</div>
        <div class="alert-callout-text">
          The Secure Score dropped by <strong>{{ score_movement }}pp</strong> this week
          ({{ score_last_pct }}% &#8594; {{ score_current_pct }}%).
          Review recently changed or expired controls in the
          <a href="https://security.microsoft.com/securescore" style="color: var(--fail-text);">Microsoft 365 Security Centre</a>
          to identify the source of the regression and restore affected controls.
        </div>
      </div>
      {% else %}
      <div class="alert-callout stable">
        <div class="alert-callout-title">Score Stable / Improving</div>
        <div class="alert-callout-text">
          The Secure Score
          {% if score_movement > 0 %}
            increased by <strong>+{{ score_movement }}pp</strong> this week
            ({{ score_last_pct }}% &#8594; {{ score_current_pct }}%).
            Continue progressing the open controls listed below to maintain momentum.
          {% else %}
            held steady at <strong>{{ score_current_pct }}%</strong> this week.
            No regression was detected. Continue working through the open controls below.
          {% endif %}
        </div>
      </div>
      {% endif %}

      <!-- Section 3: Top 3 Controls to Action -->
      <div class="section-title">Top 3 Controls to Action</div>
      {% if controls %}
      <div class="control-cards">
        {% for ctrl in controls %}
        <div class="control-card">
          <div class="control-card-header">
            <span class="control-rank">{{ ctrl.rank if ctrl.rank else loop.index }}.</span>
            <span class="control-title">{{ ctrl.title }}</span>
            <div class="control-badges">
              <span class="badge badge-{{ ctrl.severity_css }}">{{ ctrl.severity_label }}</span>
              <span class="badge-category">{{ ctrl.controlCategory }}</span>
            </div>
          </div>
          <div class="control-card-body">
            <div>
              <div class="control-block-title">Points Available</div>
              <div class="control-block-text" style="font-size: 11pt; font-weight: 700; color: var(--hero-bg);">{{ ctrl.maxScore | int }}</div>
            </div>
            <div>
              <div class="control-block-title">Action</div>
              <div class="control-block-text">{{ ctrl.remediation if ctrl.remediation else 'See Microsoft documentation.' }}</div>
            </div>
            <div>
              <div class="control-block-title">Portal Link</div>
              <div class="control-block-text">
                {% if ctrl.actionUrl %}
                <a href="{{ ctrl.actionUrl }}" style="color: var(--cta);">Open in Microsoft portal &#8594;</a>
                {% else %}
                Review in Microsoft 365 Security Centre.
                {% endif %}
              </div>
            </div>
          </div>
        </div>
        {% endfor %}
      </div>
      {% else %}
      <div class="no-controls-note">
        No control profile data was provided for this report.
        Review open improvement actions directly in the
        <a href="https://security.microsoft.com/securescore" style="color: var(--cta);">Microsoft 365 Security Centre</a>
        to identify the highest-priority controls to action this week.
      </div>
      {% endif %}

    </div>

    <div class="page-footer">
      <span class="page-footer-left">
        Intuity Technologies &#8212; Microsoft Secure Score Weekly Update &#8212; {{ tenant_name }}
      </span>
      <span class="page-footer-right">CONFIDENTIAL &#183; Page 2</span>
    </div>
  </section>

</body>
</html>
```

- [ ] **Step 2: Verify the file exists**

```
dir secure_score_graph\weekly_template.html
```

Expected: file listed with non-zero size.

---

## Task 5: Implement `render_weekly_html_report` in `__init__.py`

**Files:**
- Modify: `secure_score_graph/__init__.py`

- [ ] **Step 1: Add `render_weekly_html_report` after `render_html_report` (around line 846)**

```python
def render_weekly_html_report(json_data, control_profiles):
    """Render the 2-page weekly Jinja2 template."""
    weekly_stats = compute_weekly_stats(json_data)

    sorted_controls = sorted(
        control_profiles or [],
        key=lambda c: c.get('rank') or 9999,
    )
    top3 = []
    for cp in sorted_controls[:3]:
        sev_css, sev_label = _derive_control_severity(cp)
        top3.append({**cp, 'severity_css': sev_css, 'severity_label': sev_label})

    context = {
        **weekly_stats,
        'controls':        top3,
        'regression_mode': weekly_stats['score_movement'] < 0,
    }

    template_dir = os.path.dirname(os.path.abspath(__file__))
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=False,
    )
    template = env.get_template('weekly_template.html')
    return template.render(**context)
```

- [ ] **Step 2: Run the render tests**

```
pytest tests/test_weekly.py -v -k "render_weekly"
```

Expected: all `render_weekly_*` tests PASS.

- [ ] **Step 3: Run the full test suite to confirm nothing regressed**

```
pytest tests/test_weekly.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add secure_score_graph/__init__.py secure_score_graph/weekly_template.html
git commit -m "feat: add weekly HTML template and render_weekly_html_report"
```

---

## Task 6: Wire routing into `main()`

**Files:**
- Modify: `secure_score_graph/__init__.py` — `main()` function

- [ ] **Step 1: Locate the existing routing block in `main()`**

Find this section (around line 980):

```python
        trend_analysis = analyze_security_trends(scores, dates)
        trend_stats    = build_trend_stats(json_data, trend_analysis)
        html_content   = render_html_report(json_data, trend_analysis, control_profiles)
        pdf_bytes      = generate_pdf_with_playwright(html_content)
```

- [ ] **Step 2: Replace with the branching routing**

```python
        report_type = json_data.get('report_type', 'quarterly')

        if report_type == 'weekly':
            html_content = render_weekly_html_report(json_data, control_profiles)
            trend_stats  = {}
        else:
            trend_analysis = analyze_security_trends(scores, dates)
            trend_stats    = build_trend_stats(json_data, trend_analysis)
            html_content   = render_html_report(json_data, trend_analysis, control_profiles)

        pdf_bytes = generate_pdf_with_playwright(html_content)
```

- [ ] **Step 3: Write a routing test — append to `tests/test_weekly.py`**

```python
from unittest.mock import patch, MagicMock
import importlib
import secure_score_graph as ssg


def test_main_routes_to_weekly_renderer():
    json_payload = {
        'tenant_name': 'Contoso',
        'report_type': 'weekly',
        'result': [
            {'createdDateTime': '2024-01-14T00:00:00Z', 'currentScore': 300, 'maxScore': 445, 'averageComparativeScores': [], 'azureTenantId': 't1'},
            {'createdDateTime': '2024-01-21T00:00:00Z', 'currentScore': 312, 'maxScore': 445, 'averageComparativeScores': [], 'azureTenantId': 't1'},
        ],
        'control_profiles': [],
    }

    mock_req = MagicMock()
    mock_req.method = 'POST'
    mock_req.get_json.return_value = json_payload

    with patch.object(ssg, 'generate_pdf_with_playwright', return_value=b'%PDF-fake') as mock_pdf:
        with patch.object(ssg, 'render_weekly_html_report', wraps=ssg.render_weekly_html_report) as mock_weekly:
            with patch.object(ssg, 'render_html_report', wraps=ssg.render_html_report) as mock_quarterly:
                response = ssg.main(mock_req)
                mock_weekly.assert_called_once()
                mock_quarterly.assert_not_called()


def test_main_routes_to_quarterly_renderer_by_default():
    json_payload = {
        'tenant_name': 'Contoso',
        'result': [
            {'createdDateTime': '2024-01-14T00:00:00Z', 'currentScore': 300, 'maxScore': 445, 'averageComparativeScores': [], 'azureTenantId': 't1'},
            {'createdDateTime': '2024-01-21T00:00:00Z', 'currentScore': 312, 'maxScore': 445, 'averageComparativeScores': [], 'azureTenantId': 't1'},
        ],
        'control_profiles': [],
    }

    mock_req = MagicMock()
    mock_req.method = 'POST'
    mock_req.get_json.return_value = json_payload

    with patch.object(ssg, 'generate_pdf_with_playwright', return_value=b'%PDF-fake'):
        with patch.object(ssg, 'render_weekly_html_report', wraps=ssg.render_weekly_html_report) as mock_weekly:
            with patch.object(ssg, 'render_html_report', wraps=ssg.render_html_report) as mock_quarterly:
                response = ssg.main(mock_req)
                mock_quarterly.assert_called_once()
                mock_weekly.assert_not_called()
```

- [ ] **Step 4: Run all tests**

```
pytest tests/test_weekly.py -v
```

Expected: all tests PASS including the two new routing tests.

- [ ] **Step 5: Commit**

```bash
git add secure_score_graph/__init__.py tests/test_weekly.py
git commit -m "feat: route report_type=weekly to weekly renderer in main()"
```

---

## Task 7: Manual smoke test

**Files:** none — verification only.

- [ ] **Step 1: Render the weekly HTML locally to visually verify the output**

Create a temporary script `smoke_test_weekly.py` at the repo root:

```python
import json
from datetime import datetime
from secure_score_graph import render_weekly_html_report

json_data = {
    'tenant_name': 'Contoso Ltd',
    'report_type': 'weekly',
    'result': [
        {
            'createdDateTime': '2024-01-14T00:00:00Z',
            'currentScore': 312,
            'maxScore': 445,
            'averageComparativeScores': [{'basis': 'AllTenants', 'averageScore': 62.3}],
            'azureTenantId': 'aaaabbbb-1234-5678-abcd-111122223333',
        },
        {
            'createdDateTime': '2024-01-21T00:00:00Z',
            'currentScore': 305,
            'maxScore': 445,
            'averageComparativeScores': [{'basis': 'AllTenants', 'averageScore': 62.3}],
            'azureTenantId': 'aaaabbbb-1234-5678-abcd-111122223333',
        },
    ],
    'control_profiles': [
        {
            'rank': 1, 'title': 'Require MFA for all users', 'controlCategory': 'Identity',
            'maxScore': 9, 'implementationCost': 'low', 'userImpact': 'moderate',
            'threats': ['accountBreach', 'phishingOrWhaling'],
            'remediation': 'Create a Conditional Access policy requiring MFA for all users.',
            'remediationImpact': 'Users will be prompted for MFA on first sign-in after policy activation.',
            'actionUrl': 'https://portal.azure.com/#blade/Microsoft_AAD_IAM/ConditionalAccessBlade',
        },
        {
            'rank': 2, 'title': 'Block legacy authentication protocols', 'controlCategory': 'Identity',
            'maxScore': 8, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': ['accountBreach'],
            'remediation': 'Create a Conditional Access policy to block all legacy auth protocols.',
            'remediationImpact': 'Clients without modern auth support will be blocked.',
            'actionUrl': 'https://portal.azure.com/#blade/Microsoft_AAD_IAM/ConditionalAccessBlade',
        },
        {
            'rank': 3, 'title': 'Enable Self-Service Password Reset', 'controlCategory': 'Identity',
            'maxScore': 4, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': ['accountBreach'],
            'remediation': 'Enable SSPR for all users in Azure AD Password Reset settings.',
            'remediationImpact': 'Users can reset passwords without contacting the helpdesk.',
            'actionUrl': 'https://portal.azure.com/#blade/Microsoft_AAD_IAM/PasswordResetMenuBlade',
        },
    ],
}

html = render_weekly_html_report(json_data, json_data['control_profiles'])
with open('smoke_weekly.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Written: smoke_weekly.html — open in a browser to review.')
```

- [ ] **Step 2: Run it**

```
python smoke_test_weekly.py
```

Expected: `Written: smoke_weekly.html` — no errors.

- [ ] **Step 3: Open `smoke_weekly.html` in a browser and verify**

Check:
- Cover page: gauge ring visible, delta shows negative (305 < 312 → regression), band label present
- Page 2: 4 metric cards correct values, regression callout visible (red border), 3 control cards rendered with title/severity/action

- [ ] **Step 4: Delete the smoke test files and commit**

```bash
del smoke_test_weekly.py smoke_weekly.html
git add -A
git commit -m "test: verify weekly template renders correctly via smoke test"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `report_type: "weekly"` routing | Task 6 |
| Use last two entries from `result` | Task 2 (`compute_weekly_stats` sorts + slices `[-2:]`) |
| Pass 90-entry `result` safely | Task 2 + test `test_sorts_result_and_takes_last_two` |
| Cover page with gauge, delta, band | Task 4 (template) |
| 4 metric cards (current, last, movement, vs all tenants) | Task 4 |
| Regression alert (red) when movement < 0 | Task 4 + test `test_render_weekly_regression_mode_when_score_dropped` |
| Stable/improving callout when movement ≥ 0 | Task 4 + test `test_render_weekly_stable_mode_when_score_rose` |
| Top 3 controls by rank | Task 5 + test `test_render_weekly_only_top_3_controls_shown` |
| No-controls placeholder | Task 4 + test `test_render_weekly_no_controls_shows_placeholder` |
| `control_profiles` optional | Covered — empty list handled in Task 5 |
| Quarterly path untouched | Task 6 routing + test `test_main_routes_to_quarterly_renderer_by_default` |
| Shared PDF rendering path | Task 6 — `generate_pdf_with_playwright` called after branch |

**Placeholder scan:** No TBDs or "implement later" text found.

**Type consistency:** `compute_weekly_stats` returns `score_movement` as a float; template uses `score_movement >= 0` and `score_movement < 0` — consistent. `regression_mode` bool set in `render_weekly_html_report` and used in template `{% if regression_mode %}` — consistent. `_derive_control_severity` returns `(sev_css, sev_label)` tuple — used correctly in Tasks 3 and 5.
