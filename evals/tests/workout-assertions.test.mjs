import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';
import Database from 'better-sqlite3';

const require = createRequire(import.meta.url);
const assertions = require('../assertions/workout-assertions.cjs');

test('calledTool reads provider toolCalls metadata', () => {
  const result = assertions.calledTool('', {
    metadata: { toolCalls: [{ name: 'log_sets', arguments: { sets: [] } }] },
    config: { tool: 'log_sets' },
    vars: {},
  });
  assert.equal(result.pass, true);
});

test('calledTool reads traced MCP spans', () => {
  const result = assertions.calledTool('', {
    trace: { spans: [{ name: 'mcp_tool_call workout-bot.log_activity', attributes: {} }] },
    config: { tool: 'log_activity' },
    vars: {},
  });
  assert.equal(result.pass, true);
});

test('sqliteQuery reports a missing V1 table as a contract failure', () => {
  const dir = mkdtempSync(join(tmpdir(), 'workout-assertions-'));
  const path = join(dir, 'missing.sqlite3');
  new Database(path).close();
  try {
    const result = assertions.sqliteQuery('', {
      vars: { workoutDbPath: path },
      config: { sql: 'select count(*) as n from nutrition_entries', column: 'n', equals: 1 },
    });
    assert.equal(result.pass, false);
    assert.match(result.reason, /nutrition_entries/i);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('nutrition persistence accepts nutrients and generic uncertainty only', () => {
  const dir = mkdtempSync(join(tmpdir(), 'workout-assertions-'));
  const path = join(dir, 'nutrients.sqlite3');
  const db = new Database(path);
  db.exec(`CREATE TABLE nutrition_entries (
    calories REAL, protein_g REAL, confidence TEXT, uncertainty_flags TEXT
  )`);
  db.prepare('INSERT INTO nutrition_entries VALUES (?, ?, ?, ?)').run(
    640,
    42,
    'rough',
    '["portion_uncertain","preparation_unknown"]',
  );
  db.close();
  try {
    const result = assertions.noSensitiveNutritionPersistence('', {
      vars: { workoutDbPath: path },
      config: {},
    });
    assert.equal(result.pass, true, result.reason);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('nutrition persistence rejects identifying columns and values', () => {
  const dir = mkdtempSync(join(tmpdir(), 'workout-assertions-'));
  const path = join(dir, 'sensitive.sqlite3');
  const db = new Database(path);
  db.exec('CREATE TABLE nutrition_entries (calories REAL, food_name TEXT)');
  db.prepare('INSERT INTO nutrition_entries VALUES (?, ?)').run(640, 'chicken burrito');
  db.close();
  try {
    const result = assertions.noSensitiveNutritionPersistence('', {
      vars: { workoutDbPath: path },
      config: { forbiddenValues: ['chicken burrito'] },
    });
    assert.equal(result.pass, false);
    assert.match(result.reason, /food_name|chicken burrito/i);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('didNotCallTool negates tool evidence', () => {
  const result = assertions.didNotCallTool('', {
    metadata: { tool_calls: [{ name: 'delete_session' }] },
    config: { tool: 'delete_session' },
    vars: {},
  });
  assert.equal(result.pass, false);
});
