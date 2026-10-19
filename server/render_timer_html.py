"""Render a standalone countdown timer HTML file from server/ui/timer.html.

The output is one self-contained local file (inline CSS/JS, no network requests),
meant to be sent as a file:// attachment. Config is injected exactly like
render_report_html.py: <script>window.openai={toolOutput:<config JSON>};</script>
before the template's first <script> tag.

Usage:
  python server/render_timer_html.py out.html --duration 2:00 --label "Warm-up"
  python server/render_timer_html.py out.html --config hiit.json      # hiit_timer result
  python server/render_timer_html.py out.html --config timer.json --lang zh

Config keys (all optional):
  label        title shown on the page (hiit_timer "name" is accepted too)
  duration_s   single countdown length in seconds (default 60)
  ready_s      get-ready period before the first interval, 0-60 (default 5)
  intervals    [{"kind": work|rest|warmup|cooldown|interval, "seconds": int, "label": str}]
  rounds, segments, warmup_s, cooldown_s, hiit_id
               the structured hiit_timer result; expanded into intervals the same
               way hiit.html does (the final rest of the last round is skipped)
  lang         "en" (default) or "zh" preset for the built-in UI labels
  strings      per-label overrides, e.g. {"start": "Go"}
  sound, speech, vibrate, keep_awake   booleans (defaults true, false, true, true)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_report_html import render as _inject  # noqa: E402

DEFAULT_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "timer.html")
DEFAULTS = {"label": "Timer", "duration_s": 60, "ready_s": 5, "lang": "en"}
KINDS = ("work", "rest", "warmup", "cooldown", "interval")
LANGS = ("en", "zh")
STRING_KEYS = (
    "start", "pause", "resume", "reset", "skip", "ready", "work", "rest", "warmup", "cooldown",
    "interval", "done", "next", "round", "left", "keepAwake", "go", "wlLock", "wlVideo", "wlOff",
    "wlNone", "audioOff", "total", "tellChat", "badLink",
)
FLAGS = ("sound", "speech", "vibrate", "keep_awake")
MAX_SECONDS = 86400
MAX_INTERVALS = 2000


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
        raise ValueError("lang must be one of: " + ", ".join(LANGS))
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
    if config.get("hiit_id") is not None:
        out["hiit_id"] = config["hiit_id"]
    for flag in FLAGS:
        if flag in config:
            if not isinstance(config[flag], bool):
                raise ValueError(f"{flag} must be true or false")
            out[flag] = config[flag]
    strings = config.get("strings")
    if strings:
        if not isinstance(strings, dict):
            raise ValueError("strings must be an object")
        unknown = sorted(set(strings) - set(STRING_KEYS))
        if unknown:
            raise ValueError("unknown strings keys: " + ", ".join(unknown))
        out["strings"] = {k: str(v)[:200] for k, v in strings.items()}
    return out


def render(config, template_html):
    return _inject(normalize_config(config), template_html)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out_html")
    p.add_argument("--config", help="timer config JSON file ('-' for stdin), e.g. a hiit_timer result")
    p.add_argument("--duration", help="single countdown length: seconds or m:ss")
    p.add_argument("--label")
    p.add_argument("--ready", help="get-ready seconds before the first interval (default 5)")
    p.add_argument("--lang", choices=LANGS)
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
    if a.label is not None:
        config["label"] = a.label
    if a.ready is not None:
        config["ready_s"] = a.ready
    if a.lang is not None:
        config["lang"] = a.lang
    with open(a.template, encoding="utf-8") as f:
        template = f.read()
    try:
        html = render(config, template)
    except ValueError as e:
        p.error(str(e))
    with open(a.out_html, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
