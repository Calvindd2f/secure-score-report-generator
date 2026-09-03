import asyncio
import base64
from datetime import datetime
import json
import logging
import math
import os
import subprocess
import tempfile
from collections import defaultdict

import azure.functions as func
import numpy as np
from scipy import stats
from scipy.signal import savgol_filter
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from sklearn.metrics import r2_score, mean_squared_error
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Threat label mapping (used in recommendations table and controls list)
# ---------------------------------------------------------------------------
THREAT_LABELS = {
    'accountBreach':        'Account breach',
    'dataDeletion':         'Data deletion',
    'dataExfiltration':     'Data exfiltration',
    'dataSpillage':         'Data spillage',
    'elevationOfPrivilege': 'Privilege escalation',
    'maliciousInsider':     'Malicious insider',
    'passwordCracking':     'Password cracking',
    'phishingOrWhaling':    'Phishing',
    'spoofing':             'Spoofing',
}

# ---------------------------------------------------------------------------
# Statistical analysis — unchanged from original
# ---------------------------------------------------------------------------

def analyze_security_trends(scores, dates):
    x = np.array([date.timestamp() for date in dates])
    y = np.array(scores)
    x_normalized = (x - x.min()) / (x.max() - x.min()) if x.max() != x.min() else x

    results = {}

    slope, intercept, r_value, p_value, std_err = [
        float(v) for v in stats.linregress(x_normalized, y)
    ]
    results['linear'] = {
        'slope':      slope,
        'intercept':  intercept,
        'r_squared':  r_value ** 2,
        'p_value':    p_value,
        'std_error':  std_err,
        'trend_line': slope * x_normalized + intercept,
    }

    poly_features = PolynomialFeatures(degree=2)
    x_poly = poly_features.fit_transform(x_normalized.reshape(-1, 1))
    poly_model = LinearRegression()
    poly_model.fit(x_poly, y)
    y_poly_pred = poly_model.predict(x_poly)
    results['polynomial'] = {
        'coefficients': poly_model.coef_,
        'r_squared':    r2_score(y, y_poly_pred),
        'mse':          mean_squared_error(y, y_poly_pred),
        'trend_line':   y_poly_pred,
    }

    window_size = max(1, min(7, len(scores) // 4))
    if window_size > 1:
        moving_avg = np.convolve(scores, np.ones(window_size) / window_size, mode='valid')
        padding    = len(scores) - len(moving_avg)
        moving_avg = np.concatenate([scores[:padding], moving_avg])
    else:
        moving_avg = np.array(scores, dtype=float)
    results['moving_average'] = {'window_size': window_size, 'trend_line': moving_avg}

    if len(scores) > 5:
        window_length = min(5, len(scores) - 2)
        if window_length % 2 == 0:
            window_length -= 1
        savgol_smooth = savgol_filter(scores, window_length, 2) if window_length >= 3 else np.array(scores, dtype=float)
    else:
        savgol_smooth = np.array(scores, dtype=float)
    results['savgol'] = {'trend_line': savgol_smooth}

    if len(scores) > 10:
        first_half   = scores[:len(scores) // 2]
        second_half  = scores[len(scores) // 2:]
        var_first    = np.var(first_half)
        variance_ratio = np.var(second_half) / var_first if var_first > 0 else 1.0
        trend_strength = abs(slope) / (float(np.std(scores)) + 1e-8)
        results['statistics'] = {
            'variance_ratio': variance_ratio,
            'trend_strength': trend_strength,
            'is_stationary':  variance_ratio < 2.0,
            'volatility':     np.std(scores),
            'skewness':       stats.skew(scores),
            'kurtosis':       stats.kurtosis(scores),
        }

    if len(scores) >= 7:
        day_of_week     = [date.weekday() for date in dates]
        weekly_patterns = {}
        for day in range(7):
            day_scores = [scores[i] for i, d in enumerate(day_of_week) if d == day]
            if day_scores:
                weekly_patterns[day] = {
                    'mean':  np.mean(day_scores),
                    'std':   np.std(day_scores),
                    'count': len(day_scores),
                }
        results['seasonality'] = weekly_patterns

    changes = [scores[i] - scores[i - 1] for i in range(1, len(scores))]
    if changes:
        change_threshold  = np.std(changes) * 1.5
        results['change_points'] = {
            'significant_changes': [(i + 1, c) for i, c in enumerate(changes) if abs(c) > change_threshold],
            'change_threshold':    change_threshold,
            'total_changes':       len(changes),
            'positive_changes':    sum(1 for c in changes if c > 0),
            'negative_changes':    sum(1 for c in changes if c < 0),
        }

    return results


# ---------------------------------------------------------------------------
# Trend stats summary — unchanged from original, consumed by HTML template
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

    period_start  = dates[0].strftime('%d %b %Y')
    period_end    = dates[-1].strftime('%d %b %Y')
    score_start   = round(float(scores[0]),  2)
    score_end     = round(float(scores[-1]), 2)
    score_movement = round(score_end - score_start, 2)

    if score_end >= 80:
        score_band = 'Cyber Resilient'
    elif score_end >= 65:
        score_band = 'Near Ready'
    elif score_end >= 45:
        score_band = 'Significant Gaps'
    else:
        score_band = 'Critical Risk'

    avg_score    = round(float(np.mean(scores)),   2)
    median_score = round(float(np.median(scores)), 2)
    best_score   = round(float(max(scores)),       2)
    worst_score  = round(float(min(scores)),       2)
    volatility   = round(float(stats_block.get('volatility', np.std(scores))), 2)
    r_squared    = round(float(linear.get('r_squared', 0)), 3)
    p_value      = round(float(linear.get('p_value',   1)), 4)
    slope        = float(linear.get('slope', 0))
    trend_direction = 'improving' if slope > 0 else 'declining'

    total_changes     = int(cp.get('total_changes',  0))
    positive_changes  = int(cp.get('positive_changes', 0))
    negative_changes  = int(cp.get('negative_changes', 0))
    significant_events = int(len(cp.get('significant_changes', [])))
    positive_days_pct = round(positive_changes / total_changes * 100, 1) if total_changes else 0.0

    recent    = data[-1]
    tenant_id = recent.get('azureTenantId') or json_data.get('tenant_id', '')

    avg_all_tenants   = None
    avg_similar_seats = None
    for entry in (recent.get('averageComparativeScores') or []):
        basis = entry.get('basis')
        if basis == 'AllTenants':
            avg_all_tenants   = float(entry['averageScore'])
        elif basis == 'TotalSeats':
            avg_similar_seats = float(entry['averageScore'])

    return {
        'period_start':            period_start,
        'period_end':              period_end,
        'score_start_pct':         score_start,
        'score_end_pct':           score_end,
        'score_movement':          score_movement,
        'score_band':              score_band,
        'current_score_pts':       float(recent.get('currentScore', score_end)),
        'max_score_pts':           float(recent.get('maxScore', 100)),
        'tenant_id':               tenant_id,
        'avg_score':               avg_score,
        'median_score':            median_score,
        'best_score':              best_score,
        'worst_score':             worst_score,
        'volatility':              volatility,
        'data_points':             len(scores),
        'r_squared':               r_squared,
        'p_value':                 p_value,
        'trend_direction':         trend_direction,
        'positive_days_count':     positive_changes,
        'positive_days_pct':       positive_days_pct,
        'total_daily_changes':     total_changes,
        'negative_days_count':     negative_changes,
        'significant_events':      significant_events,
        'avg_score_all_tenants':   avg_all_tenants,
        'avg_score_similar_seats': avg_similar_seats,
    }


# ---------------------------------------------------------------------------
# SVG chart coordinate helpers
# ---------------------------------------------------------------------------

def _make_score_to_y(score_min_disp, score_max_disp):
    score_range = max(score_max_disp - score_min_disp, 0.001)
    def fn(s):
        # y=200 maps to score_min_disp (bottom), y=40 maps to score_max_disp (top)
        return 200.0 - (s - score_min_disp) / score_range * 160.0
    return fn


def _make_date_to_x(ts_min, ts_range):
    def fn(dt):
        if ts_range == 0:
            return 60.0
        return 60.0 + (dt.timestamp() - ts_min) / ts_range * 670.0
    return fn


# ---------------------------------------------------------------------------
# SVG chart data computation
# ---------------------------------------------------------------------------

def compute_svg_chart_data(scores, dates, trend_analysis):
    """Compute all SVG coordinate data for the three charts in the HTML template."""

    # ── Chart 1: Trend line (viewBox 0 0 760 260, chart area x∈[60,730] y∈[40,200]) ──

    # Snap score display bounds to nearest 5% for clean grid labels
    score_data_min = min(scores)
    score_data_max = max(scores)
    score_min_disp = max(0.0,   math.floor((score_data_min - 5) / 5) * 5)
    score_max_disp = min(100.0, math.ceil( (score_data_max + 5) / 5) * 5)

    score_to_y = _make_score_to_y(score_min_disp, score_max_disp)
    ts_min   = dates[0].timestamp()
    ts_range = dates[-1].timestamp() - ts_min
    date_to_x = _make_date_to_x(ts_min, ts_range)

    # SVG path points
    points = [(date_to_x(d), score_to_y(s)) for d, s in zip(dates, scores)]

    score_path = 'M ' + ' L '.join(f'{x:.1f},{y:.1f}' for x, y in points)
    area_path  = (
        score_path
        + f' L {points[-1][0]:.1f},200'
        + f' L {points[0][0]:.1f},200 Z'
    )

    mavg_values = trend_analysis['moving_average']['trend_line']
    mavg_points = [(date_to_x(d), score_to_y(float(v))) for d, v in zip(dates, mavg_values)]
    mavg_path   = 'M ' + ' L '.join(f'{x:.1f},{y:.1f}' for x, y in mavg_points)

    lin_trend = trend_analysis['linear']['trend_line']
    trend_x0, trend_y0 = points[0][0],  score_to_y(float(lin_trend[0]))
    trend_x1, trend_y1 = points[-1][0], score_to_y(float(lin_trend[-1]))

    # Y-axis: 5 equally spaced grid lines at y=40,80,120,160,200
    y_grid  = [40, 80, 120, 160, 200]
    y_labels = [
        {
            'x':    52,
            'y':    y + 4,
            'text': f'{score_max_disp - i * (score_max_disp - score_min_disp) / 4:.0f}%',
        }
        for i, y in enumerate(y_grid)
    ]

    # X-axis: 6 evenly spaced date labels
    n = len(dates)
    x_label_indices = [int(i * (n - 1) / 5) for i in range(6)]
    x_labels = [
        {
            'x':    round(date_to_x(dates[i])),
            'text': f'{dates[i].day} {dates[i].strftime("%b")}',
        }
        for i in x_label_indices
    ]

    # Milestone markers: top 3 significant change points by absolute magnitude
    sig_changes = trend_analysis.get('change_points', {}).get('significant_changes', [])
    top_changes = sorted(sig_changes, key=lambda t: abs(t[1]), reverse=True)[:3]
    milestones  = [
        {
            'x':     round(date_to_x(dates[idx])),
            'label': f'{dates[idx].day} {dates[idx].strftime("%b")}',
        }
        for idx, _ in top_changes
        if 0 < idx < len(dates)
    ]

    # Start / end annotation circles
    start_cx, start_cy = round(points[0][0]),  round(points[0][1])
    end_cx,   end_cy   = round(points[-1][0]), round(points[-1][1])
    # Position end label above the end point to avoid overlap
    end_label_x = max(start_cx + 30, end_cx - 40)
    end_label_y = max(20, end_cy - 10)

    score_delta    = scores[-1] - scores[0]
    direction_word = 'increased' if score_delta >= 0 else 'decreased'
    trend_caption  = (
        f'Secure Score {direction_word} by {abs(score_delta):.1f} percentage points during the period. '
        f'R² = {trend_analysis["linear"]["r_squared"]:.3f}.'
    )

    trend_chart = {
        'score_path':   score_path,
        'area_path':    area_path,
        'mavg_path':    mavg_path,
        'trend_x0':     f'{trend_x0:.1f}',
        'trend_y0':     f'{trend_y0:.1f}',
        'trend_x1':     f'{trend_x1:.1f}',
        'trend_y1':     f'{trend_y1:.1f}',
        'y_labels':     y_labels,
        'x_labels':     x_labels,
        'milestones':   milestones,
        'start_cx':     start_cx,
        'start_cy':     start_cy,
        'end_cx':       end_cx,
        'end_cy':       end_cy,
        'end_label_x':  end_label_x,
        'end_label_y':  end_label_y,
        'caption':      trend_caption,
        'aria_label':   (
            f'Secure Score trend chart from {score_data_min:.0f} percent '
            f'to {score_data_max:.0f} percent'
        ),
    }

    # ── Chart 2: Distribution histogram (viewBox 0 0 365 170) ──
    # Bars: x starts at 54, step=34, width=26; floor y=130, ceiling y=28

    n_bins = min(8, max(3, len(scores) // 10))
    hist_counts, bin_edges = np.histogram(scores, bins=n_bins)
    max_count  = max(hist_counts) if max(hist_counts) > 0 else 1
    usable_h   = 102  # 130 - 28

    dist_bars = []
    for i, count in enumerate(hist_counts):
        bar_h = max(1, int(count / max_count * usable_h))
        dist_bars.append({
            'x': 54 + i * 34,
            'y': 130 - bar_h,
            'w': 26,
            'h': bar_h,
        })

    bin_total_width = n_bins * 34
    def score_to_dist_x(s):
        bin_range = bin_edges[-1] - bin_edges[0]
        if bin_range == 0:
            return 54
        return 54 + (s - bin_edges[0]) / bin_range * bin_total_width

    mean_val   = float(np.mean(scores))
    median_val = float(np.median(scores))
    mean_x   = round(score_to_dist_x(mean_val))
    median_x = round(score_to_dist_x(median_val))

    # X-axis labels: every 2nd bar
    dist_x_labels = [
        {'x': 54 + i * 34, 'text': f'{bin_edges[i]:.0f}%'}
        for i in range(0, n_bins, 2)
    ]

    dist_chart = {
        'bars':      dist_bars,
        'mean_x':   mean_x,
        'median_x': median_x,
        'x_labels': dist_x_labels,
        'data_pts_label': f'{len(scores)} points',
    }

    # ── Chart 3: Daily changes waterfall (viewBox 0 0 365 170, midline y=84) ──

    changes     = [scores[i] - scores[i - 1] for i in range(1, len(scores))]
    n_changes   = len(changes)
    total_width = 298.0   # x from 42 to 340

    if n_changes > 0:
        x_step  = total_width / n_changes
        bar_w   = max(1, int(x_step * 0.7))
        max_abs = max(abs(c) for c in changes)
        px_per_pct = min(8.0, 40.0 / max(max_abs, 0.001))
    else:
        x_step = total_width
        bar_w  = 5
        px_per_pct = 8.0
        max_abs = 0

    positive_bars = []
    negative_bars = []
    for i, c in enumerate(changes):
        bx   = round(42 + i * x_step)
        bh   = max(1, round(abs(c) * px_per_pct))
        if c >= 0:
            positive_bars.append({'x': bx, 'y': 84 - bh, 'w': bar_w, 'h': bh})
        else:
            negative_bars.append({'x': bx, 'y': 84, 'w': bar_w, 'h': bh})

    # Y-axis labels derived from max absolute change
    y_tick_val = round(max_abs) if max_abs >= 1 else 1
    y_label_pos_y = 84 - round(y_tick_val * px_per_pct)
    y_label_neg_y = 84 + round(y_tick_val * px_per_pct)

    # Month labels: first occurrence of each month across changes' dates
    month_labels = []
    seen_months  = set()
    for i, dt in enumerate(dates[1:]):
        m_key = (dt.year, dt.month)
        if m_key not in seen_months:
            seen_months.add(m_key)
            month_labels.append({
                'x':    round(42 + i * x_step),
                'text': dt.strftime('%b'),
            })

    pos_count = sum(1 for c in changes if c > 0)
    neg_count = sum(1 for c in changes if c <= 0)
    changes_caption = (
        f'Positive movement outweighed regression days, with {pos_count} positive changes '
        f'and {neg_count} declines recorded across the review window.'
        if pos_count > neg_count else
        f'{pos_count} positive and {neg_count} negative daily changes recorded across the review window.'
    )

    changes_chart = {
        'positive_bars':   positive_bars,
        'negative_bars':   negative_bars,
        'y_label_pos_y':   y_label_pos_y,
        'y_label_neg_y':   y_label_neg_y,
        'y_label_pos_val': f'+{y_tick_val}',
        'y_label_neg_val': f'-{y_tick_val}',
        'month_labels':    month_labels,
        'caption':         changes_caption,
    }

    return {
        'trend_chart':   trend_chart,
        'dist_chart':    dist_chart,
        'changes_chart': changes_chart,
    }


# ---------------------------------------------------------------------------
# Category breakdown for Page 4
# ---------------------------------------------------------------------------

def compute_category_breakdown(control_profiles):
    """
    Returns a list of category dicts for the Score Opportunity table.
    Uses 'score' or 'implementedScore' from each control profile if present;
    otherwise reports 0 current points (the common case from the Graph API).
    """
    cat_max     = defaultdict(float)
    cat_current = defaultdict(float)

    for cp in control_profiles:
        cat = cp.get('controlCategory') or 'Other'
        cat_max[cat]     += float(cp.get('maxScore') or 0)
        cat_current[cat] += float(cp.get('score') or cp.get('implementedScore') or 0)

    breakdown = []
    for cat in sorted(cat_max, key=lambda c: cat_max[c], reverse=True):
        max_pts     = cat_max[cat]
        current_pts = cat_current[cat]
        remaining   = max_pts - current_pts
        pct         = (current_pts / max_pts * 100) if max_pts > 0 else 0.0
        bar_class   = '' if pct >= 70 else ('warn' if pct >= 50 else 'danger')
        breakdown.append({
            'name':          cat,
            'current_pts':   int(current_pts),
            'max_pts':       int(max_pts),
            'remaining_pts': int(remaining),
            'pct_complete':  round(pct, 1),
            'bar_class':     bar_class,
        })

    return breakdown


# ---------------------------------------------------------------------------
# Control severity helper
# ---------------------------------------------------------------------------

def _derive_control_severity(cp):
    """Returns (css_class, label) for a control profile based on score/threats."""
    pts     = float(cp.get('maxScore') or 0)
    threats = set(cp.get('threats') or [])
    cost    = (cp.get('implementationCost') or '').lower()

    high_risk = {'accountBreach', 'elevationOfPrivilege', 'phishingOrWhaling'}
    has_high  = bool(threats & high_risk)

    if pts >= 9 or (has_high and cost == 'low'):
        return 'critical', 'Critical'
    elif pts >= 6 or has_high:
        return 'high', 'High'
    elif pts >= 4:
        return 'medium', 'Medium'
    else:
        return 'low', 'Low'


# ---------------------------------------------------------------------------
# Narrative helpers — auto-generate from data
# ---------------------------------------------------------------------------

def _build_management_bullets(trend_stats, trend_analysis, control_profiles):
    """Return 4-5 factual management summary bullets derived from real data."""
    bullets = []

    # 1. Trend direction and R²
    direction = trend_stats.get('trend_direction', 'improving')
    movement  = trend_stats.get('score_movement', 0)
    r2        = trend_stats.get('r_squared', 0)
    sign      = '+' if movement >= 0 else ''
    bullets.append(
        f'The tenant’s Secure Score {direction} by {sign}{movement} percentage points '
        f'across the reporting period, with a linear R² of {r2:.3f} indicating '
        f'{"consistent" if r2 > 0.5 else "variable"} progress.'
    )

    # 2. Top Identity control (opportunity or completion)
    identity_ctrls = sorted(
        [c for c in (control_profiles or []) if (c.get('controlCategory') or '').lower() == 'identity'],
        key=lambda c: c.get('rank') or 9999,
    )
    if identity_ctrls:
        top_id = identity_ctrls[0]
        bullets.append(
            f'The highest-priority Identity control is “{top_id.get("title", "")}” '
            f'({top_id.get("maxScore", 0):.0f} points), with '
            f'{"low" if (top_id.get("implementationCost") or "").lower() == "low" else "moderate"} '
            f'implementation effort.'
        )

    # 3. Change-point summary
    sig_events = trend_stats.get('significant_events', 0)
    pos_days   = trend_stats.get('positive_days_count', 0)
    total      = trend_stats.get('total_daily_changes', 1) or 1
    pos_pct    = trend_stats.get('positive_days_pct', 0)
    bullets.append(
        f'{sig_events} significant score event{"s" if sig_events != 1 else ""} were detected '
        f'(large single-day changes). Positive movement was recorded on {pos_pct:.0f}% '
        f'of days ({pos_days} of {total} daily changes).'
    )

    # 4. Largest remaining opportunity category
    cats = compute_category_breakdown(control_profiles or [])
    if cats:
        top_cat = max(cats, key=lambda c: c['remaining_pts'])
        bullets.append(
            f'The largest remaining Secure Score opportunity is in the '
            f'{top_cat["name"]} category, with {top_cat["remaining_pts"]} points '
            f'of improvement potential identified.'
        )

    # 5. Benchmark comparison (if available)
    avg_all = trend_stats.get('avg_score_all_tenants')
    if avg_all is not None:
        current = trend_stats.get('score_end_pct', 0)
        diff    = current - avg_all
        sign    = 'above' if diff >= 0 else 'below'
        bullets.append(
            f'The current score of {current}% is {abs(diff):.1f} percentage points '
            f'{sign} the Microsoft benchmark average across all tenants ({avg_all:.1f}%).'
        )

    return bullets


def _build_trend_callouts(trend_stats, trend_analysis, control_profiles):
    """Return 3 trend callout dicts with title and text keys."""
    sig_changes = trend_analysis.get('change_points', {}).get('significant_changes', [])

    # Largest single-day gain
    positive_events = [(idx, val) for idx, val in sig_changes if val > 0]
    if positive_events:
        best_idx, best_val = max(positive_events, key=lambda t: t[1])
        callout_1 = {
            'title': 'Largest gain',
            'text':  f'A single-day improvement of +{best_val:.1f}pp was the most significant upward change in the period.',
        }
    else:
        direction = trend_stats.get('trend_direction', 'improving')
        callout_1 = {
            'title': 'Trend direction',
            'text':  f'The overall trend is {direction}, consistent with deliberate remediation activity.',
        }

    # Largest single-day regression
    negative_events = [(idx, val) for idx, val in sig_changes if val < 0]
    if negative_events:
        worst_idx, worst_val = min(negative_events, key=lambda t: t[1])
        callout_2 = {
            'title': 'Main regression',
            'text':  f'A single-day decline of {worst_val:.1f}pp was the largest downward movement detected.',
        }
    else:
        callout_2 = {
            'title': 'Stability',
            'text':  'No significant score regressions were detected during the reporting period.',
        }

    # Next improvement opportunity
    cats = compute_category_breakdown(control_profiles or [])
    if cats:
        top_cat = max(cats, key=lambda c: c['remaining_pts'])
        callout_3 = {
            'title': 'Next opportunity',
            'text':  f'{top_cat["name"]} controls represent the largest remaining improvement area with {top_cat["remaining_pts"]} points available.',
        }
    else:
        callout_3 = {
            'title': 'Next opportunity',
            'text':  'Review open security controls in the Microsoft 365 Security Centre to identify the next remediation priorities.',
        }

    return [callout_1, callout_2, callout_3]


def _build_priority_boxes(category_breakdown):
    """Return top 3 category priority boxes for Page 4."""
    sorted_cats = sorted(category_breakdown, key=lambda c: c['remaining_pts'], reverse=True)
    css_classes = ['danger', 'warn', '']
    titles      = ['1. Priority: ', '2. Focus: ', '3. Consider: ']
    boxes       = []
    for i, cat in enumerate(sorted_cats[:3]):
        boxes.append({
            'title':     f'{i + 1}. {cat["name"]}',
            'text':      (
                f'{cat["remaining_pts"]} points remaining '
                f'({cat["pct_complete"]}% complete). '
                f'Review controls in this category to improve the tenant’s Secure Score.'
            ),
            'css_class': css_classes[i],
        })
    return boxes


# ---------------------------------------------------------------------------
# HTML report rendering
# ---------------------------------------------------------------------------

_BAND_CSS_MAP = {
    'Cyber Resilient':  'cyber_resilient',
    'Near Ready':       'near_ready',
    'Significant Gaps': 'significant_gaps',
    'Critical Risk':    'critical_risk',
}

_BAND_GAUGE_COLOR = {
    'Cyber Resilient':  '#22c55e',
    'Near Ready':       '#f59e0b',
    'Significant Gaps': '#ef4444',
    'Critical Risk':    '#7f1d1d',
}

_BAND_DESC = {
    'Cyber Resilient':  (
        'Core Microsoft 365 security controls are mature and consistently enforced. '
        'Continued monitoring and incremental hardening is recommended.'
    ),
    'Near Ready':       (
        'The security foundation is mostly in place. Remaining medium-priority controls '
        'should be completed to reach a strong security posture.'
    ),
    'Significant Gaps': (
        'Multiple high-value controls remain incomplete. Remediation should be treated '
        'as a managed, prioritised workstream with clear milestones.'
    ),
    'Critical Risk':    (
        'Foundational protections are missing or inconsistently configured. '
        'Immediate remediation of identity and endpoint controls is recommended.'
    ),
}

_COST_CSS = {'low': 'low', 'moderate': 'mod', 'high': 'high'}
_IMPACT_CSS = {'low': 'low', 'moderate': 'mod', 'high': 'high'}


def render_html_report(json_data, trend_analysis, control_profiles):
    """Render the Jinja2 report template with computed data."""
    data   = json_data['result']
    scores = [(item['currentScore'] / item['maxScore']) * 100 for item in data]
    dates  = [item['date'] for item in data]

    tenant_name    = json_data.get('tenant_name', 'Customer Tenant')
    trend_stats    = build_trend_stats(json_data, trend_analysis)
    generated_date = datetime.now().strftime('%d %b %Y')
    generated_iso  = datetime.now().strftime('%Y-%m-%d')

    # Score gauge
    score_pct  = trend_stats['score_end_pct']
    circumference = 2 * math.pi * 42   # ≈ 263.894
    dashoffset    = circumference * (1 - score_pct / 100)
    band          = trend_stats['score_band']

    # Band-derived text
    band_desc      = _BAND_DESC.get(band, '')
    movement       = trend_stats['score_movement']
    direction_word = 'improving' if movement >= 0 else 'declining'
    band_callout_text = (
        f'The tenant’s Secure Score has been {direction_word} during this period. '
        f'Current classification: {band}. '
        f'Priority remediation areas are identified in the recommendations section below.'
    )

    # SVG charts
    svg_data = compute_svg_chart_data(scores, dates, trend_analysis)

    # Category breakdown
    category_breakdown = compute_category_breakdown(control_profiles or [])

    # Controls for recommendations table — sorted by rank
    sorted_controls = sorted(control_profiles or [], key=lambda c: c.get('rank') or 9999)
    controls_for_table = []
    for cp in sorted_controls:
        sev_css, sev_label = _derive_control_severity(cp)
        threats: list[str] = cp.get('threats') or []
        threats_str = ', '.join(THREAT_LABELS.get(t, t) for t in threats[:3])
        if len(threats) > 3:
            threats_str += f' +{len(threats) - 3}'
        impl_cost = (cp.get('implementationCost') or 'low').lower()
        user_impact = (cp.get('userImpact') or 'low').lower()
        controls_for_table.append({
            **cp,
            'severity_css':   sev_css,
            'severity_label': sev_label,
            'threats_str':    threats_str or '—',
            'cost_css':       _COST_CSS.get(impl_cost, 'low'),
            'impact_css':     _IMPACT_CSS.get(user_impact, 'low'),
        })

    # Remediation pages — 5 cards per page
    CARDS_PER_PAGE = 5
    section_titles = [
        'Identity and Access Controls',
        'Endpoint, Data Protection and Application Controls',
        'Additional Security Controls',
    ]
    remediation_pages = []
    base_page_number  = 6   # Pages 1-5 are: cover, exec summary, trend, opportunity, recommendations
    for i in range(0, len(sorted_controls), CARDS_PER_PAGE):
        chunk      = sorted_controls[i: i + CARDS_PER_PAGE]
        page_index = i // CARDS_PER_PAGE
        cards      = []
        for cp in chunk:
            sev_css, sev_label = _derive_control_severity(cp)
            cards.append({**cp, 'severity_css': sev_css, 'severity_label': sev_label})
        remediation_pages.append({
            'section_title': section_titles[min(page_index, len(section_titles) - 1)],
            'controls':      cards,
            'page_number':   base_page_number + page_index,
        })

    methodology_page_number = base_page_number + len(remediation_pages)

    # Narrative helpers
    management_bullets = _build_management_bullets(trend_stats, trend_analysis, control_profiles)
    trend_callouts     = _build_trend_callouts(trend_stats, trend_analysis, control_profiles)
    priority_boxes     = _build_priority_boxes(category_breakdown)

    remaining_pts = trend_stats['max_score_pts'] - trend_stats['current_score_pts']

    context = {
        # Identity
        'tenant_name':          tenant_name,
        'tenant_id':            trend_stats.get('tenant_id', ''),
        'generated_date':       generated_date,
        'generated_iso':        generated_iso,
        'period_start':         trend_stats['period_start'],
        'period_end':           trend_stats['period_end'],
        # Gauge
        'gauge_dashoffset':     f'{dashoffset:.2f}',
        'gauge_color':          _BAND_GAUGE_COLOR.get(band, '#ef4444'),
        # Score
        'score_end_pct':        score_pct,
        'score_start_pct':      trend_stats['score_start_pct'],
        'score_movement':       movement,
        'current_score_pts':    trend_stats['current_score_pts'],
        'max_score_pts':        trend_stats['max_score_pts'],
        'remaining_pts':        remaining_pts,
        # Band
        'score_band':           band,
        'band_css_class':       _BAND_CSS_MAP.get(band, 'significant_gaps'),
        'band_description':     band_desc,
        'band_callout_text':    band_callout_text,
        # Stats
        'avg_score':            trend_stats['avg_score'],
        'median_score':         trend_stats['median_score'],
        'best_score':           trend_stats['best_score'],
        'worst_score':          trend_stats['worst_score'],
        'volatility':           trend_stats['volatility'],
        'data_points':          trend_stats['data_points'],
        'r_squared':            trend_stats['r_squared'],
        'p_value':              trend_stats['p_value'],
        'trend_direction':      trend_stats['trend_direction'],
        'positive_days_count':  trend_stats['positive_days_count'],
        'positive_days_pct':    trend_stats['positive_days_pct'],
        'total_daily_changes':  trend_stats['total_daily_changes'],
        'negative_days_count':  trend_stats['negative_days_count'],
        'significant_events':   trend_stats['significant_events'],
        # Narrative
        'management_bullets':   management_bullets,
        'trend_callouts':       trend_callouts,
        'priority_boxes':       priority_boxes,
        # Charts
        'trend_chart':          svg_data['trend_chart'],
        'dist_chart':           svg_data['dist_chart'],
        'changes_chart':        svg_data['changes_chart'],
        # Page data
        'category_breakdown':   category_breakdown,
        'controls':             controls_for_table,
        'remediation_pages':    remediation_pages,
        'methodology_page_number': methodology_page_number,
    }

    template_dir = os.path.dirname(os.path.abspath(__file__))
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=False,   # SVG path strings must NOT be escaped
    )
    template = env.get_template('report_template.html')
    return template.render(**context)


# ---------------------------------------------------------------------------
# Digest template rendering
# ---------------------------------------------------------------------------

def render_digest_report(json_data, trend_analysis):
    """Render the weekly digest template with computed data."""
    data   = json_data['result']
    scores = [(item['currentScore'] / item['maxScore']) * 100 for item in data]
    dates  = [item['date'] for item in data]

    tenant_name    = json_data.get('tenant_name', 'Customer Tenant')
    trend_stats    = build_trend_stats(json_data, trend_analysis)
    generated_date = datetime.now().strftime('%d %b %Y %H:%M')

    score_pct     = trend_stats['score_end_pct']
    band          = trend_stats['score_band']
    movement      = trend_stats['score_movement']
    
    # Determine band info
    band_slug_map = {
        'Cyber Resilient': 'resilient',
        'Near Ready': 'near',
        'Significant Gaps': 'gaps',
        'Critical Risk': 'critical',
    }
    band_slug = band_slug_map.get(band, 'gaps')
    
    # Determine movement direction
    if movement > 0:
        delta_direction = 'up'
        score_movement_sign = '+'
        delta_class = 'good'
    elif movement < 0:
        delta_direction = 'down'
        score_movement_sign = '-'
        delta_class = 'bad'
    else:
        delta_direction = 'flat'
        score_movement_sign = ''
        delta_class = 'warn'
    
    delta_arrow = '↑' if delta_direction == 'up' else ('↓' if delta_direction == 'down' else '→')

    # SVG chart data
    svg_data = compute_svg_chart_data(scores, dates, trend_analysis)

    # Best/worst day labels
    best_idx = scores.index(max(scores))
    best_day_label = dates[best_idx].strftime('%a, %d %b')
    
    # Peer comparison defaults (if not available in data)
    avg_score_similar_seats = trend_stats.get('avg_score_similar_seats') or score_pct
    peer_delta = 0
    peer_delta_sign = '±'
    peer_direction = 'vs.'

    # Band description
    band_description = _BAND_DESC.get(band, '')

    context = {
        'tenant_name':          tenant_name,
        'tenant_id':            trend_stats.get('tenant_id', ''),
        'generated_at':         generated_date,
        'period_end':           trend_stats['period_end'],
        'data_points':          trend_stats['data_points'],
        'score_end_pct':        f"{score_pct:.0f}",
        'current_score_pts':    trend_stats['current_score_pts'],
        'max_score_pts':        trend_stats['max_score_pts'],
        'band_label':           band,
        'band_slug':            band_slug,
        'band_description':     band_description,
        'score_movement_sign':  score_movement_sign,
        'score_movement':       abs(movement),
        'delta_direction':      delta_direction,
        'delta_arrow':          delta_arrow,
        'delta_class':          delta_class,
        'avg_score_similar_seats': f"{avg_score_similar_seats:.1f}",
        'peer_delta_sign':      peer_delta_sign,
        'peer_delta':           peer_delta,
        'peer_direction':       peer_direction,
        'best_score':           f"{max(scores):.0f}",
        'best_day_label':       best_day_label,
        'worst_score':          f"{min(scores):.0f}",
        'significant_events':   trend_stats['significant_events'],
        'trend_chart':          svg_data['trend_chart'],
        'notable_changes':      [],  # Optional; can be populated from trend_analysis if needed
    }

    template_dir = os.path.dirname(os.path.abspath(__file__))
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=False,
    )
    template = env.get_template('digest.html')
    return template.render(**context)


# ---------------------------------------------------------------------------
# Playwright PDF rendering
# ---------------------------------------------------------------------------

_chromium_installed = False


async def _playwright_render(html_content):
    global _chromium_installed
    if not _chromium_installed:
        result = subprocess.run(
            ['playwright', 'install', 'chromium'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logging.warning('playwright install chromium: %s', result.stderr)
        _chromium_installed = True

    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.html', delete=False, encoding='utf-8'
    ) as f:
        f.write(html_content)
        tmp_path = f.name

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox'],
            )
            page = await browser.new_page()
            # file:// URI with networkidle allows Google Fonts @import to load
            await page.goto(
                f"file:///{tmp_path.replace(os.sep, '/')}",
                wait_until='networkidle',
                timeout=60_000,
            )
            pdf_bytes = await page.pdf(
                format='A4',
                print_background=True,
                margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'},
            )
            await browser.close()
            return pdf_bytes
    finally:
        os.unlink(tmp_path)


def generate_pdf_with_playwright(html_content):
    return asyncio.run(_playwright_render(html_content))


# ---------------------------------------------------------------------------
# Azure Function entry point
# ---------------------------------------------------------------------------

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('secure_score_graph function triggered.')

    try:
        if req.method == 'GET':
            html_content = """<!DOCTYPE html>
<html>
<head><title>Secure Score PDF Generator</title>
<style>
  body{font-family:Arial,sans-serif;margin:40px;background:#f5f5f5}
  .card{max-width:820px;margin:0 auto;background:#fff;padding:32px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)}
  h1{color:#1b4d62}h2{color:#e4007c;font-size:1rem}
  textarea{width:100%;height:180px;font-family:monospace;font-size:13px;border:1px solid #ccc;border-radius:4px;padding:8px}
  button{padding:10px 24px;background:#1b4d62;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:14px}
  button:hover{background:#3abfcf}
  pre{background:#f4f4f4;padding:12px;border-radius:4px;overflow:auto;font-size:12px}
</style>
</head>
<body><div class="card">
<h1>Microsoft Secure Score &mdash; PDF Report Generator</h1>
<h2>POST body schema</h2>
<pre>{
  "tenant_name": "Contoso Ltd",
  "result": [
    { "createdDateTime": "2024-01-01T00:00:00Z", "currentScore": 75, "maxScore": 100 }
  ],
  "control_profiles": [
    {
      "rank": 1, "title": "Enable MFA", "controlCategory": "Identity",
      "maxScore": 9, "implementationCost": "low", "userImpact": "moderate",
      "threats": ["accountBreach","phishingOrWhaling"],
      "remediation": "Require MFA for all users via Conditional Access.",
      "remediationImpact": "Users will be prompted for MFA on first sign-in.",
      "actionUrl": "https://portal.azure.com/#blade/...", "tier": "Core", "service": "AAD"
    }
  ]
}</pre>
<p><code>control_profiles</code> is optional. When omitted the recommendations section is skipped.</p>
<form id="f">
  <textarea id="j" placeholder="Paste JSON here"></textarea><br><br>
  <button type="submit">Generate PDF Report</button>
</form>
<div id="out"></div>
</div>
<script>
document.getElementById('f').onsubmit=async e=>{
  e.preventDefault();
  const r=await fetch('/api/secure-score-graph',{method:'POST',headers:{'Content-Type':'application/json'},body:document.getElementById('j').value});
  if(r.ok){const d=await r.json();document.getElementById('out').innerHTML='<p><b>Done.</b> Filename: '+d.filename+'</p>';}
  else{document.getElementById('out').textContent='Error: '+await r.text();}
};
</script>
</body></html>"""
            return func.HttpResponse(body=html_content, mimetype='text/html', status_code=200)

        json_data = req.get_json()
        if not json_data or 'result' not in json_data:
            return func.HttpResponse(
                "Provide JSON with a 'result' array of secureScore entries.",
                status_code=400,
            )

        control_profiles = json_data.get('control_profiles') or []

        # Parse and sort dates (mutates json_data['result'] in place)
        data = json_data['result']
        for item in data:
            item['date'] = datetime.strptime(
                item['createdDateTime'].split('T')[0], '%Y-%m-%d'
            )
        data.sort(key=lambda x: x['date'])

        scores = [(item['currentScore'] / item['maxScore']) * 100 for item in data]
        dates  = [item['date'] for item in data]

        trend_analysis = analyze_security_trends(scores, dates)
        
        # Check for weekly digest mode
        is_weekly = req.params.get('weekly', '').lower() == 'true'
        
        if is_weekly:
            html_content = render_digest_report(json_data, trend_analysis)
            report_type = 'digest'
        else:
            trend_stats  = build_trend_stats(json_data, trend_analysis)
            html_content = render_html_report(json_data, trend_analysis, control_profiles)
            report_type = 'full'
        
        pdf_bytes      = generate_pdf_with_playwright(html_content)

        pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
        response_data = {
            'pdf_data':     pdf_base64,
            'filename':     f"secure_score_{'weekly_digest' if is_weekly else 'report'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            'content_type': 'application/pdf',
            'report_type':  report_type,
        }
        if not is_weekly:
            response_data['trend_stats'] = build_trend_stats(json_data, trend_analysis)
        
        return func.HttpResponse(
            body=json.dumps(response_data),
            mimetype='application/json',
            status_code=200,
        )

    except Exception as e:
        logging.error(f'Error: {e}', exc_info=True)
        return func.HttpResponse(f'Error: {e}', status_code=500)
