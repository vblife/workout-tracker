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

export function assertSubscriptionEnvironment(env) {
  const present = ['OPENAI_API_KEY', 'CODEX_API_KEY'].filter((name) => env[name]);
  if (present.length) {
    throw new Error(
      `Refusing API-key billing. Unset ${present.join(' and ')} before running workout-evals; the suite must reuse the existing ChatGPT/Codex login.`,
    );
  }
}

function run() {
  assertSubscriptionEnvironment(process.env);
  mkdirSync(dirname(output), { recursive: true });
  const result = spawnSync(
    process.execPath,
    promptfooArgs,
    { cwd: fileURLToPath(new URL('..', import.meta.url)), env: process.env, stdio: 'inherit' },
  );
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  run();
}
