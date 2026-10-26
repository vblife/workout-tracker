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
  python server/render_timer_html.py out.html --routine-id 3 --label "Circuit"   # saved hiit routine (read-only)
  python server/render_timer_html.py out.html --duration 5:00 --label "5-minute timer" --cache-dir /workspace/timer-cache
  python server/render_timer_html.py out.html --config guided.json --cache-dir /workspace/timer-cache   # guided_routine_timer result
  python server/render_timer_html.py out.html --routine-id 4 --guided --default-rest 120   # saved standard routine (read-only preview)

Caching (--cache-dir DIR, or env WORKOUT_TIMER_CACHE): the rendered HTML is stored as
DIR/<key>.html, where key is a sha256 prefix of the template plus the normalized config
without hiit_id. Rendering the same timer again (same template, same config, any out path)
reuses that file; a hiit_id in the request is patched into the copy written to out.html, so
HIIT sessions with different ids share one cache entry. A "timer cache hit <key>" or
"timer cache miss <key>" line goes to stderr. Without either setting, caching is off.

Config keys (all optional):
  label        title shown on the page (hiit_timer "name" is accepted too)
  duration_s   single countdown length in seconds (default 60)
  ready_s      optional get-ready period before the first interval, 0-60 (default 0 = none)
  intervals    [{"kind": work|rest|warmup|cooldown|interval, "seconds": int, "label": str}]
  rounds, segments, warmup_s, cooldown_s, hiit_id
               the structured hiit_timer result; expanded into intervals the same
               way hiit.html does (the final rest of the last round is skipped)
  round_options  e.g. [10, 20]: with segments, shows a round-count selector on the
               idle screen; rounds is the default and must be one of the options
  mode         "guided": a set-by-set routine runner instead of a countdown (see below)
  lang         UI language: "en" (default) or "zh"; spoken cues follow it
  sound, speech, vibrate, keep_awake, tick   booleans (defaults true, false, true, true, true);
               tick false removes the soft once-per-second tick and its on-page toggle

Guided mode ("mode": "guided", e.g. a guided_routine_timer result, or --routine-id N --guided):
  steps        ordered [{"kind": work|warmup|cooldown, "label", "exercise", "reps", "weight", "unit",
               "duration_s", "set_index", "sets", ...} or {"kind": "rest", "seconds": int}]. A work step
               counts up from 0 until Done, or counts down from duration_s (timed hold; Done still allowed).
               Rest counts down and advances by itself; trailing rests are dropped.
  session_id   active workout id; with each step's session_item_id it is not part of the cache key and is
               patched into the out file like hiit_id
"""
import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_report_html import render as _inject, safe_json  # noqa: E402

DEFAULT_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "timer.html")
DEFAULTS = {"label": "Timer", "duration_s": 60, "ready_s": 0, "lang": "en"}
KINDS = ("work", "rest", "warmup", "cooldown", "interval")
LANGS = ("en", "zh")  # must match the STRINGS tables in ui/timer.html
REMOVED_KEYS = ("strings",)  # per-label overrides were removed; fail loudly instead of ignoring them
CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
FLAGS = ("sound", "speech", "vibrate", "keep_awake", "tick")
MAX_SECONDS = 86400
MAX_INTERVALS = 2000
MAX_ROUNDS = 100
MAX_ROUND_OPTIONS = 6
GUIDED_WORK_KINDS = ("work", "warmup", "cooldown")
RUN_KEYS = ("hiit_id", "session_id", "session_item_ids")  # per-run values: kept out of the cache key
CACHE_KEY_LEN = 32
INJECT_PREFIX = "<script>window.openai={toolOutput:"  # written by render_report_html.render
INJECT_SUFFIX = "};</script>"


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


def _opt_int(value, name, minimum=0, maximum=100000):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a whole number {minimum}-{maximum} (got {value!r})")
    return value


def _opt_num(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be a number >= 0 (got {value!r})")
    return value


def normalize_guided(config, out):
    """Guided routine runner: validate steps; split per-run ids (session_item_id) into session_item_ids."""
    steps = config.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("guided mode needs a non-empty steps list")
    clean, item_ids = [], []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        kind = step.get("kind", "work")
        if kind == "rest":
            seconds = parse_seconds(step.get("seconds"), "rest seconds", minimum=0, maximum=3600)
            if seconds:
                clean.append({"kind": "rest", "seconds": seconds})
                item_ids.append(None)
            continue
        if kind not in GUIDED_WORK_KINDS:
            raise ValueError("step kind must be one of: rest, " + ", ".join(GUIDED_WORK_KINDS))
        label = _label(step.get("label") or step.get("exercise"))
        if not label:
            raise ValueError("each work step needs a label or exercise")
        duration = step.get("duration_s")
        duration = None if duration in (None, 0) else parse_seconds(duration, "step duration_s", maximum=3600)
        unit = step.get("unit")
        if unit not in (None, "lb", "kg"):
            raise ValueError("step unit must be lb or kg")
        weight = _opt_num(step.get("weight"), "step weight")
        clean.append({
            "kind": kind, "label": label, "exercise": _label(step.get("exercise")) or label,
            "set_index": _opt_int(step.get("set_index"), "set_index", 1, 1000) or 1,
            "sets": _opt_int(step.get("sets"), "sets", 1, 1000) or 1,
            "reps": _opt_int(step.get("reps"), "reps", 1, 10000),
            "weight": weight, "unit": (unit or "kg") if weight is not None else None,
            "duration_s": duration, "distance_m": _opt_num(step.get("distance_m"), "distance_m"),
            "notes": _label(step.get("notes")),
            "timer": "countdown" if duration else "countup",
            "is_warmup": bool(step.get("is_warmup", kind == "warmup")),
            "loggable": bool(step.get("loggable", True)),
        })
        item_ids.append(_opt_int(step.get("session_item_id"), "session_item_id", 1, 2**62))
    while clean and clean[-1]["kind"] == "rest":  # no rest after the final step
        clean.pop()
        item_ids.pop()
    if len(clean) > MAX_INTERVALS:
        raise ValueError(f"at most {MAX_INTERVALS} steps")
    out["mode"] = "guided"
    out["steps"] = clean
    if config.get("routine_id") is not None:
        out["routine_id"] = _opt_int(config["routine_id"], "routine_id", 1, 2**62)
    if config.get("session_id") is not None:
        out["session_id"] = _opt_int(config["session_id"], "session_id", 1, 2**62)
    if any(x is not None for x in item_ids):
        out["session_item_ids"] = item_ids
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
    mode = config.get("mode")
    if mode not in (None, "timer", "guided"):
        raise ValueError(f"mode must be timer or guided (got {mode!r})")
    if mode == "guided":
        normalize_guided(config, out)
        for flag in FLAGS:
            if flag in config:
                if not isinstance(config[flag], bool):
                    raise ValueError(f"{flag} must be true or false")
                out[flag] = config[flag]
        return out
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
    labels = [config.get("label")] + [x.get("label") for x in config.get("intervals", []) + config.get("segments", [])
                                      + config.get("steps", [])]
    return [x for x in dict.fromkeys(labels) if x and CJK.search(x)]


def render(config, template_html):
    return _inject(normalize_config(config), template_html)


def _without_run_fields(normalized):
    return {k: v for k, v in normalized.items() if k not in RUN_KEYS}


_without_hiit_id = _without_run_fields  # older name


def fingerprint(normalized, template_html):
    """Cache key: sha256 of the template plus the normalized config, ignoring per-run ids (hiit_id, session ids)."""
    template_bytes = template_html.encode("utf-8") if isinstance(template_html, str) else template_html
    config = json.dumps(_without_run_fields(normalized), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256()
    digest.update(template_bytes)
    digest.update(b"\0")
    digest.update(config.encode("utf-8"))
    return digest.hexdigest()[:CACHE_KEY_LEN]


def cache_path(cache_dir, key):
    return os.path.join(cache_dir, f"{key}.html")


def apply_run_fields(html, fields):
    """Set (or, for None values, remove) per-run keys in the injected config of a rendered timer page."""
    start = html.find(INJECT_PREFIX)
    if start < 0:
        raise ValueError("timer HTML has no injected config")
    body = start + len(INJECT_PREFIX)
    end = html.find(INJECT_SUFFIX, body)
    if end < 0:
        raise ValueError("timer HTML has an unterminated injected config")
    # safe_json only adds \uXXXX escapes, which json.loads reads back unchanged.
    config = json.loads(html[body:end])
    for key, value in fields.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    return html[:body] + safe_json(config) + html[end:]


def apply_hiit_id(html, hiit_id):
    """Set (or, for None, remove) hiit_id in the injected config of a rendered timer page."""
    return apply_run_fields(html, {"hiit_id": hiit_id})


def render_cached(normalized, template_html, cache_dir):
    """Return (html, key, hit) for a normalized config, reusing cache_dir/<key>.html when present.

    The cache file never contains per-run ids (RUN_KEYS); the returned html carries normalized's, if any.
    """
    key = fingerprint(normalized, template_html)
    path = cache_path(cache_dir, key)
    run = {k: normalized.get(k) for k in RUN_KEYS}
    try:
        with open(path, encoding="utf-8") as f:
            cached = f.read()
        return apply_run_fields(cached, run), key, True
    except (OSError, ValueError):
        pass  # missing or unreadable entry: render and overwrite it
    shared = _inject(_without_run_fields(normalized), template_html)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(shared)
    os.replace(tmp, path)
    html = shared if all(v is None for v in run.values()) else _inject(normalized, template_html)
    return html, key, False


def routine_is_standard(message):
    return "is a standard routine" in message


def _routine_config(p, a):
    """hiit_timer-equivalent config for a saved hiit routine, read through the server's data layer."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sqlite3
    import core
    import db
    import routines
    rounds = None
    if a.rounds is not None:
        try:
            values = parse_rounds_arg(a.rounds)
        except ValueError as e:
            p.error(str(e))
        if len(values) != 1:
            p.error("with --routine-id, --rounds takes one number; round options come from the routine")
        rounds = values[0]
    try:
        c = db.connect_readonly(a.db)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as e:
        p.error(str(e))
    try:
        routine = routines.get_routine(c, a.routine_id)
        if a.guided:
            if rounds is not None:
                p.error("--rounds does not apply to --guided")
            rest = routines.GUIDED_DEFAULT_REST_SECONDS if a.default_rest is None else parse_seconds(a.default_rest, "--default-rest", minimum=0, maximum=3600)
            steps = routines.guided_steps(routine, rest, a.lang or "en", core.display_unit(c))
            return {"mode": "guided", "label": routine["name"], "routine_id": routine["routine_id"], "steps": steps}
        cfg = core.routine_hiit_config(routine, lang=a.lang or "en", rounds=rounds)
    except core.UserError as e:
        message = str(e)
        if routine_is_standard(message):
            message += " (or pass --guided for the set-by-set routine runner)"
        p.error(message)
    except ValueError as e:
        p.error(str(e))
    finally:
        c.close()
    cfg["label"] = cfg.pop("name")
    cfg.pop("planned_seconds", None)
    return cfg


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out_html")
    p.add_argument("--config", help="timer config JSON file ('-' for stdin), e.g. a hiit_timer result")
    p.add_argument("--duration", help="single countdown length: seconds or m:ss")
    p.add_argument("--label")
    p.add_argument("--ready", help="optional get-ready seconds before the first interval, 0-60 (default 0 = start counting at once)")
    p.add_argument("--work", help="work/rest rounds mode: work seconds per round (seconds or m:ss)")
    p.add_argument("--rest", help="rest seconds between rounds (the final rest is skipped)")
    p.add_argument("--rounds", help="round count, or a comma list like 10,20 to show a selector")
    p.add_argument("--default-rounds", help="default for a --rounds list (default: the first value)")
    p.add_argument("--routine-id", type=int, help="build the timer from a saved hiit routine (read-only database access)")
    p.add_argument("--db", help="workout database for --routine-id (default: $WORKOUT_DB, else the server default)")
    p.add_argument("--guided", action="store_true",
                   help="with --routine-id: build the guided set-by-set runner from a saved standard routine (no workout is started)")
    p.add_argument("--default-rest", help="with --guided: rest after a set when the item has no rest_seconds (default 90)")
    p.add_argument("--no-tick", dest="tick", action="store_false", default=None,
                   help="remove the soft once-per-second tick and its toggle (same as \"tick\": false)")
    p.add_argument("--lang", choices=LANGS, help="UI language (default en)")
    p.add_argument("--template", default=os.environ.get("WORKOUT_TIMER_TEMPLATE", DEFAULT_TEMPLATE))
    p.add_argument("--cache-dir", default=os.environ.get("WORKOUT_TIMER_CACHE") or None,
                   help="reuse rendered HTML from this directory, keyed by template + config without hiit_id "
                        "(default: $WORKOUT_TIMER_CACHE; caching is off when neither is set)")
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
    if (a.guided or a.default_rest is not None) and a.routine_id is None:
        p.error("--guided and --default-rest are only used with --routine-id")
    if a.default_rest is not None and not a.guided:
        p.error("--default-rest needs --guided")
    if a.routine_id is not None:
        if a.config or a.duration is not None or a.work is not None or a.rest is not None or a.default_rounds is not None:
            p.error("--routine-id cannot be combined with --config, --duration, --work, --rest or --default-rounds")
        config = _routine_config(p, a)
    elif a.db is not None:
        p.error("--db is only used with --routine-id")
    if a.duration is not None:
        for key in ("intervals", "segments", "rounds", "warmup_s", "cooldown_s", "mode", "steps"):
            config.pop(key, None)
        config["duration_s"] = a.duration
    rounds_mode = a.routine_id is None and any(v is not None for v in (a.work, a.rest, a.rounds, a.default_rounds))
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
            for key in ("intervals", "segments", "rounds", "round_options", "warmup_s", "cooldown_s", "duration_s", "mode", "steps"):
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
    if a.tick is False:
        config["tick"] = False
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
    if a.cache_dir:
        html, key, hit = render_cached(normalized, template, a.cache_dir)
        print(f"timer cache {'hit' if hit else 'miss'} {key}", file=sys.stderr)
    else:
        html = _inject(normalized, template)
    with open(a.out_html, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
