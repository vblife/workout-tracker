# Workout Bot V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the local-first Workout Bot V1 described by the approved design and make every `workout-evals` case pass.

**Architecture:** Keep the Python stdlib MCP server and split new persistence behavior into focused domain modules over the one SQLite connection. The server remains authoritative for validation, lifecycle, calculations, and reports; the agent skills own conversation flow and use server tools for state. MCP Apps resources are capability-negotiated enhancements over complete text and structured results.

**Tech Stack:** Python 3 stdlib, SQLite, JSON-RPC MCP over stdio, self-contained HTML/CSS/JavaScript MCP Apps resources, Node.js test runner, Promptfoo with Codex SDK.

**Spec:** `docs/superpowers/specs/2026-10-12-workout-bot-v1-design.md`

## Global Constraints

- Product identity is `Workout Bot`; stable slug and MCP name are `workout-bot`.
- Install root is `/home/vblife/workout`; durable data is `/workspace/vblife/workout-data/workout.db`.
- Planning is ephemeral; only accepted active-workout snapshots and explicitly saved routines persist.
- Exactly one workout may be `in_progress`; exactly 15 minutes stays active and more than 15 minutes closes it.
- Confirmed nutrition stores only nutrients, timestamps, and estimation metadata in `workout.db`.
- UI resources live only in `server/ui`; unsupported clients receive complete text and structured data.
- The nine skill identifiers are the exact `vblife-workout-*` names in the spec and all skill prose is English-only.
- The template provisions only daily plan, workout reminder, and weekly report routines.
- Python runtime behavior stays stdlib-only; USDA access is optional and failure-safe.
- All writes and migrations are transactional; destructive multi-record actions require confirmation.

## Review Focus

- A write that fails halfway must roll back the workout/routine/nutrition record and all children; Tasks 1, 3, and 4 test injected invalid children.
- At the inactivity boundary, `15:00` stays in one workout and `15:00.001` closes it; Task 2 pins both cases.
- Missing calorie inputs must produce a labelled rough estimate using documented fallbacks; Task 2 tests empty weight and duration inputs.
- Meal-identifying fields, images, portions, and USDA identifiers must be rejected before persistence; Task 4 scans schema and database bytes.
- A client without MCP Apps capability must never receive resource declarations, UI metadata, or fallback links; Task 6 tests initialization and tool calls.

---

### Task 1: Transactional V1 schema and migration

**Files:**
- Modify: `server/db.py`
- Create: `server/tests/test_v1_schema.py`

**Interfaces:**
- Consumes: `db.connect(path: str | None) -> sqlite3.Connection`.
- Produces: schema version 2 tables/columns, partial unique active-session index, and `db.transaction(connection)`.

- [x] **Step 1: Write failing migration tests** covering clean creation, V1 upgrade, historical sessions becoming completed, all new tables, foreign keys, one-active-session enforcement, and rollback on migration error.
- [x] **Step 2: Run** `python -m unittest server.tests.test_v1_schema -v`; expect missing V1 columns/tables.
- [x] **Step 3: Implement** ordered transactional migrations for sessions, routine/session items, goals, body weight, and nutrient-only nutrition entries; change the default database path.
- [x] **Step 4: Re-run** the schema test and `python -m unittest discover -s server/tests -p 'test_*.py'`; expect PASS.
- [x] **Step 5: Commit** `feat: add Workout Bot V1 schema`.

### Task 2: Active-workout lifecycle and calorie estimates

**Files:**
- Create: `server/workouts.py`
- Create: `server/calories.py`
- Modify: `server/core.py`
- Create: `server/tests/test_v1_workouts.py`

**Interfaces:**
- Produces: `reconcile_active(c, at=None)`, `get_active(c, at=None)`, `start_workout(c, *, mode, title=None, items=None, source_routine_id=None, idempotency_key=None)`, `finish_workout(c, at=None)`, `abandon_workout(c, at=None)`, and `estimate_session(c, session_id)`.

- [x] **Step 1: Write failing tests** for implicit first log, same-session boundary, stale completion/empty abandonment, explicit transitions, idempotent starts, activity logging, and rough calorie fallback.
- [x] **Step 2: Run** `python -m unittest server.tests.test_v1_workouts -v`; expect missing module/functions.
- [x] **Step 3: Implement** lifecycle reconciliation and integrate it atomically into set, activity, and HIIT logging; use a versioned MET formula with 70 kg and conservative-duration fallbacks.
- [x] **Step 4: Run focused and full Python tests**; expect PASS with legacy expectations updated only where the approved contract changed.
- [x] **Step 5: Commit** `feat: add automatic workout lifecycle`.

### Task 3: Routines, snapshots, goals, and body weight

**Files:**
- Create: `server/routines.py`
- Create: `server/goals.py`
- Create: `server/profile.py`
- Create: `server/tests/test_v1_routines_goals.py`

**Interfaces:**
- Produces routine CRUD/archive/start functions, immutable session snapshots, goal CRUD/progress functions, and body-weight CRUD/list functions returning JSON-safe dictionaries.

- [x] **Step 1: Write failing tests** for direct and completed-workout routine creation, confirmation, ordering, edits isolated from snapshots, archive/restore, active-workout conflict, day/week/month goal progress, timezone boundaries, recalculation after deletion, and weight normalization.
- [x] **Step 2: Run** the focused test; expect import failures.
- [x] **Step 3: Implement** validation and transactional domain functions, including source-workout provenance and derived goal progress.
- [x] **Step 4: Run focused and full Python tests**; expect PASS.
- [x] **Step 5: Commit** `feat: add routines goals and body weight`.

### Task 4: Confirmed nutrient-only nutrition

**Files:**
- Create: `server/nutrition.py`
- Create: `server/tests/test_v1_nutrition.py`

**Interfaces:**
- Produces: `nutrition_status(c)`, `validate_draft(payload)`, `save_entry(c, payload, confirmed)`, `list_entries(c, ...)`, `delete_entry(c, entry_id)`, `daily_totals(c, ...)`, `recommend_goals(inputs)`, and `search_usda(query, api_key=None)`.

- [x] **Step 1: Write failing tests** for confirmation enforcement, nullable nutrients, nutrient ranges, privacy-field rejection, method/confidence metadata, totals, corrections, model-only preference, safe USDA failure, and adult-only recommendation guards.
- [x] **Step 2: Run** the focused test; expect missing module/functions.
- [x] **Step 3: Implement** nutrient-only persistence and optional HTTPS USDA lookup without logging the key, query, response, image, foods, or portions.
- [x] **Step 4: Run focused/full tests and scan a fixture DB** for forbidden meal-identifying strings; expect PASS/no matches.
- [x] **Step 5: Commit** `feat: add private nutrition tracking`.

### Task 5: MCP tool surface and repeatable setup

**Files:**
- Modify: `server/server.py`
- Modify: `server/core.py`
- Create: `server/tests/test_v1_mcp_tools.py`

**Interfaces:**
- Consumes all Task 2–4 domain functions.
- Produces MCP schemas/descriptions/call routing for setup, goals, weight, routines, active workouts, nutrition, and existing workout tools.

- [x] **Step 1: Write failing protocol tests** asserting `workout-bot` identity, every tool name/schema, reconciliation before operations, partial settings updates, generic copy, safe error rollback, and structured results.
- [x] **Step 2: Run** the focused test; expect missing tools and old identity.
- [x] **Step 3: Implement** routing and descriptions that explicitly tell the agent when to call tools, never gate normal behavior on onboarding, and never expose secrets.
- [x] **Step 4: Run focused/full tests**; expect PASS.
- [x] **Step 5: Commit** `feat: expose Workout Bot V1 tools`.

### Task 6: Reports, trends, and capability-negotiated apps

**Files:**
- Modify: `server/reports.py`
- Modify: `server/server.py`
- Create: `server/ui/report.html`
- Create: `server/ui/progress.html`
- Modify: `server/ui/hiit.html`
- Delete: `server/ui/charts.html`
- Delete: `server/fallback.py`
- Delete: `server/build_site.sh`
- Delete: `site/`
- Create: `server/tests/test_v1_reports_ui.py`

**Interfaces:**
- Produces day/week/month/quarter/year/all report payloads with workout/nutrition/goals/confidence and chart-ready series; negotiated resource advertisement.

- [x] **Step 1: Write failing tests** for six weekly/monthly points and twelve annual points across training time, strength volume, and cardio duration; daily nutrition; completed-only totals; dark/narrow HTML; UI-capable and UI-incapable clients.
- [x] **Step 2: Run** the focused test; expect missing fields/resources and unconditional UI metadata.
- [x] **Step 3: Implement** report aggregation and self-contained resources; attach UI only after compatible initialization and remove all site/fallback behavior.
- [x] **Step 4: Run focused/full tests and open each HTML resource through `resources/read`**; expect PASS and valid content.
- [x] **Step 5: Commit** `feat: add comprehensive negotiated dashboards`.

### Task 7: Agent skills and documentation

**Files:**
- Replace: `skills/*/SKILL.md` with the nine approved `skills/vblife-workout-*/SKILL.md` files
- Modify: `skills/README.md`
- Modify: `AGENT.md`
- Modify: `README.md`
- Create: `server/tests/test_v1_product_contract.py`

**Interfaces:**
- Produces agent behavior instructions matching tool contracts and documented install/runtime/privacy behavior.

- [x] **Step 1: Read** `superpowers:writing-skills` and `skill-creator`, then write failing contract tests for exact skill names, English-only skill text, HIIT defaults, nutrition confirmation/privacy, proactive-but-disableable behavior, three routine prompts, paths, and removal of stale branding/copy.
- [x] **Step 2: Run** the focused contract test; expect stale names and documentation failures.
- [x] **Step 3: Write** concise skill workflows with trigger boundaries and complete `AGENT.md`/`README.md`; preserve Chinese aliases only in seed data and tests.
- [x] **Step 4: Run focused/full tests and search user-facing files** for stale paths, names, and Chinese characters in skills; expect PASS/no matches.
- [x] **Step 5: Commit** `docs: define Workout Bot agent behavior`.

### Task 8: Contract eval convergence and release verification

**Files:**
- Modify only implementation, skills, tests, or eval assertions when evidence proves a contract mismatch.
- Update: `.superpowers/sdd/2026-10-12-workout-bot-v1/progress.md`

**Interfaces:**
- Consumes the single `evals/workout-evals.yaml` suite.
- Produces a verified V1 with all deterministic, rubric, harness, and legacy tests passing.

- [x] **Step 1: Run targeted Promptfoo cases** grouped by primary skill; record every failing assertion and inspect tool traces/SQLite evidence.
- [x] **Step 2: For each genuine failure, add or tighten a deterministic unit/contract test first**, reproduce red locally, implement the smallest general fix, and rerun the targeted case.
- [x] **Step 3: Run** `npm test`, `npm run eval:validate`, all Python tests, and then `npm run eval`; require zero failures and zero errors.
- [x] **Step 4: Run verification searches** for forbidden persisted nutrition fields, stale branding/paths, unapproved skill names, extra Promptfoo configs, and extra default routines.
- [x] **Step 5: Self-review the full branch against every design-spec section**, record deferred non-blocking dependency advisories, and commit `test: verify Workout Bot V1 contract`.

## Self-Review

- Spec coverage: each design section maps to Tasks 1–8; migration, lifecycle, corrections, routines, goals, body weight/calories, nutrition/USDA, reports/UI, skills/routines/docs, and eval verification are represented.
- Placeholder scan: no `TBD`, `TODO`, or implementation placeholder remains in this plan.
- Type consistency: the MCP task consumes the exact domain entry points defined in Tasks 2–4; reports and evals consume their persisted contracts.
- Review focus: all five high-risk conditions have explicit owning-task tests.
