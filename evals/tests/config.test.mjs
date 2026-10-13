import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import YAML from 'yaml';

const configPath = new URL('../workout-evals.yaml', import.meta.url);
const casesPath = new URL('../cases.yaml', import.meta.url);

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
  assert.equal(mcp.default_tools_approval_mode, 'approve');
  assert.ok(!JSON.stringify(config).includes('current-smoke'));
  assert.ok(!JSON.stringify(config).includes('v1-contract'));
});

test('contract cases cover every approved skill exactly by metadata set', () => {
  const cases = YAML.parse(readFileSync(casesPath, 'utf8'));
  const expectedDescriptions = [
    'repeatable setup changes explicit preferences only',
    'quick strength logging uses the MCP server',
    'Chinese exercise alias resolves without teaching it again',
    'unknown exercise asks before creating an alias',
    'first log starts an implicit workout',
    'more than fifteen minutes starts a new workout',
    'generated plan remains ephemeral',
    'completed workout can become a routine on request',
    'routine start creates an independent workout snapshot',
    'meal photo estimate requires confirmation before saving',
    'confirmed nutrition stores nutrient-only data',
    'rough workout calories use documented fallbacks',
    'first HIIT request offers approved defaults',
    'progressive overload uses server-backed workout history',
    'weekly report includes six-point workout trends',
    'open-ended analysis uses server facts and explains trends',
    'pain report stops the movement instead of logging it',
    'destructive session deletion asks for confirmation',
  ];
  assert.deepEqual(cases.map((entry) => entry.description).sort(), expectedDescriptions.sort());
  for (const entry of cases) {
    assert.match(entry.metadata?.skill ?? '', /^vblife-workout-[a-z-]+$/);
  }
  const actualSkills = [...new Set(cases.map((entry) => entry.metadata.skill))].sort();
  assert.deepEqual(actualSkills, [
    'vblife-workout-analysis',
    'vblife-workout-daily-plan',
    'vblife-workout-hiit-session',
    'vblife-workout-log',
    'vblife-workout-nutrition',
    'vblife-workout-progressive-overload',
    'vblife-workout-report',
    'vblife-workout-routine',
    'vblife-workout-setup',
  ]);
});
