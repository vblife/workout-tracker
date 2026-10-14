---
name: vblife-workout-nutrition
description: Use when the user shares a meal image, asks to estimate or log nutrition, reviews nutrient history, configures USDA mode, or sets or requests nutrition goals.
---
# Image-first nutrition

Analyze the meal image in conversation and create an ephemeral estimate of likely foods and portions. Do not send an image to USDA or any persistence tool.

Call `nutrition_status`. If no USDA key or remembered decision exists, briefly explain that a free FoodData Central key can improve grounding and point to `https://fdc.nal.usda.gov/api-key-signup/`. Do not block the meal: if the user declines or has no key, clearly use a model-only estimate and record `model_only_acknowledged` only when they choose it. With a key, call `search_usda` using text candidates only.

Show likely foods/portions, nutrient totals, method, confidence, and important uncertainties. Ask for explicit confirmation. Do not call `save_nutrition` before the user confirms. Corrections create a revised draft and require confirmation again.

After confirmation, call `save_nutrition` with `confirmed: true` and a nutrient-only draft: timestamp, calories, protein, carbohydrates, fat, fiber, saturated fat, sugar, sodium, method, confidence, USDA contribution, estimator version, component count, and generic uncertainty flags. Never pass or persist food names, descriptions, portions, images, search queries, responses, or FDC identifiers.

Nutrition goals are optional. Save direct confirmed targets with `create_goal`; if a value is extreme or internally inconsistent, flag it and ask the user to verify before saving. Use `recommend_nutrition_goals` only for supported adults using explicit inputs. When inputs are incomplete, present only the supported subset and explain what is still missing. Show assumptions and ask before saving recommendations. Never infer missing demographics or present educational estimates as medical advice.
