---
name: vblife-workout-routine
description: Use when the user wants to create, save, inspect, edit, archive, restore, or start a reusable workout routine, including saving a completed workout as a routine.
---
# Saved workout routines

- Direct creation: collect a name and ordered targets, then call `create_routine` when the request clearly asks to save them.
- From a completed workout: identify its session, show the proposed copied targets when clarification is needed, and call `create_routine` with `source_session_id`. Never change the completed workout.
- Use `list_routines`, `get_routine`, or `update_routine` for discovery and edits. Edits affect future starts only.
- Routines have a `routine_type`: `standard` (sets, reps, weight; the default) or `hiit` (timed circuit). A HIIT item has `work_seconds` (default 40) and `rest_seconds` (default 20). A HIIT routine can have `rounds` and `round_options`. Filter with `list_routines` and `routine_type`. If a routine was saved or classified with the wrong type, fix it with `update_routine` and `routine_type`. To time a HIIT routine, follow `vblife-workout-hiit-session` and call `hiit_timer` with `routine_id`.
- Use `archive_routine` instead of deleting; restore with `restore_routine`.
- Call `start_routine` only when the user asks to begin. It creates an independent active-workout snapshot with pending items and no completed sets. To begin a standard routine with the guided runner, call `guided_routine_timer` instead (it calls `start_routine` for you, and on MCP Apps clients its runner logs each Done set; see below).
- Standard items may set `rest_seconds` (the rest after each set; 0 for none). Without it the guided runner rests 90 seconds after a working set. `work_seconds` stays HIIT-only; a timed hold on a standard item is `target_duration_s`.

If another workout is active, ask the user to resume, finish, or abandon it; never replace it silently. During the workout, use `add_workout_item`, `replace_workout_item`, `reorder_workout_items`, or `set_workout_item_status` for a pivot. These tools change only the active snapshot. Change the source routine only on a separate explicit request.

A conversational plan is not automatically a saved routine. Create one only when the user explicitly asks to reuse or save it.

If the user wants a countdown or rest timer, generate it from the shared repo timer template (`server/render_timer_html.py`) as described in `vblife-workout-hiit-session`: English UI by default (pass `lang` only when the user's stated language preference is Chinese, then use a Chinese label), sent as a local `file://` HTML attachment, never hosted externally. Never hand-write a timer page.

## Guided routine runner (standard routines)

When the user wants to do a saved standard routine now ("start my push day", "let's do this plan"), start the guided runner: it walks them set by set. On an MCP Apps client it is an interactive view that logs each set itself; elsewhere it is an offline page and you log from its summary.

1. Find the routine (`list_routines`, `get_routine`). If it is a HIIT routine, use `hiit_timer` instead (`guided_routine_timer` rejects HIIT routines). If another workout is active, ask the user to resume, finish, or abandon it first.
2. Call `guided_routine_timer` with `routine_id` (plus `default_rest_s` when the user asks for a different default rest than 90 seconds, and `lang: "zh"` only under the timer language rule in `vblife-workout-hiit-session`). It starts the workout and returns `session_id` and ordered `steps`. Each item becomes `target_sets` work steps (1 when unset) with its rest after each set; there is no rest after the last step. A step counts down only when the item has `target_duration_s`; otherwise the clock counts up from 0 until the user taps Done.

### Primary: MCP Apps runner (result has `ui_auto_log: true`)

3. The host shows the runner view attached to the tool result. Send one short message: press Start, tap Done after each set, Skip set to skip one, End workout to stop early. Do not render an offline page, do not ask for a summary, and do not log the sets yourself.
4. The runner logs each set the moment the user taps Done (and each timed hold when it reaches 0): one `log_sets` call with `performed_at` set to when it was done and the step's `session_item_id`. Activity-only steps (`loggable: false`) are marked completed with `set_workout_item_status`. Skipped and unreached sets are never logged.
5. When the routine finishes, or the user taps End workout, the runner saves any pending Done sets and then calls `finish_workout`, without asking. Ending with nothing marked Done still finishes the workout.
6. If the user later says the runner reported sets it could not save, or the workout is still active, check with `get_active_workout` and `list_recent`, log only the missing Done sets they list (never twice), then call `finish_workout`. Never claim anything was saved until a tool result confirms it.

### Fallback: offline page (no MCP Apps; `ui_auto_log` absent)

3. Write the result to a JSON file and run `python server/render_timer_html.py /workspace/timer-cache/guided-routine-<routine id>-<lang>.html --config <guided.json> --cache-dir /workspace/timer-cache` from the workout-tracker repo root (add `--lang zh` only under the language rule). Never hand-write the page. Send it as a `file://` attachment, never hosted externally, with one short message: open it in the phone's browser, press Start, tap Done after each set, and tell me when you finish or stop.
4. The page makes no network calls, so nothing is logged until the user comes back. Follow `completion_handoff`: when they report finishing or stopping, ask them to paste or describe the "Completed sets" list (the JSON summary under it is the exact record) if they have not already. Log only the sets marked Done: one `log_sets` call per set, in order, with the step's `exercise`, `reps`, `weight`, `unit`, `duration_s`, `distance_m`, `is_warmup`, `session_item_id`, and `performed_at` set to its `done_at`. Log them before any other workout tool call: the active workout closes after 15 minutes without logged activity, and backdating each set keeps it in this workout. Never log proposed work: skipped, unreached, or prescribed-only sets stay unlogged. If they ended early without the summary, ask where they stopped; never guess.
5. Steps with `loggable: false` (activity-only items such as a treadmill warm-up) are not sets: mark that session item completed with `set_workout_item_status`, or use `log_activity` when the user wants it recorded.
6. Call `finish_workout` when the user is done, and never claim anything was saved until the logging tools succeed.

To preview a routine as a guided page without starting a workout (nothing to log), run `python server/render_timer_html.py <out.html> --routine-id <id> --guided --cache-dir /workspace/timer-cache` (optionally `--default-rest <seconds>`).
