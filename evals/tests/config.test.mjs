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
