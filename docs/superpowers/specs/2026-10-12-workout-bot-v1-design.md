# Workout Bot V1 Design

## Purpose

Workout Bot is a private, local-first fitness assistant for Grok Bot. It replaces the first-release workflows that matter from Vibrant Fitness with a conversational experience: quick workout logging, saved agent-generated plans, daily/weekly/monthly goals, proactive coaching, configurable HIIT, progress insights, and readable report dashboards.

Each user receives an independent copy of the Bot and a private SQLite database on that user's Grok Bot computer. The first release starts fresh and does not import Vibrant Fitness data.

## Product identity and installation

- User-facing name: `Workout Bot`
- Stable slug and MCP server name: `workout-bot`
- Installed project root: `/home/vblife/workout`
- Durable per-user data directory: `/workspace/vblife/workout-data`
- Default database: `/workspace/vblife/workout-data/workout.db`
- MCP transport: local Command server over stdio
- Canonical UI resources: `/home/vblife/workout/server/ui`
- The finished template does not use or ship the repository's `site/` fallback directory.

The database is isolated from other Grok Bot users. It is not a security boundary between Bots owned by the same user because those Bots share the user's Grok Bot computer and filesystem.

## First-release scope

### Included

- Natural-language strength, bodyweight, cardio, hike, mobility, and HIIT logging.
- English and Chinese exercise-name aliases, including user-taught aliases.
- Quick conversational logging without a live workout-session screen.
- Deterministic PR, estimated one-rep max, progressive-overload, recovery, and planning calculations.
- Persisted agent-generated workout plans and plan adherence.
- Daily, weekly, and monthly goals derived from workout records.
- Optional body-weight history.
- Calorie estimates with explicit confidence and assumptions.
- Daily, weekly, monthly, quarterly, annual, and all-time reports.
- Interactive report, progress, and HIIT interfaces when the client supports MCP Apps.
- Readable text and structured-data results when the client does not support MCP Apps.
- Three proactive routines: daily plan, workout reminder, and weekly report.

### Deferred

- Vibrant Fitness data migration or import.
- Authentication or multi-user records inside one database.
- Persistent live workout state, pause/resume, set timers, and rest timers.
- Routine folders and a screen-oriented routine library.
- Apple Health, steps, standing hours, or other health-platform integrations.
- Exercise favorites, personal notes, instructional media, and social features.
- Nutrition and meal tracking.
- Public static-site hosting.
- In-widget HIIT configuration editing; first-release configuration is conversational.

## Architecture

The existing Python MCP server remains the system of record. The agent interprets natural language, gathers missing information, and explains results. The server owns persistence, validation, normalization, aggregation, goal calculations, plan linkage, calorie estimates, and all reported numbers.

```text
Grok Bot conversation
        |
        | stdio Command MCP
        v
/home/vblife/workout/server/
  protocol, domain logic, calculations, SQLite access
        |
        +-- /workspace/vblife/workout-data/workout.db
        |
        +-- /home/vblife/workout/server/ui/
              report.html
              progress.html
              hiit.html
```

The UI HTML files are self-contained MCP resources with no required external network access. They receive tool inputs and `structuredContent` from a supporting host. They are enhancements over the same text and structured tool result, not a requirement for core behavior.

## MCP capability negotiation

The server records the client capabilities supplied in the MCP `initialize` request. It advertises and attaches `io.modelcontextprotocol/ui` resources only when the client advertises compatible MCP Apps support.

When MCP Apps is supported:

1. A UI-enabled tool declares its `ui://` resource.
2. Grok Bot reads the resource from `server/ui/` through `resources/read`.
3. The host passes tool input and structured output into the sandboxed interface.
4. The HIIT interface may call `log_hiit` through the host.

When MCP Apps is not supported, the same tool remains available and returns a concise text summary plus complete structured data. No UI claim, public fallback URL, or automatic app callback is presented. The first implementation milestone must probe Grok Bot desktop and mobile behavior rather than assuming inline UI support.

## Data model

Existing settings, exercises, aliases, sessions, sets, and HIIT records remain. A versioned, transactional migration adds the following concepts.

### Goals

Each goal stores:

- Period: `day`, `week`, or `month`.
- Metric: workout sessions, training days, workout minutes, cardio minutes, strength sets, strength volume, distance, or estimated calories.
- Numeric target and unit.
- Effective start date.
- Active state and timestamps.

Goal progress is derived from current workout records in the user's configured timezone. Achievement history and streaks are recalculated from facts, so editing or deleting a workout changes the result correctly.

### Workout plans

A workout plan stores its date, title, focus, rationale, expected duration, status, and ordered items. Each item stores an exercise or activity, target sets, reps, weight, duration, distance, notes, and its link to completed work when available.

Plan statuses are `planned`, `started`, `completed`, `partial`, `skipped`, and `replaced`. Check-ins record status transitions and timestamps. Reports calculate adherence from those records and completed plan items.

When a user logs work and there is an active plan for that local date, the server links it only when the exercise or activity match is unambiguous. An ambiguous match remains unlinked until the agent confirms it.

### Body weight

Optional dated weight entries retain the input value and unit and store a normalized kilogram value. The latest entry on or before a workout is available to calorie estimation. Users may add, correct, list, or delete entries.

### Calorie estimates

Every eligible session may store estimated calories, confidence, the weight and duration used, the selected intensity/MET value, the calculation version, and human-readable assumptions.

Input priority is:

1. Weight: value supplied for the session, then latest recorded body weight, then a documented 70 kg fallback.
2. Duration: explicit duration, completed HIIT elapsed time, saved plan duration, timestamp span, then a conservative set-count heuristic.
3. Intensity: activity type and recorded RPE when available, then a conservative default for the session type.

The server uses a versioned MET-based formula. Confidence is `higher`, `moderate`, or `rough`. Reports show calorie totals alongside confidence coverage and never imply that a rough or partial estimate is measured data. Editing weight, duration, RPE, or workout contents recalculates the affected estimate.

## MCP tool surface

Existing logging, exercise resolution, statistics, progressive-overload, daily-plan context, reports, progress, and HIIT tools remain, with the product name and generic-user language updated.

New or extended responsibilities are grouped as follows:

### Setup and preferences

- Read current setup state, including units, timezone, goals, training preferences, reminder preferences, optional body weight, and HIIT defaults.
- Update only explicitly supplied settings.
- Validate and persist HIIT defaults as structured settings.

### Goals

- Create or update a goal.
- List active or historical goals.
- Remove or deactivate a goal.
- Calculate progress, achievement, and streaks for a requested period and anchor date.

### Plans

- Save a complete agent-generated plan.
- Fetch a plan by identifier or local date.
- List recent plans.
- Change plan status through valid transitions.
- Link or unlink completed work.
- Return plan adherence for reporting.

### Body weight and calories

- Record and retrieve optional body-weight entries.
- Extend logging inputs with optional workout duration and session weight.
- Return calorie estimate, confidence, and assumptions with logged sessions.

### Reports

- Add `day` to the existing report periods.
- Preserve `week`, `month`, `quarter`, `year`, and `all` periods.
- Return totals, comparison values, goals, streaks, plan adherence, PRs, workout mix, lift progress, calorie confidence coverage, and chart-ready time series.

## Conversational flows

### Repeatable setup

Setup is not a one-time gate. On first use it collects only required missing values. At any later time, the user may review or change units, timezone, training days, goals, preferred days, equipment, constraints, body weight, reminder settings, or HIIT defaults. Each confirmed change is saved immediately. Unspecified preferences are never reset.

### Quick logging

Users report completed work naturally. The agent parses it into tool arguments and logs before replying. Planned work is never marked complete merely because it was suggested. Unknown exercises require confirmation and may then be saved as aliases or new exercises.

### Saved workout plans

The agent reads current settings, recent work, recovery, goals, available equipment, and active plans before planning. It saves the plan before presenting it. A user can later start today's plan, replace it, skip it, or log work against it. Reports distinguish completed, partial, skipped, and replaced plans.

### HIIT preferences

Default values are:

- Work duration: 40 seconds.
- Rest duration: 20 seconds.
- Rounds: 20.
- Warm-up: 10 seconds.
- Cooldown: 60 seconds.

On the first HIIT request, the skill presents those defaults and asks for exercise names. The user may change any value before confirmation. The confirmed configuration becomes the saved default.

Later requests reuse the saved default unless the user supplies overrides. For an ad-hoc change, the agent asks whether the change is for this session only or should replace the default. Users can review, edit, or clear HIIT defaults through either setup or the HIIT flow.

The timer accepts any ordered list of named work and rest segments, displays the current and next segment, counts rounds and total time, supports start/pause/skip/finish, and logs completion idempotently when the host supports app-to-tool calls.

## Reports and dashboards

`server/ui/report.html` is one parameterized interface for all report periods. The hierarchy is:

1. Period and date range.
2. Primary goal progress.
3. Short coach summary.
4. Totals for workouts, training time, strength volume, cardio duration, distance, and calories when relevant.
5. Switchable trends.
6. Workout mix.
7. PRs, streaks, plan adherence, and actionable next steps.

Trend windows are:

- Daily report: recent four-week rhythm.
- Weekly report: six weekly points for training time, strength volume, and cardio duration.
- Monthly report: six monthly points for the same three metrics.
- Annual report: twelve monthly points for the same three metrics.
- Quarterly and all-time reports retain appropriate server-selected aggregation and the same readable hierarchy.

Charts must remain legible in light and dark themes and at narrow and desktop widths. Text results contain the key totals and findings even when the dashboard is rendered.

## Skills

All skill directories, YAML names, references, documentation, and routine prompts use these identifiers:

| Responsibility | Skill name |
|---|---|
| Setup and preference changes | `vblife-workout-setup` |
| Quick logging and corrections | `vblife-workout-log` |
| Saved daily planning | `vblife-workout-daily-plan` |
| Progressive overload | `vblife-workout-progressive-overload` |
| HIIT preferences and timer | `vblife-workout-hiit-session` |
| Period reports and dashboards | `vblife-workout-report` |
| Open-ended trend analysis | `vblife-workout-analysis` |

Every skill file is written entirely in English and contains no Chinese characters. Chinese exercise-name support remains in alias seed data, normalization behavior, and automated tests. The agent accepts user input in either language and may respond in the user's language.

## Default routines

The template provisions only these routines, enabled after setup establishes the user's timezone and schedule:

- Daily plan: 07:00 local time each day; remains quiet on a planned rest day except for the rest/recovery recommendation.
- Workout reminder: 17:00 local time on preferred training days; remains quiet when the day's planned or equivalent workout is already complete.
- Weekly report: Sunday at 20:00 local time.

Users may edit, pause, or delete each routine. Monthly and annual reports are available on demand and are not scheduled by default.

## Documentation

`AGENT.md` describes Workout Bot's role, tool-use rules, safety boundaries, plan persistence, numeric accuracy, language behavior, capability fallback, and the three routines.

`README.md` documents the product, exact installation and data paths, stdio command, environment overrides, directory layout, MCP Apps negotiation, test commands, routine defaults, and privacy boundary.

The old `workout-tracker` product name, Charles-specific wording, obsolete `/home/box/workout` paths, stale skill identifiers, and recommendations for extra default routines are removed from user-facing and agent-facing documentation.

## Error handling and safety

- Writes are transactional and validation errors do not leave partial records.
- Database migrations are ordered, versioned, transactional, and preserve existing repository test data.
- Unknown or ambiguous exercise names are never silently mapped.
- Destructive multi-record operations require confirmation; single-set undo remains direct.
- Plan linkage never guesses across ambiguous exercises or multiple active plans.
- Repeated HIIT completion callbacks are idempotent.
- All reported numbers come from server results.
- Pain or injury reports cause the agent to recommend stopping the movement and seeking qualified help rather than pushing through.
- Missing calorie inputs cause an explicitly rough estimate with recorded assumptions, not a fabricated high-confidence value.
- Unsupported MCP Apps hosts receive useful text and structured data without broken UI instructions.

## Verification

Automated verification covers:

- Schema migration from the current database version and clean database creation.
- Goal calculations across day, week, and month boundaries in the configured timezone.
- Goal edits, deleted workouts, and streak recalculation.
- Saved plan creation, valid status transitions, automatic unambiguous linkage, ambiguous linkage, and adherence.
- Body-weight history, normalized units, calorie input priority, fallback assumptions, confidence, and recalculation.
- Daily, weekly, monthly, quarterly, annual, and all-time reports.
- Weekly six-point, monthly six-point, and annual twelve-point series for training time, strength volume, and cardio duration.
- Capability negotiation with supporting and non-supporting clients.
- HIIT defaults, one-session overrides, saved-default replacement, idempotent completion, and arbitrary segment lists.
- Renamed skill discovery and cross-references.
- An automated scan proving every `skills/*/SKILL.md` contains no Chinese characters.
- Full unit and stdio end-to-end suites.
- Visual rendering of report, progress, and HIIT resources in light/dark themes at narrow and desktop widths.

## Acceptance criteria

- The product is named Workout Bot and the MCP server identifies as `workout-bot`.
- The installed code and durable database use the exact approved paths.
- A new user can finish repeatable setup, log work conversationally, receive and save a plan, and see plan adherence.
- Users can create daily, weekly, and monthly workout-derived goals and receive correct progress and streaks.
- Calories are estimated for eligible sessions even when inputs require documented fallbacks, with transparent confidence and assumptions.
- Weekly, monthly, and annual dashboards show comprehensive totals and all three requested trends.
- HIIT uses the approved defaults, supports arbitrary exercises and timings, and can be changed temporarily or permanently.
- MCP Apps are capability-gated, and non-supporting clients remain fully usable through text and structured data.
- Only the daily-plan, workout-reminder, and weekly-report routines are provisioned by default.
- Every renamed skill is English-only while Chinese aliases continue to resolve correctly.
- The full automated test suite passes and the three interfaces pass visual inspection.
