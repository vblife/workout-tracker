# Workout Agent Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one contract-first Promptfoo suite named `workout-evals` that evaluates every approved `vblife-workout-*` skill through the local stdio MCP server using GPT-6 Luna at max effort and the user's ChatGPT subscription.

**Architecture:** `evals/workout-evals.yaml` is the only Promptfoo configuration. A Codex SDK provider runs the bot prompt against a per-case temporary SQLite database exposed through the local `workout-bot` stdio server; extension hooks create and remove those databases, deterministic JavaScript assertions inspect tool traces and SQLite state, and explicitly pinned Codex SDK graders assess only conversational qualities. A small wrapper refuses API-key environments before invoking Promptfoo so evaluation always uses the existing ChatGPT login.

**Tech Stack:** Node.js 22.22+, Promptfoo 0.124.1, `@openai/codex-sdk` 0.162.1, `better-sqlite3`, YAML, Node's built-in test runner, Python 3 stdlib MCP server and SQLite.

**Spec:** `docs/superpowers/specs/2026-10-12-workout-bot-v1-design.md`

## Global Constraints

- There is exactly one Promptfoo configuration: `evals/workout-evals.yaml`, described as `workout-evals`; do not create `current-smoke` or `v1-contract` configurations.
- Target and model-graded providers are `openai:codex-sdk` with `model: gpt-6-luna` and `model_reasoning_effort: max`.
- Live runs reuse the local Codex ChatGPT-subscription login and remove both `OPENAI_API_KEY` and `CODEX_API_KEY` from the Promptfoo child process.
- Every case uses a unique temporary database; never use repository `workout.db` or `/workspace/vblife/workout-data/workout.db` in evals.
- The Codex target is read-only, uses `approval_policy: never`, and has network and web search disabled.
- The MCP server is local stdio and receives the case database through `WORKOUT_DB`.
- Contract cases describe approved V1 behavior even when the current server fails them; harness unit tests and config validation must remain green.
- Case metadata must cover the exact nine-skill set from the design spec; each case declares one primary `vblife-workout-*` skill.
- No captured meal image, food identity, portion, USDA query, USDA response, or API credential may be persisted in fixtures, results, or baselines.
- Node and npm dependency versions are committed through `package-lock.json`.

## Review Focus

- A developer with `OPENAI_API_KEY` already exported must receive a hard failure before Promptfoo starts, not an API-billed run; Task 4 tests both key names.
- Parallel cases must never share or clean up one another's SQLite database; Task 2 creates two fixtures and checks unique paths and independent cleanup.
- A missing V1 table or tool must be reported as a contract assertion failure rather than crashing the evaluator; Task 3 tests the database assertion's failure result.
- Provider metadata may expose tool calls as either `toolCalls` or traced MCP spans; Task 3 tests both evidence shapes.
- A live contract failure must not make harness verification appear broken; Task 5 separates unit/validation status from the recorded live baseline.

---

### Task 1: Pin the eval toolchain and single configuration

**Files:**
- Create: `package.json`
- Create: `package-lock.json`
- Create: `evals/workout-evals.yaml`
- Create: `evals/prompts/workout-agent.txt`
- Create: `evals/tests/config.test.mjs`

**Interfaces:**
- Consumes: the repository root, `AGENT.md`, `skills/`, and `server/server.py --stdio --db <path>`.
- Produces: npm scripts `test:evals`, `eval:validate`, and `eval:workout`; Promptfoo provider variables `workoutDbPath` and `repoRoot` for Task 2; one target provider definition reused verbatim by Task 3 graders.

- [ ] **Step 1: Add the Node manifest needed to execute a failing config test**

Create `package.json` with this exact public interface, then run `npm install --package-lock-only`:

```json
{
  "name": "workout-bot",
  "private": true,
  "engines": { "node": ">=22.22.0" },
  "scripts": {
    "test:evals": "node --test evals/tests/*.test.mjs",
    "eval:validate": "promptfoo validate -c evals/workout-evals.yaml",
    "eval:workout": "node evals/run-workout-evals.mjs"
  },
  "devDependencies": {
    "@openai/codex-sdk": "0.162.1",
    "better-sqlite3": "12.4.1",
    "promptfoo": "0.124.1",
    "yaml": "2.8.1"
  }
}
```

Run: `npm install --package-lock-only`

Expected: exit 0 and a lockfile with lockfile version 3.

- [ ] **Step 2: Write the failing single-config invariant test**

Create `evals/tests/config.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import YAML from 'yaml';

const configPath = new URL('../workout-evals.yaml', import.meta.url);

test('workout-evals is the only Promptfoo config and pins Luna max', () => {
  const evalDir = new URL('..', import.meta.url);
  const configs = readdirSync(evalDir).filter((name) => /(?:promptfoo|evals?).*\.ya?ml$/i.test(name));
  assert.deepEqual(configs, ['workout-evals.yaml']);
  const config = YAML.parse(readFileSync(configPath, 'utf8'));
  assert.equal(config.description, 'workout-evals');
  assert.equal(config.providers.length, 1);
  assert.equal(config.providers[0].id, 'openai:codex-sdk');
  assert.equal(config.providers[0].config.model, 'gpt-6-luna');
  assert.equal(config.providers[0].config.model_reasoning_effort, 'max');
  assert.equal(config.providers[0].config.sandbox_mode, 'read-only');
  assert.equal(config.providers[0].config.approval_policy, 'never');
  assert.equal(config.providers[0].config.network_access_enabled, false);
  assert.equal(config.providers[0].config.web_search_mode, 'disabled');
  assert.equal(config.providers[0].config.working_dir, '{{repoRoot}}');
  const mcp = config.providers[0].config.cli_config.mcp_servers['workout-bot'];
  assert.equal(mcp.command, 'python3');
  assert.deepEqual(mcp.args, ['server/server.py', '--stdio', '--db', '{{workoutDbPath}}']);
  assert.equal(mcp.cwd, '{{repoRoot}}');
  assert.equal(mcp.env.WORKOUT_DB, '{{workoutDbPath}}');
  assert.ok(!JSON.stringify(config).includes('current-smoke'));
  assert.ok(!JSON.stringify(config).includes('v1-contract'));
});
```

- [ ] **Step 3: Run the test and verify the missing configuration fails**

Run: `npm install && npm run test:evals`

Expected: FAIL with `ENOENT` for `evals/workout-evals.yaml`.

- [ ] **Step 4: Add the minimal Promptfoo configuration and bot prompt**

Create `evals/workout-evals.yaml` with one provider, one prompt, the Task 2 extension reference, `tests: file://cases.yaml`, tracing enabled, no sharing, and `maxConcurrency: 1`. Configure `working_dir: '{{repoRoot}}'` and `cli_config.mcp_servers.workout-bot` exactly as asserted above, including `cwd: '{{repoRoot}}'`, and set `enable_streaming: true` so MCP trajectories are captured. Add an explicit `agent-rubric` provider block to the reusable YAML anchor `&luna_max` so every model grader is also Luna/max.

Create `evals/prompts/workout-agent.txt` with this complete behavioral envelope around `{{userMessage}}`:

```text
Act as Workout Bot for this single user request. Follow the repository AGENT.md and the relevant skills as the product contract. Use the workout-bot MCP tools for all workout or nutrition facts and writes. Never edit repository files, inspect SQLite with shell commands, or treat a plan as completed work. Ask for confirmation when the product contract requires it. Keep the response concise and in the user's language.

User request:
{{userMessage}}
```

Because `cases.yaml` and the extension do not exist yet, add temporary empty files containing `[]` and an exported no-op `extensionHook`; Task 2 replaces the hook and Task 3 replaces the cases.

- [ ] **Step 5: Re-run the config invariant test**

Run: `npm run test:evals`

Expected: PASS, 1 test.

- [ ] **Step 6: Commit the toolchain and single configuration**

```bash
git add package.json package-lock.json evals/workout-evals.yaml evals/prompts/workout-agent.txt evals/cases.yaml evals/support/fixture-hooks.mjs evals/tests/config.test.mjs
git commit -m "test: scaffold workout agent evals"
```

### Task 2: Isolate every eval case in a temporary database

**Files:**
- Modify: `evals/support/fixture-hooks.mjs`
- Create: `evals/tests/fixture-hooks.test.mjs`

**Interfaces:**
- Consumes: Promptfoo's `extensionHook(hookName, context)` convention and the `workoutDbPath`/`repoRoot` provider variables from Task 1.
- Produces: `beforeEach` variables `workoutDbPath`, `fixtureDir`, and absolute `repoRoot`; `afterEach` recursive cleanup; exported `createFixture(test)` and `removeFixture(test)` functions for direct unit testing.

- [ ] **Step 1: Write the failing fixture-isolation tests**

Create `evals/tests/fixture-hooks.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import { existsSync, writeFileSync } from 'node:fs';
import test from 'node:test';
import { createFixture, removeFixture } from '../support/fixture-hooks.mjs';

test('each test receives a unique database outside the repository', async () => {
  const first = await createFixture({ description: 'first', vars: {} });
  const second = await createFixture({ description: 'second', vars: {} });
  try {
    assert.notEqual(first.vars.workoutDbPath, second.vars.workoutDbPath);
    assert.match(first.vars.workoutDbPath, /workout-evals-/);
    assert.ok(!first.vars.workoutDbPath.endsWith('/workout.db'));
    assert.ok(first.vars.repoRoot.endsWith('/workout-tracker'));
    assert.ok(!first.vars.workoutDbPath.startsWith(first.vars.repoRoot));
  } finally {
    await removeFixture(first);
    await removeFixture(second);
  }
});

test('cleanup removes only the fixture that completed', async () => {
  const first = await createFixture({ description: 'first', vars: {} });
  const second = await createFixture({ description: 'second', vars: {} });
  writeFileSync(first.vars.workoutDbPath, 'first');
  writeFileSync(second.vars.workoutDbPath, 'second');
  await removeFixture(first);
  assert.equal(existsSync(first.vars.fixtureDir), false);
  assert.equal(existsSync(second.vars.workoutDbPath), true);
  await removeFixture(second);
});
```

- [ ] **Step 2: Run the fixture tests and verify the placeholder hook fails**

Run: `npm run test:evals`

Expected: FAIL because `createFixture` and `removeFixture` are not exported.

- [ ] **Step 3: Implement fixture creation, Promptfoo mutation, and cleanup**

Implement `createFixture` with `mkdtemp(join(tmpdir(), 'workout-evals-'))`, set the database filename to `case.sqlite3`, resolve the repository root from the extension file, and return a shallow-cloned test with merged vars. Implement `removeFixture` with `rm(fixtureDir, { recursive: true, force: true })` only after verifying `basename(fixtureDir).startsWith('workout-evals-')`. Implement `extensionHook` so `beforeEach` returns `{ test: await createFixture(context.test) }` and `afterEach` calls `removeFixture(context.test)`.

- [ ] **Step 4: Run fixture and configuration tests**

Run: `npm run test:evals`

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit isolated fixtures**

```bash
git add evals/support/fixture-hooks.mjs evals/tests/fixture-hooks.test.mjs
git commit -m "test: isolate workout eval databases"
```

### Task 3: Add deterministic trajectory and SQLite assertions

**Files:**
- Create: `evals/assertions/workout-assertions.cjs`
- Create: `evals/tests/workout-assertions.test.mjs`

**Interfaces:**
- Consumes: Promptfoo JavaScript assertion `(output, context)` calls, `context.metadata`, optional `context.trace.spans`, `context.config`, and `context.vars.workoutDbPath`.
- Produces: named CommonJS exports `calledTool`, `didNotCallTool`, `sqliteQuery`, and `noSensitiveNutritionPersistence` referenced from `cases.yaml`.

- [ ] **Step 1: Write failing assertion-helper tests**

Create tests that require the CommonJS module and prove all of these behaviors:

```javascript
assert.equal(assertions.calledTool('', {
  metadata: { toolCalls: [{ name: 'log_sets', arguments: { sets: [] } }] },
  config: { tool: 'log_sets' }, vars: {}
}).pass, true);

assert.equal(assertions.calledTool('', {
  trace: { spans: [{ name: 'mcp_tool_call workout-bot.log_activity', attributes: {} }] },
  config: { tool: 'log_activity' }, vars: {}
}).pass, true);

assert.equal(assertions.sqliteQuery('', {
  vars: { workoutDbPath: missingTableDb },
  config: { sql: 'select count(*) as n from nutrition_entries', column: 'n', equals: 1 }
}).pass, false);

assert.equal(assertions.noSensitiveNutritionPersistence('', {
  vars: { workoutDbPath: nutrientOnlyDb }, config: {}
}).pass, true);
```

The fixture database for the final assertion must contain numeric nutrient fields and generic estimation metadata, while a second fixture containing `food_name` or a text value such as `chicken burrito` must fail.

- [ ] **Step 2: Run the assertion tests and verify the missing module fails**

Run: `npm run test:evals`

Expected: FAIL with `MODULE_NOT_FOUND` for `workout-assertions.cjs`.

- [ ] **Step 3: Implement evidence normalization and grading results**

Implement every assertion as `(output, context) => ({ pass, score, reason })`. Normalize tool evidence from `metadata.toolCalls`, `metadata.tool_calls`, and trace span names/attributes. `sqliteQuery` must open the case database read-only, run only SQL beginning with `select`, compare `context.config.column` with `equals`, and convert missing files/tables into a failed grading result. `noSensitiveNutritionPersistence` must inspect table/column names and every persisted text value for the forbidden concepts `photo`, `image`, `food_name`, `meal`, `portion`, `usda_query`, `usda_response`, `fdc_id`, and case-provided `forbiddenValues`; generic flags such as `portion_uncertain` remain allowed.

- [ ] **Step 4: Run all harness tests**

Run: `npm run test:evals`

Expected: PASS, including both metadata and trace tool-call shapes, missing-table failure, and sensitive-data detection.

- [ ] **Step 5: Commit deterministic assertions**

```bash
git add evals/assertions/workout-assertions.cjs evals/tests/workout-assertions.test.mjs
git commit -m "test: add workout eval assertions"
```

### Task 4: Protect ChatGPT-subscription authentication

**Files:**
- Create: `evals/run-workout-evals.mjs`
- Create: `evals/tests/run-workout-evals.test.mjs`
- Create: `.gitignore`

**Interfaces:**
- Consumes: local Codex login under the normal Codex home and the `promptfoo` executable installed by Task 1.
- Produces: exported `subscriptionEnvironment(env)` and a CLI that invokes `promptfoo eval -c evals/workout-evals.yaml --no-cache --output evals/results/latest.json`; ignored `evals/results/` runtime artifacts.

- [ ] **Step 1: Write failing subscription-environment tests**

Create `evals/tests/run-workout-evals.test.mjs` with tests that verify both API-key variables are removed from the child environment, unrelated variables are preserved, and the input environment is not mutated:

```javascript
const input = { PATH: '/usr/bin', OPENAI_API_KEY: 'sk-test', CODEX_API_KEY: 'test' };
assert.deepEqual(subscriptionEnvironment(input), { PATH: '/usr/bin' });
assert.equal(input.OPENAI_API_KEY, 'sk-test');
assert.equal(input.CODEX_API_KEY, 'test');
```

- [ ] **Step 2: Run the tests and verify the missing runner fails**

Run: `npm run test:evals`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `run-workout-evals.mjs`.

- [ ] **Step 3: Implement the sanitizer and runner**

Implement `subscriptionEnvironment` to clone the parent environment and remove `OPENAI_API_KEY` and `CODEX_API_KEY`. When executed as the main module, use `spawnSync` without a shell to invoke the local Promptfoo binary with the arguments above, inheriting stdio and the sanitized child environment. Exit with Promptfoo's status. Add `evals/results/` to `.gitignore`.

- [ ] **Step 4: Run all harness tests**

Run: `npm run test:evals`

Expected: PASS, including automatic API-key removal without parent-environment mutation.

- [ ] **Step 5: Commit the subscription-only runner**

```bash
git add .gitignore evals/run-workout-evals.mjs evals/tests/run-workout-evals.test.mjs
git commit -m "test: require subscription auth for workout evals"
```

### Task 5: Populate the V1 behavior contract and record the baseline

**Files:**
- Modify: `evals/cases.yaml`
- Create: `evals/baselines/initial.md`
- Modify: `README.md`
- Modify: `AGENT.md`

**Interfaces:**
- Consumes: the target provider, fixture vars, and named assertion exports from Tasks 1-4.
- Produces: 18 named contract cases covering all nine approved skills; documented commands; a sanitized baseline containing only date, model, pass/fail/error counts, and failing case names.

- [ ] **Step 1: Extend the config test with the expected contract case names**

Parse `evals/cases.yaml` and assert these descriptions appear exactly once:

```text
repeatable setup changes explicit preferences only
quick strength logging uses the MCP server
Chinese exercise alias resolves without teaching it again
unknown exercise asks before creating an alias
first log starts an implicit workout
more than fifteen minutes starts a new workout
generated plan remains ephemeral
completed workout can become a routine on request
routine start creates an independent workout snapshot
meal photo estimate requires confirmation before saving
confirmed nutrition stores nutrient-only data
rough workout calories use documented fallbacks
first HIIT request offers approved defaults
progressive overload uses server-backed workout history
weekly report includes six-point workout trends
open-ended analysis uses server facts and explains trends
pain report stops the movement instead of logging it
destructive session deletion asks for confirmation
```

Also collect `metadata.skill` from every case and assert that the unique sorted values equal exactly:

```text
vblife-workout-analysis
vblife-workout-daily-plan
vblife-workout-hiit-session
vblife-workout-log
vblife-workout-nutrition
vblife-workout-progressive-overload
vblife-workout-report
vblife-workout-routine
vblife-workout-setup
```

Assert that every case has exactly one non-empty `metadata.skill` value beginning with `vblife-workout-`.

- [ ] **Step 2: Run the test and verify the empty case file fails**

Run: `npm run test:evals`

Expected: FAIL because all 18 descriptions and all nine skill metadata values are absent.

- [ ] **Step 3: Add the 18 contract-first cases**

For each case, set one primary skill in `metadata.skill`, a concrete `userMessage`, and at least one deterministic assertion. Use `calledTool`/`didNotCallTool` for trajectories, `sqliteQuery` for state, `noSensitiveNutritionPersistence` for nutrition privacy, and exact/regex output checks for confirmation language. The progressive-overload case is tagged `vblife-workout-progressive-overload`, supplies dated completed strength work, and requires the `suggest_next` tool plus a server-backed recommendation. The open-ended-analysis case is tagged `vblife-workout-analysis`, supplies dated mixed training, requires `query_stats` or `show_progress`, and requires an evidence-based trend explanation. Add an explicit Luna/max `agent-rubric` only to these four qualitative cases: repeatable setup, generated-plan ephemerality, rough-calorie disclosure, and pain response. The explicit grader configuration must repeat `model: gpt-6-luna`, `model_reasoning_effort: max`, `sandbox_mode: read-only`, `approval_policy: never`, `network_access_enabled: false`, and `web_search_mode: disabled`.

Use this primary-skill coverage map:

| Skill | Required primary case or cases |
|---|---|
| `vblife-workout-setup` | repeatable setup, including goals and proactive-reminder preference |
| `vblife-workout-log` | quick logging, Chinese alias, unknown exercise, automatic lifecycle, rough calories, pain, and destructive confirmation |
| `vblife-workout-daily-plan` | generated plan remains ephemeral |
| `vblife-workout-progressive-overload` | server-backed progressive-overload recommendation |
| `vblife-workout-hiit-session` | first-request defaults and exercise-name question |
| `vblife-workout-routine` | create from completed workout and start an isolated snapshot |
| `vblife-workout-nutrition` | pre-save confirmation and nutrient-only confirmed persistence |
| `vblife-workout-report` | weekly six-point trends for all three requested metrics |
| `vblife-workout-analysis` | open-ended, tool-grounded trend analysis |

Use exact time-bearing prompts for lifecycle cases: `2026-10-12T08:00:00-07:00` for the first log and `2026-10-12T08:16:00-07:00` for the second. Use the approved HIIT values 40 seconds work, 20 seconds rest, 20 rounds, 10 seconds warm-up, and 60 seconds cooldown. The weekly report assertion must require six values each for training time, strength volume, and cardio duration. Nutrition confirmation must supply nutrient totals in the prompt and assert that no meal-identifying value is persisted.

- [ ] **Step 4: Run harness tests and validate Promptfoo structure**

Run: `npm run test:evals && npm run eval:validate`

Expected: PASS for every harness test and Promptfoo validation. This command does not run Luna.

- [ ] **Step 5: Document the suite and baseline semantics**

Update `README.md` and `AGENT.md` to state:

```text
npm run test:evals      # local harness tests; no model usage
npm run eval:validate   # Promptfoo schema/config validation; no model usage
npm run eval:workout    # GPT-6 Luna, max effort, existing ChatGPT/Codex login
```

Document that the wrapper removes API-key variables from the child process, results are local and ignored, every case gets a temporary database, and V1 contract failures are expected until implementation catches up.

- [ ] **Step 6: Run the live contract suite through ChatGPT subscription**

Run: `npm run eval:workout`

Expected: Promptfoo starts 18 isolated Codex SDK cases using `gpt-6-luna` at `max`; the command may exit nonzero because unsupported V1 behaviors fail, but it must produce `evals/results/latest.json` without authentication, MCP-startup, or fixture-leak errors.

- [ ] **Step 7: Record a sanitized initial baseline**

Create `evals/baselines/initial.md` with the execution date, Promptfoo/Codex SDK versions, requested model and effort, total/pass/fail/error counts, and failing case descriptions. Do not include prompts, responses, tool arguments, database contents, filesystem paths outside the repository, tokens, credentials, or meal-identifying data.

- [ ] **Step 8: Re-run non-model verification**

Run: `npm run test:evals && npm run eval:validate && python3 -m unittest discover -s server/tests -p 'test_*.py'`

Expected: all harness tests, Promptfoo validation, and existing Python unit tests pass; live contract failures remain documented only in the sanitized baseline.

- [ ] **Step 9: Commit contract cases, documentation, and baseline**

```bash
git add evals/cases.yaml evals/baselines/initial.md README.md AGENT.md evals/tests/config.test.mjs
git commit -m "test: establish Workout Bot V1 eval baseline"
```

## Self-review record

- Spec coverage: all Agent behavior evaluation requirements map to Tasks 1-5; the 18 cases cover the exact nine-skill set while server calculation and schema exhaustiveness remain in the existing/future Python suites.
- Placeholder scan: no TBD, TODO, “similar to,” or unspecified error-handling step remains.
- Type consistency: `workoutDbPath` and `repoRoot` are produced in Task 2 and consumed by Tasks 1, 3, and 5; assertion export names match the Task 5 references; npm script names match Tasks 4-5 and documentation.
- Review Focus: API-key leakage, fixture collision, missing V1 schema, dual tool-trace shapes, and baseline/harness status separation each have a named test task.
