import json
import os
import subprocess
import sys
import tempfile
import unittest


SERVER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "server.py"))


class StdioE2ETest(unittest.TestCase):
    def test_fresh_private_database_full_stdio_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = subprocess.Popen([sys.executable, SERVER, "--stdio", "--db", os.path.join(tmp, "workout.db")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            next_id = 0

            def request(method, params=None):
                nonlocal next_id
                next_id += 1
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": next_id, "method": method, "params": params or {}}) + "\n")
                process.stdin.flush()
                response = json.loads(process.stdout.readline())
                self.assertEqual(response["id"], next_id)
                self.assertNotIn("error", response)
                return response["result"]

            init = request("initialize", {"protocolVersion": "2025-06-18",
                           "capabilities": {"extensions": {"io.modelcontextprotocol/ui": {}}}})
            self.assertEqual(init["serverInfo"]["name"], "workout-bot")
            tools = {item["name"] for item in request("tools/list")["tools"]}
            self.assertIn("merge_workouts", tools)
            request("tools/call", {"name": "update_settings", "arguments": {"settings": {"timezone": "Asia/Tokyo", "unit": "kg"}}})
            logged = request("tools/call", {"name": "log_sets", "arguments": {"performed_at": "2026-10-01T15:30:00Z",
                             "sets": [{"exercise": "squat", "reps": 5, "weight": 100, "unit": "kg"}]}})
            self.assertEqual(logged["structuredContent"]["new_session"], True)
            report = request("tools/call", {"name": "report", "arguments": {"period": "day", "anchor_date": "2026-10-02"}})
            self.assertIn("structuredContent", report)
            html = request("resources/read", {"uri": "ui://workout/report.html"})["contents"][0]["text"]
            self.assertIn('id="trend-chart"', html)
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
