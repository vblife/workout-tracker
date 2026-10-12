---
name: workout-onboarding
description: Use on first contact or whenever get_context returns a non-empty onboarding_required, or when the user wants to change units, training days, goals or equipment.
---
# Workout onboarding

## Steps
1. Call `get_context` (no args). If `onboarding_required` is empty and the user isn't asking to change settings, skip this skill.
2. Ask the missing items **one question per message**, in this order, with short options:
   1. Unit — 「重量用 磅(lb) 还是 公斤(kg)?」 → `unit`: `"lb"` | `"kg"`
   2. Days per week — 「每周计划练几天?(例如 4–5)」 → `days_per_week`: integer 1–7
   3. Goals — 「主要目标?可多选:力量 / 增肌 / 减脂 / 有氧耐力 / 徒步」 → `goals`: list of short English tags, e.g. `["strength","cardio","hiking"]` (map: 力量→strength, 增肌→hypertrophy, 减脂→fat_loss, 有氧/耐力→cardio, 徒步/爬山→hiking)
   4. Equipment — 「在哪练、有什么器械?(健身房 / 家里哑铃 / 只有自重…)」 → `equipment`: list from `barbell, dumbbell, machine, cable, kettlebell, bodyweight, other` (full gym → all of them; home dumbbells → `["dumbbell","bodyweight"]`). Optional; if the user skips, leave it unset (tools mark gear as `unknown`).
   5. Optional, only if the user volunteers or asks: `preferred_days` (list), `session_minutes` (int), `experience`, `injuries` (string), `bodyweight` (number), `rep_range_default` (like `"8-12"`).
3. After **each** answer call `update_settings` with just that key, e.g. `{"settings":{"unit":"kg"}}`. Saving incrementally means nothing is lost if the user leaves halfway.
4. When `update_settings` returns `onboarding_required: []`, confirm in 1–2 lines and offer a next step: 「设置好了 ✅ 想现在记录一组、看今天的训练计划,还是开一个 HIIT 计时器?」

## Rules
- Accept answers in any language/format ("kg", "公斤", "5天", "five") and normalize them yourself.
- Never invent settings. Don't re-ask anything already in `settings`.
- To change one setting later, call `update_settings` with only that key; no need to rerun onboarding.
- Do not set `fallback_base_url` unless the user gives you an https URL for it.
