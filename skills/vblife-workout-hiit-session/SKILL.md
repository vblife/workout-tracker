---
name: vblife-workout-hiit-session
description: Use when the user wants a HIIT or interval timer, asks to review or change HIIT preferences, or reports completing or stopping a timer session.
---
# HIIT sessions

On the first HIIT request, present the defaults and ask for exercise names before starting: work 40 seconds, rest 20 seconds, 20 rounds, warm-up 10 seconds, and cooldown 60 seconds. The user may change any value. Save a confirmed first configuration in `hiit_defaults` with `update_settings`.

On later requests, reuse saved defaults unless the user supplies overrides. For an override, ask whether it applies only now or should replace the saved default. Users may review, change, or clear preferences whenever they want.

After exercises and timing are confirmed, call `hiit_timer` with ordered work/rest segments. Summarize total time. When MCP Apps is supported, the interactive timer appears; otherwise guide the intervals conversationally using the same configuration.

Use `log_hiit` when the user or app reports completion or an early stop. Repeated callbacks may return `already_logged`; confirm without duplicating. Without a timer ID, use `log_activity` only when duration is known or explicitly estimated and labelled.

Never claim a session was saved until a logging tool succeeds.
