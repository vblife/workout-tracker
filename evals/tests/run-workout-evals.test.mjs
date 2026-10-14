import assert from 'node:assert/strict';
import test from 'node:test';
import { assertSubscriptionEnvironment, promptfooArgs } from '../run-workout-evals.mjs';

test('subscription environment accepts no API keys', () => {
  assert.doesNotThrow(() => assertSubscriptionEnvironment({ PATH: process.env.PATH }));
});

test('subscription environment rejects OPENAI_API_KEY', () => {
  assert.throws(
    () => assertSubscriptionEnvironment({ OPENAI_API_KEY: 'sk-test' }),
    /OPENAI_API_KEY/,
  );
});

test('subscription environment rejects CODEX_API_KEY', () => {
  assert.throws(
    () => assertSubscriptionEnvironment({ CODEX_API_KEY: 'test' }),
    /CODEX_API_KEY/,
  );
});

test('runner serializes live cases', () => {
  assert.deepEqual(promptfooArgs.slice(-2), ['--max-concurrency', '1']);
});
