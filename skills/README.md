# Workout Tracking bot — skills
Each skill lives in `<slug>/SKILL.md` (YAML frontmatter `name` + `description`, then the recipe). All use the `workout-tracker` MCP connector (17 tools; schemas in `../server/server.py`).

| Skill | When to use |
|---|---|
| [workout-onboarding](workout-onboarding/SKILL.md) | First run / `onboarding_required` non-empty / changing unit, days, goals, equipment |
| [log-workout](log-workout/SKILL.md) | User reports sets, holds, cardio or hikes; corrections and undo |
| [daily-workout-plan](daily-workout-plan/SKILL.md) | "What should I do today?", plan requests, daily-plan routine |
| [progressive-overload](progressive-overload/SKILL.md) | Next weight/reps/sets for an exercise; how progression works |
| [hiit-session](hiit-session/SKILL.md) | Build and start an interval timer; log finished HIIT |
| [workout-report](workout-report/SKILL.md) | Week/month/quarter/year/all-time reports and progress charts; report routines |
| [workout-analysis](workout-analysis/SKILL.md) | Open-ended questions about trends, balance, plateaus |

Tool → skill map: get_context/update_settings (onboarding, all); resolve_exercise/add_alias/add_exercise/log_sets/list_recent/delete_set/log_activity/delete_session (log-workout); daily_plan_context (daily-workout-plan); suggest_next (progressive-overload, daily-workout-plan); hiit_timer/log_hiit (hiit-session); report/show_progress (workout-report, workout-analysis); query_stats (workout-analysis).

Suggested routines: daily plan 07:00 local → daily-workout-plan; Sunday 20:00 → workout-report `week`; 1st of month → `month`; first day of quarter/year → `quarter`/`year`.
