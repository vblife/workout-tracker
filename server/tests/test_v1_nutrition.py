import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm
import core
import nutrition


class NutritionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "workout.db")
        self.c = dbm.connect(self.path)
        core.update_settings(self.c, {"timezone": "America/Los_Angeles"})
        self.draft = {
            "consumed_at": "2026-10-12T19:30:00Z", "capture_timezone": "America/Los_Angeles",
            "calories_kcal": 620, "protein_g": 38, "carbohydrates_g": 72, "fat_g": 21,
            "fiber_g": None, "estimation_method": "model_only", "confidence": "rough",
            "usda_contributed": False, "estimator_version": "agent-v1", "source_components": 3,
            "uncertainty_flags": ["portion_uncertain"],
        }

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_confirmation_is_required_before_save(self):
        with self.assertRaises(core.UserError):
            nutrition.save_entry(self.c, self.draft, confirmed=False)
        self.assertEqual(self.c.execute("SELECT count(*) FROM nutrition_entries").fetchone()[0], 0)

    def test_save_stores_only_nutrients_timestamp_and_estimation_metadata(self):
        saved = nutrition.save_entry(self.c, self.draft, confirmed=True)
        self.assertEqual(saved["local_date"], "2026-10-12")
        self.assertIsNone(saved["fiber_g"])
        self.assertEqual((saved["estimation_method"], saved["confidence"]), ("model_only", "rough"))
        schema = " ".join(r[1] for r in self.c.execute("PRAGMA table_info(nutrition_entries)"))
        for forbidden in ("food", "portion", "image", "description", "query", "fdc"):
            self.assertNotIn(forbidden, schema.lower())

    def test_privacy_fields_and_invalid_nutrients_are_rejected(self):
        for field in ("food", "portion", "image_url", "meal_description", "fdc_id", "usda_query"):
            payload = {**self.draft, field: "secret meal detail"}
            with self.assertRaises(core.UserError, msg=field):
                nutrition.validate_draft(payload)
        with self.assertRaises(core.UserError):
            nutrition.validate_draft({**self.draft, "protein_g": -1})
        with self.assertRaises(core.UserError):
            nutrition.validate_draft({k: v for k, v in self.draft.items() if k not in ("calories_kcal", "protein_g", "carbohydrates_g", "fat_g", "fiber_g")})

    def test_totals_update_and_delete(self):
        one = nutrition.save_entry(self.c, self.draft, confirmed=True)
        nutrition.save_entry(self.c, {**self.draft, "consumed_at": "2026-10-12T23:00:00Z", "calories_kcal": 300, "protein_g": 20}, confirmed=True)
        totals = nutrition.daily_totals(self.c, "2026-10-12")
        self.assertEqual((totals["entries"], totals["calories_kcal"], totals["protein_g"]), (2, 920, 58))
        nutrition.delete_entry(self.c, one["entry_id"])
        self.assertEqual(nutrition.daily_totals(self.c, "2026-10-12")["entries"], 1)

    def test_update_requires_fresh_confirmation(self):
        saved = nutrition.save_entry(self.c, self.draft, confirmed=True)
        with self.assertRaises(core.UserError):
            nutrition.update_entry(self.c, saved["entry_id"], {**self.draft, "calories_kcal": 500}, confirmed=False)
        updated = nutrition.update_entry(self.c, saved["entry_id"], {**self.draft, "calories_kcal": 500}, confirmed=True)
        self.assertEqual(updated["calories_kcal"], 500)

    def test_status_and_usda_failure_are_safe(self):
        status = nutrition.nutrition_status(self.c, environ={})
        self.assertFalse(status["usda_available"])
        self.assertNotIn("api_key", status)
        result = nutrition.search_usda("generic candidate", api_key="not-returned", opener=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
        self.assertEqual(result["status"], "model_only_fallback")
        self.assertNotIn("not-returned", str(result))

    def test_recommendations_require_supported_adult_inputs(self):
        with self.assertRaises(core.UserError):
            nutrition.recommend_goals({"age": 17, "weight_kg": 70, "height_cm": 175, "sex": "male", "activity_level": "moderate", "objective": "maintenance"})
        result = nutrition.recommend_goals({"age": 35, "weight_kg": 80, "height_cm": 180, "sex": "male", "activity_level": "moderate", "objective": "muscle_gain"})
        self.assertGreater(result["recommendations"]["calories_kcal"], 2000)
        self.assertEqual(result["version"], "mifflin-dga-v1")
        self.assertFalse(result["saved"])

    def test_partial_recommendations_do_not_fabricate_missing_profile_values(self):
        result = nutrition.recommend_goals({"age": 35, "weight_kg": 80})
        self.assertNotIn("calories_kcal", result["recommendations"])
        self.assertIn("protein_g_range", result["recommendations"])
        self.assertIn("height_cm", result["missing_for_energy"])
        self.assertEqual(result["assumptions"], {"age": 35, "weight_kg": 80})

    FULL = {"age": 40, "weight_kg": 90, "height_cm": 178, "sex": "male", "activity_level": "light", "objective": "fat_loss"}

    def test_invalid_enum_errors_list_accepted_values(self):
        cases = [("objective", "bulk", "maintenance, fat_loss, muscle_gain"),
                 ("activity_level", "high", "sedentary, light, moderate, active, very_active"),
                 ("sex", "other", "male, female")]
        for key, value, accepted in cases:
            with self.assertRaises(core.UserError) as raised:
                nutrition.recommend_goals({**self.FULL, key: value})
            self.assertIn(accepted, str(raised.exception))
            self.assertIn(repr(value), str(raised.exception))
        with self.assertRaises(core.UserError):
            nutrition.recommend_goals({**self.FULL, "height_cm": 0})

    def test_low_risk_aliases_normalize_to_canonical_inputs(self):
        canonical = nutrition.recommend_goals(self.FULL)
        for alias in ("lose", "weight_loss"):
            result = nutrition.recommend_goals({**self.FULL, "objective": alias})
            self.assertEqual(result["recommendations"], canonical["recommendations"])
            self.assertEqual(result["assumptions"]["objective"], "fat_loss")
        self.assertEqual(nutrition.recommend_goals({**self.FULL, "objective": "maintain"})["assumptions"]["objective"], "maintenance")
        aged = {k: v for k, v in self.FULL.items() if k != "age"}
        result = nutrition.recommend_goals({**aged, "age_years": 40})
        self.assertEqual(result["recommendations"], canonical["recommendations"])
        self.assertEqual(result["assumptions"]["age"], 40)
        self.assertNotIn("ignored_inputs", result)
        with self.assertRaises(core.UserError):
            nutrition.recommend_goals({**self.FULL, "age_years": 41})
        with self.assertRaises(core.UserError):  # alias still goes through the adult guardrail
            nutrition.recommend_goals({**aged, "age_years": 16})
        with self.assertRaises(core.UserError):  # ambiguous words are not guessed
            nutrition.recommend_goals({**self.FULL, "objective": "gain"})

    def test_unused_inputs_are_reported_not_silently_dropped(self):
        result = nutrition.recommend_goals({**self.FULL, "goal_weight_kg": 80, "target_date": "2027-03-01"})
        self.assertEqual(result["ignored_inputs"], ["goal_weight_kg", "target_date"])
        self.assertNotIn("goal_weight_kg", result["assumptions"])
        for flag in nutrition.GUARDRAIL_FLAGS:
            with self.assertRaises(core.UserError):
                nutrition.recommend_goals({**self.FULL, flag: True})

    def test_recommendation_keys_map_to_goal_metrics(self):
        import goals
        rec = nutrition.recommend_goals(self.FULL)["recommendations"]
        self.assertEqual(set(rec), {"calories_kcal", "calories_kcal_max", "maintenance_kcal", "protein_g", "protein_g_range",
                                    "fiber_g", "saturated_fat_g_max", "sodium_mg_max"})
        self.assertIn("maintenance_kcal", core.SETTING_KEYS)  # a setting, not a goal metric
        for key in set(rec) - {"maintenance_kcal"}:
            metric = key.replace("_range", "").replace("_max", "")
            self.assertIn(metric, goals.NUTRITION_METRICS, key)

    def test_fat_loss_recommends_calorie_maximum_and_maintenance(self):
        fat_loss = nutrition.recommend_goals({**self.FULL, "objective": "fat_loss"})["recommendations"]
        maintenance = nutrition.recommend_goals({**self.FULL, "objective": "maintenance"})["recommendations"]
        self.assertEqual(fat_loss["calories_kcal_max"], fat_loss["calories_kcal"])
        self.assertNotIn("calories_kcal_range", fat_loss)
        self.assertEqual(fat_loss["maintenance_kcal"], maintenance["calories_kcal"])
        self.assertEqual(maintenance["maintenance_kcal"], maintenance["calories_kcal"])
        self.assertNotIn("calories_kcal_max", maintenance)
        self.assertLess(fat_loss["calories_kcal_max"], fat_loss["maintenance_kcal"])
        self.assertEqual(nutrition.recommend_goals({**self.FULL, "objective": "muscle_gain"})["recommendations"].keys()
                         & {"calories_kcal_max"}, set())

    def test_maintenance_kcal_is_a_first_class_setting(self):
        saved = core.update_settings(self.c, {"maintenance_kcal": 2370})["settings"]
        self.assertEqual(saved["maintenance_kcal"], 2370.0)
        for bad in (0, -5, 50000, "2370", True):
            with self.assertRaises(core.UserError):
                core.update_settings(self.c, {"maintenance_kcal": bad})
        self.assertEqual(core.get_settings(self.c)["maintenance_kcal"], 2370.0)
        self.assertNotIn("maintenance_kcal", core.update_settings(self.c, {"maintenance_kcal": None})["settings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
