---
name: vblife-workout-report
description: Use when the user requests a daily, weekly, monthly, quarterly, annual, or all-time workout and nutrition report, dashboard, recap, or scheduled daily or weekly report.
---
# Reports and dashboards

Call `report` with `day`, `week`, `month`, `quarter`, `year`, or `all` and an anchor date when supplied. Days and periods are user-local in the user's saved timezone; a missing anchor date means today's user-local date. Do not require setup first. If the result has `timezone_missing: true`, still deliver the report and ask the user once for their timezone as described in `vblife-workout-config`. Use the structured JSON from that call as the only source of numbers; never invent totals, trends, or goal rates.

## Primary delivery: Grok Bot Page (Canvas)

For every report period (`day`, `week`, `month`, `quarter`, `year`, or `all`), build an interactive Page with `WriteCanvas` (or hand the structured result to an executor that writes the Page). Follow the Cursor-managed `canvas` skill for kit rules, width, and chat delivery.

### Chat message

After the Page is written, send one short text summary in the user's language plus the Page link as a markdown label. Do not paste the full report body into chat. Do not attach a standalone HTML file for the report.

### Page shape (UX pattern)

Match the shipped weekly example titled like `Training & Nutrition Week · <start>–<end>` (wide dashboard with Segmented training / nutrition views). Adapt the same layout to other periods:

1. `Page` with `width="wide"`, a period title, and a one- or two-sentence `summary` of the main takeaway.
2. Optional `TableOfContents` when there are about six or more sections.
3. One short `Callout` for the period highlight (tone only when it fits info / success / warning / danger).
4. Overview: two-column `Grid` of `Properties` for training totals and nutrient averages (or day totals for a daily report). When `energy_balance.available` is true, add the energy deficit next to calories: the period total `total_energy_deficit_kcal` for multi-day periods, or the day's `energy_deficit_kcal` (see Energy deficit below).
5. Trends (multi-day periods only): `Segmented` switching training vs nutrition, then nested `Segmented` for the metric or nutrient; `LineChart` / `ColumnSeries` from `trend_series` in the `report` JSON (see Trend series below). The first and default training metric is training days, never training minutes. A daily report has no trend section.
6. Goal progress table with hit rates and `Status` pills when goals exist.
7. Period-over-period comparison when the tool returns a prior window.
8. Strength / mix / PRs / streaks / routine use when present; omit empty sections (never render placeholder empty states).
9. One short recommendations section grounded in the data.

### Trend series

`trend_series.primary_training_metric` is `training_days` for week, month, quarter, year, and all. Chart training days as the primary training trend; `training_minutes` remains on each point only for backward compatibility, so do not chart it as the featured training trend or describe training time as the headline trend.

- Week: `trend_series.day_points` has one point per day of the report week with `date`, `trained` (1 or 0), `sessions`, `intake_kcal`, `energy_deficit_kcal`, and that day's confirmed nutrient totals `protein_g`, `fat_g`, `saturated_fat_g`, `fiber_g`, `sugar_g`, and `sodium_mg`. Chart the daily energy deficit with trained days marked. `trend_series.points` (group_by `week`, six weekly points) adds `training_days` per week for the recent rhythm.
- Month: `trend_series.points` has six monthly points; year has twelve monthly points. Each point has `training_days` (days trained in that month), `total_energy_deficit_kcal`, `avg_daily_energy_deficit_kcal`, and `days_with_intake`, plus `strength_volume` and `cardio_minutes`.
- Every multi-day point carries `training_days`, `total_energy_deficit_kcal`, `avg_daily_energy_deficit_kcal`, and `days_with_intake`. The deficit fields are null when `maintenance_kcal` is missing or the bucket has no confirmed intake; leave those points out of the deficit chart rather than plotting zero.
- Every multi-day point (week, month, quarter, year, all) also carries chart-facing nutrient fields `intake_kcal`, `protein_g`, `fat_g`, `saturated_fat_g`, `fiber_g`, `sugar_g`, and `sodium_mg`. On `points` these are daily averages over the days in that bucket that logged the nutrient (the same rule as `nutrition.*_daily_average`), not period sums; `intake_kcal` on a point is average daily calories over days with calories logged. On `day_points` the same names are that day's totals.
- Nutrition trend: under the nutrition side of the trend `Segmented`, add a nested `Segmented` with options for intake (calories), energy deficit, protein, fat, saturated fat, fiber, sodium, and sugar. Carbohydrates are not a trend option. Week charts nutrients from `day_points` (one bar or point per day); month, quarter, year, and all chart nutrients from `points`. Energy deficit uses `energy_deficit_kcal` on `day_points`, and `avg_daily_energy_deficit_kcal` or `total_energy_deficit_kcal` on `points`. Label axes and series with nutrient names and units only.
- A nutrient field is null when that day or bucket has no confirmed entry logging it. Omit null values from the chart (leave a gap); never plot them as 0. Skip a nutrient option entirely when every value in the series is null.
- Strength volume and cardio duration stay as secondary training metrics.
- Day: `trend_series` keeps a 28-day rhythm for legacy clients without training-day fields. Do not build or describe a multi-week training-days or training-time trend in a daily report.

Use `show_progress` only when the user asks for a focused lift chart; that can stay an MCP Apps or chart reply and does not replace the period Page.

### Daily vs weekly Pages

Daily (`period=day`) and weekly (`period=week`) reports use the same Page pattern: wide `Page`, the same Overview grid, and a `Segmented` training / nutrition switch. They differ in what they compare and emphasize.

| | Daily (`period=day`) | Weekly (`period=week`) |
|---|---|---|
| Title | `Training & Nutrition Day · <date>` style | `Training & Nutrition Week · <start>–<end>` style |
| Comparison | Today vs yesterday from `vs_previous` (day-over-day) | This week vs the prior week from `vs_previous` (week-over-week) |
| Overview | Day totals for training and nutrients | Training totals and per-day nutrient averages |
| Trends | None: no training-days or multi-week trend on a daily report | Per-day energy deficit with trained days marked and per-day nutrient totals (`day_points`), plus six weekly points for training days, strength volume, and cardio duration |
| Goals | Distance to each daily target (how much is left, or how far over); for a calorie maximum, kcal under or over the maximum | Goal hit rates across the week |
| Energy deficit | `energy_deficit_kcal` vs `target_energy_deficit_kcal` | Week total `total_energy_deficit_kcal`, plus `avg_daily_energy_deficit_kcal` and `days_meeting_deficit_target` of `days_with_intake` |
| Tips | Two or three short, concrete actions for tomorrow | Two or three short actions for the coming week |

Tips for both say what to do more or less of (for example more cardio minutes, one fewer hard session, more mobility) and which nutrients to eat more or less of (for example more protein, less added sugar, more fiber). Tips name nutrients and amounts only, never foods, dishes, or meals.

A scheduled daily report runs in the evening, so treat the day as possibly unfinished: phrase distance to goals as what is still open today, not as a missed target.

### Energy deficit

`report` returns `energy_balance`, computed from `settings.maintenance_kcal` and the user's own `calories_kcal` maximum goal. Show it only from those fields; never compute it from a calorie number in goal assumptions or memory, and never hard-code a calorie value.

- Day: `maintenance_kcal`, `intake_kcal`, `energy_deficit_kcal` (maintenance minus intake), `target_energy_deficit_kcal` (maintenance minus the calorie maximum), `within_calorie_max`, `met_deficit_target`, `kcal_under_max` (negative means over the maximum), and `deficit_vs_target_kcal`.
- Week, month, quarter, year, and all: `total_energy_deficit_kcal` (sum of each day's deficit on days with confirmed intake), `avg_daily_energy_deficit_kcal`, `days_meeting_deficit_target`, `days_within_calorie_max`, and `days_with_intake`. Week and month also return per-day `days`. There is no separate total for a day; use `energy_deficit_kcal`.
- Always call out the period total when `energy_balance.available` is true, in both the chat summary and the Page, for example "This week created about 2,450 kcal of energy deficit" (the number comes only from `total_energy_deficit_kcal`, rounded). A negative total is a surplus: say "about N kcal over maintenance" instead of a negative deficit.
- The total covers logged days only; when `days_with_intake` is lower than the days in the period, say how many days it covers.
- Distance to the calorie goal is "N kcal under the maximum" or "N kcal over the maximum", plus the deficit vs its target (for example "deficit 570 kcal, target 470 kcal"). A calorie maximum is an upper limit, not a range.
- Keep consumed and workout-burned energy separate. The deficit is against maintenance only; never add or subtract workout calories.
- If `maintenance_kcal` is null, omit the deficit and add one short note that it needs a saved maintenance estimate (see `vblife-workout-nutrition`). If there is no calorie maximum goal, show the deficit without a target.
- A daily report may run before the day ends: phrase an open day as the deficit so far.
- Labels: "Energy deficit", "Total energy deficit", and "Target deficit" in English. In Chinese, use the standard Chinese term for energy deficit (pinyin "nengliang quekou"), its total form (pinyin "zong nengliang quekou"), and its target form; the period sentence is the Chinese for "this week created about N kcal of energy deficit" using that term.

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
2. Training days, sessions, strength volume, cardio duration, distance, and estimated workout calories with confidence coverage.
3. Confirmed nutrition totals for a day or per-day averages, recorded-day count, and goal-hit rates for longer periods; keep consumed and burned energy separate.
4. Energy deficit when `energy_balance.available` is true: for a day, the day's deficit and kcal under or over the calorie maximum; for longer periods, always the period total (`total_energy_deficit_kcal`, for example "this week created about N kcal of energy deficit"), then the average daily deficit and days meeting the target.
5. One or two actionable recommendations.

Weekly reports explain which days were trained and the daily energy deficit, the six weekly points for training days, strength volume, and cardio duration, plus the change vs the prior week. Monthly reports explain training days and energy deficit across six monthly points. Annual reports explain twelve monthly points. Daily reports cover today vs yesterday, distance to daily goals, and the day's energy deficit when available, with no multi-week training trend.

The chat summary is always in the user's language, stays short, and ends with the Page link. Never paste report HTML or the full Page body into chat.

All numbers and units come from tool output. When data is sparse or estimates are rough, say so. Empty periods remain useful: state that no completed workouts or confirmed nutrition entries exist and suggest a next step. For an empty day, still build the Page when there is any goal progress or a previous-day comparison to show. Skip building a Page only when there is truly nothing to show, and then say so in chat.
