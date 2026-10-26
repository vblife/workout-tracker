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
4. The runner logs each set the moment the user taps Done (and each timed hold when it reaches 0): one `log_sets` call with `performed_at` set to when it was done, the step's `session_item_id`, and the runner's `session_id`. If that workout is no longer active, `log_sets` saves nothing and returns `status: "rejected"` with a `note`; the set is never logged into another workout, and the runner treats it as not saved (so it does not finish the workout). Activity-only steps (`loggable: false`) are marked completed with `set_workout_item_status`. Skipped and unreached sets are never logged.
5. When the routine finishes, or the user taps End workout, the runner saves any pending Done sets and then calls `finish_workout` with its `session_id`, without asking. If that workout had already ended, the result is `already_finished` and any other active workout is left alone. Ending with nothing marked Done still finishes the workout.
6. If the user later says the runner reported sets it could not save, or the workout is still active, check with `get_active_workout` and `list_recent`, log only the missing Done sets they list (never twice), then call `finish_workout`. If the sets were rejected because the runner's workout had ended and another workout is active, ask the user which workout they belong to before logging them. Never claim anything was saved until a tool result confirms it.

### Fallback: offline page (`ui_auto_log: false`, `delivery: "offline_page"`)

The result always says which path applies. `delivery_reason` explains why the client got the offline page (it did not negotiate the `io.modelcontextprotocol/ui` extension). Never render an offline page for a result with `ui_auto_log: true`; `render_timer_html.py` refuses it.

3. Write the result to a JSON file and run `python server/render_timer_html.py /workspace/timer-cache/guided-routine-<routine id>-<lang>.html --config <guided.json> --cache-dir /workspace/timer-cache` from the workout-tracker repo root (add `--lang zh` only under the language rule). Never hand-write the page. Send it as a `file://` attachment, never hosted externally, with one short message: open it in the phone's browser, press Start, tap Done after each set, and tap Send to Workout Bot on the finish screen.
4. The page makes no network calls. It keeps the Done sets on the device for this workout, so closing or reloading it loses nothing: reopening shows the finish screen (or resumes paused at the saved step). Its Send to Workout Bot button sends a short line plus a line starting `WORKOUT_BOT_GUIDED` followed by JSON (`session_id`, `routine_id`, `ended`, `completed`), through the phone's share sheet, or the clipboard when sharing is unavailable.
5. When a message carries that result (the `WORKOUT_BOT_GUIDED` line, or JSON with `workout_bot: "guided_routine"`), immediately call `log_guided_result` with its `session_id`, `completed`, and `ended`. Do not ask questions first, do not call `log_sets` set by set, and do not call `finish_workout`: `log_guided_result` saves every Done set with `performed_at` set to its `done_at`, marks `loggable: false` items completed, skips sets already saved (sending twice is safe), reopens the runner's workout if the 15-minute idle rule closed it while the user trained offline (never one that was finished or abandoned on purpose), and finishes it. Then reply with what was saved, from the tool result.
6. If it returns `rejected` (another workout is active now, or the runner's workout was already finished or abandoned on purpose), nothing was saved: ask which workout the sets belong to. If it returns `needs_confirmation`, nothing was saved: confirm the unknown exercise and call it again. If the user says they are done without sending the result, ask them to tap Send to Workout Bot (reopening the page shows it again); if they cannot, ask where they stopped and log only what they confirm. Never guess, and never claim anything was saved until a tool result confirms it.

To preview a routine as a guided page without starting a workout (nothing to log), run `python server/render_timer_html.py <out.html> --routine-id <id> --guided --cache-dir /workspace/timer-cache` (optionally `--default-rest <seconds>`).
