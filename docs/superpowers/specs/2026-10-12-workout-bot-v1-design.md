# Workout Bot V1 Design

## Purpose

Workout Bot is a private, local-first fitness assistant for Grok Bot. It replaces the first-release workflows that matter from Vibrant Fitness with a conversational experience: quick workout and nutrition logging, conversational workout planning, reusable workout routines, resumable active workouts, daily/weekly/monthly goals, proactive coaching, configurable HIIT, progress insights, and readable report dashboards.

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
- Quick conversational logging that automatically starts a workout on the first logged set or activity and completes it after 15 minutes of inactivity.
- One resumable active workout at a time, without requiring a live workout screen.
- Deterministic PR, estimated one-rep max, progressive-overload, recovery, and planning calculations.
- Ephemeral agent-generated workout plans; starting one persists only an active-workout snapshot, never a plan record.
- Reusable workout routines created from completed workouts on request.
- Starting, resuming, pivoting, completing, or abandoning an active workout from a saved routine.
- Daily, weekly, and monthly goals derived from workout records.
- Optional body-weight history.
- Calorie estimates with explicit confidence and assumptions.
- Image-first nutrition capture with confirmation before every save.
- Nutrient-only nutrition history in the same private `workout.db` database.
- Optional USDA FoodData Central lookups with a clearly disclosed model-only fallback.
- User-defined or bot-recommended daily calorie and nutrient goals.
- Daily, weekly, monthly, quarterly, annual, and all-time reports.
- Interactive report, progress, and HIIT interfaces when the client supports MCP Apps.
- Readable text and structured-data results when the client does not support MCP Apps.
- Three proactive Grok Bot routines: daily plan, workout reminder, and weekly report.

### Deferred

- Vibrant Fitness data migration or import.
- Authentication or multi-user records inside one database.
- A specialized live-workout screen, automatic set timers, and rest timers.
- Workout-routine folders and a screen-oriented routine library.
- Apple Health, steps, standing hours, or other health-platform integrations.
- Exercise favorites, personal notes, instructional media, and social features.
- Persistent meal photos, food names, portions, USDA queries, FDC identifiers, or a conventional item-level food diary.
- Barcode scanning, packaged-food label scanning, and restaurant-menu integrations.
- Pediatric, pregnancy, breastfeeding, eating-disorder, or condition-specific nutrition recommendations.
- Clinical micronutrient assessment or medical nutrition therapy.
- Public static-site hosting.
- In-widget HIIT configuration editing; first-release configuration is conversational.

## Architecture

The existing Python MCP server remains the system of record. The agent interprets natural language and meal images, gathers missing information, prepares ephemeral workout plans and nutrition drafts, and explains results. The server owns persistence, validation, normalization, aggregation, goal calculations, workout-routine snapshots, active-workout lifecycle, workout calorie estimates, optional USDA lookups, and all reported totals.

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
        |
        +-- USDA FoodData Central API (optional HTTPS;
            text food queries only, never meal images)
```

The UI HTML files are self-contained MCP resources with no required external network access. They receive tool inputs and `structuredContent` from a supporting host. They are enhancements over the same text and structured tool result, not a requirement for core behavior. USDA access is a server-side enhancement to nutrition estimation and is never required for logging.

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

All persistent nutrition state—including confirmed entries, nutrition goals, recommendation inputs, and the model-only preference—lives in tables within `/workspace/vblife/workout-data/workout.db`. No nutrition sidecar database or file is created. The USDA API key is the sole exception because it is a secret supplied through the environment rather than application data.

### Goals

Each goal stores:

- Domain: `workout` or `nutrition`.
- Period: `day`, `week`, or `month`; first-release nutrition goals use `day`.
- Metric: workout sessions, training days, workout minutes, cardio minutes, strength sets, strength volume, distance, workout calories burned, nutrition calories consumed, protein, carbohydrates, fat, fiber, saturated fat, sugar, or sodium.
- Target semantics: `minimum`, `maximum`, `exact`, or `range`, with the applicable target value or lower and upper bounds.
- Unit and display precision.
- Origin: `user` or `recommended`, plus a recommendation-version identifier and non-sensitive assumptions when applicable.
- Effective start date.
- Active state and timestamps.

Goal progress is derived from current workout and confirmed nutrition records in the user's configured timezone. Achievement history and streaks are recalculated from facts, so editing or deleting a workout or nutrition entry changes the result correctly. A direct user target always supersedes a bot recommendation for the same metric and effective period.

### Workout routines

`workout_routines` stores reusable templates with an identifier, user-visible name, optional description, estimated duration, active or archived state, optional source workout identifier, and timestamps. `workout_routine_items` stores each routine's ordered exercise or activity plus target sets, reps, weight, duration, distance, and notes.

A routine is created only when the user explicitly asks to save a completed workout as a routine or explicitly creates one through the routine flow. Creating from a completed workout copies normalized exercises and actual work into editable targets, presents the proposed routine for confirmation, and records the source workout after saving. The completed workout remains unchanged.

Routine edits affect future starts only. A referenced routine is archived instead of hard-deleted so historical workout links remain valid.

### Active workouts and routine snapshots

The existing `sessions` table gains `in_progress`, `completed`, and `abandoned` lifecycle states; nullable `source_routine_id`; a start mode of `implicit`, `routine`, or `conversational_plan`; `last_activity_at`; and consistent `started_at` and `ended_at` handling. Existing historical sessions migrate as `completed`.

Starting a saved routine transactionally creates an `in_progress` workout and copies its ordered items into `workout_session_items`. These session items are an independent snapshot of the routine targets, not a stored workout plan. Starting an accepted conversational plan uses the same session-item snapshot with no source routine. Merely generating or viewing a plan writes nothing.

Only one workout may be `in_progress`. Its session items support `pending`, `completed`, `skipped`, and `replaced` states. The user may add ad-hoc session items, replace or reorder exercises, or log different sets while training. These changes affect only the active workout; the source routine changes only after a separate explicit update request.

Actual sets and activities link to the active workout and, when applicable, its session item. Resuming returns the snapshot, completed work, and next unfinished item. Finishing records the actual result and calculates derived statistics. Abandoning preserves the session as `abandoned` until the user explicitly deletes it.

### Automatic workout lifecycle

When the first set or activity is logged and no workout is active, the server creates an `in_progress` session with start mode `implicit` and attaches the entry in the same transaction. Each subsequent logged set or activity updates `last_activity_at`. Entries at or within 15 minutes of the last activity remain in the same workout.

Before every MCP operation and when the server opens the database, the server reconciles the active session. If a session containing actual work has been inactive for more than 15 minutes, it becomes `completed`, remaining pending session items become `skipped`, and `ended_at` is set to the last actual activity time. If a new log triggered reconciliation, the new entry starts a new implicit workout after the prior workout is closed.

An explicitly started routine or conversational plan with no actual work becomes `abandoned` after 15 minutes, with the timeout boundary recorded as its close time. Abandoned sessions are excluded from workout totals. Explicit finish or abandon remains available and takes effect immediately.

The server does not depend on an in-process timer or scheduled worker. A stale row may physically remain `in_progress` while the stdio server is not running, but no client can observe it as active: every startup and operation reconciles it first. The completion time reflects the last activity rather than the later detection time. The inactivity threshold is a fixed V1 rule and uses UTC timestamps.

### Body weight

Optional dated weight entries retain the input value and unit and store a normalized kilogram value. The latest entry on or before a workout is available to calorie estimation. Users may add, correct, list, or delete entries.

### Calorie estimates

Every eligible session may store estimated calories, confidence, the weight and duration used, the selected intensity/MET value, the calculation version, and human-readable assumptions.

Input priority is:

1. Weight: value supplied for the session, then latest recorded body weight, then a documented 70 kg fallback.
2. Duration: explicit duration, completed HIIT elapsed time, active-workout timestamp span, source-routine estimated duration, then a conservative set-count heuristic.
3. Intensity: activity type and recorded RPE when available, then a conservative default for the session type.

The server uses a versioned MET-based formula. Confidence is `higher`, `moderate`, or `rough`. Reports show calorie totals alongside confidence coverage and never imply that a rough or partial estimate is measured data. Editing weight, duration, RPE, or workout contents recalculates the affected estimate.

### Nutrition entries

Confirmed nutrition is stored in a `nutrition_entries` table in `/workspace/vblife/workout-data/workout.db`; there is no separate nutrition database. Each entry stores:

- A generated identifier, consumption timestamp in UTC, the capture timezone, and derived local date.
- Estimated calories in kilocalories.
- Protein, carbohydrates, total fat, fiber, saturated fat, and total sugar in grams when credibly estimable.
- Sodium in milligrams when credibly estimable.
- Estimation method: `usda_hybrid` or `model_only`.
- Confidence: `higher`, `moderate`, or `rough`.
- Whether USDA data contributed, the estimator version, the number of source components, and generic uncertainty flags such as `portion_uncertain` or `preparation_unknown`.
- Confirmation, creation, and update timestamps.

At least one nutrient value is required. Missing nutrient values remain null rather than being converted to zero. Photos, food names, meal descriptions, portions, USDA search queries, USDA responses, FDC identifiers, and any metadata that reconstructs the meal are never written to SQLite, logs, analytics, or UI persistence. Draft analysis exists only for the current conversational confirmation flow and is discarded after confirmation, cancellation, or expiry.

### Nutrition settings and recommendation inputs

The user's USDA setup preference and optional nutrition-goal inputs are stored as settings in the same `workout.db`. Optional inputs are body weight, height, age, sex used for calculation, activity level, and objective (`maintenance`, `fat_loss`, or `muscle_gain`). The bot stores an input only after the user supplies or confirms it; omitted inputs are never inferred.

The USDA key itself is never stored in SQLite or committed to the project. It is supplied to the server through the `USDA_FDC_API_KEY` environment secret. A remembered `model_only_acknowledged` preference prevents repeated setup prompts after a user declines USDA configuration and can be reset through setup.

Nutrition recommendations are suggestions, not prerequisites for logging. Users may enter calorie or nutrient goals directly without supplying profile data. Recommended goals must show their inputs, assumptions, and basis and require confirmation before saving. When inputs are incomplete, the bot recommends only values supportable from the available data and explains what it could not calculate; unlike workout calorie burn, nutrition-goal calculations never substitute a 70 kg fallback or invent demographic values.

For adults with the required inputs, the recommendation engine uses a versioned energy-estimation rule set based on the Mifflin-St Jeor resting-energy equation, activity level, and conservative objective adjustments. Macronutrient and nutrient suggestions use the current USDA Dietary Reference Intake and Dietary Guidelines guardrails available when the rule-set version is released. The source edition and calculator version are included in recommendation metadata so future guidance can be introduced without silently changing historical goals.

## MCP tool surface

Existing logging, exercise resolution, statistics, progressive-overload, daily-plan context, reports, progress, and HIIT tools remain, with the product name and generic-user language updated.

New or extended responsibilities are grouped as follows:

### Setup and preferences

- Read current setup state, including units, timezone, goals, training preferences, reminder preferences, optional body weight, optional nutrition recommendation inputs, USDA-mode preference, and HIIT defaults.
- Update only explicitly supplied settings.
- Validate and persist HIIT defaults as structured settings.

### Goals

- Create or update a goal.
- List active or historical goals.
- Remove or deactivate a goal.
- Calculate progress, achievement, and streaks for a requested period and anchor date.

### Workout routines

- Create a routine directly or propose one from a completed workout.
- Retrieve and list active or archived routines.
- Edit routine metadata, ordering, and targets.
- Archive or restore a routine without breaking historical workout links.
- Start an active workout from a routine using a transactional snapshot.

### Active workouts

- Start an accepted conversational plan as an active workout without creating a plan record.
- Automatically create an implicit active workout when logging begins without one.
- Reconcile inactivity before every operation and at database startup.
- Retrieve and resume the one active workout.
- Add, replace, reorder, skip, or complete session items without modifying a source routine.
- Log, edit, or remove actual sets and activities against the active session.
- Finish or abandon the active workout through valid state transitions.
- Correct timeout grouping by moving an entry or merging adjacent completed workouts, then recalculate derived values.
- Return completed-workout provenance through nullable `source_routine_id`.

### Body weight and calories

- Record and retrieve optional body-weight entries.
- Extend logging inputs with optional workout duration and session weight.
- Return calorie estimate, confidence, and assumptions with logged sessions.

### Nutrition

- Report whether USDA hybrid estimation is available without exposing the API key.
- Search USDA FoodData Central with agent-produced text candidates when the key is configured; the server never accepts or transmits an image to USDA.
- Validate a proposed nutrient-only draft and return normalized values, confidence, and generic uncertainty flags without saving it.
- Save a validated draft only after the agent states that the user confirmed it.
- Retrieve daily nutrition totals and goal progress.
- Correct or delete a confirmed nutrition entry.
- Save user-entered or confirmed bot-recommended daily nutrition goals.

The server treats the USDA API as optional and failure-prone. Missing keys, timeouts, rate limits, malformed results, or no credible match produce an explicit model-only path rather than blocking the user. External request details and food-identifying response data are not persisted.

### Reports

- Add `day` to the existing report periods.
- Preserve `week`, `month`, `quarter`, `year`, and `all` periods.
- Return workout totals, nutrition totals, comparison values, goals, streaks, routine usage, PRs, workout mix, lift progress, calorie-confidence coverage, nutrition-estimate confidence, and chart-ready time series.

## Conversational flows

### Repeatable setup

Setup is not a one-time gate. On first use it collects only required missing values. At any later time, the user may review or change units, timezone, training days, workout or nutrition goals, preferred days, equipment, constraints, body weight, optional nutrition recommendation inputs, USDA mode, reminder settings, or HIIT defaults. Each confirmed change is saved immediately. Unspecified preferences are never reset.

### Quick logging

Users report work naturally. Before logging, the server reconciles any stale active workout. If a current workout remains active, the new set or activity joins it and refreshes `last_activity_at`; otherwise, the server automatically starts an implicit workout and attaches the entry. The agent does not need to ask the user to start first. Suggested work is never marked complete merely because it was proposed. Unknown exercises require confirmation and may then be saved as aliases or new exercises.

A later entry more than 15 minutes after the previous activity closes the prior workout before starting a new implicit workout. If the user says an entry was grouped incorrectly, correction tools may move it to the intended completed workout or merge adjacent workouts, with derived statistics recalculated transactionally.

### Conversational plans

The agent reads current settings, recent work, recovery, goals, available equipment, saved routines, and any active workout before planning. The proposed plan remains in conversation and is not written to SQLite. The user may follow it, change it, or ignore it.

If the user starts the proposed plan, the server creates an active workout and snapshots the accepted items into `workout_session_items`; it still does not create a plan record. Later pivots alter only the active workout. If the user never starts it, no plan data remains in the database.

### Saved routines and active workouts

When requested, the agent proposes a reusable routine from a completed workout, lets the user name or edit it, and saves it only after confirmation. The user can list, inspect, edit, archive, restore, or start a saved routine.

Starting a routine creates the active-workout snapshot. Starting another workout while one is active does not replace it silently; the agent asks the user to resume, finish, or abandon the existing workout first. Resuming shows completed work and the next pending item. The user can pivot conversationally, and the source routine remains unchanged unless the user explicitly asks to update it.

### Image-first nutrition logging

The user uploads a meal image. The agent analyzes the image in conversational context and prepares an ephemeral list of likely foods and portions. That list is used only to calculate and explain the draft; it is never sent to the persistence tool.

If `USDA_FDC_API_KEY` is configured, the agent sends text candidate queries through the server's FoodData Central lookup and combines credible matches with its visual portion estimates. The meal image never leaves Grok Bot through the USDA integration. If no key is configured, the user has declined setup, the API fails, or no credible match is available, the agent uses model-based estimation and explicitly labels the draft as less grounded.

Before saving, the agent shows the inferred foods and portions, nutrient totals, estimation method, confidence, and important uncertainties. The user may confirm, correct the draft, or cancel. Corrections trigger a fresh calculation and another confirmation. Only a final confirmed nutrient-only payload is written to `nutrition_entries`; cancellation and abandoned drafts write nothing.

On the first nutrition request without a USDA key or remembered decision, `vblife-workout-nutrition` explains the hybrid benefit and guides the user to the free [FoodData Central API key signup](https://fdc.nal.usda.gov/api-key-signup/) page. If the user declines, the skill records the non-secret model-only preference, states that future values are model estimates, and continues. The user may enable or disable USDA mode later through either setup or the nutrition flow.

### Nutrition goals

Users may state daily targets directly, and those values are saved after confirmation without requiring demographic or body-profile data. When asked to recommend goals, the bot uses only optional inputs the user has supplied, shows the calculation assumptions and ranges, and asks for confirmation before saving. A user can accept all suggestions, change individual values, or keep only selected goals.

The daily summary keeps energy consumed and estimated workout energy burned as separate values; it never presents their difference as a precise net-calorie measurement. It shows progress against calorie, protein, carbohydrate, fat, fiber, saturated-fat, sugar, and sodium goals only when the corresponding goal and nutrient data exist.

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
2. Primary workout and nutrition goal progress.
3. Short coach summary.
4. Workout totals for sessions, training time, strength volume, cardio duration, distance, and estimated calories burned when relevant.
5. Nutrition totals or daily averages for calories consumed, protein, carbohydrates, fat, fiber, saturated fat, sugar, and sodium when available.
6. Estimate confidence and coverage, kept separate for workout calories and nutrition entries.
7. Switchable workout trends.
8. Workout mix.
9. PRs, streaks, routine usage, nutrition goal adherence, and actionable next steps.

Trend windows are:

- Daily report: recent four-week rhythm.
- Weekly report: six weekly points for training time, strength volume, and cardio duration.
- Monthly report: six monthly points for the same three metrics.
- Annual report: twelve monthly points for the same three metrics.
- Quarterly and all-time reports retain appropriate server-selected aggregation and the same readable hierarchy.

Daily reports show that day's confirmed nutrient totals and each configured daily nutrition goal. Longer reports show per-day nutrition averages, the number of days with confirmed entries, goal-hit rates, and confidence coverage; they do not treat days without nutrition entries as zero intake. Nutrition does not add a default scheduled routine.

Workout totals include completed sessions only. A currently active session may be shown separately as in progress, while abandoned empty sessions are omitted. Report generation always runs stale-session reconciliation before aggregation.

Charts must remain legible in light and dark themes and at narrow and desktop widths. Text results contain the key totals and findings even when the dashboard is rendered.

## Skills

All skill directories, YAML names, references, documentation, and routine prompts use these identifiers:

| Responsibility | Skill name |
|---|---|
| Setup and preference changes | `vblife-workout-setup` |
| Quick logging, active-workout lifecycle, and corrections | `vblife-workout-log` |
| Ephemeral daily planning and starting an accepted plan | `vblife-workout-daily-plan` |
| Progressive overload | `vblife-workout-progressive-overload` |
| HIIT preferences and timer | `vblife-workout-hiit-session` |
| Saved workout routines and starting from a routine | `vblife-workout-routine` |
| Meal-photo estimation, confirmation, and nutrition goals | `vblife-workout-nutrition` |
| Period reports and dashboards | `vblife-workout-report` |
| Open-ended trend analysis | `vblife-workout-analysis` |

Every skill file is written entirely in English and contains no Chinese characters. Chinese exercise-name support remains in alias seed data, normalization behavior, and automated tests. The agent accepts user input in either language and may respond in the user's language.

## Default Grok Bot routines

These are scheduled Grok Bot automations, distinct from user-created workout routines. The template provisions only the following automations after setup establishes the user's timezone and schedule:

- Daily plan: 07:00 local time each day; remains quiet on a planned rest day except for the rest/recovery recommendation.
- Workout reminder: 17:00 local time on preferred training days; remains quiet when a workout is active or the day's workout is already complete.
- Weekly report: Sunday at 20:00 local time.

Users may edit, pause, or delete each scheduled automation. Monthly and annual reports are available on demand and are not scheduled by default.

## Documentation

`AGENT.md` describes Workout Bot's role, tool-use rules, safety boundaries, ephemeral planning, workout-routine persistence, active-workout lifecycle, nutrition confirmation and privacy rules, numeric accuracy, language behavior, capability fallback, and the three scheduled Grok Bot routines.

`README.md` documents the product, exact installation and data paths, stdio command, environment overrides, `USDA_FDC_API_KEY` setup with the official free-key link, model-only fallback, directory layout, MCP Apps negotiation, test commands, workout-routine and active-workout commands, scheduled-automation defaults, and privacy boundary.

The old `workout-tracker` product name, Charles-specific wording, obsolete `/home/box/workout` paths, stale skill identifiers, and recommendations for extra default routines are removed from user-facing and agent-facing documentation.

## External reference basis

- [USDA FoodData Central API Guide](https://fdc.nal.usda.gov/api-guide/) for API access, key handling, endpoints, rate limits, and CC0 food-composition data.
- [USDA DRI Calculator](https://www.nal.usda.gov/human-nutrition-and-food-safety/dri-calculator) and the current [Dietary Guidelines for Americans](https://www.dietaryguidelines.gov/) for general nutrient guardrails.
- [Mifflin-St Jeor original equation](https://pubmed.ncbi.nlm.nih.gov/2305711/) for adult resting-energy estimation.
- [NIDDK Body Weight Planner limitations](https://www.niddk.nih.gov/health-information/weight-management/body-weight-planner) for the adult-only, non-pregnancy, non-breastfeeding safety boundary and medical-advice disclaimer.

## Error handling and safety

- Writes are transactional and validation errors do not leave partial records.
- Database migrations are ordered, versioned, transactional, and preserve existing repository test data.
- Unknown or ambiguous exercise names are never silently mapped.
- Destructive multi-record operations require confirmation; single-set undo remains direct.
- Starting a routine or accepted conversational plan is transactional and idempotent, so retries cannot create duplicate active workouts.
- The database enforces at most one `in_progress` workout. A second start never silently replaces the active workout.
- Logging the first set or activity without an active workout creates the implicit workout and the entry atomically.
- Every database startup and MCP operation reconciles sessions inactive for more than 15 minutes before reading or writing observable state.
- Stale sessions with work complete at their last activity time; stale empty explicit sessions become abandoned. Neither rule depends on a background process.
- Timeout-boundary behavior is deterministic: exactly 15 minutes remains the same workout, while more than 15 minutes closes it.
- Routine snapshots are isolated from later routine edits, and active-workout pivots never mutate the source routine without a separate explicit request.
- Referenced routines are archived rather than hard-deleted, preserving historical `source_routine_id` links.
- Repeated HIIT completion callbacks are idempotent.
- All reported numbers come from server results.
- Pain or injury reports cause the agent to recommend stopping the movement and seeking qualified help rather than pushing through.
- Missing calorie inputs cause an explicitly rough estimate with recorded assumptions, not a fabricated high-confidence value.
- A nutrition entry cannot be persisted unless the final nutrient-only draft was explicitly confirmed in the current conversation.
- USDA receives only text search queries. Meal images are never transmitted to USDA, and the API key is never returned by a tool, logged, written to SQLite, or included in a report.
- Model-only estimates and USDA-assisted estimates are visibly distinguished. Missing nutrients remain unknown rather than zero.
- Nutrition recommendations are limited to generally healthy adults. The bot does not generate personalized targets for minors, pregnancy, breastfeeding, known eating-disorder concerns, or medical conditions that materially affect nutrition; it recommends qualified clinical guidance instead.
- Directly entered goals remain allowed, but the bot flags extreme or internally inconsistent values and asks the user to verify them before saving.
- Nutrition estimates and goals are educational estimates, not diagnoses or medical advice.
- Unsupported MCP Apps hosts receive useful text and structured data without broken UI instructions.

## Verification

Automated verification covers:

- Schema migration from the current database version and clean database creation.
- Goal calculations across day, week, and month boundaries in the configured timezone.
- Goal edits, deleted workouts, and streak recalculation.
- Workout-routine creation from a completed workout, direct creation, editing, ordering, archiving, restoring, and historical source links.
- Active-workout creation from a routine snapshot and from an accepted conversational plan without any persisted plan record.
- Implicit active-workout creation on the first logged set or activity, including atomic rollback on validation failure.
- Logs at, below, and above the 15-minute boundary; `last_activity_at` refresh; stale completion at the last activity time; and automatic creation of a new workout for the next log.
- Reconciliation on database startup and before every read or write, proving the observed result does not require a resident timer or scheduled worker.
- Auto-abandonment of empty explicit starts, automatic skipping of pending snapshot items, and exclusion of abandoned sessions from totals.
- Correction of timeout grouping by moving entries or merging adjacent completed workouts with full derived-value recalculation.
- Single-active-workout enforcement, idempotent start, resume, valid state transitions, incremental set logging, pivots, explicit finish, explicit abandon, and explicit deletion.
- Snapshot isolation when a routine changes, plus the rule that active-workout changes do not update a routine without an explicit request.
- Migration of historical workouts to `completed` and schema checks for `workout_routines`, `workout_routine_items`, and `workout_session_items`.
- Body-weight history, normalized units, calorie input priority, fallback assumptions, confidence, and recalculation.
- Clean creation and migration of `nutrition_entries` and nutrition-goal fields inside `/workspace/vblife/workout-data/workout.db`, with no separate database.
- Nutrition-draft validation, explicit-confirmation enforcement, correction, cancellation, update, deletion, aggregation, and timezone boundaries.
- Hybrid USDA lookup, text-only outbound payloads, missing-key behavior, decline persistence, timeouts, rate limits, invalid responses, no-match fallback, and model-only disclosure.
- Persistence tests proving photos, food names, meal descriptions, portions, search queries, USDA responses, and FDC identifiers never enter SQLite or application logs.
- Nullable nutrient handling, confidence coverage, and the rule that missing days are excluded rather than counted as zero intake.
- User-entered nutrition goals, partially supportable recommendations, recommendation confirmation, source-version metadata, and replacement of recommended goals by direct user goals.
- Daily, weekly, monthly, quarterly, annual, and all-time reports.
- Weekly six-point, monthly six-point, and annual twelve-point series for training time, strength volume, and cardio duration.
- Capability negotiation with supporting and non-supporting clients.
- HIIT defaults, one-session overrides, saved-default replacement, idempotent completion, and arbitrary segment lists.
- Renamed skill discovery and cross-references, including `vblife-workout-routine` and `vblife-workout-nutrition`.
- An automated scan proving every `skills/*/SKILL.md` contains no Chinese characters.
- Full unit and stdio end-to-end suites.
- Visual rendering of report, progress, and HIIT resources in light/dark themes at narrow and desktop widths.

## Acceptance criteria

- The product is named Workout Bot and the MCP server identifies as `workout-bot`.
- The installed code and durable database use the exact approved paths.
- A new user can finish repeatable setup, receive an ephemeral plan, start it as a resumable workout, pivot, and finish or abandon it without creating a plan record.
- A user can create a reusable routine from a completed workout on request and start, resume, or pivot from a saved routine.
- Logging a first set or activity automatically starts a workout when none is active, without requiring a separate start command.
- A workout with logged activity automatically completes after more than 15 minutes of inactivity, using the last activity as its effective end time; the next log begins a new workout.
- The 15-minute lifecycle remains correct across stdio server restarts without a background timer.
- Users can create daily, weekly, and monthly workout-derived goals and receive correct progress and streaks.
- Calories are estimated for eligible sessions even when inputs require documented fallbacks, with transparent confidence and assumptions.
- A user can upload a meal image, review and correct the estimate, and save it only after explicit confirmation.
- Confirmed nutrition nutrient totals, timestamps, and non-identifying estimation metadata are stored in `/workspace/vblife/workout-data/workout.db`; no meal image, food name, portion, USDA query, response, or identifier is persisted.
- USDA hybrid estimation uses a user-provided environment secret and degrades clearly to model-only estimation when unavailable or declined.
- A user can set daily nutrition goals directly or confirm optional bot recommendations; missing profile values are never fabricated.
- Daily and longer reports show nutrition goal progress and estimation coverage without conflating calories consumed with estimated workout calories burned.
- Weekly, monthly, and annual dashboards show comprehensive totals and all three requested trends.
- HIIT uses the approved defaults, supports arbitrary exercises and timings, and can be changed temporarily or permanently.
- MCP Apps are capability-gated, and non-supporting clients remain fully usable through text and structured data.
- Only the daily-plan, workout-reminder, and weekly-report Grok Bot automations are provisioned by default.
- Every renamed skill is English-only while Chinese aliases continue to resolve correctly.
- The full automated test suite passes and the three interfaces pass visual inspection.
