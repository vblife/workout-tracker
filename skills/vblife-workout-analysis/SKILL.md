---
name: vblife-workout-analysis
description: Use when the user asks an open-ended question about workout trends, improvement, frequency, balance, plateaus, comparisons, or what recorded evidence suggests changing.
---
# Workout analysis

Choose server facts that answer the question:

- One exercise over time: call `query_stats` for e1RM, volume, reps, best set, or frequency with the relevant date range and grouping.
- Overall balance, adherence, cardio, nutrition, or routine use: call `report` for the relevant period.
- Recent detail: use `list_recent` or `get_context`.
- A focused visual: use `show_progress`.

When the request includes logging history and then analyzing it, log the completed work first, then call the analysis tool. Setup is optional and must not block analysis.

Lead with the direct conclusion, cite two to four returned facts, explain whether the direction is improving, flat, or declining, and give one to three practical next steps. Distinguish insufficient data from a true plateau. All numbers, dates, PRs, and trends must come from current tool results.

Analysis is read-only unless the user also explicitly requests a write. Do not change settings, goals, routines, or records merely because a recommendation follows from the analysis.
