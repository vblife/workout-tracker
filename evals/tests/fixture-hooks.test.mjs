import assert from 'node:assert/strict';
import { existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { createFixture, removeFixture } from '../support/fixture-hooks.mjs';

test('each test receives a unique database outside the repository', async () => {
  const first = await createFixture({ description: 'first', vars: {} });
  const second = await createFixture({ description: 'second', vars: {} });
  try {
    assert.notEqual(first.vars.workoutDbPath, second.vars.workoutDbPath);
    assert.match(first.vars.workoutDbPath, /workout-evals-/);
    assert.ok(!first.vars.workoutDbPath.endsWith('/workout.db'));
    assert.equal(existsSync(join(first.vars.repoRoot, 'server/server.py')), true);
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
