---
name: vblife-workout-progressive-overload
description: Use when the user asks what sets, reps, weight, duration, or progression to attempt next, whether to add load, or why an exercise has stalled.
---
# Progressive overload

Call `suggest_next` for the exercise after any requested history has been logged. Supply a requested rep range or set count; otherwise use server defaults.

Present the returned sets, reps, weight, unit, and rationale. Explain the returned status plainly:

- `increase_weight`: the prior top sets earned the server-selected increment.
- `add_reps`: keep load and build reps within the range.
- `deload` or `reset`: use the lighter server prescription and explain the recorded trigger.
- `no_history`: give the returned conservative starting guidance and invite the user to log it afterward.
- `progress`: follow the returned bodyweight, timed, or cardio progression.

If equipment is unknown or unavailable, say so and offer an appropriate alternative. Do not calculate a competing prescription or invent workout history. A recommendation is not completed work and must not be logged unless the user later reports doing it.
