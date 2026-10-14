# Workout Bot

Workout Bot is a private, local-first Grok Bot fitness assistant for conversational workout and nutrition logging, flexible plans, reusable routines, goals, HIIT, coaching, and progress reports. Each installed bot instance uses its own SQLite database and starts fresh; no Vibrant Fitness migration is required.

## Installation and command

Install the project at `/home/vblife/workout`. Durable user data belongs in `/workspace/vblife/workout-data`; the default database is `/workspace/vblife/workout-data/workout.db`.

Configure Grok Bot to launch the local stdio Command server:

```text
command: /usr/bin/python3
args: [/home/vblife/workout/server/server.py, --stdio]
```

`WORKOUT_DB` overrides the database path. `USDA_FDC_API_KEY` optionally enables USDA FoodData Central lookups. Obtain a free key at https://fdc.nal.usda.gov/api-key-signup/. The key stays in the environment and is never returned or stored in SQLite. Without it—or when USDA is unavailable—Workout Bot continues with a clearly labelled model-only estimate.

## Product behavior

- Quick logs automatically start a workout; inactivity longer than 15 minutes completes it.
- Conversational plans are ephemeral. Starting an accepted plan stores only its active-workout snapshot.
- Completed workouts can become reusable routines on request. Start, resume, add, replace, reorder, skip, finish, or abandon items in a routine snapshot without changing its source routine. Misgrouped entries can be moved or adjacent completed workouts merged with confirmation.
- Body weight is optional. Workout calories always remain estimates with confidence and assumptions.
- Meal photos are analyzed in conversation. Saving requires confirmation and writes only nutrients, timestamp, and estimation metadata to `workout.db`.
- Reports support day, week, month, quarter, year, and all time, including six-week, six-month, and twelve-month trends as applicable.

## MCP Apps

Self-contained resources live in `server/ui/`: `report.html`, `progress.html`, and `hiit.html`. The server inspects client capabilities during MCP initialization. Compatible MCP Apps clients receive resource metadata; other clients receive complete text and structured data with no broken interface or public-page fallback.

## Layout

```text
server/             MCP protocol, SQLite migrations, domain logic, calculations
server/ui/          negotiated report, progress, and HIIT resources
skills/             nine vblife-workout-* agent skills
evals/              the single workout-evals Promptfoo suite
docs/superpowers/   approved design and implementation plans
```

## Scheduled routines

The template provisions only a daily plan at 07:00 local, a workout reminder at 17:00 on preferred training days, and a weekly report Sunday at 20:00. They are proactive by default after schedule setup, and users may disable or edit them. Monthly and annual reports remain available on demand.

## Privacy boundary

The database is private to this bot instance on the user's Grok Bot computer. It is not a security boundary from other bots owned by the same user on that computer. Meal images, food names, portions, USDA queries/results, and FDC identifiers are not persisted. There is no nutrition sidecar database.

## Verification

```text
python3 -m unittest discover -s server/tests -p 'test_*.py'
npm run test:evals
npm run eval:validate
npm run eval:workout
```

The live eval uses GPT-6 Luna with max reasoning through the ChatGPT subscription login and rejects `OPENAI_API_KEY` or `CODEX_API_KEY` to prevent accidental API billing.
