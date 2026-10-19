# Workout Bot skills

Workout Bot uses exactly these ten agent skills:

| Skill | Responsibility |
|---|---|
| `vblife-workout-config` | Repeatable preferences, goals, reminders, nutrition mode, and HIIT defaults |
| `vblife-workout-log` | Quick logs, lifecycle, calories, corrections, and safety |
| `vblife-workout-daily-plan` | Ephemeral day plans and accepted-plan starts |
| `vblife-workout-progressive-overload` | Server-backed next-session prescriptions |
| `vblife-workout-hiit-session` | HIIT configuration, timer, completion, and the shared countdown timer template |
| `vblife-workout-routine` | Reusable routines and independent workout snapshots |
| `vblife-workout-nutrition` | Meal-image estimates, confirmation, privacy, and goals |
| `vblife-workout-report` | Readable period reports and dashboards |
| `vblife-workout-analysis` | Open-ended trend and evidence analysis |
| `vblife-workout-install` | Installs or updates the bot, connector, skills, and routines from this repository |

Every `SKILL.md` is English-only so the instructions remain portable. Runtime input may use English or Chinese exercise aliases, and Workout Bot replies in the user's language. Generated timer pages are the exception: their UI and labels are English unless the user's stated language preference is Chinese, in which case the bot passes `lang: zh` and a Chinese label.
