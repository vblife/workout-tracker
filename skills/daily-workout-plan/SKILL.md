---
name: daily-workout-plan
description: Use when the user asks what to do today/this session/tomorrow, wants a workout plan or routine, or a scheduled daily-plan routine fires.
---
# Daily workout plan

## Steps
1. `get_context {}`. If `onboarding_required` is non-empty, run the workout-onboarding skill first (at least unit + days/week).
2. `daily_plan_context {}` (or `{"date":"YYYY-MM-DD"}` for another day). Read:
   - `suggested_focus` + `rationale` (upper | lower | full_body | cardio_hiit | hike | rest_or_mobility | optional_light)
   - `week` (days_trained, target, still_needed, cardio_minutes vs last week, `cardio_cap_10pct`, hikes)
   - `muscle_recovery` (48h rule), `candidate_exercises` (each may include `suggestion` from suggest_next), `equipment_known`, `notes`.
3. Respect user constraints from the message (time available, "no legs today", location, injury) — they override `suggested_focus`; explain briefly if you deviate.
4. Build the plan:
   - **Strength day**: 4–6 exercises from `candidate_exercises` covering the focus (compound first, then accessories, then core). Prefer exercises with history (`times_logged` > 0) and use their `suggestion` (sets × reps @ weight) exactly. For exercises without history call `suggest_next {"exercise": "..."}` or give the no-history starting tip.
   - **Equipment unknown** (`equipment_known: false` or `equipment_status: "unknown"`): give a bodyweight/dumbbell-friendly plan or offer a gym and a no-equipment version side by side, and ask once what gear is available (then `update_settings {"settings":{"equipment":[...]}}`). Never assume a barbell.
   - **cardio_hiit**: suggest 20–30 min cardio or a HIIT block (offer to start it with the hiit-session skill). Keep weekly cardio ≤ `cardio_cap_10pct` minutes.
   - **hike**: suggest a hike (duration/elevation progression ≤10% over recent hikes) plus optional mobility.
   - **rest_or_mobility / optional_light**: recommend rest, walking, stretching; no hard training.
   - Add a 5-min warm-up and, on strength days, a short cardio finisher if the week's cardio is behind.
5. Reply concisely (Chinese by default):
   ```
   今天:上肢(腿部还在恢复,本周已练 2/5 天)
   1. 杠铃卧推 3×8 @125 lb(上次 3×12 @120 全完成 → 加重)
   2. 哑铃划船 3×10 @45 lb
   ...
   收尾:15 分钟椭圆机
   ```
   Then offer: 「要我开始计时 / 边练边记录吗?」

## Rules
- All numbers (weights, reps, days, minutes) come from tool output; if a tool gave none, say so instead of inventing.
- Don't log anything just because it was planned; log only what the user reports doing.
