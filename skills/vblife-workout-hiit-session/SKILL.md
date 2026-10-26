---
name: vblife-workout-hiit-session
description: Use when the user wants a HIIT, interval, or any countdown timer (rest, plank, warm-up), asks to review or change HIIT preferences, or reports completing or stopping a timer session.
---
# HIIT sessions

On the first HIIT request, present the defaults and ask for exercise names before starting: work 40 seconds, rest 20 seconds, 20 rounds, warm-up 10 seconds, and cooldown 60 seconds. The user may change any value. Save a confirmed first configuration in `hiit_defaults` with `update_settings`.

On later requests, reuse saved defaults unless the user supplies overrides. For an override, ask whether it applies only now or should replace the saved default. Users may review, change, or clear preferences whenever they want.

## Saved HIIT routines

A HIIT routine is a saved routine with `routine_type: "hiit"`. It holds an ordered exercise list where each item has `work_seconds` (default 40) and `rest_seconds` (the rest after it; default 20, 0 for none). The routine can also hold `rounds` (circuit repeats, default one pass) and optional `round_options` for the HTML timer's round selector. When the user starts HIIT:

1. Call `list_routines` with `routine_type: "hiit"`.
2. **No HIIT routine:** offer to define one. Ask for the ordered exercise list and the work/rest seconds (offer 40/20, or the saved `hiit_defaults`). Ask whether the circuit should repeat. Show the plan, then save it with `create_routine` (`routine_type: "hiit"`, `items` with `exercise`, `work_seconds`, `rest_seconds`, and optional `rounds`) once the user agrees. Then build the timer from it. If the user would rather not save, run it ad hoc (below).
3. **Exactly one HIIT routine:** use it by default. Say its name, exercise count, and total time, and let the user pick ad hoc instead.
4. **Several:** list them by name and ask which one, unless the user already named it.
5. Build the timer with `hiit_timer` and `routine_id` (plus `rounds` to override the circuit count, which must be one of `round_options` if the routine has them). Pass `lang: "zh"` only under the language rule below, so the exercise names come out in Chinese. Each exercise is one work interval with its rest after it, rounds repeat the circuit, and the final rest is skipped. During each work interval the timer shows the exercise and "Next: <next exercise>".

If a routine is the wrong type (for example a timed circuit saved as standard), fix it with `update_routine` and `routine_type`. Change per-item timing with `update_routine` and `items`.

## Ad hoc HIIT

Ad hoc HIIT without a routine still works. After exercises and timing are confirmed, call `hiit_timer` with ordered work/rest segments. Summarize total time. When MCP Apps is supported, the interactive timer appears.

## Fallback: HTML timer when MCP Apps is not supported

If the client cannot render the interactive timer (for example a chat without MCP Apps, Grok Bot on mobile, or the user says they don't see a timer), do not just guide intervals in prose. Immediately generate the HTML timer from the repo template and send it as an attachment, without waiting to be asked:

1. Write the structured `hiit_timer` result (including `hiit_id`, `rounds`, `segments`, `warmup_s`, `cooldown_s`, `name`, and `round_options` when present) to a JSON file. A timer built from a routine works the same way; keep its `hiit_id` so completion can be logged. Write it with `name` and every exercise name in the timer's UI language (English by default; translate names if needed and keep all timing values exactly as returned), then run `python server/render_timer_html.py /workspace/timer-cache/hiit-routine-<routine id or adhoc>-r<rounds>-<lang>.html --config <hiit.json> --cache-dir /workspace/timer-cache` (add `--lang zh` only under the language rule below) from the workout-tracker repo root (two levels above this skill folder). The template expands the segments exactly like the interactive timer: warm-up, each work segment with its exercise name and round, rests, and the cooldown, with the final rest of the last round skipped.
2. Never hand-write a custom timer page. The template already provides the big countdown, phase and "up next" lines, colors per phase, Start/Pause/Reset/Skip, a dark high-contrast theme, a 5-second get-ready period, a soft short-short-long beep cue when counting starts, at every interval change (lower pitch into rest), and at the finish, a soft once-per-second clock tick while counting (skipped where a beep plays, none during get ready; the on-page "Tick" toggle turns it off, and `--no-tick` or `"tick": false` removes it if the user does not want it), a truly silent keep-alive track (no background hum), vibration where supported, and the screen-off safeguards documented in `docs/timer.md`.
3. Send the file as a `file://` attachment, then one short message: open it in the phone's browser, press Start, keep the screen on for the most reliable cues, and tell me when you finish or where you stopped so I can log it.

## Reusing generated timer HTML

Pass `--cache-dir /workspace/timer-cache` on every `render_timer_html.py` call, countdown and HIIT alike. The renderer keys the cache on the template plus the normalized config, so starting the same HIIT routine again or asking for the same countdown again (for example another 5-minute timer days later) reuses the HTML it already generated instead of rendering it again. It prints `timer cache hit <key>` or `timer cache miss <key>` on stderr.

- The cache content is authoritative: the same content is reused even when the out path differs. Stable out paths under the cache dir still help, for example `/workspace/timer-cache/timer-<slug>.html` for a countdown and `/workspace/timer-cache/hiit-routine-<id>-r<rounds>-<lang>.html` for a HIIT routine.
- `hiit_id` is not part of the cache key. Still call `hiit_timer` whenever completion should be logged so you get a fresh `hiit_id`, and put that id in the config. The renderer reuses the cached page and writes the current `hiit_id` into the out file.
- If the file at the intended out path was rendered from the same parameters, sending it is fine, but run the renderer with `--cache-dir` first whenever a new `hiit_id` must be embedded. Never skip the CLI in that case, or the page shows a stale `hiit_id`.
- The cached file is still a local file: send it as a `file://` attachment and never host it externally. The language rule below is unchanged.

## Countdown timers (any timer request)

Every countdown timer the bot sends, HIIT or not (rest timer, plank hold, warm-up, stretching, "2-minute timer"), must come from the same repo template, with an English UI by default and a label in the UI's language, delivered as a local `file://` HTML attachment. Never write a timer page by hand and never host it externally.

- Single countdown: `python server/render_timer_html.py /workspace/timer-cache/timer-<slug>.html --duration <seconds or m:ss> --label "<label>" --cache-dir /workspace/timer-cache`.
- Default 40/20 HIIT (one exercise per round) when the user has not fixed the round count: send one file that lets them pick 10 or 20 rounds before Start: `python server/render_timer_html.py /workspace/timer-cache/hiit-40-20-r10-20-en.html --work 40 --rest 20 --rounds 10,20 --label "HIIT 40/20" --cache-dir /workspace/timer-cache`. Add `--default-rounds 20` to preselect 20. A single number (`--rounds 10`) gives a fixed count with no selector. The config equivalent is `"round_options": [10, 20]` next to `segments` (with `rounds` as the default). The final rest is skipped, so the totals are 9:40 and 19:40 plus the 5-second get ready.
- Saved HIIT routine without a `hiit_timer` call: `python server/render_timer_html.py /workspace/timer-cache/hiit-routine-<id>-r<rounds>-<lang>.html --routine-id <id> --label "<English name>" --cache-dir /workspace/timer-cache` reads the routine read-only from the workout database (`$WORKOUT_DB`, or `--db <path>`). Add `--lang zh` for Chinese exercise names under the language rule below, and `--rounds <n>` to override the circuit count. Prefer the `hiit_timer` + `--config` path when completion should be logged, because only `hiit_timer` creates a `hiit_id`.
- Work/rest sequence without a `hiit_timer` call: write `{"label": ..., "intervals": [{"kind": "work", "seconds": 40, "label": "Squats"}, {"kind": "rest", "seconds": 20}]}` to a JSON file and pass it with `--config` (plus `--cache-dir /workspace/timer-cache`). Valid kinds are `work`, `rest`, `warmup`, `cooldown`, and `interval`.
- Optional: `--ready <seconds>` changes the 5-second get-ready period; `"speech": true` adds spoken countdown numbers.
- Language: the timer UI is English by default (Start, Pause, Reset, Get ready, ...), even when the user chats in another language. Pass `--lang zh` (or `"lang": "zh"` in the config) only when the user's stated language preference is Chinese, meaning they asked for a Chinese timer or Chinese UI, or saved that preference. Chatting in Chinese is not enough. With `zh`, write the label and interval labels in Chinese; otherwise pass English labels (for example `--label "2-minute timer"`, `"label": "Squats"`). Only `en` and `zh` exist; any other value is rejected. Labels are shown exactly as passed. With `en` the generator prints a warning on stderr if a label contains CJK characters; if you see it, regenerate with an English label. Spoken cues (`"speech": true`) follow the UI language. Your chat reply can still be in the user's language.
- Keep the file local and send it as a `file://` attachment. Never upload it to Google Drive or any other external host; it must work offline with no network requests.
- Be honest about screen-off behavior (details in `docs/timer.md`): time stays correct after the phone wakes, and beeps are pre-scheduled so they usually keep playing while locked on Android Chrome, but iOS may pause audio when locked, in-app viewers are less reliable than the real browser, and iPhones do not vibrate from web pages.

## Logging

Use `log_hiit` (with the `hiit_id` from `hiit_timer`) when the user or app reports completion or an early stop. Repeated callbacks may return `already_logged`; confirm without duplicating. Without a timer ID, use `log_activity` only when duration is known or explicitly estimated and labelled.

Never claim a session was saved until a logging tool succeeds.
