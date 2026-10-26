---
name: vblife-workout-config
description: Use when the user wants to review or change Workout Bot preferences, goals, reminders, equipment, schedule, units, nutrition mode, or HIIT defaults.
---
# Repeatable configuration

Setup is optional and repeatable, never a prerequisite for logging, planning, coaching, or reports.

1. Call `get_context` and summarize only relevant existing values.
2. Ask only for information needed by the current request. Never infer demographic, health, equipment, or schedule details.
3. Save each confirmed preference with `update_settings`, sending only explicitly supplied keys so omitted values remain unchanged.
4. Use `create_goal` for a new concrete goal and `update_goal` for changes, not the legacy free-form `goals` setting. Direct user goals do not require profile data. For recommended daily nutrition goals, follow the goal setup workflow in `vblife-workout-nutrition`.
5. Proactive daily-plan, workout-reminder, daily-report, and weekly-report routines are enabled by default after schedule setup; a user can turn any or all off. Store the explicit reminder choice.
6. Body weight is optional; record it through `add_body_weight` when requested.

HIIT defaults are work 40 seconds, rest 20 seconds, 20 rounds, warm-up 10 seconds, and cooldown 60 seconds. USDA and nutrition-profile setup are optional; never store an API key in settings.

Confirm exactly what changed and offer the requested next action. Do not rerun a one-time onboarding flow.
