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


if __name__ == "__main__":
    unittest.main(verbosity=2)
