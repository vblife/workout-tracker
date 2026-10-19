import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core
import render_timer_html as r

PREFIX = "<script>window.openai={toolOutput:"


def template():
    with open(r.DEFAULT_TEMPLATE, encoding="utf-8") as f:
        return f.read()


def injected(html):
    start = html.index(PREFIX) + len(PREFIX)
    end = html.index("};</script>", start)
    return html[start:end]


class TimerTemplateTest(unittest.TestCase):
    def test_template_is_self_contained(self):
        html = r.render({"duration_s": 120, "label": "Warm-up"}, template())
        self.assertNotRegex(html, r"(?i)https?://")
        self.assertNotRegex(html, r"(?i)<link\b|@import")
        self.assertNotRegex(html, r"(?<![A-Za-z])url\(")  # no CSS url(); URL.createObjectURL is fine
        self.assertNotRegex(html, r"(?i)\bsrc\s*=\s*[\"']?//")
        for src in re.findall(r"(?i)\b(?:src|href)\s*=\s*[\"']([^\"']*)", html):
            self.assertTrue(src.startswith("data:"), src)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)

    def test_template_has_screen_off_safeguards(self):
        html = template()
        for needle in ("navigator.wakeLock", "visibilitychange", "AudioContext", "navigator.mediaSession",
                       "MediaMetadata", "navigator.vibrate", "data:video/mp4;base64,", "data:video/webm;base64,",
                       "audio/wav", "Date.now()", "playsinline", 'id="awake"'):
            self.assertIn(needle, html)
        self.assertNotIn("setInterval(function(){left--", html)

    def test_english_labels_by_default(self):
        html = template()
        self.assertIn('<button id="start" type="button">Start</button>', html)
        self.assertIn('<button id="reset" type="button">Reset</button>', html)
        self.assertIn('start:"Start",pause:"Pause",resume:"Resume",reset:"Reset"', html)
        self.assertIn('lang:"en"', html)
        self.assertEqual(r.normalize_config({})["lang"], "en")

    def test_injects_before_first_script_and_escapes(self):
        html = r.render({"label": "</script><!-- & <b> 练", "duration_s": 30}, template())
        self.assertEqual(html.count(PREFIX), 1)
        self.assertLess(html.index(PREFIX), html.index("<script>\n(function(){"))
        payload = injected(html)
        self.assertNotIn("<", payload)
        self.assertNotIn(">", payload)
        data = json.loads(payload)
        self.assertEqual(data["label"], "</script><!-- & <b> 练")
        self.assertEqual(html.count("</script>"), 2)


class TimerConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = r.normalize_config({})
        self.assertEqual(cfg["label"], "Timer")
        self.assertEqual(cfg["ready_s"], 5)
        self.assertEqual(cfg["intervals"], [{"kind": "interval", "seconds": 60, "label": None, "round": 0}])
        self.assertEqual(cfg["total_s"], 60)
        self.assertNotIn("strings", cfg)

    def test_duration_forms(self):
        self.assertEqual(r.normalize_config({"duration_s": "2:00"})["total_s"], 120)
        self.assertEqual(r.normalize_config({"duration_s": "45"})["total_s"], 45)
        self.assertEqual(r.normalize_config({"duration_s": 90.0, "ready_s": 0})["ready_s"], 0)
        for bad in (0, -5, True, "abc", 1.5, "1:2:3", r.MAX_SECONDS + 1):
            with self.assertRaises(ValueError, msg=bad):
                r.normalize_config({"duration_s": bad})
        with self.assertRaises(ValueError):
            r.normalize_config({"ready_s": 61})

    def test_explicit_intervals(self):
        cfg = r.normalize_config({"label": "Plank", "intervals": [
            {"kind": "work", "seconds": 30, "label": "Plank"},
            {"kind": "rest", "seconds": "0:15"},
            {"seconds": 10}]})
        self.assertEqual([(x["kind"], x["seconds"], x["label"]) for x in cfg["intervals"]],
                         [("work", 30, "Plank"), ("rest", 15, None), ("interval", 10, None)])
        self.assertEqual(cfg["total_s"], 55)
        with self.assertRaises(ValueError):
            r.normalize_config({"intervals": [{"kind": "sprint", "seconds": 10}]})
        with self.assertRaises(ValueError):
            r.normalize_config({"intervals": [{"kind": "work"}]})
        with self.assertRaises(ValueError):
            r.normalize_config({"intervals": "work 30"})

    def test_hiit_timer_result_expands_like_hiit_html(self):
        hiit = core.hiit_config(rounds=3, name="Tabata", warmup_s=10, cooldown_s=60, segments=[
            {"kind": "work", "seconds": 40, "exercise": "Burpees"},
            {"kind": "work", "seconds": 40, "exercise": "Squats"},
            {"kind": "rest", "seconds": 20}])
        cfg = r.normalize_config({"hiit_id": 9, **hiit})
        kinds = [x["kind"] for x in cfg["intervals"]]
        self.assertEqual(kinds[0], "warmup")
        self.assertEqual(kinds[-1], "cooldown")
        self.assertEqual(kinds[-2], "work")  # final rest of the last round is skipped
        self.assertEqual(kinds.count("rest"), 2)
        self.assertEqual(cfg["total_s"], hiit["planned_seconds"])
        self.assertEqual(cfg["label"], "Tabata")
        self.assertEqual(cfg["rounds"], 3)
        self.assertEqual(cfg["hiit_id"], 9)
        self.assertEqual([x["label"] for x in cfg["intervals"] if x["kind"] == "work"][:2], ["Burpees", "Squats"])
        self.assertEqual([x["round"] for x in cfg["intervals"]][1:4], [1, 1, 1])
        with self.assertRaises(ValueError):
            r.normalize_config({"rounds": 0, "segments": [{"kind": "work", "seconds": 5}]})

    def test_flags_lang_and_strings(self):
        cfg = r.normalize_config({"lang": "zh", "speech": True, "strings": {"start": "Go!"}})
        self.assertEqual((cfg["lang"], cfg["speech"], cfg["strings"]), ("zh", True, {"start": "Go!"}))
        for bad in ({"lang": "fr"}, {"speech": "yes"}, {"strings": {"bogus": "x"}}, {"strings": "x"}):
            with self.assertRaises(ValueError, msg=bad):
                r.normalize_config(bad)
        with self.assertRaises(ValueError):
            r.normalize_config([1, 2])

    def test_template_and_generator_agree_on_string_keys(self):
        html = template()
        en = re.search(r"\ben:\{(.*?)\},\n zh:", html, re.S).group(1)
        self.assertEqual(set(re.findall(r"(\w+):\"", en)), set(r.STRING_KEYS))

    def test_cli_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "hiit.json")
            out = os.path.join(tmp, "t.html")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"name": "HIIT", "rounds": 2, "segments": [{"kind": "work", "seconds": 20}, {"kind": "rest", "seconds": 10}],
                           "warmup_s": 0, "cooldown_s": 0}, f)
            script = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")
            subprocess.run([sys.executable, script, out, "--config", cfg_path, "--ready", "3"], check=True)
            with open(out, encoding="utf-8") as f:
                data = json.loads(injected(f.read()))
            self.assertEqual((data["ready_s"], data["total_s"], len(data["intervals"])), (3, 50, 3))
            subprocess.run([sys.executable, script, out, "--config", cfg_path, "--duration", "2:00", "--label", "Rest"], check=True)
            with open(out, encoding="utf-8") as f:
                data = json.loads(injected(f.read()))
            self.assertEqual((data["label"], data["total_s"], len(data["intervals"])), ("Rest", 120, 1))

    def test_cli_bad_input_is_a_clean_usage_error(self):
        script = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.html")
            bad_json = os.path.join(tmp, "bad.json")
            with open(bad_json, "w", encoding="utf-8") as f:
                f.write("{not json")
            not_object = os.path.join(tmp, "list.json")
            with open(not_object, "w", encoding="utf-8") as f:
                json.dump([1, 2], f)
            for args in (["--duration", "abc"], ["--config", bad_json], ["--config", not_object, "--duration", "30"]):
                res = subprocess.run([sys.executable, script, out, *args], capture_output=True, text=True)
                self.assertEqual(res.returncode, 2, args)
                self.assertNotIn("Traceback", res.stderr, args)
                self.assertIn("error:", res.stderr, args)
                self.assertFalse(os.path.exists(out), args)


if __name__ == "__main__":
    unittest.main()
