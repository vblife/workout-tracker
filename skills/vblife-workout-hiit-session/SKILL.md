---
name: vblife-workout-hiit-session
description: Use when the user wants a HIIT, interval, or any countdown timer (rest, plank, warm-up), asks to review or change HIIT preferences, or reports completing or stopping a timer session.
---
# HIIT sessions

On the first HIIT request, present the defaults and ask for exercise names before starting: work 40 seconds, rest 20 seconds, 20 rounds, warm-up 10 seconds, and cooldown 60 seconds. The user may change any value. Save a confirmed first configuration in `hiit_defaults` with `update_settings`.

On later requests, reuse saved defaults unless the user supplies overrides. For an override, ask whether it applies only now or should replace the saved default. Users may review, change, or clear preferences whenever they want.

After exercises and timing are confirmed, call `hiit_timer` with ordered work/rest segments. Summarize total time. When MCP Apps is supported, the interactive timer appears.

## Fallback: HTML timer when MCP Apps is not supported

If the client cannot render the interactive timer (for example a chat without MCP Apps, Grok Bot on mobile, or the user says they don't see a timer), do not just guide intervals in prose. Immediately generate the HTML timer from the repo template and send it as an attachment, without waiting to be asked:

1. Write the exact structured `hiit_timer` result (including `hiit_id`, `rounds`, `segments`, `warmup_s`, `cooldown_s`, and `name`) to a JSON file, then run `python server/render_timer_html.py /workspace/hiit-timer.html --config <hiit.json>` from the workout-tracker repo root (two levels above this skill folder). The template expands the segments exactly like the interactive timer: warm-up, each work segment with its exercise name and round, rests, and the cooldown, with the final rest of the last round skipped.
2. Never hand-write a custom timer page. The template already provides the big countdown, phase and "up next" lines, colors per phase, Start/Pause/Reset/Skip, a dark high-contrast theme, a 5-second get-ready period, a soft short-short-long beep cue when counting starts, at every interval change (lower pitch into rest), and at the finish, vibration where supported, and the screen-off safeguards documented in `docs/timer.md`.
3. Send the file as a `file://` attachment, then one short message: open it in the phone's browser, press Start, keep the screen on for the most reliable cues, and tell me when you finish or where you stopped so I can log it.

## Countdown timers (any timer request)

Every countdown timer the bot sends, HIIT or not (rest timer, plank hold, warm-up, stretching, "2-minute timer"), must come from the same template. Never write a timer page by hand.

- Single countdown: `python server/render_timer_html.py /workspace/timer-<slug>.html --duration <seconds or m:ss> --label "<label>"`.
- Work/rest sequence without a `hiit_timer` call: write `{"label": ..., "intervals": [{"kind": "work", "seconds": 40, "label": "Squats"}, {"kind": "rest", "seconds": 20}]}` to a JSON file and pass it with `--config`. Valid kinds are `work`, `rest`, `warmup`, `cooldown`, and `interval`.
- Optional: `--ready <seconds>` changes the 5-second get-ready period; `"speech": true` adds spoken countdown numbers.
- UI labels are English by default (Start, Pause, Reset). Keep the default unless the user asks for another language; then pass `--lang zh` or a `strings` override in the config. The timer label itself can be any text the user wants.
- Keep the file local and send it as a `file://` attachment. Never upload it to Google Drive or any other external host; it must work offline with no network requests.
- Be honest about screen-off behavior (details in `docs/timer.md`): time stays correct after the phone wakes, and beeps are pre-scheduled so they usually keep playing while locked on Android Chrome, but iOS may pause audio when locked, in-app viewers are less reliable than the real browser, and iPhones do not vibrate from web pages.

## Logging

Use `log_hiit` (with the `hiit_id` from `hiit_timer`) when the user or app reports completion or an early stop. Repeated callbacks may return `already_logged`; confirm without duplicating. Without a timer ID, use `log_activity` only when duration is known or explicitly estimated and labelled.

Never claim a session was saved until a logging tool succeeds.
