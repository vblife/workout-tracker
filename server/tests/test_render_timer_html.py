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
        for needle in ("navigator.wakeLock", "visibilitychange", "AudioContext", "navigator.vibrate",
                       "Date.now()", 'id="awake"'):
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
        self.assertEqual(cfg["ready_s"], 0)  # no get-ready period by default
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


class TimerCacheTest(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")
    HIIT = {"name": "Circuit", "rounds": 3, "warmup_s": 10, "cooldown_s": 30,
            "segments": [{"kind": "work", "seconds": 40, "exercise": "Squats"}, {"kind": "rest", "seconds": 20}]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cache = os.path.join(self.tmp, "cache")

    def run_cli(self, out_name, *args, env_cache=None):
        env = {k: v for k, v in os.environ.items() if k != "WORKOUT_TIMER_CACHE"}
        if env_cache is not None:
            env["WORKOUT_TIMER_CACHE"] = env_cache
        out = os.path.join(self.tmp, out_name)
        res = subprocess.run([sys.executable, self.SCRIPT, out, *args], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        with open(out, encoding="utf-8") as f:
            return f.read(), res.stderr

    def hiit_cli(self, out_name, hiit_id):
        cfg = os.path.join(self.tmp, f"hiit-{hiit_id}.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({**self.HIIT, "hiit_id": hiit_id}, f)
        return self.run_cli(out_name, "--config", cfg, "--cache-dir", self.cache)

    def cache_files(self):
        return sorted(os.listdir(self.cache)) if os.path.isdir(self.cache) else []

    def test_same_countdown_twice_is_a_hit_with_identical_output(self):
        args = ("--duration", "5:00", "--label", "5-minute timer", "--cache-dir", self.cache)
        first, err1 = self.run_cli("a.html", *args)
        second, err2 = self.run_cli("b.html", *args)
        key = r.fingerprint(r.normalize_config({"duration_s": 300, "label": "5-minute timer"}), template())
        self.assertEqual(err1.strip(), f"timer cache miss {key}")
        self.assertEqual(err2.strip(), f"timer cache hit {key}")
        self.assertEqual(len(key), 32)
        self.assertEqual(first, second)
        self.assertEqual(self.cache_files(), [f"{key}.html"])
        with open(r.cache_path(self.cache, key), encoding="utf-8") as f:
            self.assertEqual(f.read(), first)
        self.assertNotIn("hiit_id", json.loads(injected(first)))
        self.assertEqual(first, r.render({"duration_s": 300, "label": "5-minute timer"}, template()))

    def test_hiit_sessions_share_one_entry_and_embed_the_current_hiit_id(self):
        first, err1 = self.hiit_cli("h1.html", 1)
        second, err2 = self.hiit_cli("h2.html", 2)
        self.assertIn("timer cache miss", err1)
        self.assertIn("timer cache hit", err2)
        self.assertEqual(len(self.cache_files()), 1)
        self.assertEqual(json.loads(injected(first))["hiit_id"], 1)
        self.assertEqual(json.loads(injected(second))["hiit_id"], 2)
        expected = r.normalize_config({**self.HIIT, "hiit_id": 2})
        self.assertEqual(json.loads(injected(second)), expected)
        with open(os.path.join(self.cache, self.cache_files()[0]), encoding="utf-8") as f:
            self.assertNotIn("hiit_id", json.loads(injected(f.read())))
        a = r.normalize_config({**self.HIIT, "hiit_id": 1})
        b = r.normalize_config({**self.HIIT, "hiit_id": "abc"})
        self.assertEqual(r.fingerprint(a, template()), r.fingerprint(b, template()))
        self.assertEqual(r.fingerprint(a, template()), r.fingerprint(r.normalize_config(self.HIIT), template()))

    def test_hit_without_hiit_id_after_a_session_with_one(self):
        self.hiit_cli("h1.html", 7)
        cfg = os.path.join(self.tmp, "plain.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(self.HIIT, f)
        html, err = self.run_cli("plain.html", "--config", cfg, "--cache-dir", self.cache)
        self.assertIn("timer cache hit", err)
        self.assertNotIn("hiit_id", json.loads(injected(html)))

    def test_apply_hiit_id_round_trips_escaped_json(self):
        html = r.render({"duration_s": 60, "label": "<Plank> & hold"}, template())
        patched = r.apply_hiit_id(html, 9)
        cfg = json.loads(injected(patched))
        self.assertEqual((cfg["hiit_id"], cfg["label"]), (9, "<Plank> & hold"))
        self.assertNotIn("<Plank>", injected(patched))
        self.assertEqual(r.apply_hiit_id(patched, None), html)
        with self.assertRaises(ValueError):
            r.apply_hiit_id("<html></html>", 1)

    def test_template_or_config_change_is_a_new_key(self):
        cfg = r.normalize_config({"duration_s": 300, "label": "5-minute timer"})
        base = r.fingerprint(cfg, template())
        self.assertNotEqual(base, r.fingerprint(cfg, template() + "<!-- v2 -->"))
        self.assertNotEqual(base, r.fingerprint(r.normalize_config({"duration_s": 240, "label": "5-minute timer"}), template()))
        self.assertNotEqual(base, r.fingerprint(r.normalize_config({"duration_s": 300, "label": "Plank"}), template()))
        self.assertNotEqual(base, r.fingerprint(r.normalize_config({"duration_s": 300, "label": "5-minute timer",
                                                                    "lang": "zh"}), template()))
        self.run_cli("a.html", "--duration", "5:00", "--label", "5-minute timer", "--cache-dir", self.cache)
        _, err = self.run_cli("b.html", "--duration", "4:00", "--label", "5-minute timer", "--cache-dir", self.cache)
        self.assertIn("timer cache miss", err)
        alt = os.path.join(self.tmp, "timer-v2.html")
        with open(alt, "w", encoding="utf-8") as f:
            f.write(template() + "<!-- v2 -->")
        _, err = self.run_cli("c.html", "--duration", "5:00", "--label", "5-minute timer",
                              "--template", alt, "--cache-dir", self.cache)
        self.assertIn("timer cache miss", err)
        self.assertEqual(len(self.cache_files()), 3)

    def test_env_var_enables_the_cache(self):
        _, err = self.run_cli("a.html", "--duration", "90", env_cache=self.cache)
        self.assertIn("timer cache miss", err)
        self.assertEqual(len(self.cache_files()), 1)

    def test_no_cache_dir_writes_only_the_out_file(self):
        html, err = self.run_cli("only.html", "--duration", "5:00", "--label", "5-minute timer")
        self.assertEqual(err, "")
        self.assertEqual(os.listdir(self.tmp), ["only.html"])
        self.assertEqual(html, r.render({"duration_s": 300, "label": "5-minute timer"}, template()))


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
        # Checkbox toggles (Keep screen on, Tick): label uses --fg (checked above); the checked box uses
        # --accent, which must be visible on every background (non-text, 3:1).
        css = template()[template().index("<style>"):template().index("</style>")]
        self.assertIn(".toggle{color:var(--fg)", css)
        self.assertIn(".toggle input{width:24px;height:24px;accent-color:var(--accent)}", css)
        for state in STATES:
            self.assertGreaterEqual(contrast(p["accent"], p[f"bg-{state}"]), 3.0, state)


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


def _node_eval(begin, end, expr):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    html = template()
    code = html[html.index(begin):html.index(end)]
    out = subprocess.run([node, "-e", code + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def _timeline(segs):
    t = 0
    for s in segs:
        s["start"], t = t, t + s["seconds"]
        s["end"] = t
    return segs


class TimerReadyDefaultTest(unittest.TestCase):
    """No get-ready period by default on any timer; ready_s / --ready (0-60) stays optional."""

    def test_template_and_generator_default_to_zero(self):
        self.assertIn("ready_s:0,", template())
        self.assertEqual(r.DEFAULTS["ready_s"], 0)
        self.assertEqual(r.normalize_config({"duration_s": 120})["ready_s"], 0)
        self.assertEqual(r.normalize_config({"rounds": 2, "segments": [{"kind": "work", "seconds": 40}]})["ready_s"], 0)
        guided = {"mode": "guided", "steps": [{"kind": "work", "exercise": "Squat", "reps": 5}]}
        self.assertEqual(r.normalize_config(guided)["ready_s"], 0)
        self.assertEqual(r.normalize_config({**guided, "ready_s": 10})["ready_s"], 10)

    def test_cli_default_and_ready_flag(self):
        script = r.__file__
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.html")
            subprocess.run([sys.executable, script, out, "--duration", "1:00", "--label", "One minute"], check=True)
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.loads(injected(f.read()))["ready_s"], 0)
            subprocess.run([sys.executable, script, out, "--duration", "1:00", "--label", "One minute", "--ready", "5"], check=True)
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.loads(injected(f.read()))["ready_s"], 5)
        help_text = subprocess.run([sys.executable, script, "--help"], check=True, capture_output=True, text=True).stdout
        self.assertIn("default 0", help_text)

    def test_hiit_widget_has_no_default_get_ready(self):
        with open(os.path.join(os.path.dirname(r.__file__), "ui", "hiit.html"), encoding="utf-8") as f:
            widget = f.read()
        self.assertNotIn("countdown=3", widget)
        self.assertIn("ready_s:0}", widget)
        self.assertIn("countdown=readyS()", widget)

class TimerTickTest(unittest.TestCase):
    CUES = ("/* cue-schedule:begin", "/* cue-schedule:end */")

    def ticks_and_cues(self, segs):
        segs = json.dumps(_timeline(segs))
        return _node_eval(*self.CUES, "(function(s){var c=buildCues(s);return {ticks:buildTicks(s,c),cues:c.map(function(x){return x.t})}})(" + segs + ")")

    def test_one_tick_per_second_skipping_cue_seconds_and_ready(self):
        got = self.ticks_and_cues([{"kind": "ready", "ready": True, "seconds": 5}, {"kind": "work", "seconds": 40},
                                   {"kind": "rest", "seconds": 20}, {"kind": "work", "seconds": 40}])
        cues = set(got["cues"])
        expected = [t for t in range(6, 105) if t not in cues]
        self.assertEqual(got["ticks"], expected)
        self.assertEqual(len(got["ticks"]), 37 + 17 + 37)  # 39/19 whole seconds inside each interval, minus 2 shorts
        self.assertTrue(all(t >= 5 for t in got["ticks"]))  # none during get ready
        self.assertTrue(all(abs(t - c) >= 0.5 for t in got["ticks"] for c in got["cues"]))
        self.assertEqual([b - a for a, b in zip(got["ticks"], got["ticks"][1:]) if b - a != 1], [4, 4])  # gaps only at cues

    def test_ticks_for_single_countdown_and_round_options(self):
        got = self.ticks_and_cues([{"kind": "interval", "seconds": 10}])
        self.assertEqual(got["ticks"], [1, 2, 3, 4, 5, 6, 7])  # 0 is the begin beep; 8, 9, 10 are the finish cue
        for n, count in ((10, 10 * 37 + 9 * 17), (20, 20 * 37 + 19 * 17)):
            cfg = r.normalize_config({"segments": [{"kind": "work", "seconds": 40}, {"kind": "rest", "seconds": 20}],
                                      "warmup_s": 0, "cooldown_s": 0, "round_options": [10, 20], "rounds": n})
            got = self.ticks_and_cues([{"kind": "ready", "ready": True, "seconds": 5}] + cfg["intervals"])
            self.assertEqual(len(got["ticks"]), count)

    def test_ticks_wired_to_schedule_toggle_and_config(self):
        html = template()
        self.assertIn('TICK=SOUND&&bool(cfg.tick,true)', html)
        self.assertIn("ticks=TICK?buildTicks(segs,cues):[]", html)  # tick:false -> no ticks at all
        self.assertIn("if(tickOn)for(i=0;i<ticks.length;i++)", html)  # pre-scheduled in topUp with the cues
        self.assertIn('$("tickBox").hidden=!TICK', html)  # toggle removed when tick is false or sound is off
        self.assertIn("tickOn=tb.checked;if(running)reschedule()", html)
        self.assertIn('<input type="checkbox" id="tickOn" checked>', html)  # default on
        self.assertIn(".toggle[hidden]{display:none}", html)
        self.assertEqual((ui_tables()["en"]["tick"], ui_tables()["zh"]["tick"]), ("Tick", "滴答声"))
        self.assertIn('tick:"tick"', html)  # ?tick=0 / #tick=off

    def test_tick_sound_is_short_soft_and_quieter_than_cues(self):
        html = template()
        gain = float(re.search(r"var TICK_GAIN=([\d.]+)", html).group(1))
        cue_gains = [float(x) for x in re.findall(r"[\d.]+", re.search(r"GAIN=\{short:([\d.]+),long:([\d.]+)\}", html).group(0))]
        self.assertLessEqual(gain, min(cue_gains) / 4)
        begin = html.index("function tickBuffer(")
        code = html[begin:html.index("\n", html.index("return d}", begin))]
        data = _node_eval_code(code, "Array.from(tickBuffer(48000))")
        self.assertEqual(len(data), 960)  # 20 ms
        self.assertAlmostEqual(max(abs(x) for x in data), 1.0, places=5)
        self.assertLess(max(abs(x) for x in data[480:]), 0.05)  # 2.5 ms decay constant: under 5% after 10 ms
        self.assertEqual(data[-1], 0)

    def test_audio_never_registers_a_media_session(self):
        # Sound is Web Audio only. Any media element, any Media Session API access, or audioSession "playback"
        # can make iOS/Android (or the host app of an in-app webview) show a Now Playing / media card on Start
        # and pause other apps' audio. The templates checked here are what the bot actually sends.
        for html in (template(), r.render({"duration_s": 30}, template()), r.render({"mode": "guided", "steps": [
                {"kind": "work", "label": "Squat", "sets": 1, "set_index": 1, "reps": 5}]}, template())):
            self.assertNotRegex(html, r"(?i)<audio\b|<video\b|new Audio\(|createElement\([\"'](?:audio|video)")
            self.assertNotRegex(html, r"(?i)data:(?:audio|video)/")
            self.assertNotRegex(html, r"\.play\(")  # no HTMLMediaElement.play()
            self.assertNotIn("mediaSession", html)  # not even reads or clears: WebKit creates a session on access
            self.assertNotIn("MediaMetadata", html)
            self.assertNotIn("setActionHandler", html)
            self.assertNotIn("setPositionState", html)
            self.assertNotIn("playbackState", html)
            self.assertNotRegex(html, r"audioSession\.type\s*=\s*[\"'](?!ambient[\"'])")  # only ever "ambient"
            self.assertIn('navigator.audioSession.type="ambient"', html)
            self.assertNotRegex(html, r"id=[\"']?nosleep|disableRemotePlayback|playsinline")
        html = template()
        self.assertEqual(html.count("ambientAudio();"), 4)  # at load, countdown resume, guided begin and resume
        self.assertIn('catch(e){}}\nambientAudio();\n', html)  # top-level call at load, before any tap
        self.assertEqual(html.count("createOscillator"), 1)  # only the cue tone(); no hidden hum oscillator
        self.assertIn("createBuffer(1,1,22050)", html)  # the unlock buffer is one zero sample

    def test_ambient_audio_session_set_before_audio_context_on_every_start(self):
        html = template()
        for fn in ("function resume(){", "function begin(){"):
            i = html.index(fn)
            body = html[i:html.index("}", html.index("ensureAudio()", i))]
            self.assertLess(body.index("ambientAudio()"), body.index("ensureAudio()"), fn)
        i = html.index('else if(phase==="paused")')
        self.assertLess(html.index("ambientAudio()", i), html.index("ensureAudio()", i))

    def test_keep_awake_is_wake_lock_only(self):
        html = template()
        start = html.index("function acquireAwake(")
        code = html[start:html.index("function status(", start)]
        self.assertIn('navigator.wakeLock.request("screen")', code)
        self.assertNotRegex(code, r"(?i)video|audio|\.play\(")
        self.assertNotIn("wlVideo", html)
        self.assertIn('awakeMode="none"', code)  # refused or missing wake lock: status asks to keep the screen on

    def test_cli_no_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.html")
            script = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")
            subprocess.run([sys.executable, script, out, "--duration", "60", "--no-tick"], check=True, capture_output=True)
            with open(out, encoding="utf-8") as f:
                self.assertIs(json.loads(injected(f.read()))["tick"], False)
            subprocess.run([sys.executable, script, out, "--duration", "60"], check=True, capture_output=True)
            with open(out, encoding="utf-8") as f:
                self.assertNotIn("tick", json.loads(injected(f.read())))
        self.assertIs(r.normalize_config({"duration_s": 60, "tick": False})["tick"], False)
        with self.assertRaises(ValueError):
            r.normalize_config({"duration_s": 60, "tick": "off"})


def _node_eval_code(code, expr):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    out = subprocess.run([node, "-e", code + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


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
        self.assertIn("cues=buildCues(segs);ticks=", html)  # cues are rebuilt for the chosen count
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
