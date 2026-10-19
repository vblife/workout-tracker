---
name: vblife-workout-routine
description: Use when the user wants to create, save, inspect, edit, archive, restore, or start a reusable workout routine, including saving a completed workout as a routine.
---
# Saved workout routines

- Direct creation: collect a name and ordered targets, then call `create_routine` when the request clearly asks to save them.
- From a completed workout: identify its session, show the proposed copied targets when clarification is needed, and call `create_routine` with `source_session_id`. Never change the completed workout.
- Use `list_routines`, `get_routine`, or `update_routine` for discovery and edits. Edits affect future starts only.
- Use `archive_routine` instead of deleting; restore with `restore_routine`.
- Call `start_routine` only when the user asks to begin. It creates an independent active-workout snapshot with pending items and no completed sets.

If another workout is active, ask the user to resume, finish, or abandon it; never replace it silently. During the workout, use `add_workout_item`, `replace_workout_item`, `reorder_workout_items`, or `set_workout_item_status` for a pivot. These tools change only the active snapshot. Change the source routine only on a separate explicit request.

A conversational plan is not automatically a saved routine. Create one only when the user explicitly asks to reuse or save it.

If the user wants a countdown or rest timer, generate it from the shared repo timer template (`server/render_timer_html.py`) as described in `vblife-workout-hiit-session`: English UI and an English label, sent as a local `file://` HTML attachment, never hosted externally. Never hand-write a timer page.
