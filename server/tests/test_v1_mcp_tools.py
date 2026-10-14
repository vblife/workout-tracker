import os
import sys
import tempfile
import unittest
import importlib.util

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import workouts

_spec = importlib.util.spec_from_file_location("workout_mcp_server", os.path.join(os.path.dirname(__file__), "..", "server.py"))
mcp_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_server)


EXPECTED_NEW_TOOLS = {
    "create_goal", "update_goal", "list_goals", "delete_goal", "goal_progress",
    "add_body_weight", "list_body_weight", "update_body_weight", "delete_body_weight",
    "create_routine", "list_routines", "get_routine", "update_routine", "archive_routine", "restore_routine", "start_routine",
    "start_workout", "get_active_workout", "finish_workout", "abandon_workout",
    "add_workout_item", "replace_workout_item", "reorder_workout_items", "set_workout_item_status",
    "move_set", "merge_workouts", "update_set", "update_activity",
    "nutrition_status", "search_usda", "validate_nutrition", "save_nutrition", "list_nutrition",
    "update_nutrition", "delete_nutrition", "nutrition_daily_totals", "recommend_nutrition_goals",
}


class MCPToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = mcp_server.Server(os.path.join(self.tmp.name, "workout.db"))

    def tearDown(self):
        self.s.c.close()
        self.tmp.cleanup()

    def call(self, name, arguments=None):
        return self.s.handle({"method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})

    def test_identity_generic_instructions_and_complete_tool_surface(self):
        init = self.s.handle({"method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        self.assertEqual(init["serverInfo"]["name"], "workout-bot")
        instructions = init["instructions"].lower()
        self.assertNotIn("charles", instructions)
        self.assertNotIn("must complete setup", instructions)
        tools = self.s.handle({"method": "tools/list", "params": {}})["tools"]
        names = {tool["name"] for tool in tools}
        self.assertTrue(EXPECTED_NEW_TOOLS.issubset(names))
        for tool in tools:
            self.assertFalse(tool["inputSchema"].get("additionalProperties", True), tool["name"])

    def test_setup_updates_only_explicit_preferences(self):
        first = self.call("update_settings", {"settings": {"unit": "kg", "reminders_enabled": False,
            "hiit_defaults": {"work_duration_s": 40, "rest_duration_s": 20, "rounds": 20, "warmup_s": 10, "cooldown_s": 60}}})
        self.assertFalse(first.get("isError", False))
        self.call("update_settings", {"settings": {"days_per_week": 4}})
        settings = self.call("get_context")["structuredContent"]["settings"]
        self.assertEqual(settings["unit"], "kg")
        self.assertFalse(settings["reminders_enabled"])
        self.assertEqual(settings["hiit_defaults"]["rounds"], 20)
        self.call("add_body_weight", {"value": 80, "unit": "kg"})
        context = self.call("get_context")["structuredContent"]
        self.assertEqual(context["latest_body_weight"]["input_value"], 80)
        self.assertIn("nutrition_setup", context)
        self.call("update_settings", {"settings": {"hiit_defaults": None}})
        self.assertNotIn("hiit_defaults", self.call("get_context")["structuredContent"]["settings"])

    def test_every_operation_reconciles_stale_workout(self):
        started = workouts.start_workout(self.s.c, mode="conversational_plan", at="2020-01-01T08:00:00Z")
        self.s.handle({"method": "tools/list", "params": {}})
        status = self.s.c.execute("SELECT status FROM sessions WHERE id=?", (started["session_id"],)).fetchone()[0]
        self.assertEqual(status, "abandoned")

    def test_domain_calls_return_structured_content_and_errors_roll_back(self):
        routine = self.call("create_routine", {"name": "Simple", "items": [{"exercise": "squat", "target_sets": 3, "target_reps": 5}]})
        self.assertEqual(routine["structuredContent"]["name"], "Simple")
        started = self.call("start_routine", {"routine_id": routine["structuredContent"]["routine_id"], "idempotency_key": "mcp-start"})
        self.assertEqual(started["structuredContent"]["start_mode"], "routine")
        bad = self.call("save_nutrition", {"confirmed": True, "draft": {"food": "private", "calories_kcal": 100,
            "estimation_method": "model_only", "confidence": "rough"}})
        self.assertTrue(bad["isError"])
        self.assertEqual(self.s.c.execute("SELECT count(*) FROM nutrition_entries").fetchone()[0], 0)

    def test_server_never_exposes_usda_secret(self):
        old = os.environ.get("USDA_FDC_API_KEY")
        os.environ["USDA_FDC_API_KEY"] = "top-secret-key"
        try:
            result = self.call("nutrition_status")
            self.assertTrue(result["structuredContent"]["usda_available"])
            self.assertNotIn("top-secret-key", str(result))
        finally:
            if old is None: os.environ.pop("USDA_FDC_API_KEY", None)
            else: os.environ["USDA_FDC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
