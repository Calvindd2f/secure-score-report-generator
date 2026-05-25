# Weekly Secure Score Report — Design Spec
**Date:** 2026-05-26  
**Branch:** weekly  
**Status:** Approved

---

## Overview

Add a weekly "quick update" report mode to the existing Azure Function. The quarterly report remains untouched. A new `report_type: "weekly"` flag in the POST body routes to a new 2-page PDF that shows: score snapshot, regression alert, and top 3 controls to action.

---

## Data Input

POST body format (same as quarterly, with two additions):

```json
{
  "tenant_name": "Contoso Ltd",
  "report_type": "weekly",
  "result": [
    { "createdDateTime": "2024-01-14T00:00:00Z", "currentScore": 312, "maxScore": 445, "averageComparativeScores": [...] },
    { "createdDateTime": "2024-01-21T00:00:00Z", "currentScore": 305, "maxScore": 445, "averageComparativeScores": [...] }
  ],
  "control_profiles": [ ... ]
}
```

- `report_type: "weekly"` triggers the weekly renderer. Absent or any other value → existing quarterly flow, unchanged.
- `result` is sorted by date; the **last two entries** are used as `last_week` and `this_week`. Callers may pass more entries (e.g. full 90-day window) safely — the function extracts the relevant pair.
- `control_profiles` is optional. If absent, the top 3 controls section shows a placeholder message.
- No per-control regression diffing — only the top-level score delta is used to determine regression status.

---

## Page Layout

### Page 1 — Cover

Same visual language as the quarterly cover (dark hero header, gradient stripe, Intuity branding).

| Element | Detail |
|---|---|
| Header | Intuity Technologies wordmark + CONFIDENTIAL badge |
| Eyebrow | "Microsoft Secure Score" |
| Title | "Weekly Secure Score Update" |
| Subtitle | `{{ tenant_name }}` |
| Meta | Tenant ID, Generated date, Week of (formatted from `this_week.createdDateTime`) |
| Score gauge | Same SVG ring as quarterly, showing current score % |
| Delta display | Large `+2.1pp` / `-1.4pp` number below gauge, green if ≥ 0, red if negative |
| Band label | Cyber Resilient / Near Ready / Significant Gaps / Critical Risk + description |
| Footer | Confidentiality notice + intuity.ie |

### Page 2 — Weekly Summary

Three stacked sections separated by section titles.

**Section 1 — Score Snapshot (metric strip)**  
4 cards across: Current Score, Last Week Score, Week Movement, vs All Tenants (shown as "N/A" if `averageComparativeScores` absent).

**Section 2 — Regression Alert (callout box)**  
- Movement < 0: red left-border box, title "Score Regression This Week", text explains delta and directs to Microsoft 365 Security Centre.
- Movement ≥ 0: green/teal left-border box, title "Score Stable / Improving", brief positive confirmation.

**Section 3 — Top 3 Controls to Action**  
Three priority cards (one per control), showing:
- Rank number
- Control title
- Category badge
- Points value
- Severity badge (derived via existing `_derive_control_severity`)
- Remediation one-liner (from `remediation` field)

If `control_profiles` is empty/absent: show a short note directing to the Microsoft 365 Security Centre.

Footer: "Intuity Technologies — Microsoft Secure Score Report — `{{ tenant_name }}`" | "CONFIDENTIAL · Page N"

---

## Python Changes — `__init__.py`

### New: `compute_weekly_stats(json_data)`

Extracts the two relevant data points and returns a flat dict:

```python
{
  'tenant_name', 'tenant_id', 'generated_date', 'generated_iso',
  'week_of_label',           # e.g. "21 Jan 2025"
  'score_current_pct',       # (currentScore/maxScore)*100, rounded 2dp
  'score_last_pct',          # same for previous entry
  'score_movement',          # score_current_pct - score_last_pct, rounded 2dp
  'current_score_pts',       # raw currentScore int
  'max_score_pts',           # raw maxScore int
  'score_band',              # band label string
  'band_css_class',          # CSS modifier
  'band_description',        # band description string
  'gauge_color',             # hex color for SVG ring
  'gauge_dashoffset',        # computed SVG dashoffset string
  'avg_score_all_tenants',   # float or None
}
```

No scipy/sklearn used — arithmetic only.

### New: `render_weekly_html_report(json_data, control_profiles)`

- Calls `compute_weekly_stats(json_data)`
- Sorts `control_profiles` by `rank`, takes first 3
- Derives severity for each via existing `_derive_control_severity`
- Renders `weekly_template.html` via Jinja2 `FileSystemLoader` (same pattern as quarterly)
- Returns HTML string

### Modified: `main()` routing

```python
report_type = json_data.get('report_type', 'quarterly')
if report_type == 'weekly':
    html_content = render_weekly_html_report(json_data, control_profiles)
else:
    # existing quarterly path — untouched
    trend_analysis = analyze_security_trends(scores, dates)
    ...
    html_content = render_html_report(json_data, trend_analysis, control_profiles)
```

The PDF rendering path (`generate_pdf_with_playwright`) is shared — no change needed there.

### Not modified

`analyze_security_trends`, `build_trend_stats`, `compute_svg_chart_data`, `render_html_report`, `compute_category_breakdown`, `_derive_control_severity`, `_build_management_bullets`, `_build_trend_callouts`, `_build_priority_boxes` — all unchanged.

---

## New File: `secure_score_graph/weekly_template.html`

Self-contained Jinja2 template. Reuses all CSS variables and component classes from the quarterly template (copy the `:root` block and shared styles). Does not import or extend the quarterly template.

Template variables mirror `compute_weekly_stats` output plus `controls` (list of top 3 enriched control dicts) and `regression_mode` (bool).

---

## Out of Scope

- Per-control regression diffing (no `controlScores` diff between weeks)
- Email/Teams delivery
- Quarterly report modifications
- Historical trend chart (weekly format has no chart page)
