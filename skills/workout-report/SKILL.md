---
name: workout-report
description: Use when the user asks for a weekly/monthly/quarterly/annual/all-time summary, recap or progress chart, or when a scheduled report routine fires.
---
# Workout report

## Steps
1. Pick the period: 本周/this week → `week`; 本月 → `month`; 本季度 → `quarter`; 今年 → `year`; 全部/历史 → `all`. "Last week/month" → pass `anchor_date` = any date inside that period (YYYY-MM-DD). Scheduled weekly routine → `week` for the week just ending.
2. `report {"period": "...", "anchor_date"?: "..."}`. The desktop app renders the report UI from the result.
3. Write a short text summary (Chinese default) on top — the UI doesn't replace it:
   - 1 line headline: days trained vs target (`summary.days_trained`, `target_days_per_week`, `adherence`), and the change vs `vs_previous`.
   - Up to 4 bullets from `highlights`, `prs`, `lifts` (e1RM change %, `plateau_4w`), cardio minutes / hikes / distance.
   - 1–2 concrete recommendations for next period (e.g. a plateaued lift → switch rep range or deload; cardio behind → add a session; under target days → plan specific days).
4. If the user wants a chart for a specific lift or metric: `show_progress {"exercise":"...","metric":"e1rm"|"volume"|"best_set"|"reps","range":"4w"|"12w"|"6m"|"1y"|"all"}`; overall trend without exercise: `metric` `volume`|`sets`|`days`|`cardio_minutes`. Summarize `stats` (latest, best, change_pct).
5. Mobile: if `fallback_url` is non-null, include 「📱 手机查看图表:<url>」. If null, mention the charts show in the desktop app and give the key numbers in text.

## Rules
- Use only numbers from the tool result; units from `unit`, `distance_unit`, `elevation_unit`.
- Empty period (all zeros) → say so plainly and suggest logging or planning, no fake encouragement stats.
