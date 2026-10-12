---
name: workout-analysis
description: Use when the user asks analytical questions about their training history (trends, comparisons, frequency, balance, plateaus, "am I improving", "what should I change") rather than a standard period report.
---
# Workout analysis

## Choose the data
- One lift over time → `query_stats {"metric":"e1rm","exercise":"...","group_by":"week","from_date"?, "to_date"?}` (dates YYYY-MM-DD inclusive). Strength trend = e1RM; work done = `volume`; rep work for bodyweight = `reps`; heaviest-set detail = `best_set`; how often = `frequency` (sets/sessions per bucket).
- Overall balance / muscle split / adherence → `report` for the relevant period (`summary.sets_by_muscle`, `sessions_by_type`, `adherence`, `lifts`).
- Recent detail → `list_recent {"limit": 20}` or `get_context {"days": 28}`.
- Visual → `show_progress` (mention `fallback_url` on mobile when non-null).
Make several calls if needed (e.g. compare two lifts, or this quarter vs last quarter with two `report` calls using `anchor_date`).

## Analyze
Look for: e1RM slope (stalled ≥4 weeks = plateau), push vs pull vs legs set balance (aim roughly equal push/pull; legs not neglected), weekly days vs target, cardio minutes trend (≤10%/week growth), long gaps, PR frequency.

## Answer
- Lead with the direct answer in 1–2 sentences, then 2–4 supporting numbers from the tools, then 1–3 actionable recommendations (e.g. rep-range change via `suggest_next target_rep_range`, add a pull exercise, schedule a hike, deload week).
- If data is insufficient (few sessions), say what's missing and how many more weeks of logging would make the trend meaningful.

## Rules
- Every number must come from a tool call in this conversation. No estimates presented as data, no invented history.
- Analysis is read-only: don't log, delete, or change settings unless the user asks.
