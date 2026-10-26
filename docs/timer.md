# Countdown timer template

`server/ui/timer.html` is the standard countdown timer. `server/render_timer_html.py` turns it into one
self-contained local HTML file that the bot sends as a `file://` attachment. The file makes zero network
requests: CSS and JS are inline; sounds are generated with Web Audio. The page contains no media elements. Never host it externally.

## Generating a timer

```text
# single countdown
python server/render_timer_html.py /workspace/timer-plank.html --duration 1:30 --label "Plank"

# work/rest sequence or a hiit_timer result (rounds, segments, warmup_s, cooldown_s, hiit_id)
python server/render_timer_html.py /workspace/hiit-timer.html --config hiit.json

# default 40/20 HIIT: one file, the user picks 10 or 20 rounds before Start
python server/render_timer_html.py /workspace/hiit-timer.html --work 40 --rest 20 --rounds 10,20 --label "HIIT 40/20"
```

Add `--cache-dir DIR` (or set `WORKOUT_TIMER_CACHE=DIR`) to reuse rendered pages. The generator stores each
page as `DIR/<key>.html`, where the key is a sha256 prefix of the template plus the normalized config, and a
later call with the same template and config copies that page to the out path instead of rendering again
(`timer cache hit <key>` / `timer cache miss <key>` on stderr). `hiit_id` is not part of the key: the cached
page has none, and the current request's `hiit_id` is patched into the out file, so every session of the
same HIIT routine shares one entry. Caching is off when neither is set. The bot uses
`--cache-dir /workspace/timer-cache`.

The generator validates the config and injects it the same way `render_report_html.py` does:
`<script>window.openai={toolOutput:<config JSON>};</script>` before the template's first `<script>`, with
`<`, `>` and `&` escaped. Config keys:

| Key | Default | Meaning |
|---|---|---|
| `label` (or `name`) | `Timer` | Title shown on the page and as the tab title |
| `duration_s` | `60` | Single countdown, seconds or `m:ss` |
| `ready_s` | `0` | Optional get-ready period before the first interval (0-60); 0 = counting starts at once |
| `intervals` | none | `[{"kind": "work"/"rest"/"warmup"/"cooldown"/"interval", "seconds": 40, "label": "Burpees"}]` |
| `rounds`, `segments`, `warmup_s`, `cooldown_s` | none | A `hiit_timer` result; expanded like `hiit.html` (final rest of the last round skipped) |
| `round_options` | none | With `segments`: round counts offered on the idle screen, e.g. `[10, 20]` (see Round selector) |
| `hiit_id` | none | Shown on the finished screen so the user can report it for `log_hiit` |
| `lang` | `en` | UI language: `en` or `zh` (see Language below) |
| `sound`, `speech`, `vibrate`, `keep_awake` | `true`, `false`, `true`, `true` | Feature switches |
| `tick` | `true` | Soft once-per-second tick and its on-page toggle; `false` (CLI `--no-tick`) removes both (see Tick below) |

The page also reads URL query parameters (`?duration=90&label=Plank&ready=3&lang=zh`) and the URL hash
(base64url JSON like `hiit.html`, or `#duration=90&label=Plank`). Precedence: built-in defaults, then the
injected config, then the query string, then the hash.

## From a saved HIIT routine

A routine with `routine_type: "hiit"` (see `docs/database.md`) can drive the timer in two ways:

- `hiit_timer` with `routine_id`, optionally `rounds` and `lang`, and then `--config` with that result. Only
  this path creates a `hiit_id`, so it is the one to use when completion should be logged.
- `--routine-id N` on the CLI. This reads the routine read-only through the server's data layer
  (`db.connect_readonly` + `routines.get_routine`) from `--db PATH` or `$WORKOUT_DB`. It accepts `--rounds N`
  (one number), `--label`, `--lang`, `--ready`, and `--no-tick`. It cannot be combined with `--config`,
  `--duration`, `--work`, or `--rest`.

Both paths build the same config. Each item becomes one work interval with its exercise name (`name_zh` with
`lang zh`), followed by its `rest_seconds`; a rest of 0 means none. Rounds repeat the whole circuit: the
routine's `rounds`, or 1. The final rest is skipped, and the default 10 s warm-up and 60 s cool-down apply.
If the routine has `round_options`, the page shows the round selector; otherwise the count is fixed. During a
work interval that is followed by rest, the "Next" line names the upcoming exercise ("Next: Jump Squat", in zh
"下一个: ..."). During rest it shows the next work interval and its length, as before. All other behavior
(theme, cues, tick, language) is unchanged.

## Guided routine runner (standard routines)

`"mode": "guided"` turns the same template into a set-by-set runner for a saved standard routine. The config
is the `guided_routine_timer` result (`--config guided.json`), or `--routine-id N --guided` for a read-only
preview that starts no workout (`--default-rest SECONDS` changes the 90 s default; `--rounds` is rejected).

- **Steps.** Each item becomes `target_sets` work steps (1 when unset). A rest step follows each set: the
  item's `rest_seconds` (0 = none), otherwise the default (90 s, `default_rest_s` / `--default-rest`).
  Warm-up and cool-down items (named so in `activity_name` or `notes`, e.g. "Treadmill warm-up", "Stretch")
  rest only when they set `rest_seconds` themselves. There is no rest after the final step.
- **Clock.** A work step counts down only when the item has `target_duration_s` (it beeps short, short, long and
  counts as done when it reaches 0). Every other work step counts up from 0:00 until the green **Done** button
  (same size as the other timer buttons) is tapped. Rests count down and move on by themselves; **Next** ends a rest early. **Skip set** skips the set
  and its rest. **Pause** freezes the clock. **End workout** needs a second tap within 4 s.
- **Finish.** The finish screen lists only the sets marked Done (or holds that reached 0), with "N/M sets done",
  and a JSON summary (`<pre id="summary">` and `body[data-guided-summary]`):
  `{"workout_bot": "guided_routine", "session_id", "routine_id", "ended": "finished" | "ended_early",
  "completed_sets", "total_sets", "completed": [{exercise, set_index, reps, weight, unit, duration_s,
  distance_m, is_warmup, loggable, session_item_id, elapsed_s, done_at, ...}]}`. For a timed hold, `duration_s`
  is the seconds actually held (capped at the target); for an untimed step with no reps or distance it is the
  elapsed time.
- **Logging (offline page, fallback).** The rendered file makes no network calls. `guided_routine_timer`
  already started the workout (`start_routine`); the bot then calls `log_sets` only for the completed entries,
  one call per set in order with `performed_at = done_at` and the entry's `session_item_id` (see
  `completion_handoff`), and `finish_workout`. Backdating matters because an active workout closes after 15
  minutes without logged activity.
- **Logging (MCP Apps, primary).** When the client negotiates the `io.modelcontextprotocol/ui` extension,
  `guided_routine_timer` carries `_meta.ui.resourceUri = ui://workout/guided.html`, which serves this same
  template, and its result gains `ui_auto_log: true`. The view gets its config from
  `ui/notifications/tool-result` and talks to the host over the MCP Apps postMessage bridge (`ui/initialize`,
  `tools/call`, `ui/update-model-context`, `ui/notifications/size-changed`, `ui/resource-teardown`), like the
  HIIT widget. Each Done set (or timed hold reaching 0) is logged at once with one `log_sets` call
  (`performed_at = done_at`, `session_item_id`); a `loggable: false` step with a session item is marked
  completed with `set_workout_item_status`. Calls run one at a time in order, once per step. On End workout or
  the natural finish the page waits for pending calls, retries a failed call once (never a timed-out one,
  which may have been saved), and then calls `finish_workout` with the runner's `session_id`, without asking,
  even when nothing was marked Done. If that workout already ended (for example it closed after 15 minutes
  idle and another workout became active from chat), `finish_workout` changes nothing and returns
  `status: "already_finished"` with a `note`, so End never finishes an unrelated workout. Skipped and unreached sets are never logged. If a set still could not be saved, `finish_workout` is not
  called and the page falls back to the offline summary with the unlogged steps (`auto_log` in the JSON) so the
  bot can log them. Without the bridge (the rendered file, or a host without MCP Apps) nothing is sent.
- **Cache.** `session_id` and the per-step `session_item_id`s (stored as a top-level `session_item_ids` list)
  are per-run fields like `hiit_id`: they are left out of the cache key and patched into the out file, so
  starting the same routine again reuses the cached page.

Cues, tick, the no-media-session audio, wake lock, theme, and the en/zh language rule are shared with countdown mode.

## Round selector

For work/rest rounds (`segments` + `rounds`, one exercise per round), `round_options` lets the user pick the
round count on the page before Start. The idle screen shows one large toggle button per option ("10 rounds",
"20 rounds"; "10 组", "20 组" with `lang: "zh"`) under a "Rounds" / "组数" heading. Picking one rebuilds the
interval sequence, the progress bar, the total time, and the cue schedule for that count. After Start the
selector is hidden (and ignored); Reset shows it again. `?rounds=20` or `#rounds=20` preselects an option.
The choice is not written back to the URL, so a base64 config hash is never overwritten.

Totals keep the skip-final-rest rule: `warmup + n*work + (n-1)*rest + cooldown`, plus the get-ready period.
With 40/20 that is 9:40 for 10 rounds and 19:40 for 20 rounds (plus `ready_s` when a get-ready period is set; none by default).

| CLI | Config | Result |
|---|---|---|
| `--work 40 --rest 20 --rounds 10,20` | `"segments": [work 40, rest 20], "round_options": [10, 20]` | Selector, default 10 |
| `... --rounds 10,20 --default-rounds 20` | `..., "round_options": [10, 20], "rounds": 20` | Selector, default 20 |
| `--work 40 --rest 20 --rounds 10` | `..., "rounds": 10` | Fixed 10 rounds, no selector |

On the CLI the first `--rounds` value is the default unless `--default-rounds` is given; `--rest` is optional.
`--work`/`--rest` take seconds or `m:ss`. `--rounds` replaces any rounds or intervals from `--config` and keeps
its other keys, such as `label` or `lang`. Validation fails with one `error:` line and exit 2. Round counts must
be whole numbers from 1 to 100. `round_options` needs 2 to 6 distinct values, which are sorted ascending. The
default must be one of the options. `round_options` only works with `segments`, never with `intervals` or
`duration`. `--work` needs `--rounds` and the reverse, and neither can be combined with `--duration`. The injected
config carries `round_options`, the cleaned `segments`, and `round_totals` (for example
`{"10": 580, "20": 1180}`). `intervals`/`total_s` describe the default count.

## Language

The UI is **English by default**, even for users who chat in another language. The template has two fixed text
tables, `en` and `zh`. Set `lang: "zh"` in the config or pass `--lang zh` for a Chinese UI (buttons, phase names,
status lines). Spoken cues (`speech: true`) use the same language (`en-US` or `zh-CN`). There are no per-label
`strings` overrides; the generator rejects that key.

Unknown values fail clearly in the generator. `--lang fr` is an argparse usage error, and `{"lang": "fr"}` exits
2 with `lang must be one of: en, zh`. A typo is a bot mistake, so failing loudly beats silently showing a
different language. Inside the page, a `lang` from the URL (`?lang=zh` or `#lang=zh`) can switch the UI, and an
unknown URL value falls back to English, because a page that is already open cannot fail.

Labels (`label`/`name` and interval or exercise labels) come from the caller and are shown exactly as given;
the generator never rewrites or transliterates them. Bots pass labels in the UI language: English by default,
and Chinese only with `lang: "zh"`. With `lang: "en"` the CLI prints
`warning: timer label is not English: ...` on stderr when a label contains CJK characters. It still writes the
file, so a timer is never lost because of the label. With `lang: "zh"` a Chinese label gives no warning.

Bots pass `lang: "zh"` only when the user's stated language preference is Chinese (an explicit request or a
saved preference), not just because they chat in Chinese.

## Cues

One cue shape is used everywhere: **short, short, long**. The two short beeps play 2 s and 1 s before a
boundary and the long beep starts exactly on 0, so it doubles as the countdown. There are no separate 3-2-1
beeps.

- Start: by default (`ready_s: 0`) there is no get-ready period, counting starts at once, and only the long
  beep plays at Start, because there is no time for the short beeps. With an optional `ready_s` (or `--ready N`,
  1-60) a get-ready period plays first and ends with short, short, long as counting begins.
- Every interval change in a sequence: short, short, long into the next interval, with a short vibration.
  Changes into rest or cool-down use the same shape at a lower pitch, so you can tell work from rest
  without looking.
- End: the same short, short, long, plus a longer vibration pattern.
- A phase shorter than 3 s only gets the short beeps that fit after it starts.
- Tones are soft sines (work/start 523 Hz short and 659 Hz long; rest 440 Hz short and 392 Hz long). They use
  a 30 ms attack, an exponential release, and moderate gain. No square waves and no high pitches.
- Optional speech (`speech: true`) speaks the countdown numbers only while the page is visible. Beeps are the
  reliable path.

## Tick

While counting, a soft clock-like tick plays once per second, like a second hand. It is a 20 ms click (a
1.6 kHz sine plus a little noise, decaying with a 2.5 ms time constant) rendered once into an `AudioBuffer`,
played at gain 0.05, against 0.28-0.32 for the cue beeps.

- Ticks sit on whole seconds from the start of each counting interval. A tick is skipped within 0.5 s of any
  cue beep, so ticks never collide with short, short, long. In a 40 s work interval you hear 37 ticks, then
  the two shorts and the long.
- There are no ticks during the optional get-ready period (off by default): it already ends with its own
  short, short, long, so silence before Go keeps the start cue distinct.
- `buildTicks(segs, cues)` is a pure function next to `buildCues`, and the ticks are pre-scheduled on the same
  `AudioContext` with the same horizon, top-up, and drift check. They keep playing with the screen off
  whenever the beeps do. Pause, Skip, and Reset cancel them with the cues; Resume and a round change reschedule them.
- A "Tick" toggle (zh: "滴答声"), on by default, sits under "Keep screen on". Turning it off mid-run reschedules
  without ticks. `tick: false`, `?tick=0`, or the CLI flag `--no-tick` removes the ticks and the toggle
  entirely. With `sound: false` there are no ticks either.

## Look

The timer is always dark (`color-scheme: dark`, no `prefers-color-scheme` switch), so it looks the same in
light and dark mode and inside in-app viewers. Each state has its own dark background: idle, get-ready, work,
rest, warm-up, cool-down, plain countdown, paused, and done. Text, buttons, and the progress bar come from one
palette of CSS variables in `:root`. `test_render_timer_html.py` checks the palette against WCAG AA:

- Body text on every state background: 14.1-17.1:1.
- Secondary text: 9.9-11.9:1.
- Buttons: 6.1-11.7:1.
- Round toggles: selected 10.9:1 and unselected 11.6:1. The selected vs. unselected fill is 7.4:1, so the
  selection is clear without relying on text.
- Progress bar (non-text, 3:1 required): track vs. background is at least 3.5:1, and fill vs. track is 3.6:1.

## How it keeps working with the screen off

The old ad hoc timer decremented a counter in `setInterval`, so it froze when the browser throttled or
suspended the page. The template layers several techniques:

1. **Timestamp-based time.** Elapsed time is `Date.now()` minus the start time (plus banked time across
   pauses), so the display and the finish are correct the moment the page runs again, however long JS was
   throttled or frozen. Verified in headless Chrome by freezing the page for 6 s mid-run.
2. **Pre-scheduled Web Audio cues.** On Start, one `AudioContext` (created inside the tap so it is allowed to
   play) schedules every beep as sine oscillators at absolute `AudioContext` times (up to 20 minutes ahead,
   topped up while running). Scheduled audio runs on the audio thread, so it does not depend on JS timers
   firing in the background. Pause, Skip, and Reset cancel the scheduled nodes; Resume reschedules them. A
   drift check reschedules if the audio clock and the wall clock disagree by more than 250 ms (for example
   after the context was suspended).
3. **No media session.** Sound is Web Audio only. The page has no `<audio>` or `<video>` element, never
   calls `HTMLMediaElement.play()`, never touches `navigator.mediaSession` (no metadata, no action handlers,
   no `playbackState`, not even to clear them), and never sets `navigator.audioSession.type = "playback"`.
   Any of those can make iOS and Android (or the app hosting an in-app webview) treat the page as a media
   player: Start opens a Now Playing / media notification card and pauses other apps' audio (for example
   TikTok). The page sets `navigator.audioSession.type = "ambient"` where supported, at load and again
   before the `AudioContext` is created or resumed on every Start and Resume, so cues mix with other audio.
   The only unlock is a one-sample silent `AudioContext` buffer inside the tap; no oscillator runs
   continuously. `server/tests/test_render_timer_html.py` fails if any of these come back.
   History: the first versions looped a silent `<audio>` track with Media Session metadata and action
   handlers and set the audio session to `"playback"`, so Start registered a media player. #32 removed
   those but still cleared `navigator.mediaSession` on every Start and kept a muted looping `<video>`
   keep-awake fallback; Now Playing still appeared on Start on an iPhone. In current WebKit, reading
   `navigator.mediaSession` creates a platform media session and `setActionHandler(…, null)` updates the
   system remote commands, and a playing `<video>` is a media session client that host apps can observe, so
   both are gone now. A WebKit `AudioContext` is Now Playing eligible only with audio session `"playback"` or
   `"play-and-record"`, which the page never sets.
   Trade-off: without a media session, iOS may silence cues when the ringer switch is on silent and may
   suspend audio once the screen locks, so keep the screen on.
4. **Screen Wake Lock only**, with a visible "Keep screen on" toggle (on by default), re-acquired on
   `visibilitychange`. There is no video fallback (the NoSleep.js muted looping video was removed because a
   playing media element can bring up Now Playing). If the API is missing or the request is refused, the
   status line says keep-screen-on is not available, so leave the screen on by hand or raise the
   auto-lock time. The status line shows which mode is active.
5. **Vibration** via `navigator.vibrate` at transitions and at the end, where supported.

## What works where (honest summary)

Nothing here has been tested on physical phones yet; the notes below come from platform documentation and
known browser behavior, plus desktop/headless Chrome testing. Please report what you see on your device.

| | Keep screen on | Screen turned off / locked | Vibration |
|---|---|---|---|
| **Android Chrome** | Wake Lock (Chrome 84+). | Pre-scheduled beeps usually keep playing for a while, but Chrome may throttle or suspend a hidden page that is not playing media. No media notification appears. Time is always correct on return. | Yes, but Chrome may ignore `vibrate()` while the page is hidden. |
| **iOS Safari** | Wake Lock in Safari 16.4+ (Home Screen web apps only from iOS 18.4); older iOS has no keep-screen-on (the status line says so). | Best effort. iOS suspends page JS when the screen locks unless the page is actively playing media. The page does not register a media session (so no Now Playing card), so scheduled beeps may stop once the screen locks, and iOS may also suspend or interrupt Web Audio (calls, Siri, Low Power Mode). With the ringer switch on silent, cues may be muted. Time is always correct on return; tap Resume if the status line says sound is blocked. | Not supported on iOS (no Vibration API). |
| **In-app webviews** (Grok Bot app, other chat apps) | Depends on the host app. Android WebView and iOS WKWebView follow their engines, but the app can block the wake lock or inline/background media. Without the wake lock there is no keep-screen-on; the status line says so. | Usually worse than the real browser: many apps pause their webview when backgrounded or when the screen locks. Media Session is not available in Android WebView. Some file previewers do not run JavaScript at all. | Android WebView: usually. iOS: no. |

Practical guidance for users:

- Most reliable: keep the screen on (leave the toggle on) and the timer page in front.
- If it must run locked, open the file in the real browser (Chrome on Android, Safari on iOS) rather than an
  in-app preview, press Start, then lock. Keep the volume up.
- The wake lock needs a secure context. `file://` pages count in Chrome and Safari, but some in-app viewers
  load files in ways that do not. There the status line says keep-screen-on is not available: leave the
  screen on by hand or open the file in the real browser.
- Pressing the power button always turns the screen off; no web API can prevent that. Only audio keeps
  running in the background, and only when the platform allows it.

## Relationship to `hiit.html`

`hiit.html` stays the MCP Apps resource for `hiit_timer` (it talks to the host and calls `log_hiit`), and is not
changed here to keep the risk low. The timer template accepts the same `hiit_timer` result and expands it the
same way, so it is the HTML fallback whenever MCP Apps is unavailable. A later change could move `hiit.html`
onto the same timing and audio engine.
