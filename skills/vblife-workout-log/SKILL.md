---
name: vblife-workout-log
description: Use when the user reports completed strength, bodyweight, cardio, hike, mobility, or HIIT work, or asks to correct, undo, regroup, finish, or delete workout records.
---
# Conversational workout logging

Log completed work promptly; never log proposed work or something the user says they did not do.

- Strength or timed sets: call `log_sets` with the user's exercise wording, reps, count, weight/unit, duration, distance, RPE, and exact timestamp when supplied. The server automatically starts an implicit workout and keeps entries at or within 15 minutes together.
- Whole cardio or hikes: call `log_activity`. Do not demand body weight or duration solely for calories; return the tool's estimate, label its confidence, and disclose its fallback assumptions.
- Unknown or ambiguous exercise: show candidates and ask what the user means. Call `add_alias` or `add_exercise` only after confirmation, then retry.
- Confirm successful logs from returned values. Mention a PR only when the server marks it.
- When logging against a started routine or accepted plan, include `session_item_id` when the completed work corresponds to a snapshot item. Use `set_workout_item_status` for an explicit skip or manual completion.
- A single-set undo explicitly requested by the user may call `delete_set` directly. Before deleting a whole session or multiple records, identify the target and ask for explicit confirmation.
- Use `update_set` or `update_activity` to correct actual work. Use `move_set` to correct one entry grouped into the wrong workout. Use `merge_workouts` only for adjacent completed workouts and only after explicit confirmation.
- Use `get_active_workout`, `finish_workout`, or `abandon_workout` when requested. Do not silently replace an active workout.

If the user reports sharp pain or injury, stop the movement, refuse to fabricate or encourage another set, and suggest qualified professional care without diagnosing. Setup is optional; missing preferences never block a log.

If the user wants a countdown or rest timer, generate it from the shared timer template (`server/render_timer_html.py`) as described in `vblife-workout-hiit-session`; never hand-write a timer page.
