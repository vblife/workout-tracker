import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILLS = {
    "vblife-workout-config", "vblife-workout-log", "vblife-workout-daily-plan",
    "vblife-workout-progressive-overload", "vblife-workout-hiit-session",
    "vblife-workout-routine", "vblife-workout-nutrition", "vblife-workout-report",
    "vblife-workout-analysis", "vblife-workout-install",
}


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


class ProductContractTest(unittest.TestCase):
    def test_exact_skill_directories_and_frontmatter_names(self):
        path = os.path.join(ROOT, "skills")
        directories = {name for name in os.listdir(path) if os.path.isdir(os.path.join(path, name))}
        self.assertEqual(directories, SKILLS)
        for name in SKILLS:
            text = read(f"skills/{name}/SKILL.md")
            self.assertRegex(text, rf"(?m)^name: {re.escape(name)}$")
            self.assertRegex(text, r"(?m)^description: Use when ")

    def test_skill_files_are_english_only(self):
        for name in SKILLS:
            text = read(f"skills/{name}/SKILL.md")
            self.assertIsNone(re.search(r"[\u3400-\u9fff]", text), name)

    def test_skills_pin_high_risk_behavior(self):
        combined = "\n".join(read(f"skills/{name}/SKILL.md") for name in SKILLS)
        self.assertIn("40 seconds", combined)
        self.assertIn("20 seconds", combined)
        self.assertIn("20 rounds", combined)
        self.assertIn("10 seconds", combined)
        self.assertIn("60 seconds", combined)
        self.assertIn("explicit confirmation", combined)
        self.assertIn("nutrient-only", combined)
        self.assertIn("15 minutes", combined)
        self.assertIn("never log proposed work", combined.lower())
        self.assertIn("daily_plan_context", combined)
        self.assertIn("create_routine", combined)
        self.assertIn("query_stats", combined)

    def test_agent_contract_and_only_three_scheduled_routines(self):
        text = read("bot/instructions.md")
        self.assertIn("Workout Bot", text)
        self.assertIn("workout-bot", text)
        self.assertIn("ephemeral", text.lower())
        self.assertIn("explicit confirmation", text.lower())
        self.assertNotIn("Scheduled Grok Bot routines", text)
        routines = read("bot/routines.yaml")
        names = re.findall(r"(?m)^    name: (.+)$", routines)
        self.assertEqual(names, ["Daily plan", "Workout reminder", "Weekly report"])
        self.assertNotIn("Charles", text)
        self.assertNotIn("/home/box/workout", text)
        self.assertNotIn("fallback_url", text)
        self.assertNotIn("within 30 min", text)

    def test_readme_documents_runtime_privacy_and_testing(self):
        text = read("README.md")
        for expected in ("# Workout Bot", "/home/vblife/workout", "/workspace/vblife/workout-data/workout.db",
                         "USDA_FDC_API_KEY", "https://fdc.nal.usda.gov/api-key-signup/", "MCP Apps",
                         "npm run eval:workout", "python3 -m unittest", "daily plan", "workout reminder", "weekly report"):
            self.assertIn(expected, text)
        self.assertNotIn("Workout Tracker", text)
        self.assertNotIn("/home/box/workout", text)
        self.assertNotIn("site/", text)

    def test_live_server_copy_is_user_generic(self):
        text = "\n".join(read(path) for path in ("server/server.py", "server/coach.py"))
        self.assertNotIn("Charles", text)
        self.assertNotIn("workout-tracker", text)
        self.assertNotIn("/home/box/workout", text)
        self.assertNotIn("fallback_url", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
