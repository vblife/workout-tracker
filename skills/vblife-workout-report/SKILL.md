---
name: vblife-workout-report
description: Use when the user requests a daily, weekly, monthly, quarterly, annual, or all-time workout and nutrition report, dashboard, recap, or scheduled daily or weekly report.
---
# Reports and dashboards

Call `report` with `day`, `week`, `month`, `quarter`, `year`, or `all` and an anchor date when supplied. Do not require setup first. Use the structured JSON from that call as the only source of numbers; never invent totals, trends, or goal rates.

## Primary delivery: Grok Bot Page (Canvas)

For every report period (`day`, `week`, `month`, `quarter`, `year`, or `all`), build an interactive Page with `WriteCanvas` (or hand the structured result to an executor that writes the Page). Follow the Cursor-managed `canvas` skill for kit rules, width, and chat delivery.

### Chat message

After the Page is written, send one short text summary in the user's language plus the Page link as a markdown label. Do not paste the full report body into chat. Do not attach a standalone HTML file for the report.

### Page shape (UX pattern)

Match the shipped weekly example titled like `Training week · <start>–<end>` (wide dashboard with Segmented training / nutrition views). Adapt the same layout to other periods:

1. `Page` with `width="wide"`, a period title, and a one- or two-sentence `summary` of the main takeaway.
2. Optional `TableOfContents` when there are about six or more sections.
3. One short `Callout` for the period highlight (tone only when it fits info / success / warning / danger).
4. Overview: two-column `Grid` of `Properties` for training totals and nutrient averages (or day totals for a daily report).
5. Trends: `Segmented` switching training vs nutrition, then nested `Segmented` for the metric or nutrient; `LineChart` / `ColumnSeries` from the period's trend points in the `report` JSON (six weekly points for week/month views, twelve monthly points for year, recent four-week rhythm for day).
6. Goal progress table with hit rates and `Status` pills when goals exist.
7. Period-over-period comparison when the tool returns a prior window.
8. Strength / mix / PRs / streaks / routine use when present; omit empty sections (never render placeholder empty states).
9. One short recommendations section grounded in the data.

Use `show_progress` only when the user asks for a focused lift chart; that can stay an MCP Apps or chart reply and does not replace the period Page.

### Daily vs weekly Pages

Daily (`period=day`) and weekly (`period=week`) reports use the same Page pattern: wide `Page`, the same Overview grid, and a `Segmented` training / nutrition switch. They differ in what they compare and emphasize.

| | Daily (`period=day`) | Weekly (`period=week`) |
|---|---|---|
| Title | `Training day · <date>` style | `Training week · <start>–<end>` style |
| Comparison | Today vs yesterday from `vs_previous` (day-over-day) | This week vs the prior week from `vs_previous` (week-over-week) |
| Overview | Day totals for training and nutrients | Training totals and per-day nutrient averages |
| Trends | Recent four-week rhythm from the `report` trend points | Six weekly points for training time, strength volume, and cardio duration |
| Goals | Distance to each daily target (how much is left, or how far over) | Goal hit rates across the week |
| Tips | Two or three short, concrete actions for tomorrow | Two or three short actions for the coming week |

Tips for both say what to do more or less of (for example more cardio minutes, one fewer hard session, more mobility) and which nutrients to eat more or less of (for example more protein, less added sugar, more fiber). Tips name nutrients and amounts only, never foods, dishes, or meals.

A scheduled daily report runs in the evening, so treat the day as possibly unfinished: phrase distance to goals as what is still open today, not as a missed target.

### Nutrition privacy on the Page

Same rules as `vblife-workout-nutrition`: nutrients and goals only. Never put food names, dish names, portions, meal descriptions, images, USDA queries, FDC ids, or raw meal text on the Page, in chart labels, in tips, or in chat. This applies equally to daily and weekly Pages.

### Page copy language

Write Page titles, headings, labels, and Callout text in the user's language (same as chat). Keep skill files themselves English.

## Still call `report`

Always call `report` first so:

- numbers come from the server, and
- a client that still supports MCP Apps can render the legacy in-app dashboard if the host offers it.

MCP Apps is optional bonus UI. The Page is the deliverable the user is meant to open and return to. Calling `report` does not replace writing the Page.

## Do not attach report HTML

Do not run `server/render_report_html.py` or send `/workspace/workout-report-*.html` for these reports. Pages open on desktop and phone, so the old HTML attachment fallback for mobile MCP Apps gaps is retired for reports. (HIIT and other countdown timers still use their own HTML template path in `vblife-workout-hiit-session`; that is unchanged.)

If `WriteCanvas` fails after retries, send the short text summary from the structured `report` result and say the Page could not be built. Do not fall back to inventing a custom HTML layout.

## Text summary content

Whether or not the Page writes cleanly, the short chat summary still covers:

1. Period and primary goal progress.
2. Sessions, training minutes, strength volume, cardio duration, distance, and estimated workout calories with confidence coverage.
3. Confirmed nutrition totals for a day or per-day averages, recorded-day count, and goal-hit rates for longer periods; keep consumed and burned energy separate.
4. One or two actionable recommendations.

Weekly reports explain the six weekly points for training time, strength volume, and cardio duration, plus the change vs the prior week. Monthly reports explain the same three metrics across six monthly points. Annual reports explain twelve monthly points. Daily reports cover today vs yesterday, distance to daily goals, and the recent four-week rhythm.

The chat summary is always in the user's language, stays short, and ends with the Page link. Never paste report HTML or the full Page body into chat.

All numbers and units come from tool output. When data is sparse or estimates are rough, say so. Empty periods remain useful: state that no completed workouts or confirmed nutrition entries exist and suggest a next step. For an empty day, still build the Page when there is any goal progress or a previous-day comparison to show. Skip building a Page only when there is truly nothing to show, and then say so in chat.
