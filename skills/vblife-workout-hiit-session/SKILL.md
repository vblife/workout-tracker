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

1. Write the structured `hiit_timer` result (including `hiit_id`, `rounds`, `segments`, `warmup_s`, `cooldown_s`, and `name`) to a JSON file, with `name` and every exercise name in the timer's UI language (English by default; translate names if needed and keep all timing values exactly as returned), then run `python server/render_timer_html.py /workspace/hiit-timer.html --config <hiit.json>` (add `--lang zh` only under the language rule below) from the workout-tracker repo root (two levels above this skill folder). The template expands the segments exactly like the interactive timer: warm-up, each work segment with its exercise name and round, rests, and the cooldown, with the final rest of the last round skipped.
2. Never hand-write a custom timer page. The template already provides the big countdown, phase and "up next" lines, colors per phase, Start/Pause/Reset/Skip, a dark high-contrast theme, a 5-second get-ready period, a soft short-short-long beep cue when counting starts, at every interval change (lower pitch into rest), and at the finish, vibration where supported, and the screen-off safeguards documented in `docs/timer.md`.
3. Send the file as a `file://` attachment, then one short message: open it in the phone's browser, press Start, keep the screen on for the most reliable cues, and tell me when you finish or where you stopped so I can log it.

## Countdown timers (any timer request)

Every countdown timer the bot sends, HIIT or not (rest timer, plank hold, warm-up, stretching, "2-minute timer"), must come from the same repo template, with an English UI by default and a label in the UI's language, delivered as a local `file://` HTML attachment. Never write a timer page by hand and never host it externally.

- Single countdown: `python server/render_timer_html.py /workspace/timer-<slug>.html --duration <seconds or m:ss> --label "<label>"`.
- Default 40/20 HIIT (one exercise per round) when the user has not fixed the round count: send one file that lets them pick 10 or 20 rounds before Start: `python server/render_timer_html.py /workspace/hiit-timer.html --work 40 --rest 20 --rounds 10,20 --label "HIIT 40/20"`. Add `--default-rounds 20` to preselect 20. A single number (`--rounds 10`) gives a fixed count with no selector. The config equivalent is `"round_options": [10, 20]` next to `segments` (with `rounds` as the default). The final rest is skipped, so the totals are 9:40 and 19:40 plus the 5-second get ready.
- Work/rest sequence without a `hiit_timer` call: write `{"label": ..., "intervals": [{"kind": "work", "seconds": 40, "label": "Squats"}, {"kind": "rest", "seconds": 20}]}` to a JSON file and pass it with `--config`. Valid kinds are `work`, `rest`, `warmup`, `cooldown`, and `interval`.
- Optional: `--ready <seconds>` changes the 5-second get-ready period; `"speech": true` adds spoken countdown numbers.
- Language: the timer UI is English by default (Start, Pause, Reset, Get ready, ...), even when the user chats in another language. Pass `--lang zh` (or `"lang": "zh"` in the config) only when the user's stated language preference is Chinese, meaning they asked for a Chinese timer or Chinese UI, or saved that preference. Chatting in Chinese is not enough. With `zh`, write the label and interval labels in Chinese; otherwise pass English labels (for example `--label "2-minute timer"`, `"label": "Squats"`). Only `en` and `zh` exist; any other value is rejected. Labels are shown exactly as passed. With `en` the generator prints a warning on stderr if a label contains CJK characters; if you see it, regenerate with an English label. Spoken cues (`"speech": true`) follow the UI language. Your chat reply can still be in the user's language.
- Keep the file local and send it as a `file://` attachment. Never upload it to Google Drive or any other external host; it must work offline with no network requests.
- Be honest about screen-off behavior (details in `docs/timer.md`): time stays correct after the phone wakes, and beeps are pre-scheduled so they usually keep playing while locked on Android Chrome, but iOS may pause audio when locked, in-app viewers are less reliable than the real browser, and iPhones do not vibrate from web pages.

## Logging

Use `log_hiit` (with the `hiit_id` from `hiit_timer`) when the user or app reports completion or an early stop. Repeated callbacks may return `already_logged`; confirm without duplicating. Without a timer ID, use `log_activity` only when duration is known or explicitly estimated and labelled.

Never claim a session was saved until a logging tool succeeds.
