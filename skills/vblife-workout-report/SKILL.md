---
name: vblife-workout-report
description: Use when the user requests a daily, weekly, monthly, quarterly, annual, or all-time workout and nutrition report, dashboard, recap, or scheduled weekly report.
---
# Reports and dashboards

Call `report` with `day`, `week`, `month`, `quarter`, `year`, or `all` and an anchor date when supplied. Do not require setup first.

Always provide an easy-to-read text summary from the structured result, whether or not an MCP Apps dashboard renders:

1. Period and primary goal progress.
2. Sessions, training minutes, strength volume, cardio duration, distance, and estimated workout calories with confidence coverage.
3. Confirmed nutrition totals for a day or per-day averages, recorded-day count, and goal-hit rates for longer periods; keep consumed and burned energy separate.
4. Trends, workout mix, PRs, streaks, routine use, and one or two actionable recommendations.

Weekly reports explain the six weekly points for training time, strength volume, and cardio duration. Monthly reports explain the same three metrics across six monthly points. Annual reports explain twelve monthly points. Daily reports cover the recent four-week rhythm.

Use `show_progress` for a requested lift or focused chart. All numbers and units come from tool output. When data is sparse or estimates are rough, say so rather than implying measurement. Empty periods remain useful: state that no completed workouts or confirmed nutrition entries exist and suggest a next step.
