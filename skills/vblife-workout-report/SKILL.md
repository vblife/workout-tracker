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

## Always deliver the report as an HTML attachment too

Do this for every report (day, week, month, quarter, year, or all), even when an MCP Apps dashboard renders, because Grok Bot on mobile doesn't support MCP Apps yet:

1. Call `report` normally so a compatible client can render the MCP Apps dashboard in the app.
2. Build a standalone HTML file from the repo template `server/ui/report.html` in the workout-tracker checkout (on the box: `/home/vblife/workout/server/ui/report.html`). Never write a custom layout. Insert `<script>window.openai={toolOutput:<report JSON>};</script>` before the template's first `<script>` tag, using the exact structured `report` result with `ensure_ascii=False`.
3. Save it under `/workspace/` as `workout-report-<period>-<anchor-or-label>.html`, then send it in chat as a `file://` attachment so the user can tap it open.
4. Keep the file local. Never upload it to Google Drive or any other external service or link-sharing host.
5. If the template is missing, say so, and still send the text summary.
