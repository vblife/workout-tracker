---
name: vblife-workout-nutrition
description: Use when the user shares a meal image, asks to estimate or log nutrition, reviews nutrient history, configures USDA mode, or sets or requests nutrition goals.
---
# Image-first nutrition

Analyze the meal image in conversation and create an ephemeral estimate of likely foods and portions. Do not send an image to USDA or any persistence tool.

Call `nutrition_status`. If no USDA key or remembered decision exists, briefly explain that a free FoodData Central key can improve grounding and point to `https://fdc.nal.usda.gov/api-key-signup/`. Do not block the meal: if the user declines or has no key, clearly use a model-only estimate and record `model_only_acknowledged` only when they choose it. With a key, call `search_usda` using text candidates only.

Show likely foods/portions, nutrient totals, method, confidence, and important uncertainties. Ask for explicit confirmation. Do not call `save_nutrition` before the user confirms. Corrections create a revised draft and require confirmation again.

After confirmation, call `save_nutrition` with `confirmed: true` and a nutrient-only draft: timestamp (a value without an offset is user-local; omit `capture_timezone` to use the user's timezone setting), calories, protein, carbohydrates, fat, fiber, saturated fat, sugar, sodium, method, confidence, USDA contribution, estimator version, component count, and generic uncertainty flags. Never pass or persist food names, descriptions, portions, images, search queries, responses, or FDC identifiers.

## Nutrition goals

Nutrition goals are optional. Save direct confirmed targets with `create_goal`; if a value is extreme or internally inconsistent, flag it and ask the user to verify before saving. Use `recommend_nutrition_goals` only for supported adults using explicit inputs. When inputs are incomplete, present only the supported subset and explain what is still missing. Show assumptions and ask before saving recommendations. Never infer missing demographics or present educational estimates as medical advice.

### Goal setup workflow

Use this when a user asks for help setting daily nutrition goals. It is repeatable, never a prerequisite for logging.

1. **Check what exists.** Call `list_goals` with `domain: "nutrition"` and `get_context` (for units and the latest body weight). Summarize any active nutrition goals and ask whether to replace or keep them.
2. **Gather explicit inputs.** Ask only for what is missing, in one short message: age, sex (used only for the energy equation), height, current weight, goal weight and target date, and activity level (`sedentary`, `light`, `moderate`, `active`, or `very_active`). Also ask whether any health constraints or dietary limits apply. Never infer sex or age from a name, photo, voice, or writing style. A logged body weight counts only if the user confirms it is current. Convert lb to kg and feet/inches to cm.
3. **Pick the objective.** Use the user's goal weight and date to choose one of the accepted values: `fat_loss` (goal below current weight), `maintenance`, or `muscle_gain`. Goal weight and target date are not tool inputs. The tool uses a fixed adjustment (about 350 kcal/day below maintenance for `fat_loss`, 250 above for `muscle_gain`). If the implied weekly pace differs a lot from that, say so plainly and let the user choose how to proceed. For `fat_loss`, the calorie goal is a daily maximum (eat at or under it), never a range.
4. **Guardrails.** If the user reports pregnancy, breastfeeding, an eating disorder, or a medical condition for which a clinician manages their diet, pass the matching flag (`pregnant`, `breastfeeding`, `eating_disorder`, `medical_condition`). The tool then declines; suggest a registered dietitian or their clinician. The user may still save targets they bring from that clinician as direct goals. Never leave out a reported flag to get numbers.
5. **Call `recommend_nutrition_goals`** with `inputs` using these exact keys: `age`, `sex` (`male` or `female`), `height_cm`, `weight_kg`, `activity_level`, `objective`. Do not send `age_years`, `lose`, or other spellings, and do not send goal weight or date (unused keys come back in `ignored_inputs`). If the tool returns an error listing accepted values, fix the input and retry once.
6. **Present the result.** Show `calories_kcal` (for `fat_loss` it is also returned as `calories_kcal_max`; present it as a daily maximum, for example "at most N kcal/day"), `maintenance_kcal` (the estimated maintenance energy, also called TDEE), `protein_g` and `protein_g_range`, `fiber_g`, `saturated_fat_g_max`, `sodium_mg_max`, the `assumptions`, the `basis`, and the `notice` that this is an educational estimate. The tool gives no sugar value; offer a sugar maximum only if the user wants one and chooses the number.
7. **Adjustments the user asks for.** When the user states a constraint (for example high LDL cholesterol or a kidney or creatinine concern), you may offer common adjustments as options. Examples: saturated fat at or below about 6% of calories instead of the generic 10%, protein toward the lower end of the range, or fiber of at least 30 g. Present them as user-requested adjustments, not medical advice. Show the resulting numbers and suggest confirming them with their clinician. Do not raise or lower a target on your own.
8. **Confirm the full set.** List every goal to save with its metric, semantics, value or range, and unit, plus the maintenance energy to save as `settings.maintenance_kcal`. Ask for explicit confirmation. Save nothing until the user agrees.
9. **Save each metric once with `create_goal`.** Use `domain: "nutrition"`, `period: "day"`, `effective_start_date` set to today's user-local date (YYYY-MM-DD), and `recommendation_version` set to the returned `version`. If a goal for that metric is already active, use `update_goal` on it instead of creating a second one.

   | Metric | Semantics | Unit | Source |
   |---|---|---|---|
   | `calories_kcal` (objective `fat_loss`) | `maximum` | `kcal` | `calories_kcal_max`; never a range |
   | `calories_kcal` (`maintenance` or `muscle_gain`) | `range` (or `exact`) | `kcal` | `calories_kcal`; a range only with bounds the user chooses |
   | `protein_g` | `range` (or `minimum`) | `g` | `protein_g_range` |
   | `saturated_fat_g` | `maximum` | `g` | `saturated_fat_g_max` |
   | `fiber_g` | `minimum` | `g` | `fiber_g` |
   | `sodium_mg` | `maximum` | `mg` | `sodium_mg_max` |
   | `sugar_g` | `maximum` | `g` | user-chosen only |

   Use `lower_value` and `upper_value` for `range` and `target_value` for the others. Use `origin: "recommended"` for a value saved unchanged from the tool and `origin: "user"` for anything the user set or adjusted. Put the inputs and any adjustment reason in `assumptions` as short strings, for example `["age 40", "weight_kg 90", "objective fat_loss", "user-requested: saturated fat about 6% of calories"]`. `carbohydrates_g` and `fat_g` goals are also accepted when the user asks for them.
10. **Save maintenance energy as a setting.** When the user confirms the `maintenance_kcal` estimate, or gives their own maintenance number (for example from a tracker or clinician), save it with `update_settings` as `{"maintenance_kcal": <number>}`. This is the only place reports read maintenance from; do not keep it only in goal `assumptions` or free text. Update it the same way when the user's weight or activity changes and they confirm a new estimate. Send `null` only if the user asks to clear it.
11. **Confirm and offer a daily check.** Report what was saved. Then offer an optional evening nutrition check at a time the user picks. Create it as a scheduled routine only if they accept, as a plain 5-field cron in user-local time with no `CRON_TZ=` prefix; it is not one of the default routines. Suggested prompt: "Call `list_goals` with domain nutrition and `goal_progress` for each active goal with anchor_date set to today's user-local date, plus `nutrition_daily_totals` for today. Send a short summary of each goal (met, not yet met, or over the limit) and what is left. Treat nutrients missing from logs as unknown, not zero. Do not log anything."

### Energy deficit

Energy deficit is `settings.maintenance_kcal` minus confirmed calorie intake for a day. The target deficit is `maintenance_kcal` minus the user's own `calories_kcal` maximum goal; meeting the deficit target is the same as staying at or under that maximum. Use each user's own numbers from `get_context`, `list_goals`, and `report`'s `energy_balance`; never assume a calorie number. Workout-burned calories are a separate estimate and are never added to or subtracted from the deficit. Example: maintenance 2370 kcal and a 1900 kcal maximum give a target deficit of 470 kcal; eating 1800 kcal is a 570 kcal deficit, 100 kcal under the maximum.

Label it "energy deficit" in English. When speaking Chinese, use the standard Chinese term for energy deficit (pinyin "nengliang quekou"), and describe the calorie goal as an upper limit rather than a range. If `maintenance_kcal` is not set, say the deficit cannot be computed yet and offer to estimate or record it.

`nutrition_status` reports USDA estimation mode, not goal progress; use `goal_progress` and `nutrition_daily_totals` for comparisons.
