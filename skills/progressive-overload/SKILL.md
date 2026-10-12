---
name: progressive-overload
description: Use when the user asks what weight/reps/sets to do next for an exercise, whether to add weight, why progress stalled, or how progression works.
---
# Progressive overload

## Steps
1. `suggest_next {"exercise": "<name as user said>"}`. Optional: `target_rep_range` like `"5-8"` if the user wants strength-focused reps (or `"12-15"` for endurance), `sets` (default 3).
2. Present `sets × reps @ weight unit` and translate `rationale` into one plain sentence. Explain by `status`:
   - `increase_weight` — all top sets reached the top of the rep range → add the increment, drop back to the bottom of the range.
   - `add_reps` — keep the weight, add a rep per set until every set hits the top of the range.
   - `deload` — missed the bottom of the range two sessions in a row, or estimated 1RM fell >5% → ~10% lighter to rebuild.
   - `reset` — more than 14 days off → ~10% lighter to ease back in.
   - `no_history` — give the starting tip; ask them to log the first session.
   - `progress` — bodyweight/timed/cardio progression (more reps, an extra set, longer hold; cardio ≤10%/week).
3. If `equipment_status` is `unknown`/`unavailable`, mention it and offer an alternative exercise that fits their gear (check with `resolve_exercise` / `suggest_next` on the alternative).
4. When asked "how does this work": double progression in a rep range (default from settings `rep_range_default`, else 8–12); estimated 1RM by Epley = weight × (1 + reps/30); increments +5 lb upper / +10 lb lower (kg: per-exercise step); RPE above 8.5 blocks a weight increase.

## Rules
- Never compute your own prescription numbers; use the tool's. You may add form/recovery advice.
- To change the default rep range permanently: `update_settings {"settings":{"rep_range_default":"6-10"}}`.
