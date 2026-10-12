---
name: hiit-session
description: Use when the user wants a HIIT/interval/Tabata/EMOM-style timer, describes intervals to run, or reports finishing a HIIT timer (including pasting the timer's "finished HIIT #id" line).
---
# HIIT session

## 1. Build the timer config
Translate the description into `hiit_timer` args:
- `segments`: ordered list run each round, each `{"kind":"work"|"rest","seconds":N,"exercise":"..."}` (exercise optional; use the user's names).
- `rounds` (1–100), optional `warmup_s`, `cooldown_s`, `name`.
- The final rest of the last round is skipped automatically.
Examples
- "Tabata burpees" → `{"name":"Tabata","rounds":8,"segments":[{"kind":"work","seconds":20,"exercise":"Burpee"},{"kind":"rest","seconds":10}]}`
- 「40秒开合跳、20秒休息、40秒登山跑、20秒休息,做5轮,热身2分钟」 → rounds 5, warmup_s 120, four segments in that order.
- "EMOM 10 min, 10 kettlebell swings" → rounds 10, one `work` 60s segment with exercise "Kettlebell Swing ×10".
If key info is missing (work/rest seconds or rounds), propose a sensible default and ask for a quick OK only if the user seems to care; otherwise just start.

## 2. Start it
Call `hiit_timer`. Then reply briefly with the plan summary and total time from the result.
- Desktop app: the timer UI renders in chat; pressing Start → Finish logs automatically (via `log_hiit`). Say: 「计时器已就绪,点 Start 开始,完成后会自动记录。」
- Mobile / no UI: if `fallback_url` is non-null, give it: 「手机上打开这个链接计时:<url>」. If it is null, say the interactive timer shows only in the desktop app and offer to talk them through it or to log manually afterwards. Remember the `hiit_id` from the result.

## 3. Log completion when it didn't auto-log
- User pastes 「finished HIIT #12 — 8 rounds, 230s」 or says they finished → `log_hiit {"hiit_id":12,"completed_rounds":8,"total_seconds":230,"status":"completed"}` (use `"aborted"` if they stopped early; estimate seconds from what they say if needed and say it's an estimate).
- Result `already_logged` → it was logged by the UI; just confirm.
- No hiit_id known (did intervals without the timer) → `log_activity {"type":"cardio","duration_min":N,"title":"HIIT","notes":"<structure>"}`.

## Rules
- Never claim a session was logged unless `log_hiit`/`log_activity` returned success.
