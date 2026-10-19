"""Render a standalone countdown timer HTML file from server/ui/timer.html.

The output is one self-contained local file (inline CSS/JS, no network requests),
meant to be sent as a file:// attachment. Config is injected exactly like
render_report_html.py: <script>window.openai={toolOutput:<config JSON>};</script>
before the template's first <script> tag.

The UI text (Start, Pause, Reset, ...) is English by default; lang "zh" switches it to
Chinese. Unknown lang values are rejected. The caller's label is shown exactly as given,
so it must be in the same language as the UI. With lang "en" the CLI prints a warning
on stderr when a label contains CJK characters.

Usage:
  python server/render_timer_html.py out.html --duration 2:00 --label "Warm-up"
  python server/render_timer_html.py out.html --config hiit.json      # hiit_timer result
  python server/render_timer_html.py out.html --duration 2:00 --label "..." --lang zh
  python server/render_timer_html.py out.html --work 40 --rest 20 --rounds 10,20 --label "HIIT 40/20"

Config keys (all optional):
  label        title shown on the page (hiit_timer "name" is accepted too)
  duration_s   single countdown length in seconds (default 60)
  ready_s      get-ready period before the first interval, 0-60 (default 5)
  intervals    [{"kind": work|rest|warmup|cooldown|interval, "seconds": int, "label": str}]
  rounds, segments, warmup_s, cooldown_s, hiit_id
               the structured hiit_timer result; expanded into intervals the same
               way hiit.html does (the final rest of the last round is skipped)
  round_options  e.g. [10, 20]: with segments, shows a round-count selector on the
               idle screen; rounds is the default and must be one of the options
  lang         UI language: "en" (default) or "zh"; spoken cues follow it
  sound, speech, vibrate, keep_awake   booleans (defaults true, false, true, true)
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_report_html import render as _inject  # noqa: E402

DEFAULT_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "timer.html")
DEFAULTS = {"label": "Timer", "duration_s": 60, "ready_s": 5, "lang": "en"}
KINDS = ("work", "rest", "warmup", "cooldown", "interval")
LANGS = ("en", "zh")  # must match the STRINGS tables in ui/timer.html
REMOVED_KEYS = ("strings",)  # per-label overrides were removed; fail loudly instead of ignoring them
CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
FLAGS = ("sound", "speech", "vibrate", "keep_awake")
MAX_SECONDS = 86400
MAX_INTERVALS = 2000
MAX_ROUNDS = 100
MAX_ROUND_OPTIONS = 6


def _round_count(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ROUNDS:
        raise ValueError(f"{name} must be a whole number 1-{MAX_ROUNDS} (got {value!r})")
    return value


def parse_round_options(value):
    """Validate round_options: 2-6 distinct round counts, returned sorted ascending."""
    if not isinstance(value, list):
        raise ValueError("round_options must be a list of round counts, e.g. [10, 20]")
    options = [_round_count(x, "each round option") for x in value]
    if len(set(options)) != len(options):
        raise ValueError(f"round_options must not repeat values (got {options})")
    if not 2 <= len(options) <= MAX_ROUND_OPTIONS:
        raise ValueError(f"round_options needs 2-{MAX_ROUND_OPTIONS} values; use rounds for a fixed count")
    return sorted(options)


def parse_rounds_arg(text):
    """CLI --rounds: '10' (fixed) or '10,20' (selector options, in the given order)."""
    parts = [x.strip() for x in str(text).split(",")]
    if not all(x.isdigit() for x in parts):
        raise ValueError(f"--rounds must be a positive whole number or a comma list like 10,20 (got {text!r})")
    return [_round_count(int(x), "--rounds values") for x in parts]


def parse_seconds(value, name, minimum=1, maximum=MAX_SECONDS):
    """Accept an int, an integral float, a digit string, or "m:ss"."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number of seconds")
    if isinstance(value, str):
        text = value.strip()
        if ":" in text:
            mins, _, secs = text.partition(":")
            if not (mins.isdigit() and secs.isdigit() and len(secs) <= 2):
                raise ValueError(f"{name} must be seconds or m:ss")
            value = int(mins) * 60 + int(secs)
        elif text.isdigit():
            value = int(text)
        else:
            raise ValueError(f"{name} must be seconds or m:ss")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number of seconds")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be {minimum}-{maximum} seconds")
    return value


def _label(value):
    return None if value is None else str(value)[:200]


def expand_hiit(cfg):
    """Expand a hiit_timer result into intervals (same order and skip rule as hiit.html)."""
    rounds = cfg.get("rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 100:
        raise ValueError("rounds must be 1-100")
    segments = cfg.get("segments") or []
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments must be a non-empty list")
    out = []
    warmup = parse_seconds(cfg.get("warmup_s", 0) or 0, "warmup_s", minimum=0)
    cooldown = parse_seconds(cfg.get("cooldown_s", 0) or 0, "cooldown_s", minimum=0)
    if warmup:
        out.append({"kind": "warmup", "seconds": warmup, "label": None, "round": 0})
    for r in range(1, rounds + 1):
        for j, seg in enumerate(segments):
            if not isinstance(seg, dict):
                raise ValueError("each segment must be an object")
            kind = seg.get("kind", "work")
            if kind not in ("work", "rest"):
                raise ValueError("segment kind must be work|rest")
            if r == rounds and j == len(segments) - 1 and kind == "rest":
                continue
            out.append({"kind": kind, "seconds": parse_seconds(seg.get("seconds"), "segment seconds"),
                        "label": _label(seg.get("exercise") or seg.get("label")), "round": r})
    if cooldown:
        out.append({"kind": "cooldown", "seconds": cooldown, "label": None, "round": rounds})
    return out


def normalize_config(config):
    """Validate a timer config and return the explicit form injected into the template."""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("timer config must be a JSON object")
    out = {
        "label": _label(config.get("label") or config.get("name") or DEFAULTS["label"]),
        "ready_s": parse_seconds(config.get("ready_s", DEFAULTS["ready_s"]), "ready_s", minimum=0, maximum=60),
        "lang": config.get("lang", DEFAULTS["lang"]),
    }
    if out["lang"] not in LANGS:
        raise ValueError(f"lang must be one of: {', '.join(LANGS)} (got {out['lang']!r})")
    for key in REMOVED_KEYS:
        if key in config:
            raise ValueError(f"'{key}' is not supported: pick the UI language with lang ({', '.join(LANGS)})")
    options = None
    if config.get("round_options") is not None:
        options = parse_round_options(config["round_options"])
        if config.get("intervals") or config.get("segments") is None:
            raise ValueError("round_options needs work/rest rounds (segments), not intervals or a single duration")
        default = config.get("rounds", options[0])
        _round_count(default, "rounds")
        if default not in options:
            raise ValueError(f"rounds ({default}) must be one of round_options {options}")
        config = {**config, "rounds": default}
    if config.get("intervals"):
        items = config["intervals"]
        if not isinstance(items, list):
            raise ValueError("intervals must be a list")
        intervals = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each interval must be an object")
            kind = item.get("kind", "interval")
            if kind not in KINDS:
                raise ValueError("interval kind must be one of: " + ", ".join(KINDS))
            rnd = item.get("round", 0)
            if isinstance(rnd, bool) or not isinstance(rnd, int) or rnd < 0:
                raise ValueError("interval round must be a non-negative integer")
            intervals.append({"kind": kind, "seconds": parse_seconds(item.get("seconds"), "interval seconds"),
                              "label": _label(item.get("label") or item.get("exercise")), "round": rnd})
        if config.get("rounds") is not None:
            out["rounds"] = config["rounds"]
    elif config.get("segments") is not None or config.get("rounds") is not None:
        intervals = expand_hiit(config)
        out["rounds"] = config["rounds"]
    else:
        seconds = parse_seconds(config.get("duration_s", DEFAULTS["duration_s"]), "duration_s")
        intervals = [{"kind": "interval", "seconds": seconds, "label": None, "round": 0}]
    if len(intervals) > MAX_INTERVALS:
        raise ValueError(f"at most {MAX_INTERVALS} intervals")
    total = sum(x["seconds"] for x in intervals)
    if total > MAX_SECONDS:
        raise ValueError(f"total timer length must be at most {MAX_SECONDS} seconds")
    out["intervals"] = intervals
    out["total_s"] = total
    if options:
        # The page rebuilds the sequence for the chosen count; intervals/total_s reflect the default.
        totals = {str(n): sum(x["seconds"] for x in expand_hiit({**config, "rounds": n})) for n in options}
        if max(totals.values()) > MAX_SECONDS:
            raise ValueError(f"total timer length must be at most {MAX_SECONDS} seconds")
        out["round_options"] = options
        out["segments"] = [{"kind": x.get("kind", "work"), "seconds": parse_seconds(x.get("seconds"), "segment seconds"),
                            "label": _label(x.get("exercise") or x.get("label"))} for x in config["segments"]]
        out["warmup_s"] = parse_seconds(config.get("warmup_s", 0) or 0, "warmup_s", minimum=0)
        out["cooldown_s"] = parse_seconds(config.get("cooldown_s", 0) or 0, "cooldown_s", minimum=0)
        out["round_totals"] = totals
    if config.get("hiit_id") is not None:
        out["hiit_id"] = config["hiit_id"]
    for flag in FLAGS:
        if flag in config:
            if not isinstance(config[flag], bool):
                raise ValueError(f"{flag} must be true or false")
            out[flag] = config[flag]
    return out


def cjk_labels(config):
    """Labels (title and interval labels) in a normalized config that contain CJK characters."""
    labels = [config.get("label")] + [x.get("label") for x in config.get("intervals", []) + config.get("segments", [])]
    return [x for x in dict.fromkeys(labels) if x and CJK.search(x)]


def render(config, template_html):
    return _inject(normalize_config(config), template_html)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out_html")
    p.add_argument("--config", help="timer config JSON file ('-' for stdin), e.g. a hiit_timer result")
    p.add_argument("--duration", help="single countdown length: seconds or m:ss")
    p.add_argument("--label")
    p.add_argument("--ready", help="get-ready seconds before the first interval (default 5)")
    p.add_argument("--work", help="work/rest rounds mode: work seconds per round (seconds or m:ss)")
    p.add_argument("--rest", help="rest seconds between rounds (the final rest is skipped)")
    p.add_argument("--rounds", help="round count, or a comma list like 10,20 to show a selector")
    p.add_argument("--default-rounds", help="default for a --rounds list (default: the first value)")
    p.add_argument("--lang", choices=LANGS, help="UI language (default en)")
    p.add_argument("--template", default=os.environ.get("WORKOUT_TIMER_TEMPLATE", DEFAULT_TEMPLATE))
    a = p.parse_args(argv)
    config = {}
    if a.config:
        try:
            if a.config == "-":
                config = json.load(sys.stdin)
            else:
                with open(a.config, encoding="utf-8") as f:
                    config = json.load(f)
        except ValueError as e:
            p.error(f"--config is not valid JSON: {e}")
        if not isinstance(config, dict):
            p.error("--config must be a JSON object")
    if a.duration is not None:
        for key in ("intervals", "segments", "rounds", "warmup_s", "cooldown_s"):
            config.pop(key, None)
        config["duration_s"] = a.duration
    rounds_mode = any(v is not None for v in (a.work, a.rest, a.rounds, a.default_rounds))
    if rounds_mode:
        try:
            if a.duration is not None:
                raise ValueError("--duration cannot be combined with --work/--rest/--rounds")
            if a.work is None or a.rounds is None:
                raise ValueError("work/rest rounds mode needs both --work and --rounds")
            rounds = parse_rounds_arg(a.rounds)
            segments = [{"kind": "work", "seconds": parse_seconds(a.work, "--work")}]
            if a.rest is not None:
                segments.append({"kind": "rest", "seconds": parse_seconds(a.rest, "--rest")})
            for key in ("intervals", "segments", "rounds", "round_options", "warmup_s", "cooldown_s", "duration_s"):
                config.pop(key, None)
            config.update({"segments": segments, "warmup_s": 0, "cooldown_s": 0})
            if len(rounds) == 1:
                if a.default_rounds is not None:
                    raise ValueError("--default-rounds needs a --rounds list like 10,20")
                config["rounds"] = rounds[0]
            else:
                default = rounds[0]
                if a.default_rounds is not None:
                    default = parse_rounds_arg(a.default_rounds)
                    if len(default) != 1:
                        raise ValueError("--default-rounds takes one number")
                    default = default[0]
                    if default not in rounds:
                        raise ValueError(f"--default-rounds {default} must be one of --rounds {a.rounds}")
                config.update({"round_options": rounds, "rounds": default})
        except ValueError as e:
            p.error(str(e))
    if a.label is not None:
        config["label"] = a.label
    if a.ready is not None:
        config["ready_s"] = a.ready
    if a.lang is not None:
        config["lang"] = a.lang
    with open(a.template, encoding="utf-8") as f:
        template = f.read()
    try:
        normalized = normalize_config(config)
    except ValueError as e:
        p.error(str(e))
    if normalized["lang"] == "en":
        for label in cjk_labels(normalized):
            print(f"warning: timer label is not English: {label!r}; the UI is English (lang en), "
                  "pass an English label or --lang zh", file=sys.stderr)
    html = _inject(normalized, template)
    with open(a.out_html, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
