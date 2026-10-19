import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core
import render_timer_html as r

PREFIX = "<script>window.openai={toolOutput:"
CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
# The same ranges written as JS/JSON \\uXXXX escapes, any hex case.
ESCAPED_CJK = re.compile(r"(?i)\\u(?:2e|2f|3[0-9a-f]|[4-9][0-9a-f]|a[c-f]|[bc][0-9a-f]|d[0-7]|f9|fa|ff)[0-9a-f]{2}")


def template():
    with open(r.DEFAULT_TEMPLATE, encoding="utf-8") as f:
        return f.read()


def ui_tables():
    """Parse the STRINGS tables of the template into {lang: {key: text}}."""
    html = template()
    body = html[html.index("var STRINGS={"):html.index("\n};", html.index("var STRINGS={"))]
    tables = {}
    for lang, inner in re.findall(r"\n (\w+):\{(.*?)\}(?=,\n \w+:\{|$)", body + "\n", re.S):
        tables[lang] = {k: json.loads('"' + v + '"') for k, v in re.findall(r'(\w+):"((?:[^"\\]|\\.)*)"', inner)}
    return tables


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
        self.assertIn('lang:"en"', html)
        self.assertEqual(r.normalize_config({})["lang"], "en")
        en = ui_tables()["en"]
        self.assertEqual((en["start"], en["pause"], en["resume"], en["reset"]), ("Start", "Pause", "Resume", "Reset"))
        # Any language other than a known table falls back to English at runtime (hash/query params).
        self.assertIn('var LANG=Object.prototype.hasOwnProperty.call(STRINGS,cfg.lang)?cfg.lang:"en",L=STRINGS[LANG];', html)

    def test_cjk_only_inside_the_zh_table(self):
        html = template()
        zh_start = html.index("\n zh:{")
        outside = html[:zh_start] + html[html.index("\n};", zh_start):]
        self.assertIsNone(CJK.search(html))  # the zh table is \\u-escaped ASCII
        self.assertIsNone(ESCAPED_CJK.search(outside))
        self.assertIsNotNone(ESCAPED_CJK.search(html[zh_start:]))
        self.assertIsNone(CJK.search(r.render({"label": "Plank", "duration_s": 60}, html)))

    def test_zh_table_is_complete_chinese(self):
        tables = ui_tables()
        self.assertEqual(set(tables), set(r.LANGS))
        self.assertEqual(set(tables["zh"]), set(tables["en"]))
        zh = tables["zh"]
        self.assertEqual((zh["start"], zh["pause"], zh["resume"], zh["reset"], zh["skip"]), ("开始", "暂停", "继续", "重置", "跳过"))
        self.assertEqual(zh["keepAwake"], "保持屏幕常亮")
        for key, value in zh.items():
            if value and key not in ("interval",):
                self.assertTrue(CJK.search(value), key)
        html = template()
        self.assertIn('u.lang=LANG==="zh"?"zh-CN":"en-US"', html)  # speech follows lang
        self.assertIn("document.documentElement.lang=LANG", html)
        self.assertIn('lang:"lang"', html)  # ?lang= / #lang= URL params

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

    def test_flags_lang_and_removed_strings(self):
        cfg = r.normalize_config({"speech": True, "vibrate": False, "lang": "zh"})
        self.assertEqual((cfg["speech"], cfg["vibrate"], cfg["lang"]), (True, False, "zh"))
        self.assertEqual(json.loads(injected(r.render({"lang": "zh"}, template())))["lang"], "zh")
        for bad in ({"speech": "yes"}, {"lang": "fr"}, {"lang": "ZH"}, {"lang": ""}, {"lang": None},
                    {"strings": {"start": "Go"}}):
            with self.assertRaises(ValueError, msg=bad):
                r.normalize_config(bad)
        with self.assertRaises(ValueError):
            r.normalize_config([1, 2])
        self.assertEqual(r.LANGS, ("en", "zh"))
        self.assertFalse(hasattr(r, "STRING_KEYS"))

    def test_label_kept_verbatim_and_cjk_flagged(self):
        cfg = r.normalize_config({"label": "2 分钟计时", "duration_s": 120})
        self.assertEqual(cfg["label"], "2 分钟计时")
        self.assertEqual(r.cjk_labels(cfg), ["2 分钟计时"])
        cfg = r.normalize_config({"label": "Tabata", "intervals": [{"kind": "work", "seconds": 20, "label": "深蹲"},
                                                                   {"kind": "rest", "seconds": 10}]})
        self.assertEqual(r.cjk_labels(cfg), ["深蹲"])
        self.assertEqual(r.cjk_labels(r.normalize_config({"label": "Plank 1:30"})), [])
        self.assertFalse(hasattr(r, "non_english_labels"))

    def test_cli_lang_and_label_warning_per_lang(self):
        script = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")

        def run(*args):
            return subprocess.run([sys.executable, script, out, *args], capture_output=True, text=True, encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.html")
            for bad in (["--lang", "fr"], ["--lang", "zh-CN"]):
                res = run("--duration", "60", *bad)
                self.assertEqual(res.returncode, 2, bad)
                self.assertIn("invalid choice", res.stderr)
                self.assertFalse(os.path.exists(out))
            cfg_path = os.path.join(tmp, "fr.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"lang": "fr"}, f)
            res = run("--config", cfg_path)
            self.assertEqual(res.returncode, 2)
            self.assertIn("lang must be one of: en, zh", res.stderr)
            self.assertNotIn("Traceback", res.stderr)
            # lang en (default) + CJK label: warn, still write the file, keep the label verbatim.
            res = run("--duration", "2:00", "--label", "2 分钟计时")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("warning: timer label is not English", res.stderr)
            with open(out, encoding="utf-8") as f:
                data = json.loads(injected(f.read()))
            self.assertEqual((data["label"], data["lang"]), ("2 分钟计时", "en"))
            # lang zh + Chinese label: no warning, Chinese UI requested.
            res = run("--duration", "2:00", "--label", "2 分钟计时", "--lang", "zh")
            self.assertEqual((res.returncode, res.stderr), (0, ""))
            with open(out, encoding="utf-8") as f:
                data = json.loads(injected(f.read()))
            self.assertEqual((data["label"], data["lang"]), ("2 分钟计时", "zh"))
            res = run("--duration", "2:00", "--label", "2-minute timer")
            self.assertEqual((res.returncode, res.stderr), (0, ""))

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


def _hex_luminance(color):
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((_hex_luminance(a), _hex_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def palette():
    html = template()
    root = re.search(r":root\{(.*?)\}", html, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\b", root))


STATES = ("idle", "ready", "work", "rest", "warmup", "cooldown", "interval", "paused", "done")


class TimerThemeTest(unittest.TestCase):
    def test_dark_theme_in_every_state_without_light_flip(self):
        html = template()
        css = html[html.index("<style>"):html.index("</style>")]
        self.assertIn("color-scheme:dark", css)
        self.assertNotIn("prefers-color-scheme", html)
        self.assertNotIn("light", re.search(r"color-scheme:([^;]*);", css).group(1))
        for state in STATES:
            self.assertIn(f"body.{state}{{background:var(--bg-{state})}}", css)
            self.assertLess(_hex_luminance(palette()[f"bg-{state}"]), 0.05, state)  # genuinely dark
        # Every colour outside :root comes from the palette variables.
        rest = css[css.index("}", css.index(":root{")) + 1:]
        self.assertIsNone(re.search(r":\s*#[0-9a-fA-F]{3,8}\b", rest))
        self.assertNotRegex(rest, r"rgba?\(|opacity:\s*\.[0-5]")

    def test_palette_meets_wcag_aa(self):
        p = palette()
        ratios = {}
        for state in STATES:
            bg = p[f"bg-{state}"]
            ratios[f"fg/{state}"] = contrast(p["fg"], bg)
            ratios[f"muted/{state}"] = contrast(p["muted"], bg)
        ratios["button"] = contrast(p["btn-fg"], p["btn-bg"])
        ratios["start"] = contrast(p["start-fg"], p["start-bg"])
        ratios["pause"] = contrast(p["pause-fg"], p["pause-bg"])
        ratios["disabled"] = contrast(p["disabled-fg"], p["disabled-bg"])
        ratios["round selected"] = contrast(p["sel-fg"], p["sel-bg"])
        ratios["round unselected"] = contrast(p["unsel-fg"], p["unsel-bg"])
        # The selected round toggle must stand out from the unselected one (non-text, 3:1).
        self.assertGreaterEqual(contrast(p["sel-bg"], p["unsel-bg"]), 3.0)
        for name, ratio in ratios.items():
            self.assertGreaterEqual(ratio, 4.5, f"{name}: {ratio:.2f}")
        # Progress bar: track visible on every background, fill visible on the track (WCAG 1.4.11, 3:1).
        for state in STATES:
            self.assertGreaterEqual(contrast(p["bar-track"], p[f"bg-{state}"]), 3.0, state)
        self.assertGreaterEqual(contrast(p["bar-fill"], p["bar-track"]), 3.0)


class TimerSoundTest(unittest.TestCase):
    def test_soft_sine_tones_only(self):
        html = template()
        for harsh in ("square", "sawtooth", "triangle"):
            self.assertNotIn(harsh, html)
        self.assertIn('o.type="sine"', html)
        tones = re.search(r"var TONES=(\{.*?\}\}),", html).group(1)
        freqs = [float(x) for x in re.findall(r":(\d+(?:\.\d+)?)", tones)]
        self.assertEqual(len(freqs), 4)
        for f in freqs:
            self.assertTrue(380 <= f <= 700, f)
        self.assertNotRegex(html, r"tone\(\d")  # every beep goes through playCue -> TONES
        self.assertIn("linearRampToValueAtTime(peak,at+0.03)", html)  # gentle attack
        self.assertIn("exponentialRampToValueAtTime(0.001,at+d)", html)  # gentle release

    def _cues(self, segs):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        html = template()
        code = html[html.index("/* cue-schedule:begin"):html.index("/* cue-schedule:end */")]
        t = 0
        for s in segs:
            s["start"], t = t, t + s["seconds"]
            s["end"] = t
        script = code + "\nprocess.stdout.write(JSON.stringify(buildCues(" + json.dumps(segs) + ")));"
        out = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True).stdout
        return [(c["t"], c["type"], c["tone"], c["event"]) for c in json.loads(out)]

    def test_short_short_long_at_begin_transitions_and_finish(self):
        cues = self._cues([{"kind": "ready", "ready": True, "seconds": 5}, {"kind": "work", "seconds": 40},
                           {"kind": "rest", "seconds": 20}, {"kind": "work", "seconds": 40}])
        self.assertEqual(cues, [
            (3, "short", "main", "begin"), (4, "short", "main", "begin"), (5, "long", "main", "begin"),
            (43, "short", "rest", "next"), (44, "short", "rest", "next"), (45, "long", "rest", "next"),
            (63, "short", "main", "next"), (64, "short", "main", "next"), (65, "long", "main", "next"),
            (103, "short", "main", "finish"), (104, "short", "main", "finish"), (105, "long", "main", "finish"),
        ])

    def test_single_countdown_without_ready_and_short_segments(self):
        self.assertEqual(self._cues([{"kind": "interval", "seconds": 120}]), [
            (0, "long", "main", "begin"),
            (118, "short", "main", "finish"), (119, "short", "main", "finish"), (120, "long", "main", "finish")])
        # A 2 s ready period only has room for one short beep before the long one.
        self.assertEqual(self._cues([{"kind": "ready", "ready": True, "seconds": 2}, {"kind": "interval", "seconds": 1}]), [
            (1, "short", "main", "begin"), (2, "long", "main", "begin"), (3, "long", "main", "finish")])


class TimerRoundSelectorTest(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")
    HIIT = {"label": "HIIT 40/20", "segments": [{"kind": "work", "seconds": 40}, {"kind": "rest", "seconds": 20}],
            "warmup_s": 0, "cooldown_s": 0}

    def test_config_with_round_options(self):
        cfg = r.normalize_config({**self.HIIT, "round_options": [20, 10], "rounds": 10})
        self.assertEqual(cfg["round_options"], [10, 20])
        self.assertEqual(cfg["rounds"], 10)
        self.assertEqual(cfg["round_totals"], {"10": 580, "20": 1180})  # n*40 + (n-1)*20, final rest skipped
        self.assertEqual(cfg["total_s"], 580)
        self.assertEqual([x["kind"] for x in cfg["intervals"]][-2:], ["rest", "work"])
        self.assertEqual(len(cfg["intervals"]), 19)
        self.assertEqual(cfg["segments"], [{"kind": "work", "seconds": 40, "label": None},
                                           {"kind": "rest", "seconds": 20, "label": None}])
        for n in (10, 20):
            self.assertEqual(cfg["round_totals"][str(n)],
                             core.hiit_config(rounds=n, warmup_s=0, cooldown_s=0, segments=self.HIIT["segments"])["planned_seconds"])
        # Default falls back to the first (smallest) option.
        self.assertEqual(r.normalize_config({**self.HIIT, "round_options": [20, 10]})["rounds"], 10)
        self.assertNotIn("round_options", r.normalize_config({**self.HIIT, "rounds": 10}))

    def test_round_options_validation(self):
        bad = [
            {**self.HIIT, "round_options": [10, 20], "rounds": 15},
            {**self.HIIT, "round_options": [10]},
            {**self.HIIT, "round_options": [10, 10]},
            {**self.HIIT, "round_options": [0, 10]},
            {**self.HIIT, "round_options": [10, 101]},
            {**self.HIIT, "round_options": [10, True]},
            {**self.HIIT, "round_options": "10,20"},
            {**self.HIIT, "round_options": [1, 2, 3, 4, 5, 6, 7]},
            {"duration_s": 60, "round_options": [10, 20]},
            {"intervals": [{"kind": "work", "seconds": 5}], "segments": self.HIIT["segments"], "round_options": [10, 20]},
        ]
        for cfg in bad:
            with self.assertRaises(ValueError, msg=cfg):
                r.normalize_config(cfg)

    def test_template_has_selector_wired_to_rebuild(self):
        html = template()
        self.assertIn('<div id="rsel" role="group" aria-labelledby="rselLbl" hidden>', html)
        self.assertIn("function rebuild(){", html)
        self.assertIn("cues=buildCues(segs)}", html)  # cues are rebuilt for the chosen count
        self.assertIn("function pickRounds(n){if(started||", html)  # locked after Start
        self.assertIn("box.hidden=started", html)  # hidden while running, back after Reset
        self.assertIn('rounds:"rounds"', html)  # #rounds=20 / ?rounds=20 preselect
        self.assertEqual((ui_tables()["en"]["rounds"], ui_tables()["en"]["roundsN"]), ("Rounds", "{n} rounds"))
        self.assertEqual((ui_tables()["zh"]["rounds"], ui_tables()["zh"]["roundsN"]), ("组数", "{n} 组"))

    def test_placeholders_match_between_locales(self):
        tables = ui_tables()
        for key, text in tables["en"].items():
            self.assertEqual(set(re.findall(r"\{\w+\}", text)), set(re.findall(r"\{\w+\}", tables["zh"][key])), key)

    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.html")
            res = subprocess.run([sys.executable, self.SCRIPT, out, *args], capture_output=True, text=True)
            data = None
            if os.path.exists(out):
                with open(out, encoding="utf-8") as f:
                    data = json.loads(injected(f.read()))
            return res, data

    def test_cli_rounds_list_and_fixed(self):
        res, data = self.run_cli("--work", "40", "--rest", "20", "--rounds", "10,20", "--label", "HIIT 40/20")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((data["round_options"], data["rounds"], data["round_totals"]), ([10, 20], 10, {"10": 580, "20": 1180}))
        res, data = self.run_cli("--work", "40", "--rest", "20", "--rounds", "10,20", "--default-rounds", "20")
        self.assertEqual((data["rounds"], data["total_s"]), (20, 1180))
        res, data = self.run_cli("--work", "0:40", "--rest", "20", "--rounds", "10")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("round_options", data)
        self.assertEqual((data["rounds"], data["total_s"]), (10, 580))
        res, data = self.run_cli("--work", "30", "--rounds", "5")  # no rest
        self.assertEqual((data["total_s"], len(data["intervals"])), (150, 5))

    def test_cli_rounds_errors_are_one_line_usage_errors(self):
        cases = [
            (["--work", "40", "--rounds", "10,x"], "comma list"),
            (["--work", "40", "--rounds", "10,10"], "repeat"),
            (["--work", "40", "--rounds", "0,20"], "1-100"),
            (["--work", "-5", "--rounds", "10"], "--work"),
            (["--work", "40", "--rest", "0", "--rounds", "10"], "--rest"),
            (["--rounds", "10,20"], "needs both --work and --rounds"),
            (["--work", "40"], "needs both --work and --rounds"),
            (["--work", "40", "--rounds", "10,20", "--default-rounds", "15"], "must be one of"),
            (["--work", "40", "--rounds", "10", "--default-rounds", "10"], "needs a --rounds list"),
            (["--work", "40", "--rounds", "10,20", "--duration", "60"], "cannot be combined"),
        ]
        for args, needle in cases:
            res, data = self.run_cli(*args)
            self.assertEqual(res.returncode, 2, args)
            self.assertIsNone(data, args)
            self.assertNotIn("Traceback", res.stderr, args)
            errors = [x for x in res.stderr.splitlines() if "error:" in x]
            self.assertEqual(len(errors), 1, res.stderr)
            self.assertIn(needle, errors[0], args)

    def test_cue_schedule_for_each_round_option(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        html = template()
        code = html[html.index("/* cue-schedule:begin"):html.index("/* cue-schedule:end */")]
        for n in (10, 20):
            cfg = r.normalize_config({**self.HIIT, "round_options": [10, 20], "rounds": n})
            segs, t = [{"kind": "ready", "ready": True, "seconds": 5}] + cfg["intervals"], 0
            for x in segs:
                x["start"], t = t, t + x["seconds"]
                x["end"] = t
            script = code + "\nprocess.stdout.write(JSON.stringify(buildCues(" + json.dumps(segs) + ")));"
            cues = json.loads(subprocess.run([node, "-e", script], check=True, capture_output=True, text=True).stdout)
            self.assertEqual(len(cues), 3 * (2 * n))  # ready + n work + (n-1) rest, three beeps each
            longs = [c for c in cues if c["type"] == "long"]
            self.assertEqual([c["t"] for c in longs][0], 5)
            self.assertEqual(longs[-1], {"t": 5 + cfg["total_s"], "type": "long", "tone": "main", "event": "finish", "seg": 2 * n})
            self.assertEqual(sum(1 for c in longs if c["tone"] == "rest"), n - 1)


if __name__ == "__main__":
    unittest.main()
