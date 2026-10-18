---
name: vblife-workout-hiit-session
description: Use when the user wants a HIIT or interval timer, asks to review or change HIIT preferences, or reports completing or stopping a timer session.
---
# HIIT sessions

On the first HIIT request, present the defaults and ask for exercise names before starting: work 40 seconds, rest 20 seconds, 20 rounds, warm-up 10 seconds, and cooldown 60 seconds. The user may change any value. Save a confirmed first configuration in `hiit_defaults` with `update_settings`.

On later requests, reuse saved defaults unless the user supplies overrides. For an override, ask whether it applies only now or should replace the saved default. Users may review, change, or clear preferences whenever they want.

After exercises and timing are confirmed, call `hiit_timer` with ordered work/rest segments. Summarize total time. When MCP Apps is supported, the interactive timer appears.

## Fallback: HTML timer when MCP Apps is not supported

If the client cannot render the interactive timer (for example a chat without MCP Apps, or the user says they don't see a timer), do not just guide intervals in prose. Immediately generate a self-contained HTML timer file and send it as an attachment, without waiting to be asked:

1. Write one HTML file (e.g. `/workspace/hiit-timer.html`) with inline CSS and JS only, no external assets, using the exact same segments returned by `hiit_timer`: warm-up, each work segment labeled with its exercise name and index (e.g. `Work 3/20`), rest segments showing the next exercise, and the cooldown.
2. Requirements: big countdown readable on a phone; current phase and exercise name; an "up next" line; distinct background colors for warm-up, work, rest, and cooldown; Start/Pause and Skip buttons; short Web Audio beeps on the last 3 seconds and at each transition; a finished screen at the end. Use the user's language for labels.
3. Send the file as an attachment, then one short message: open it in a browser, press Start, and tell me when you finish or where you stopped so I can log it.

## Logging

Use `log_hiit` (with the `hiit_id` from `hiit_timer`) when the user or app reports completion or an early stop. Repeated callbacks may return `already_logged`; confirm without duplicating. Without a timer ID, use `log_activity` only when duration is known or explicitly estimated and labelled.

Never claim a session was saved until a logging tool succeeds.
