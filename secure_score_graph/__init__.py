import base64
from collections import defaultdict
from datetime import datetime, timedelta
from html.parser import HTMLParser
import html as html_module
import io
import json
import logging
import math
import re

import azure.functions as func
import numpy as np
from scipy import stats
from scipy.signal import savgol_filter
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from sklearn.metrics import r2_score, mean_squared_error

# ---------------------------------------------------------------------------
# HTML stripping utility
# ---------------------------------------------------------------------------

def _strip_html(text):
    class _S(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, d):
            self.parts.append(d)
    s = _S()
    s.feed(text or '')
    return html_module.unescape(' '.join(s.parts).strip())


# ---------------------------------------------------------------------------
# Trend analysis (unchanged)
# ---------------------------------------------------------------------------

def analyze_security_trends(scores, dates):
    x = np.array([date.timestamp() for date in dates])
    y = np.array(scores)
    x_norm = (x - x.min()) / (x.max() - x.min()) if x.max() != x.min() else x

    results = {}

    lr = stats.linregress(x_norm, y)
    results['linear'] = {
        'slope':      float(lr.slope),
        'intercept':  float(lr.intercept),
        'r_squared':  float(lr.rvalue) ** 2,
        'p_value':    float(lr.pvalue),
        'std_error':  float(lr.stderr),
        'trend_line': float(lr.slope) * x_norm + float(lr.intercept),
    }

    poly = PolynomialFeatures(degree=2)
    x_poly = poly.fit_transform(x_norm.reshape(-1, 1))
    pm = LinearRegression().fit(x_poly, y)
    y_poly = pm.predict(x_poly)
    results['polynomial'] = {
        'coefficients': pm.coef_,
        'r_squared': r2_score(y, y_poly),
        'mse': mean_squared_error(y, y_poly),
        'trend_line': y_poly,
    }

    w = max(1, min(7, len(scores) // 4))
    if w > 1:
        ma = np.convolve(scores, np.ones(w) / w, mode='valid')
        pad = len(scores) - len(ma)
        ma = np.concatenate([scores[:pad], ma])
    else:
        ma = np.array(scores, dtype=float)
    results['moving_average'] = {'window_size': w, 'trend_line': ma}

    if len(scores) > 5:
        wl = min(5, len(scores) - 2)
        if wl % 2 == 0:
            wl -= 1
        sg = savgol_filter(scores, wl, 2) if wl >= 3 else np.array(scores, dtype=float)
    else:
        sg = np.array(scores, dtype=float)
    results['savgol'] = {'trend_line': sg}

    if len(scores) > 10:
        h1, h2 = scores[:len(scores)//2], scores[len(scores)//2:]
        vf = np.var(h1)
        results['statistics'] = {
            'variance_ratio': np.var(h2) / vf if vf > 0 else 1.0,
            'trend_strength': abs(float(lr.slope)) / (float(np.std(scores)) + 1e-8),
            'is_stationary': (np.var(h2) / vf if vf > 0 else 1.0) < 2.0,
            'volatility': float(np.std(scores)),
            'skewness': float(stats.skew(scores)),
            'kurtosis': float(stats.kurtosis(scores)),
        }

    if len(scores) >= 7:
        dow = [d.weekday() for d in dates]
        wp = {}
        for day in range(7):
            ds = [scores[i] for i, d in enumerate(dow) if d == day]
            if ds:
                wp[day] = {'mean': np.mean(ds), 'std': np.std(ds), 'count': len(ds)}
        results['seasonality'] = wp

    changes = [scores[i] - scores[i-1] for i in range(1, len(scores))]
    if changes:
        thr = np.std(changes) * 1.5
        results['change_points'] = {
            'significant_changes': [(i+1, c) for i, c in enumerate(changes) if abs(c) > thr],
            'change_threshold': thr,
            'total_changes': len(changes),
            'positive_changes': sum(1 for c in changes if c > 0),
            'negative_changes': sum(1 for c in changes if c < 0),
        }

    return results


# ---------------------------------------------------------------------------
# Trend stats summary
# ---------------------------------------------------------------------------

def build_trend_stats(json_data, trend_analysis):
    data = json_data.get('result', [])
    if not data:
        return {}

    scores = [(item['currentScore'] / item['maxScore']) * 100 for item in data]
    dates  = [item['date'] for item in data]

    linear      = trend_analysis.get('linear', {})
    stats_block = trend_analysis.get('statistics', {})
    cp          = trend_analysis.get('change_points', {})

    score_start = round(float(scores[0]), 2)
    score_end   = round(float(scores[-1]), 2)

    if score_end >= 80:
        score_band = 'Cyber Resilient'
    elif score_end >= 65:
        score_band = 'Near Ready'
    elif score_end >= 45:
        score_band = 'Significant Gaps'
    else:
        score_band = 'Critical Risk'

    total_changes    = int(cp.get('total_changes', 0))
    positive_changes = int(cp.get('positive_changes', 0))
    negative_changes = int(cp.get('negative_changes', 0))

    recent    = data[-1]
    avg_all   = None
    avg_seats = None
    for entry in (recent.get('averageComparativeScores') or []):
        basis = entry.get('basis')
        if basis == 'AllTenants':
            avg_all = float(entry['averageScore'])
        elif basis == 'TotalSeats':
            avg_seats = float(entry['averageScore'])

    return {
        'period_start':            dates[0].strftime('%d %b %Y'),
        'period_end':              dates[-1].strftime('%d %b %Y'),
        'score_start_pct':         score_start,
        'score_end_pct':           score_end,
        'score_movement':          round(score_end - score_start, 2),
        'score_band':              score_band,
        'current_score_pts':       float(recent.get('currentScore', score_end)),
        'max_score_pts':           float(recent.get('maxScore', 100)),
        'tenant_id':               recent.get('azureTenantId') or json_data.get('tenant_id', ''),
        'avg_score':               round(float(np.mean(scores)), 2),
        'median_score':            round(float(np.median(scores)), 2),
        'best_score':              round(float(max(scores)), 2),
        'worst_score':             round(float(min(scores)), 2),
        'volatility':              round(float(stats_block.get('volatility', np.std(scores))), 2),
        'data_points':             len(scores),
        'r_squared':               round(float(linear.get('r_squared', 0)), 3),
        'p_value':                 round(float(linear.get('p_value', 1)), 4),
        'trend_direction':         'improving' if linear.get('slope', 0) > 0 else 'declining',
        'positive_days_count':     positive_changes,
        'positive_days_pct':       round(positive_changes / total_changes * 100, 1) if total_changes else 0.0,
        'total_daily_changes':     total_changes,
        'negative_days_count':     negative_changes,
        'significant_events':      int(len(cp.get('significant_changes', []))),
        'avg_score_all_tenants':   avg_all,
        'avg_score_similar_seats': avg_seats,
    }


# ---------------------------------------------------------------------------
# HTML report builder
# ---------------------------------------------------------------------------

THREAT_LABELS = {
    'accountBreach': 'Account breach',
    'dataDeletion': 'Data deletion',
    'dataExfiltration': 'Data exfiltration',
    'dataSpillage': 'Data spillage',
    'elevationOfPrivilege': 'Privilege escalation',
    'maliciousInsider': 'Malicious insider',
    'passwordCracking': 'Password cracking',
    'phishingOrWhaling': 'Phishing',
    'spoofing': 'Spoofing',
}

CSS = """
@import url("https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Lato:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap");

:root {
  --hero-bg: #1b4d62;
  --cta: #3abfcf;
  --brand-pink: #e4007c;
  --white: #ffffff;
  --light-grey: #f2f4f5;
  --border: #e5e7eb;
  --text-primary: #2d2d2d;
  --text-muted: #6b7280;
  --pass-bg: #dcfce7; --pass-text: #15803d; --pass-border: #86efac;
  --fail-bg: #fee2e2; --fail-text: #dc2626; --fail-border: #fca5a5;
  --skip-bg: #f3f4f6; --skip-text: #6b7280; --skip-border: #d1d5db;
  --sev-critical-bg: #7f1d1d; --sev-critical-text: #ffffff;
  --sev-high-bg: #f97316;    --sev-high-text: #ffffff;
  --sev-medium-bg: #f59e0b;  --sev-medium-text: #1a1a1a;
  --sev-low-bg: #3b82f6;     --sev-low-text: #ffffff;
  --sev-info-bg: #e5e7eb;    --sev-info-text: #374151;
  --font-heading: "Poppins", Arial, sans-serif;
  --font-body: "Lato", Arial, sans-serif;
  --font-mono: "JetBrains Mono", Consolas, monospace;
  --page-w: 210mm; --page-h: 297mm; --gutter: 14mm;
}
@page { size: A4; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0;
    -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
html, body { font-family: var(--font-body); color: var(--text-primary);
             background: var(--white); font-size: 10pt; line-height: 1.55; }
.page { width: var(--page-w); min-height: var(--page-h); position: relative;
        overflow: hidden; background: var(--white);
        page-break-before: always; page-break-inside: avoid; }
.page:first-child { page-break-before: auto; }
/* Cover */
.cover { background: var(--hero-bg); display: flex; flex-direction: column;
         width: 100%; height: var(--page-h); color: var(--white); }
.cover-header { padding: 12mm var(--gutter) 8mm; border-bottom: 1px solid rgba(255,255,255,.12);
                display: flex; align-items: center; justify-content: space-between; }
.cover-logo-intuity { font-family: var(--font-heading); font-weight: 700;
                      font-size: 20pt; color: var(--brand-pink); letter-spacing: -.5px; }
.cover-logo-tech { font-family: var(--font-heading); font-weight: 600; font-size: 8pt;
                   color: rgba(255,255,255,.7); letter-spacing: 3px; text-transform: uppercase;
                   margin-left: 4px; align-self: center; }
.cover-confidential-badge { font-size: 7pt; color: rgba(255,255,255,.5); letter-spacing: 1.5px;
                            text-transform: uppercase; border: 1px solid rgba(255,255,255,.25);
                            padding: 3px 8px; border-radius: 3px; }
.cover-stripe { height: 3px; background: linear-gradient(90deg,var(--cta) 0%,var(--brand-pink) 100%); }
.cover-body { flex: 1; display: flex; flex-direction: column; justify-content: center; padding: 0 var(--gutter); }
.cover-eyebrow { font-size: 8pt; font-weight: 700; letter-spacing: 3px; text-transform: uppercase;
                 color: var(--cta); margin-bottom: 6mm; }
.cover-title { font-family: var(--font-heading); font-weight: 700; font-size: 30pt;
               line-height: 1.15; color: var(--white); margin-bottom: 3mm; }
.cover-subtitle { font-family: var(--font-heading); font-weight: 600; font-size: 16pt;
                  color: var(--cta); margin-bottom: 10mm; }
.cover-meta { display: flex; flex-direction: column; gap: 2mm; margin-bottom: 12mm; }
.cover-meta-row { display: flex; align-items: baseline; gap: 6px; }
.cover-meta-label { font-size: 8pt; color: rgba(255,255,255,.5); text-transform: uppercase;
                    letter-spacing: 1px; width: 30mm; flex-shrink: 0; }
.cover-meta-value { font-size: 10pt; color: rgba(255,255,255,.9); font-weight: 500; }
.cover-score-area { display: flex; align-items: center; gap: 10mm; margin-top: 4mm; }
.score-gauge { position: relative; width: 44mm; height: 44mm; flex-shrink: 0; }
.score-gauge svg { width: 100%; height: 100%; transform: rotate(-90deg); }
.score-gauge-track { fill: none; stroke: rgba(255,255,255,.1); stroke-width: 8; }
.score-gauge-fill { fill: none; stroke-width: 8; stroke-linecap: round; }
.score-gauge-inner { position: absolute; inset: 0; display: flex; flex-direction: column;
                     align-items: center; justify-content: center; }
.score-number { font-family: var(--font-heading); font-size: 20pt; font-weight: 700;
                color: var(--white); line-height: 1; }
.score-denom { font-size: 7pt; color: rgba(255,255,255,.5); }
.score-band-info { display: flex; flex-direction: column; gap: 2mm; }
.score-band-label { font-family: var(--font-heading); font-weight: 600; font-size: 14pt; }
.score-band-desc { font-size: 9pt; color: rgba(255,255,255,.65); max-width: 80mm; line-height: 1.45; }
.band-ready { color: #22c55e; }
.band-near_ready { color: #f59e0b; }
.band-significant_gaps { color: #ef4444; }
.band-not_ready { color: #fca5a5; }
.cover-footer { padding: 6mm var(--gutter); border-top: 1px solid rgba(255,255,255,.1);
                display: flex; align-items: center; justify-content: space-between; }
.cover-footer-center { font-size: 7.5pt; color: rgba(255,255,255,.4); }
.cover-footer-right { font-family: var(--font-mono); font-size: 7pt; color: rgba(255,255,255,.3); }
/* Shared page chrome */
.page-inner { padding: 0 var(--gutter) 14mm; }
.page-header-bar { background: var(--hero-bg); padding: 5mm var(--gutter) 4mm;
                   display: flex; align-items: center; justify-content: space-between; margin-bottom: 8mm; }
.page-header-bar h1 { font-family: var(--font-heading); font-weight: 700; font-size: 13pt;
                      color: var(--white); line-height: 1.2; }
.page-header-right { text-align: right; }
.page-header-tenant { font-size: 8pt; color: rgba(255,255,255,.7); display: block; }
.page-header-date { font-family: var(--font-mono); font-size: 7.5pt; color: var(--cta); display: block; }
.page-footer { position: absolute; bottom: 0; left: 0; right: 0; padding: 3mm var(--gutter);
               border-top: 1px solid var(--border); display: flex; align-items: center;
               justify-content: space-between; background: var(--white); }
.page-footer-left { font-size: 7pt; color: var(--text-muted); }
.page-footer-right { font-family: var(--font-mono); font-size: 7pt; color: var(--text-muted); }
/* Executive summary */
.summary-lede { font-size: 10.2pt; line-height: 1.65; color: var(--text-primary); margin-bottom: 5mm; }
.summary-lede strong { color: var(--hero-bg); }
.metric-strip { display: grid; grid-template-columns: repeat(4,1fr); gap: 3mm; margin: 5mm 0 6mm; }
.metric-card { border: 1px solid var(--border); border-radius: 5px; padding: 3.5mm; background: var(--white); }
.metric-label { display: block; font-size: 6.8pt; font-weight: 700; letter-spacing: .7px;
                text-transform: uppercase; color: var(--text-muted); margin-bottom: 1.5mm; }
.metric-value { display: block; font-family: var(--font-heading); font-size: 17pt;
                font-weight: 700; color: var(--hero-bg); line-height: 1; }
.metric-value.good { color: var(--pass-text); }
.metric-value.warn { color: #f59e0b; }
.metric-sub { display: block; font-size: 7.3pt; color: var(--text-muted); margin-top: 1.5mm; }
.summary-grid { display: grid; grid-template-columns: 1.1fr .9fr; gap: 5mm; margin-top: 5mm; }
.summary-panel { border: 1px solid var(--border); border-radius: 5px; padding: 4mm; background: var(--white); }
.summary-panel-title { font-family: var(--font-heading); font-size: 9pt; font-weight: 700;
                       color: var(--hero-bg); margin-bottom: 2.5mm; }
.summary-list { list-style: none; display: flex; flex-direction: column; gap: 2.3mm; }
.summary-list li { font-size: 8.5pt; line-height: 1.45; color: var(--text-primary);
                   padding-left: 4mm; position: relative; }
.summary-list li::before { content: ""; width: 1.7mm; height: 1.7mm; border-radius: 50%;
                            background: var(--cta); position: absolute; left: 0; top: 1.7mm; }
.mini-table td:first-child { font-weight: 700; color: var(--hero-bg); width: 48%; }
.band-callout { background: var(--light-grey); border-left: 4px solid var(--cta);
                border-radius: 0 4px 4px 0; padding: 4mm 5mm; margin-top: 5mm; }
.band-callout-title { font-family: var(--font-heading); font-weight: 600; font-size: 9pt;
                      color: var(--hero-bg); margin-bottom: 1.5mm; }
.band-callout-text { font-size: 8.5pt; color: var(--text-primary); line-height: 1.55; }
/* Trend page */
.section-title { font-family: var(--font-heading); font-weight: 700; font-size: 12pt;
                 color: var(--hero-bg); margin-bottom: 4mm; padding-bottom: 2mm;
                 border-bottom: 2px solid var(--cta); }
.section-intro { font-size: 9pt; color: var(--text-muted); margin-bottom: 5mm; line-height: 1.5; }
.chart-page-intro { font-size: 9pt; color: var(--text-muted); margin-bottom: 4mm; line-height: 1.5; }
.chart-frame { border: 1px solid var(--border); border-radius: 6px; background: var(--white);
               overflow: hidden; margin-bottom: 4mm; }
.chart-frame-header { background: var(--hero-bg); color: var(--white); padding: 3mm 4mm;
                      display: flex; align-items: center; justify-content: space-between; }
.chart-frame-title { font-family: var(--font-heading); font-size: 9pt; font-weight: 700; }
.chart-frame-meta { font-family: var(--font-mono); font-size: 7pt; color: var(--cta); }
.svg-chart { width: 100%; height: auto; display: block; background: #fbfcfd; }
.chart-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; margin-bottom: 4mm; }
.chart-caption { padding: 2.5mm 4mm 3mm; font-size: 7.8pt; color: var(--text-muted);
                 line-height: 1.45; border-top: 1px solid var(--border); }
.chart-legend { display: flex; gap: 4mm; align-items: center; font-size: 7.5pt;
                color: var(--text-muted); margin-top: 2mm; }
.legend-item { display: inline-flex; align-items: center; gap: 1.5mm; }
.legend-swatch { width: 8mm; height: 2px; display: inline-block; background: var(--cta); }
.legend-swatch.magenta { background: var(--brand-pink); }
.legend-swatch.navy { background: var(--hero-bg); }
.trend-callout-row { display: grid; grid-template-columns: repeat(3,1fr); gap: 3mm; margin-top: 4mm; }
.trend-callout { background: var(--light-grey); border-left: 3px solid var(--cta);
                 border-radius: 0 4px 4px 0; padding: 3mm; }
.trend-callout strong { display: block; font-family: var(--font-heading); font-size: 8pt;
                        color: var(--hero-bg); margin-bottom: 1mm; }
.trend-callout span { display: block; font-size: 7.5pt; color: var(--text-muted); line-height: 1.4; }
/* Tables */
table { width: 100%; border-collapse: collapse; font-family: var(--font-body); font-size: 8.5pt; }
thead tr { background: var(--hero-bg); color: var(--white); }
thead th { font-family: var(--font-heading); font-weight: 600; font-size: 7.5pt;
           text-transform: uppercase; letter-spacing: .6px; padding: 3mm 3.5mm;
           text-align: left; white-space: nowrap; }
tbody tr { border-bottom: 1px solid var(--border); }
tbody tr:nth-child(even) { background: var(--light-grey); }
tbody td { padding: 2.5mm 3.5mm; vertical-align: top; color: var(--text-primary); line-height: 1.45; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 3px;
         font-family: var(--font-heading); font-size: 7pt; font-weight: 700;
         text-transform: uppercase; letter-spacing: .5px; white-space: nowrap; }
.badge-critical { background: var(--sev-critical-bg); color: var(--sev-critical-text); }
.badge-high     { background: var(--sev-high-bg);     color: var(--sev-high-text); }
.badge-medium   { background: var(--sev-medium-bg);   color: var(--sev-medium-text); }
.badge-low      { background: var(--sev-low-bg);      color: var(--sev-low-text); }
.badge-info     { background: var(--sev-info-bg);     color: var(--sev-info-text); }
.pill { display: inline-block; padding: 1px 7px; border-radius: 20px;
        font-size: 7.5pt; font-weight: 700; white-space: nowrap; }
.pill-pass, .pill-licensed { background: var(--pass-bg); color: var(--pass-text); border: 1px solid var(--pass-border); }
.pill-not-licensed { background: var(--skip-bg); color: var(--skip-text); border: 1px solid var(--skip-border); }
.cost-low,.impact-low { color: #15803d; font-weight: 700; }
.cost-mod,.impact-mod { color: #f59e0b; font-weight: 700; }
.cost-high,.impact-high { color: #dc2626; font-weight: 700; }
/* Score opportunity */
.opportunity-summary { display: grid; grid-template-columns: 1.05fr .95fr; gap: 5mm; margin-bottom: 5mm; }
.opportunity-copy { font-size: 9pt; line-height: 1.55; color: var(--text-primary); }
.opportunity-copy p+p { margin-top: 2.5mm; }
.score-stack-card { border: 1px solid var(--border); border-radius: 6px; background: var(--white); padding: 4mm; }
.score-stack-title { font-family: var(--font-heading); font-size: 9pt; font-weight: 700;
                     color: var(--hero-bg); margin-bottom: 3mm; }
.score-stack-total { display: flex; align-items: baseline; gap: 2mm; }
.score-stack-number { font-family: var(--font-heading); font-size: 24pt; font-weight: 700;
                      color: var(--hero-bg); line-height: 1; }
.score-stack-label { font-size: 8pt; color: var(--text-muted); }
.score-stack-bar { margin-top: 3mm; height: 7mm; border-radius: 20px;
                   background: var(--light-grey); overflow: hidden; border: 1px solid var(--border); }
.score-stack-fill { height: 100%; background: linear-gradient(90deg,var(--cta),var(--brand-pink)); }
.score-stack-note { font-size: 7.5pt; color: var(--text-muted); margin-top: 2mm; line-height: 1.4; }
.opp-bar { width: 100%; height: 5mm; background: var(--light-grey); border-radius: 20px;
           overflow: hidden; border: 1px solid var(--border); }
.opp-bar-fill { height: 100%; background: var(--cta); border-radius: 20px; }
.opp-bar-fill.warn { background: #f59e0b; }
.opp-bar-fill.danger { background: #ef4444; }
.priority-box-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 3mm; margin-top: 5mm; }
.priority-box { background: var(--light-grey); border-radius: 5px; padding: 3.5mm;
                border-left: 3px solid var(--cta); }
.priority-box.danger { border-left-color: #ef4444; }
.priority-box.warn { border-left-color: #f59e0b; }
.priority-box-title { font-family: var(--font-heading); font-size: 8.2pt; font-weight: 700;
                      color: var(--hero-bg); margin-bottom: 1.5mm; }
.priority-box-text { font-size: 7.6pt; color: var(--text-muted); line-height: 1.4; }
/* Recommendations table */
.rec-table-compact { font-size: 7.8pt; }
.rec-table-compact th { font-size: 6.7pt; padding: 2.4mm 2.6mm; }
.rec-table-compact td { padding: 2.2mm 2.6mm; line-height: 1.35; }
/* Remediation cards */
.remediation-grid { display: grid; grid-template-columns: 1fr; gap: 3.2mm; }
.remediation-card { border: 1px solid var(--border); border-radius: 6px;
                    background: var(--white); overflow: hidden; }
.remediation-card-header { display: flex; justify-content: space-between; gap: 4mm;
                           background: var(--light-grey); border-bottom: 1px solid var(--border);
                           padding: 3mm 3.5mm; }
.remediation-title { font-family: var(--font-heading); font-size: 8.8pt; font-weight: 700;
                     color: var(--hero-bg); line-height: 1.25; }
.remediation-meta { display: flex; gap: 1.5mm; align-items: flex-start; flex-shrink: 0; }
.remediation-body { padding: 3mm 3.5mm; display: grid;
                    grid-template-columns: 1fr 1fr .9fr; gap: 3mm; }
.remediation-block-title { font-family: var(--font-heading); font-size: 7.2pt; font-weight: 700;
                           color: var(--text-muted); text-transform: uppercase; letter-spacing: .6px;
                           margin-bottom: 1mm; }
.remediation-block-text { font-size: 7.5pt; line-height: 1.45; color: var(--text-primary); }
/* Timeline */
.timeline-strip { display: grid; grid-template-columns: repeat(4,1fr); gap: 2.5mm; margin: 4mm 0 5mm; }
.timeline-step { background: var(--light-grey); border-radius: 5px; padding: 3mm;
                 border-top: 3px solid var(--cta); }
.timeline-step strong { display: block; font-family: var(--font-heading); font-size: 8pt;
                        color: var(--hero-bg); margin-bottom: 1mm; }
.timeline-step span { display: block; font-size: 7.4pt; color: var(--text-muted); line-height: 1.35; }
/* Methodology */
.method-section { margin-bottom: 7mm; }
.method-section-title { font-family: var(--font-heading); font-weight: 600; font-size: 10pt;
                        color: var(--hero-bg); margin-bottom: 2.5mm; }
.method-section-body { font-size: 9pt; color: var(--text-primary); line-height: 1.6; }
.method-section-body p+p { margin-top: 2mm; }
.scoring-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; margin-top: 3mm; }
.scoring-item { background: var(--light-grey); border-radius: 4px; padding: 3mm 4mm; }
.scoring-item-label { font-family: var(--font-heading); font-size: 8pt; font-weight: 600;
                      color: var(--hero-bg); margin-bottom: 1mm; }
.scoring-item-desc { font-size: 7.5pt; color: var(--text-muted); line-height: 1.45; }
.disclaimer-box { background: var(--fail-bg); border: 1px solid var(--fail-border);
                  border-radius: 4px; padding: 4mm 5mm; margin-top: 5mm; }
.disclaimer-box-title { font-family: var(--font-heading); font-weight: 700; font-size: 9pt;
                        color: var(--fail-text); margin-bottom: 2mm; }
.disclaimer-box-text { font-size: 8pt; color: var(--text-primary); line-height: 1.55; }
.contact-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 3mm; margin-top: 4mm; }
.contact-item { background: var(--light-grey); border-radius: 4px; padding: 3mm 4mm; }
.contact-item-label { font-family: var(--font-heading); font-size: 7.5pt; font-weight: 600;
                      color: var(--text-muted); text-transform: uppercase; letter-spacing: .8px;
                      margin-bottom: 1mm; }
.contact-item-value { font-size: 8.5pt; color: var(--hero-bg); font-weight: 500; }
.method-note { background: var(--light-grey); border-radius: 5px; padding: 3.5mm 4mm;
               font-size: 8.2pt; color: var(--text-primary); line-height: 1.5; margin-top: 3mm; }
@media print {
  .page { page-break-before: always; page-break-inside: avoid; }
  .page:first-child { page-break-before: auto; }
  .category-header { page-break-after: avoid; }
  thead { display: table-header-group; }
  tr { page-break-inside: avoid; }
}
"""


def _he(s):
    return html_module.escape(str(s or ''))


def _badge(severity):
    sev = (severity or '').lower()
    cls = {'critical': 'badge-critical', 'high': 'badge-high',
           'medium': 'badge-medium', 'low': 'badge-low'}.get(sev, 'badge-info')
    return f'<span class="badge {cls}">{_he(severity or "Info")}</span>'


def _cost_cls(v):
    v = (v or '').lower()
    return {'low': 'cost-low', 'moderate': 'cost-mod', 'high': 'cost-high'}.get(v, '')


def _impact_cls(v):
    v = (v or '').lower()
    return {'low': 'impact-low', 'moderate': 'impact-mod', 'high': 'impact-high'}.get(v, '')


def _opp_bar_cls(pct):
    if pct >= 75:
        return ''
    if pct >= 55:
        return 'warn'
    return 'danger'


def _build_trend_svg(scores, dates, trend_analysis):
    """Generate the main 760x260 trend SVG from real data."""
    W, H = 760, 260
    PAD_L, PAD_R, PAD_T, PAD_B = 60, 30, 35, 55

    cw = W - PAD_L - PAD_R
    ch = H - PAD_T - PAD_B

    s_min = max(0, min(scores) - 5)
    s_max = min(100, max(scores) + 5)
    s_range = s_max - s_min or 1

    def sx(i):
        return PAD_L + (i / (len(scores) - 1)) * cw if len(scores) > 1 else PAD_L

    def sy(v):
        return PAD_T + ch - ((v - s_min) / s_range) * ch

    # polyline points
    def pts(vals):
        return ' '.join(f'{sx(i):.1f},{sy(v):.1f}' for i, v in enumerate(vals))

    ma = trend_analysis['moving_average']['trend_line']
    linear = trend_analysis['linear']['trend_line']

    # grid lines (5 horizontal)
    grid_vals = [s_min + (s_max - s_min) * k / 4 for k in range(5)]
    grid_lines = ''.join(
        f'<line x1="{PAD_L}" y1="{sy(v):.1f}" x2="{W-PAD_R}" y2="{sy(v):.1f}" stroke="#e5e7eb" stroke-width="1"/>'
        for v in grid_vals
    )
    y_labels = ''.join(
        f'<text x="{PAD_L-8}" y="{sy(v)+4:.1f}" font-size="11" fill="#6b7280" text-anchor="end">{v:.0f}%</text>'
        for v in grid_vals
    )

    # change-point verticals
    cp_lines = ''
    for day_idx, _ in (trend_analysis.get('change_points', {}).get('significant_changes', []) or [])[:4]:
        if 0 < day_idx < len(dates):
            cp_lines += (f'<line x1="{sx(day_idx):.1f}" y1="{PAD_T}" x2="{sx(day_idx):.1f}" '
                         f'y2="{PAD_T+ch}" stroke="#e4007c" stroke-width="1.2" stroke-dasharray="5 5" opacity="0.4"/>')

    # area fill
    area_pts = (f'{sx(0):.1f},{sy(s_min):.1f} ' +
                ' '.join(f'{sx(i):.1f},{sy(v):.1f}' for i, v in enumerate(scores)) +
                f' {sx(len(scores)-1):.1f},{sy(s_min):.1f}')

    # x labels (6 evenly spaced)
    x_label_count = min(6, len(dates))
    x_labels = ''
    for k in range(x_label_count):
        idx = int(k * (len(dates) - 1) / max(x_label_count - 1, 1))
        x_labels += (f'<text x="{sx(idx):.1f}" y="{H-8}" font-size="10" fill="#6b7280" text-anchor="middle">'
                     f'{dates[idx].strftime("%d %b")}</text>')

    start_score = f'{scores[0]:.1f}%'
    end_score   = f'{scores[-1]:.1f}%'

    return f'''<svg class="svg-chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="sf" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="#3abfcf" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="#3abfcf" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="#fbfcfd"/>
  <g>{grid_lines}</g>
  <g font-family="Lato,Arial,sans-serif">{y_labels}</g>
  {cp_lines}
  <polygon points="{area_pts}" fill="url(#sf)"/>
  <polyline points="{pts(scores)}" fill="none" stroke="#1b4d62" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
  <polyline points="{pts(ma)}" fill="none" stroke="#3abfcf" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <polyline points="{pts(linear)}" fill="none" stroke="#e4007c" stroke-width="2" stroke-dasharray="8 6" stroke-linecap="round"/>
  <g font-family="Poppins,Arial,sans-serif" font-weight="700">
    <circle cx="{sx(0):.1f}" cy="{sy(scores[0]):.1f}" r="4" fill="#1b4d62"/>
    <text x="{sx(0)+10:.1f}" y="{sy(scores[0])+4:.1f}" font-size="12" fill="#1b4d62">{start_score}</text>
    <circle cx="{sx(len(scores)-1):.1f}" cy="{sy(scores[-1]):.1f}" r="4" fill="#3abfcf"/>
    <text x="{sx(len(scores)-1)-10:.1f}" y="{sy(scores[-1])-8:.1f}" font-size="12" fill="#1b4d62" text-anchor="end">{end_score}</text>
  </g>
  <g font-family="Lato,Arial,sans-serif">{x_labels}</g>
</svg>'''


def _build_dist_svg(scores):
    """Score distribution bar chart, 365x170."""
    W, H = 365, 170
    n_bins = min(10, max(2, len(scores) // 4))
    lo, hi = min(scores), max(scores)
    if hi == lo:
        hi = lo + 1
    edges = [lo + (hi - lo) * k / n_bins for k in range(n_bins + 1)]
    counts = [0] * n_bins
    for s in scores:
        idx = min(int((s - lo) / (hi - lo) * n_bins), n_bins - 1)
        counts[idx] += 1
    max_c = max(counts) or 1
    PAD_L, PAD_R, PAD_T, PAD_B = 42, 20, 20, 30
    cw = W - PAD_L - PAD_R
    ch = H - PAD_T - PAD_B
    bw = cw / n_bins * 0.8
    bars = ''
    for i, c in enumerate(counts):
        bh = c / max_c * ch
        x = PAD_L + (i + 0.1) * cw / n_bins
        bars += f'<rect x="{x:.1f}" y="{PAD_T+ch-bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="#3abfcf"/>'
    mean_v = float(np.mean(scores))
    med_v  = float(np.median(scores))
    def vx(v):
        return PAD_L + (v - lo) / (hi - lo) * cw
    x_labels = ''
    for k in range(min(5, n_bins + 1)):
        v = lo + (hi - lo) * k / 4
        x_labels += f'<text x="{vx(v):.1f}" y="{H-4}" font-size="9" fill="#6b7280" text-anchor="middle">{v:.0f}%</text>'
    return f'''<svg class="svg-chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <rect width="{W}" height="{H}" fill="#fbfcfd"/>
  {bars}
  <line x1="{vx(mean_v):.1f}" y1="{PAD_T}" x2="{vx(mean_v):.1f}" y2="{PAD_T+ch}" stroke="#e4007c" stroke-width="2" stroke-dasharray="5 4"/>
  <line x1="{vx(med_v):.1f}"  y1="{PAD_T}" x2="{vx(med_v):.1f}"  y2="{PAD_T+ch}" stroke="#f59e0b" stroke-width="2" stroke-dasharray="4 4"/>
  <g font-family="Lato,Arial,sans-serif" font-size="9">
    <text x="{vx(mean_v)+4:.1f}" y="{PAD_T+12}" fill="#e4007c">Mean {mean_v:.1f}%</text>
    <text x="{vx(med_v)+4:.1f}"  y="{PAD_T+24}" fill="#f59e0b">Median {med_v:.1f}%</text>
  </g>
  <g font-family="Lato,Arial,sans-serif">{x_labels}</g>
</svg>'''


def _build_change_svg(scores):
    """Daily changes waterfall, 365x170."""
    changes = [scores[i] - scores[i-1] for i in range(1, len(scores))]
    if not changes:
        return '<svg class="svg-chart" viewBox="0 0 365 170" xmlns="http://www.w3.org/2000/svg"></svg>'
    W, H = 365, 170
    PAD_L, PAD_R, PAD_T, PAD_B = 34, 20, 20, 30
    cw = W - PAD_L - PAD_R
    ch = H - PAD_T - PAD_B
    mid_y = PAD_T + ch / 2
    max_abs = max(abs(c) for c in changes) or 1
    n = len(changes)
    bw = max(1, cw / n * 0.7)
    bars = ''
    for i, c in enumerate(changes):
        x = PAD_L + (i + 0.15) * cw / n
        h = abs(c) / max_abs * (ch / 2)
        if c >= 0:
            bars += f'<rect x="{x:.1f}" y="{mid_y-h:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="#3abfcf" opacity=".85"/>'
        else:
            bars += f'<rect x="{x:.1f}" y="{mid_y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="#e4007c" opacity=".85"/>'
    return f'''<svg class="svg-chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <rect width="{W}" height="{H}" fill="#fbfcfd"/>
  {bars}
  <line x1="{PAD_L}" y1="{mid_y:.1f}" x2="{W-PAD_R}" y2="{mid_y:.1f}" stroke="#1b4d62" stroke-width="1.2"/>
  <g font-family="Lato,Arial,sans-serif" font-size="9" fill="#6b7280">
    <text x="{PAD_L-4}" y="{PAD_T+8}" text-anchor="end">+{max_abs:.1f}</text>
    <text x="{PAD_L-4}" y="{mid_y+4:.1f}" text-anchor="end">0</text>
    <text x="{PAD_L-4}" y="{PAD_T+ch-2:.1f}" text-anchor="end">-{max_abs:.1f}</text>
  </g>
</svg>'''


def _page_header(title, tenant, date_str):
    return f'''<div class="page-header-bar">
  <h1>{_he(title)}</h1>
  <div class="page-header-right">
    <span class="page-header-tenant">{_he(tenant)}</span>
    <span class="page-header-date">{_he(date_str)}</span>
  </div>
</div>'''


def _page_footer(tenant, page_label):
    return f'''<div class="page-footer">
  <span class="page-footer-left">Intuity Technologies — Microsoft Secure Score Report — {_he(tenant)}</span>
  <span class="page-footer-right">CONFIDENTIAL · {_he(page_label)}</span>
</div>'''


def build_html_report(json_data, trend_analysis, control_profiles, trend_stats):
    data        = json_data['result']
    tenant_name = json_data.get('tenant_name', 'Customer Tenant')
    tenant_id   = trend_stats.get('tenant_id', '')
    generated   = datetime.now().strftime('%d %b %Y')
    date_str    = datetime.now().strftime('%Y-%m-%d')
    period      = f"{trend_stats['period_start']} – {trend_stats['period_end']}"

    scores = [(item['currentScore'] / item['maxScore']) * 100 for item in data]
    dates  = [item['date'] for item in data]

    score_end  = trend_stats['score_end_pct']
    score_pts  = trend_stats['current_score_pts']
    max_pts    = trend_stats['max_score_pts']
    score_band = trend_stats['score_band']

    # Gauge SVG values
    r = 42
    circ = 2 * math.pi * r
    offset = circ * (1 - score_end / 100)
    gauge_color = {'Cyber Resilient': '#22c55e', 'Near Ready': '#f59e0b',
                   'Significant Gaps': '#ef4444', 'Critical Risk': '#7f1d1d'}.get(score_band, '#3abfcf')
    band_css = score_band.lower().replace(' ', '_')

    # ── Page 1: Cover ──────────────────────────────────────────────────────
    cover = f'''<section class="page cover">
  <div class="cover-header">
    <div>
      <span class="cover-logo-intuity">intuity</span>
      <span class="cover-logo-tech">Technologies</span>
    </div>
    <span class="cover-confidential-badge">Confidential</span>
  </div>
  <div class="cover-stripe"></div>
  <div class="cover-body">
    <div class="cover-eyebrow">Microsoft Secure Score</div>
    <div class="cover-title">Quarterly Secure Score<br/>Report</div>
    <div class="cover-subtitle">{_he(tenant_name)}</div>
    <div class="cover-meta">
      <div class="cover-meta-row">
        <span class="cover-meta-label">Tenant ID</span>
        <span class="cover-meta-value" style="font-family:var(--font-mono);font-size:8.5pt">{_he(tenant_id)}</span>
      </div>
      <div class="cover-meta-row">
        <span class="cover-meta-label">Generated</span>
        <span class="cover-meta-value">{generated}</span>
      </div>
      <div class="cover-meta-row">
        <span class="cover-meta-label">Period</span>
        <span class="cover-meta-value" style="font-family:var(--font-mono);font-size:8.5pt">{_he(period)}</span>
      </div>
      <div class="cover-meta-row">
        <span class="cover-meta-label">Prepared by</span>
        <span class="cover-meta-value">Intuity Technologies</span>
      </div>
    </div>
    <div class="cover-score-area">
      <div class="score-gauge">
        <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
          <circle class="score-gauge-track" cx="50" cy="50" r="{r}"/>
          <circle class="score-gauge-fill" cx="50" cy="50" r="{r}"
            stroke="{gauge_color}" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.2f}"/>
        </svg>
        <div class="score-gauge-inner">
          <span class="score-number">{score_end:.1f}</span>
          <span class="score-denom">/ 100</span>
        </div>
      </div>
      <div class="score-band-info">
        <span class="score-band-label band-{band_css}">{_he(score_band)}</span>
        <span class="score-band-desc">
          {score_end:.1f}% ({trend_stats["score_movement"]:+.1f}pp) over the review period.<br/>
          Trend is {_he(trend_stats["trend_direction"])} (R²={trend_stats["r_squared"]:.3f}).
        </span>
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
</section>'''

    # ── Page 2: Executive Summary ───────────────────────────────────────────
    movement_cls = 'good' if trend_stats['score_movement'] > 0 else 'warn'
    pos_pct_cls  = 'good' if trend_stats['positive_days_pct'] >= 60 else 'warn'

    exec_summary = f'''<section class="page">
  {_page_header("Executive Summary", tenant_name, date_str)}
  <div class="page-inner">
    <p class="summary-lede">
      Over the reporting period from <strong>{_he(trend_stats["period_start"])}</strong> to
      <strong>{_he(trend_stats["period_end"])}</strong>, <strong>{_he(tenant_name)}</strong>
      achieved a Secure Score of <strong>{score_end:.1f}%</strong>
      (<strong>{score_pts:.0f} / {max_pts:.0f} points</strong>).
      The overall trend is <strong>{_he(trend_stats["trend_direction"])}</strong>
      (R²={trend_stats["r_squared"]:.3f}, p={trend_stats["p_value"]:.3f}).
    </p>
    <div class="metric-strip">
      <div class="metric-card">
        <span class="metric-label">Current Score</span>
        <span class="metric-value">{score_end:.1f}%</span>
        <span class="metric-sub">{score_pts:.0f} / {max_pts:.0f} points</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Score Movement</span>
        <span class="metric-value {movement_cls}">{trend_stats["score_movement"]:+.1f}</span>
        <span class="metric-sub">percentage points</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Trend Strength</span>
        <span class="metric-value">{trend_stats["r_squared"]:.3f}</span>
        <span class="metric-sub">linear R² correlation</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Positive Days</span>
        <span class="metric-value {pos_pct_cls}">{trend_stats["positive_days_pct"]:.0f}%</span>
        <span class="metric-sub">{trend_stats["positive_days_count"]} of {trend_stats["total_daily_changes"]} daily changes</span>
      </div>
    </div>
    <div class="summary-grid">
      <div class="summary-panel">
        <div class="summary-panel-title">Score Statistics</div>
        <table class="mini-table"><tbody>
          <tr><td>Average score</td><td>{trend_stats["avg_score"]:.1f}%</td></tr>
          <tr><td>Median score</td><td>{trend_stats["median_score"]:.1f}%</td></tr>
          <tr><td>Best score</td><td>{trend_stats["best_score"]:.1f}%</td></tr>
          <tr><td>Worst score</td><td>{trend_stats["worst_score"]:.1f}%</td></tr>
          <tr><td>Volatility</td><td>{trend_stats["volatility"]:.2f}%</td></tr>
          <tr><td>Data points</td><td>{trend_stats["data_points"]}</td></tr>
          <tr><td>Significant events</td><td>{trend_stats["significant_events"]} detected</td></tr>
        </tbody></table>
      </div>
      <div class="summary-panel">
        <div class="summary-panel-title">Benchmark Comparisons</div>
        <table class="mini-table"><tbody>
          <tr><td>All tenants avg</td><td>{f'{trend_stats["avg_score_all_tenants"]:.1f}%' if trend_stats.get("avg_score_all_tenants") else "N/A"}</td></tr>
          <tr><td>Similar size avg</td><td>{f'{trend_stats["avg_score_similar_seats"]:.1f}%' if trend_stats.get("avg_score_similar_seats") else "N/A"}</td></tr>
          <tr><td>Current position</td><td>{_he(score_band)}</td></tr>
        </tbody></table>
      </div>
    </div>
    <div class="band-callout">
      <div class="band-callout-title">Secure Score Position: {_he(score_band)}</div>
      <div class="band-callout-text">
        The score of {score_end:.1f}% places this tenant in the <strong>{_he(score_band)}</strong> band.
        Intuity recommends reviewing the top-ranked controls below to target the highest-value
        improvement opportunities.
      </div>
    </div>
  </div>
  {_page_footer(tenant_name, "Page 2")}
</section>'''

    # ── Page 3: Trend Analysis ──────────────────────────────────────────────
    trend_svg  = _build_trend_svg(scores, dates, trend_analysis)
    dist_svg   = _build_dist_svg(scores)
    change_svg = _build_change_svg(scores)

    cp = trend_analysis.get('change_points', {})
    largest_gain = max((c for _, c in cp.get('significant_changes', [])), default=0)
    largest_drop = min((c for _, c in cp.get('significant_changes', [])), default=0)

    trend_page = f'''<section class="page">
  {_page_header("Trend Analysis", tenant_name, date_str)}
  <div class="page-inner">
    <div class="section-title">90-Day Secure Score Movement</div>
    <p class="chart-page-intro">
      Score movement across {trend_stats["data_points"]} data points from
      {_he(trend_stats["period_start"])} to {_he(trend_stats["period_end"])}.
      {trend_stats["significant_events"]} significant change events detected.
    </p>
    <div class="chart-frame">
      <div class="chart-frame-header">
        <span class="chart-frame-title">Secure Score Trend (% of Maximum)</span>
        <span class="chart-frame-meta">{_he(trend_stats["period_start"])} – {_he(trend_stats["period_end"])}</span>
      </div>
      {trend_svg}
      <div class="chart-caption">
        Score moved from {trend_stats["score_start_pct"]:.1f}% to {score_end:.1f}%
        ({trend_stats["score_movement"]:+.1f}pp). Significant change events are marked with pink dashed lines.
        <div class="chart-legend">
          <span class="legend-item"><span class="legend-swatch navy"></span>Daily score</span>
          <span class="legend-item"><span class="legend-swatch"></span>{trend_analysis["moving_average"]["window_size"]}-day average</span>
          <span class="legend-item"><span class="legend-swatch magenta"></span>Linear trend</span>
        </div>
      </div>
    </div>
    <div class="chart-grid-2">
      <div class="chart-frame">
        <div class="chart-frame-header">
          <span class="chart-frame-title">Score Distribution</span>
          <span class="chart-frame-meta">{trend_stats["data_points"]} points</span>
        </div>
        {dist_svg}
        <div class="chart-caption">Score distribution across the reporting period. Mean: {trend_stats["avg_score"]:.1f}%, Median: {trend_stats["median_score"]:.1f}%.</div>
      </div>
      <div class="chart-frame">
        <div class="chart-frame-header">
          <span class="chart-frame-title">Daily Score Changes</span>
          <span class="chart-frame-meta">+ / − movement</span>
        </div>
        {change_svg}
        <div class="chart-caption">{trend_stats["positive_days_count"]} positive and {trend_stats["negative_days_count"]} negative daily changes recorded.</div>
      </div>
    </div>
    <div class="trend-callout-row">
      <div class="trend-callout">
        <strong>Largest gain</strong>
        <span>{f'+{largest_gain:.1f}pp in a single day' if largest_gain > 0 else 'No major single-day gain detected.'}</span>
      </div>
      <div class="trend-callout">
        <strong>Main regression</strong>
        <span>{f'{largest_drop:.1f}pp largest single-day decline' if largest_drop < 0 else 'No significant regression events.'}</span>
      </div>
      <div class="trend-callout">
        <strong>Trend quality</strong>
        <span>R²={trend_stats["r_squared"]:.3f} — {'strong linear trend' if trend_stats["r_squared"] > 0.7 else 'moderate trend signal'}.</span>
      </div>
    </div>
  </div>
  {_page_footer(tenant_name, "Page 3")}
</section>'''

    # ── Page 4: Score Opportunity by Category ──────────────────────────────
    cat_scores  = defaultdict(lambda: {'current': 0.0, 'max': 0.0})
    if control_profiles:
        for cp_item in control_profiles:
            cat = cp_item.get('controlCategory') or 'Other'
            cat_scores[cat]['max']     += float(cp_item.get('maxScore') or 0)
            cat_scores[cat]['current'] += float(cp_item.get('scoreInPercentage', 0) or 0) * float(cp_item.get('maxScore') or 0) / 100.0

    cat_rows = ''
    for cat, vals in sorted(cat_scores.items(), key=lambda x: x[1]['max'], reverse=True):
        mx  = vals['max']
        cur = min(vals['current'], mx)
        rem = mx - cur
        pct = (cur / mx * 100) if mx else 0
        bar_cls = _opp_bar_cls(pct)
        cat_rows += f'''<tr>
  <td><strong>{_he(cat)}</strong></td>
  <td>{cur:.0f}</td><td>{mx:.0f}</td><td>{rem:.0f}</td>
  <td><div class="opp-bar"><div class="opp-bar-fill {bar_cls}" style="width:{pct:.0f}%;"></div></div></td>
</tr>'''

    opp_page = f'''<section class="page">
  {_page_header("Score Opportunity by Category", tenant_name, date_str)}
  <div class="page-inner">
    <div class="section-title">Where the Remaining Secure Score Points Sit</div>
    <div class="opportunity-summary">
      <div class="opportunity-copy">
        <p>
          This tenant currently holds <strong>{score_pts:.0f} of {max_pts:.0f} available points</strong>,
          leaving <strong>{max_pts - score_pts:.0f} points</strong> of Secure Score improvement opportunity.
        </p>
        <p>
          Identity and endpoint controls should be prioritised first, as they directly reduce
          account compromise, privilege escalation, unmanaged device access, and malware exposure.
        </p>
      </div>
      <div class="score-stack-card">
        <div class="score-stack-title">Current Secure Score Position</div>
        <div class="score-stack-total">
          <span class="score-stack-number">{score_pts:.0f}</span>
          <span class="score-stack-label">/ {max_pts:.0f} points achieved</span>
        </div>
        <div class="score-stack-bar">
          <div class="score-stack-fill" style="width:{score_end:.1f}%;"></div>
        </div>
        <div class="score-stack-note">Current position: {score_end:.1f}%. Remaining: {max_pts - score_pts:.0f} points.</div>
      </div>
    </div>
    <table>
      <thead><tr><th>Category</th><th>Current</th><th>Available</th><th>Remaining</th><th>Completion</th></tr></thead>
      <tbody>{cat_rows}</tbody>
    </table>
    <div class="band-callout" style="margin-top:5mm;">
      <div class="band-callout-title">Interpretation</div>
      <div class="band-callout-text">
        Focus remediation on the lowest-completion categories first. Identity and Device controls
        typically offer the highest risk reduction relative to effort.
      </div>
    </div>
  </div>
  {_page_footer(tenant_name, "Page 4")}
</section>'''

    # ── Page 5: Recommendations table ──────────────────────────────────────
    sorted_cps = sorted(control_profiles or [], key=lambda x: x.get('rank') or 9999)
    rec_rows = ''
    for i, cp_item in enumerate(sorted_cps[:20], 1):
        threats = cp_item.get('threats') or []
        threat_str = ', '.join(THREAT_LABELS.get(t, t) for t in threats[:3])
        if len(threats) > 3:
            threat_str += f' +{len(threats)-3}'
        sev = (cp_item.get('severity') or 'medium').capitalize()
        impl = (cp_item.get('implementationCost') or 'n/a').capitalize()
        user = (cp_item.get('userImpact') or 'n/a').capitalize()
        rec_rows += f'''<tr>
  <td>{_he(cp_item.get("rank") or i)}</td>
  <td>{_he(cp_item.get("title") or "—")}</td>
  <td>{_he(cp_item.get("controlCategory") or "—")}</td>
  <td>{float(cp_item.get("maxScore") or 0):.0f}</td>
  <td>{_badge(sev)}</td>
  <td class="{_cost_cls(impl)}">{_he(impl)}</td>
  <td class="{_impact_cls(user)}">{_he(user)}</td>
  <td>{_he(threat_str or "—")}</td>
</tr>'''

    rec_page = f'''<section class="page">
  {_page_header("Security Control Recommendations", tenant_name, date_str)}
  <div class="page-inner">
    <div class="section-title">Prioritised Secure Score Improvement Actions</div>
    <p class="section-intro">
      Controls ordered by Microsoft rank. Implementation cost and user impact are colour-coded:
      <span class="cost-low">Low</span> /
      <span class="cost-mod">Moderate</span> /
      <span class="cost-high">High</span>.
    </p>
    <table class="rec-table-compact">
      <thead><tr><th>#</th><th>Control</th><th>Category</th><th>Pts</th>
        <th>Severity</th><th>Cost</th><th>User Impact</th><th>Threats Reduced</th></tr></thead>
      <tbody>{rec_rows}</tbody>
    </table>
    <div class="timeline-strip">
      <div class="timeline-step"><strong>Week 1–2</strong><span>Identity baseline: MFA, legacy auth, admin accounts.</span></div>
      <div class="timeline-step"><strong>Week 3–4</strong><span>PIM rollout, SSPR, password protection.</span></div>
      <div class="timeline-step"><strong>Week 5–8</strong><span>Endpoint enrolment, compliance, Defender onboarding.</span></div>
      <div class="timeline-step"><strong>Week 9–12</strong><span>DLP, audit, OAuth review, guest access.</span></div>
    </div>
    <div class="band-callout">
      <div class="band-callout-title">Implementation Note</div>
      <div class="band-callout-text">
        User-facing controls should be piloted before tenant-wide enforcement. MFA,
        device compliance, DLP, and access blocking can interrupt users if communications
        and service-owner approvals are skipped.
      </div>
    </div>
  </div>
  {_page_footer(tenant_name, "Page 5")}
</section>'''

    # ── Pages 6+: Remediation playbooks (up to 10 controls, 3 per page) ────
    remediation_pages = ''
    top_cps = sorted_cps[:10]
    per_page = 3
    for page_idx in range(0, len(top_cps), per_page):
        chunk = top_cps[page_idx:page_idx + per_page]
        cards = ''
        for cp_item in chunk:
            sev  = (cp_item.get('severity') or 'medium').capitalize()
            lic  = 'Licensed' if cp_item.get('isLicensed') else 'Not Licensed'
            lic_cls = 'pill-licensed' if cp_item.get('isLicensed') else 'pill-not-licensed'
            rem  = _strip_html(cp_item.get('remediation') or 'No remediation guidance available.')
            imp  = _strip_html(cp_item.get('remediationImpact') or '')
            url  = cp_item.get('actionUrl') or ''
            cards += f'''<div class="remediation-card">
  <div class="remediation-card-header">
    <div class="remediation-title">{_he(cp_item.get("rank",""))}. {_he(cp_item.get("title","Unnamed control"))}</div>
    <div class="remediation-meta">{_badge(sev)}<span class="pill {lic_cls}">{lic}</span></div>
  </div>
  <div class="remediation-body">
    <div>
      <div class="remediation-block-title">Remediation</div>
      <div class="remediation-block-text">{_he(rem[:300])}</div>
    </div>
    <div>
      <div class="remediation-block-title">User Impact</div>
      <div class="remediation-block-text">{_he(imp[:200]) if imp else "No user impact data."}</div>
    </div>
    <div>
      <div class="remediation-block-title">Action</div>
      <div class="remediation-block-text">
        {'<a href="' + _he(url) + '" style="color:var(--cta);">→ Take action in portal</a>' if url else "See Microsoft documentation."}
      </div>
    </div>
  </div>
</div>'''
        page_num = 6 + page_idx // per_page
        remediation_pages += f'''<section class="page">
  {_page_header("Remediation Playbook", tenant_name, date_str)}
  <div class="page-inner">
    <div class="section-title">Top Controls — Items {page_idx+1}–{page_idx+len(chunk)}</div>
    <div class="remediation-grid">{cards}</div>
  </div>
  {_page_footer(tenant_name, f"Page {page_num}")}
</section>'''

    # ── Final page: Methodology ─────────────────────────────────────────────
    methodology = f'''<section class="page">
  {_page_header("Methodology and Disclaimer", tenant_name, date_str)}
  <div class="page-inner">
    <div class="method-section">
      <div class="method-section-title">Assessment Methodology</div>
      <div class="method-section-body">
        <p>This report is based on Microsoft Secure Score data retrieved via the Microsoft Graph API
        and analysed using statistical trend modelling across the reporting period. It reviews enabled
        controls, incomplete improvement actions, score movement, and prioritised remediation opportunities.</p>
        <p>Secure Score is used as a directional benchmark for Microsoft 365 security maturity. It should
        be interpreted alongside operational context, licensing, business requirements, and user impact.</p>
      </div>
    </div>
    <div class="method-section">
      <div class="method-section-title">Scoring Interpretation</div>
      <div class="scoring-grid">
        <div class="scoring-item"><div class="scoring-item-label">80–100: Strong Position</div>
          <div class="scoring-item-desc">Core controls are mature, consistently enforced, and actively monitored.</div></div>
        <div class="scoring-item"><div class="scoring-item-label">65–79: Improving Position</div>
          <div class="scoring-item-desc">Foundation is mostly in place, with remaining medium-value controls to complete.</div></div>
        <div class="scoring-item"><div class="scoring-item-label">40–64: Significant Gaps</div>
          <div class="scoring-item-desc">Multiple high-value controls remain incomplete. Remediation is a managed workstream.</div></div>
        <div class="scoring-item"><div class="scoring-item-label">0–39: High Exposure</div>
          <div class="scoring-item-desc">Foundational protections are missing or inconsistently configured.</div></div>
      </div>
    </div>
    <div class="disclaimer-box">
      <div class="disclaimer-box-title">Disclaimer</div>
      <div class="disclaimer-box-text">
        This report is provided for informational and advisory purposes only. Secure Score is not a
        guarantee of security, breach prevention, regulatory compliance, or absence of risk. Findings
        and recommendations should be validated against {_he(tenant_name)}'s business requirements,
        licensing position, and change control processes before implementation.
      </div>
    </div>
    <div class="method-section" style="margin-top:6mm;">
      <div class="method-section-title">Contact</div>
      <div class="contact-grid">
        <div class="contact-item"><div class="contact-item-label">Prepared By</div>
          <div class="contact-item-value">Intuity Technologies</div></div>
        <div class="contact-item"><div class="contact-item-label">Support</div>
          <div class="contact-item-value">technicalsupport@intuity.ie</div></div>
        <div class="contact-item"><div class="contact-item-label">Website</div>
          <div class="contact-item-value">intuity.ie</div></div>
      </div>
    </div>
  </div>
  {_page_footer(tenant_name, "Final Page")}
</section>'''

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Secure Score Report — {_he(tenant_name)}</title>
<style>{CSS}</style>
</head>
<body>
{cover}
{exec_summary}
{trend_page}
{opp_page}
{rec_page}
{remediation_pages}
{methodology}
</body>
</html>'''


# ---------------------------------------------------------------------------
# Azure Function entry point
# ---------------------------------------------------------------------------

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('secure_score_graph function triggered.')

    try:
        if req.method == 'GET':
            html_content = """<!DOCTYPE html>
<html><head><title>Secure Score Report Generator</title>
<style>
  body{font-family:Arial,sans-serif;margin:40px;background:#f5f5f5}
  .card{max-width:820px;margin:0 auto;background:#fff;padding:32px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)}
  h1{color:#1b4d62}h2{color:#e4007c;font-size:1rem}
  textarea{width:100%;height:180px;font-family:monospace;font-size:13px;border:1px solid #ccc;border-radius:4px;padding:8px}
  button{padding:10px 24px;background:#1b4d62;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:14px}
  button:hover{background:#3abfcf}pre{background:#f4f4f4;padding:12px;border-radius:4px;overflow:auto;font-size:12px}
</style></head>
<body><div class="card">
<h1>Microsoft Secure Score — PDF Report Generator</h1>
<h2>POST body schema</h2>
<pre>{
  "tenant_name": "Contoso Ltd",
  "result": [{ "createdDateTime": "2024-01-01T00:00:00Z", "currentScore": 75, "maxScore": 100 }],
  "control_profiles": [{ "rank": 1, "title": "Enable MFA", "controlCategory": "Identity",
    "maxScore": 9, "implementationCost": "low", "userImpact": "moderate",
    "threats": ["accountBreach"], "remediation": "Create a Conditional Access policy.",
    "actionUrl": "https://portal.azure.com", "severity": "critical", "isLicensed": true }]
}</pre>
<form id="f">
  <textarea id="j" placeholder="Paste JSON here"></textarea><br><br>
  <button type="submit">Generate PDF Report</button>
</form>
<div id="out"></div></div>
<script>
document.getElementById('f').onsubmit=async e=>{
  e.preventDefault();
  const r=await fetch('/api/secure-score-graph',{method:'POST',
    headers:{'Content-Type':'application/json'},body:document.getElementById('j').value});
  if(r.ok){const d=await r.json();document.getElementById('out').innerHTML=
    '<p><b>Done.</b> Filename: '+d.filename+'</p>';}
  else{document.getElementById('out').textContent='Error: '+await r.text();}
};
</script></body></html>"""
            return func.HttpResponse(body=html_content, mimetype='text/html', status_code=200)

        json_data = req.get_json()
        if not json_data or 'result' not in json_data:
            return func.HttpResponse(
                "Provide JSON with a 'result' array of secureScore entries.", status_code=400)

        # Parse dates
        for item in json_data['result']:
            item['date'] = datetime.strptime(item['createdDateTime'].split('T')[0], '%Y-%m-%d')
        json_data['result'].sort(key=lambda x: x['date'])

        # Flatten control_profiles
        raw_profiles = json_data.get('control_profiles') or None
        if raw_profiles:
            control_profiles = []
            for item in raw_profiles:
                if isinstance(item, list):
                    control_profiles.extend(item)
                else:
                    control_profiles.append(item)
        else:
            control_profiles = None

        scores = [(item['currentScore'] / item['maxScore']) * 100 for item in json_data['result']]
        dates  = [item['date'] for item in json_data['result']]
        trend_analysis = analyze_security_trends(scores, dates)
        trend_stats    = build_trend_stats(json_data, trend_analysis)

        html_report = build_html_report(json_data, trend_analysis, control_profiles, trend_stats)

        # Render HTML → PDF via Playwright
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page()
            page.set_content(html_report, wait_until='networkidle')
            pdf_bytes = page.pdf(format='A4', print_background=True, margin={
                'top': '0', 'right': '0', 'bottom': '0', 'left': '0'})
            browser.close()

        pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
        response_data = {
            'pdf_data':     pdf_base64,
            'filename':     f"secure_score_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            'content_type': 'application/pdf',
            'trend_stats':  trend_stats,
        }
        return func.HttpResponse(
            body=json.dumps(response_data), mimetype='application/json', status_code=200)

    except Exception as e:
        logging.error(f'Error: {e}', exc_info=True)
        return func.HttpResponse(f'Error: {e}', status_code=500)
