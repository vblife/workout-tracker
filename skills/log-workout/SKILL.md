---
name: log-workout
description: Use when the user reports exercise they did (sets, reps, weights, holds, runs, hikes, cardio) in any language, or asks to correct/undo/delete something logged.
---
# Log a workout

## 1. Parse the message into `log_sets` args
One message can contain several exercises. Build `sets: [...]`, one item per distinct set pattern:
- `exercise`: the name exactly as the user said it (「卧推」, "bench", "push ups"). The server normalizes Chinese/English/aliases; don't translate.
- `reps` (int), `weight` (number, omit for bodyweight), `unit` (`lb`/`kg`; 磅→lb, 公斤/千克→kg; omit if not stated so settings apply).
- `count` = number of identical sets: "3x8" / "3×8" / "3组8个" / "8 reps × 3 sets" → `reps: 8, count: 3`. "5x5 @225" → `reps 5, count 5, weight 225`.
- Different reps per set ("8, 7, 6 at 135") → separate items (or one item each).
- Holds/time: "plank 60s", 「平板支撑 1分钟」 → `duration_s: 60`. Distance for a cardio exercise set: `distance_m`.
- Optional: `rpe` ("RPE 8", 「很吃力」≈9), `is_warmup: true` for 热身组, `note`, and always `raw_text` = the user's original phrase.
- `performed_at`: only if the user says when ("yesterday 6pm", 「今天早上」). Use ISO local time without zone, e.g. `2026-10-10T18:00:00` (treated as the user's local time). Otherwise omit (= now).

Examples
- "10 push ups" → `{"sets":[{"exercise":"push ups","reps":10,"raw_text":"10 push ups"}]}`
- "bench press, 8 reps, 120lb" → `{"sets":[{"exercise":"bench press","reps":8,"weight":120,"unit":"lb","raw_text":"..."}]}`
- 「深蹲 3x8 100公斤,然后引体向上 10 个」 → two items: squat `reps 8, count 3, weight 100, unit kg`; 引体向上 `reps 10`.

If a number is genuinely missing (e.g. weight for a barbell lift), ask once briefly; don't guess.

## 2. Handle the result
- `status: "logged"` → confirm in one short line per exercise using the returned canonical name and numbers, e.g. 「✅ 已记录:杠铃卧推 8×120 lb(e1RM 152)」. If any `is_pr: true`, add 「🎉 新纪录!」. Don't dump JSON.
- `status: "needs_confirmation"` → **nothing was saved**. For each problem:
  - candidates exist → ask once: 「“X” 是指 A 吗?(或 B)」. On yes: `add_alias {"exercise": <exercise_id or canonical>, "alias": "<user's term>"}`, then retry the same `log_sets`.
  - no good candidate → ask what it is (category + main muscles + equipment), then `add_exercise {"canonical": "English Title Case", "category": "...", "name_zh": "...", "muscle_groups": [...], "equipment": "...", "is_bodyweight": bool, "tracking": "reps_weight|reps|duration|distance", "aliases": ["<user's term>"]}`, then retry.
  - validation errors (missing reps etc.) → ask for the missing value.
- `isError: true` → explain briefly and fix the args; never claim it was saved.

## 3. Whole cardio sessions / hikes → `log_activity`
"hiked 3 hours, 7 miles, 2000 ft", 「跑步 30 分钟 5 公里」 → `log_activity {"type":"hike"|"run"|"walk"|"cycle"|"swim"|"row"|"cardio"|"other","duration_min":..., "distance":..., "distance_unit":"mi"|"km", "elevation_gain":..., "elevation_unit":"ft"|"m", "performed_at"?, "rpe"?, "notes"?}`. Units default to mi/ft when settings.unit is lb, km/m when kg — pass explicit units if the user stated them. Duration is required; ask if missing.

## 4. Corrections
- 「撤销」/"undo"/"delete that" → `delete_set {}` (removes the most recent set). Confirm what was deleted.
- Specific set → `list_recent {"limit": 10}` (optionally `exercise`), identify the `set_id`, `delete_set {"set_id": N}`.
- "Actually it was 110 not 120" → delete the wrong set, then `log_sets` the corrected one (keep the original `performed_at` if it was in the past).
- Wrong hike/cardio/HIIT session → find `session_id` (from earlier results or `get_context`) and `delete_session`. Confirm before deleting a whole session with many sets.

## Rules
- Log first, chat second: keep replies short (the user may be mid-workout).
- Never invent sets, weights or PRs; only report what the tool returned.
