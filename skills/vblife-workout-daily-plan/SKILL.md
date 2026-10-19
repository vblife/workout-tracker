---
name: vblife-workout-daily-plan
description: Use when the user asks what to train today or on another day, requests a workout plan, or the scheduled daily-plan routine runs.
---
# Ephemeral daily planning

1. Call `daily_plan_context` for the requested date. Do this even when setup preferences are missing; use safe bodyweight choices and state assumptions instead of blocking.
2. Use the returned schedule progress, recovery, cardio cap, constraints, equipment status, candidate exercises, and server-backed prescriptions. Call `suggest_next` for a requested exercise when needed.
3. Produce a concise, usable plan with warm-up, ordered work, and optional cooldown. User constraints and pain override the suggested focus.
4. State that the plan is flexible: the user may follow it, change it, or pivot.

The plan remains in conversation. Merely generating or showing it must not call `start_workout`, create a routine, create a session, or log sets. Only after the user explicitly says to start the accepted plan may you call `start_workout` with `mode: conversational_plan` and snapshot its items. Later pivots change only the active snapshot.

All historical facts and prescriptions come from tools. Never log proposed work as completed.

If the user wants a countdown or rest timer, generate it from the shared repo timer template (`server/render_timer_html.py`) as described in `vblife-workout-hiit-session`: English UI by default (pass `lang` only when the user's stated language preference is Chinese, then use a Chinese label), sent as a local `file://` HTML attachment, never hosted externally. Never hand-write a timer page.
