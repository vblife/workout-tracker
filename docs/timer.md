# Countdown timer template

`server/ui/timer.html` is the standard countdown timer. `server/render_timer_html.py` turns it into one
self-contained local HTML file that the bot sends as a `file://` attachment. The file makes zero network
requests: CSS, JS, the keep-awake video (base64 `data:` URIs) and the silent keep-alive audio (a WAV built in
JS as a `blob:` URL) are all inline. Never host it externally.

## Generating a timer

```text
# single countdown
python server/render_timer_html.py /workspace/timer-plank.html --duration 1:30 --label "Plank"

# work/rest sequence or a hiit_timer result (rounds, segments, warmup_s, cooldown_s, hiit_id)
python server/render_timer_html.py /workspace/hiit-timer.html --config hiit.json
```

The generator validates the config and injects it the same way `render_report_html.py` does:
`<script>window.openai={toolOutput:<config JSON>};</script>` before the template's first `<script>`, with
`<`, `>` and `&` escaped. Config keys:

| Key | Default | Meaning |
|---|---|---|
| `label` (or `name`) | `Timer` | Title shown on the page and on the lock screen media card |
| `duration_s` | `60` | Single countdown, seconds or `m:ss` |
| `ready_s` | `5` | Get-ready period before the first interval (0-60) |
| `intervals` | none | `[{"kind": "work"/"rest"/"warmup"/"cooldown"/"interval", "seconds": 40, "label": "Burpees"}]` |
| `rounds`, `segments`, `warmup_s`, `cooldown_s` | none | A `hiit_timer` result; expanded like `hiit.html` (final rest of the last round skipped) |
| `hiit_id` | none | Shown on the finished screen so the user can report it for `log_hiit` |
| `lang` | `en` | Built-in label preset: `en` (Start/Pause/Reset...) or `zh` |
| `strings` | none | Override any built-in label, e.g. `{"start": "Go"}` |
| `sound`, `speech`, `vibrate`, `keep_awake` | `true`, `false`, `true`, `true` | Feature switches |

The page also reads URL query parameters (`?duration=90&label=Plank&ready=3&lang=en`) and the URL hash
(base64url JSON like `hiit.html`, or `#duration=90&label=Plank`). Precedence: built-in defaults, then the
injected config, then the query string, then the hash.

## Cues

- Start: get-ready period (default 5 s), beeps at 3, 2, 1, then a distinct rising "go" sound.
- Every interval: beeps for the last 3 seconds, then a transition sound (rising "go" into work, falling tone
  into rest) and a short vibration.
- End: last-3-seconds beeps, a distinct four-note finish sound, and a longer vibration pattern.
- Optional speech (`speech: true`) speaks the countdown numbers only while the page is visible. Beeps are the
  reliable path.

## How it keeps working with the screen off

The old ad hoc timer decremented a counter in `setInterval`, so it froze when the browser throttled or
suspended the page. The template layers several techniques:

1. **Timestamp-based time.** Elapsed time is `Date.now()` minus the start time (plus banked time across
   pauses), so the display and the finish are correct the moment the page runs again, however long JS was
   throttled or frozen. Verified in headless Chrome by freezing the page for 6 s mid-run.
2. **Pre-scheduled Web Audio cues.** On Start, one `AudioContext` (created inside the tap so it is allowed to
   play) schedules every beep as oscillators at absolute `AudioContext` times (up to 20 minutes ahead,
   topped up while running). Scheduled audio runs on the audio thread, so it does not depend on JS timers
   firing in the background. Pause, Skip, and Reset cancel the scheduled nodes; Resume reschedules them. A
   drift check reschedules if the audio clock and the wall clock disagree by more than 250 ms (for example
   after the context was suspended).
3. **Audio session keep-alive.** A silent looping `<audio>` track plays while the timer runs, with Media Session
   metadata (label and current phase), play/pause/next handlers, and position state. On iOS
   `navigator.audioSession.type = "playback"` is also set where supported. This keeps the page an active
   media session and on iOS also lets Web Audio play with the ringer switch on silent.
4. **Screen Wake Lock** with a visible "Keep screen on" toggle (on by default), re-acquired on
   `visibilitychange`. If the API is missing or the request is refused, a tiny muted looping inline video
   (NoSleep.js technique, base64 inline) is played instead. The status line shows which mode is active.
5. **Vibration** via `navigator.vibrate` at transitions and at the end, where supported.

## What works where (honest summary)

Nothing here has been tested on physical phones yet; the notes below come from platform documentation and
known browser behavior, plus desktop/headless Chrome testing. Please report what you see on your device.

| | Keep screen on | Screen turned off / locked | Vibration |
|---|---|---|---|
| **Android Chrome** | Wake Lock (Chrome 84+). | Good: a page playing audio is exempt from heavy background throttling; pre-scheduled beeps keep playing and a media notification appears. Time is always correct on return. | Yes, but Chrome may ignore `vibrate()` while the page is hidden. |
| **iOS Safari** | Wake Lock in Safari 16.4+ (Home Screen web apps only from iOS 18.4); older iOS uses the video fallback. | Best effort. iOS suspends page JS when the screen locks unless the page is actively playing media. The silent audio track and the media session aim to keep audio alive so scheduled beeps fire, but iOS may still suspend or interrupt Web Audio (calls, Siri, other apps' audio, Low Power Mode). Time is always correct on return; tap Resume if the status line says sound is blocked. | Not supported on iOS (no Vibration API). |
| **In-app webviews** (Grok Bot app, other chat apps) | Depends on the host app. Android WebView and iOS WKWebView follow their engines, but the app can block the wake lock or inline/background media. The video fallback usually still keeps the screen on while the page is visible. | Usually worse than the real browser: many apps pause their webview when backgrounded or when the screen locks. Media Session is not available in Android WebView. Some file previewers do not run JavaScript at all. | Android WebView: usually. iOS: no. |

Practical guidance for users:

- Most reliable: keep the screen on (leave the toggle on) and the timer page in front.
- If it must run locked, open the file in the real browser (Chrome on Android, Safari on iOS) rather than an
  in-app preview, press Start, then lock. Keep the volume up.
- The wake lock needs a secure context. `file://` pages count in Chrome and Safari, but some in-app viewers
  load files in ways that do not; the video fallback covers that case while the page is visible.
- Pressing the power button always turns the screen off; no web API can prevent that. Only audio keeps
  running in the background, and only when the platform allows it.

## Relationship to `hiit.html`

`hiit.html` stays the MCP Apps resource for `hiit_timer` (it talks to the host and calls `log_hiit`), and is not
changed here to keep the risk low. The timer template accepts the same `hiit_timer` result and expands it the
same way, so it is the HTML fallback whenever MCP Apps is unavailable. A later change could move `hiit.html`
onto the same timing and audio engine.
