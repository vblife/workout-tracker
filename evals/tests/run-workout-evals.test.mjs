import assert from 'node:assert/strict';
import test from 'node:test';
import * as runner from '../run-workout-evals.mjs';

test('subscription environment removes API keys without mutating its input', () => {
  assert.equal(typeof runner.subscriptionEnvironment, 'function');
  const input = {
    PATH: '/usr/bin',
    WORKOUT_EVAL_MARKER: 'preserved',
    OPENAI_API_KEY: 'sk-test',
    CODEX_API_KEY: 'codex-test',
  };

  const child = runner.subscriptionEnvironment(input);

  assert.deepEqual(child, {
    PATH: '/usr/bin',
    WORKOUT_EVAL_MARKER: 'preserved',
  });
  assert.equal(input.OPENAI_API_KEY, 'sk-test');
  assert.equal(input.CODEX_API_KEY, 'codex-test');
});

test('runner passes the sanitized environment to the Promptfoo child', () => {
  assert.equal(typeof runner.runWorkoutEvals, 'function');
  let childEnvironment;
  const status = runner.runWorkoutEvals(
    {
      PATH: '/usr/bin',
      WORKOUT_EVAL_MARKER: 'preserved',
      OPENAI_API_KEY: 'sk-test',
      CODEX_API_KEY: 'codex-test',
    },
    (_executable, _args, options) => {
      childEnvironment = options.env;
      return { status: 0 };
    },
  );

  assert.equal(status, 0);
  assert.deepEqual(childEnvironment, {
    PATH: '/usr/bin',
    WORKOUT_EVAL_MARKER: 'preserved',
  });
});

test('runner serializes live cases', () => {
  assert.deepEqual(runner.promptfooArgs.slice(-2), ['--max-concurrency', '1']);
});
