import { mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const config = fileURLToPath(new URL('./workout-evals.yaml', import.meta.url));
const output = fileURLToPath(new URL('./results/latest.json', import.meta.url));
const promptfoo = fileURLToPath(
  new URL('../node_modules/promptfoo/dist/src/entrypoint.js', import.meta.url),
);

export const promptfooArgs = [
  promptfoo,
  'eval',
  '-c',
  config,
  '--no-cache',
  '--output',
  output,
  '--max-concurrency',
  '1',
];

export function subscriptionEnvironment(env) {
  const childEnv = { ...env };
  delete childEnv.OPENAI_API_KEY;
  delete childEnv.CODEX_API_KEY;
  return childEnv;
}

export function runWorkoutEvals(env = process.env, spawn = spawnSync) {
  mkdirSync(dirname(output), { recursive: true });
  const result = spawn(
    process.execPath,
    promptfooArgs,
    {
      cwd: fileURLToPath(new URL('..', import.meta.url)),
      env: subscriptionEnvironment(env),
      stdio: 'inherit',
    },
  );
  if (result.error) throw result.error;
  return result.status ?? 1;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  process.exitCode = runWorkoutEvals();
}
