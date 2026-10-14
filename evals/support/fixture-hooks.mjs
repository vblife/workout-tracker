import { mkdtemp, rm } from 'node:fs/promises';
import { basename, join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('../..', import.meta.url));

export async function createFixture(test) {
  const fixtureDir = await mkdtemp(join(tmpdir(), 'workout-evals-'));
  return {
    ...test,
    vars: {
      ...(test.vars ?? {}),
      fixtureDir,
      repoRoot,
      workoutDbPath: join(fixtureDir, 'case.sqlite3'),
    },
  };
}

export async function removeFixture(test) {
  const fixtureDir = test?.vars?.fixtureDir;
  if (!fixtureDir) return;
  if (!basename(fixtureDir).startsWith('workout-evals-')) {
    throw new Error(`Refusing to remove non-eval fixture directory: ${fixtureDir}`);
  }
  await rm(fixtureDir, { recursive: true, force: true });
}

export async function extensionHook(hookName, context) {
  if (hookName === 'beforeEach') {
    return { test: await createFixture(context.test) };
  }
  if (hookName === 'afterEach') {
    await removeFixture(context.test);
  }
}
