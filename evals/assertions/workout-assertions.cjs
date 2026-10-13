const Database = require('better-sqlite3');

function grade(pass, reason) {
  return { pass, score: pass ? 1 : 0, reason };
}

function toolNames(context) {
  const metadata = context?.metadata ?? {};
  const calls = [
    ...(Array.isArray(metadata.toolCalls) ? metadata.toolCalls : []),
    ...(Array.isArray(metadata.tool_calls) ? metadata.tool_calls : []),
  ];
  const names = calls
    .map((call) => call?.name ?? call?.tool ?? call?.function?.name)
    .filter(Boolean)
    .map(String);

  for (const span of context?.trace?.spans ?? []) {
    const attributes = span?.attributes ?? {};
    const explicit =
      attributes['mcp.tool.name'] ??
      attributes['gen_ai.tool.name'] ??
      attributes.tool_name ??
      attributes.name;
    if (explicit) names.push(String(explicit));
    if (span?.name) names.push(String(span.name));
  }
  return names;
}

function hasTool(context, wanted) {
  return toolNames(context).some((name) => {
    const finalSegment = name.split(/[./]/).at(-1);
    return name === wanted || finalSegment === wanted || name.includes(wanted);
  });
}

function calledTool(_output, context) {
  const tool = context?.config?.tool;
  const pass = Boolean(tool) && hasTool(context, tool);
  return grade(pass, pass ? `Called ${tool}` : `No evidence that ${tool} was called`);
}

function didNotCallTool(_output, context) {
  const tool = context?.config?.tool;
  const pass = Boolean(tool) && !hasTool(context, tool);
  return grade(pass, pass ? `${tool} was not called` : `Forbidden tool ${tool} was called`);
}

function sqliteQuery(_output, context) {
  const path = context?.vars?.workoutDbPath;
  const { sql, column, equals } = context?.config ?? {};
  if (!path || typeof sql !== 'string' || !/^\s*select\b/i.test(sql)) {
    return grade(false, 'sqliteQuery requires a case database and a SELECT statement');
  }
  try {
    const db = new Database(path, { readonly: true, fileMustExist: true });
    try {
      const row = db.prepare(sql).get();
      const actual = row?.[column];
      const pass = Object.is(actual, equals);
      return grade(pass, pass ? `${column} matched ${JSON.stringify(equals)}` : `${column} was ${JSON.stringify(actual)}, expected ${JSON.stringify(equals)}`);
    } finally {
      db.close();
    }
  } catch (error) {
    return grade(false, `SQLite contract check failed: ${error.message}`);
  }
}

const forbiddenSchemaTerms = [
  'photo',
  'image',
  'food_name',
  'meal',
  'portion',
  'usda_query',
  'usda_response',
  'fdc_id',
];

function sensitiveText(value, forbiddenValues) {
  const text = String(value).toLowerCase();
  for (const forbidden of forbiddenValues) {
    if (forbidden && text.includes(String(forbidden).toLowerCase())) return String(forbidden);
  }
  const withoutAllowedFlags = text
    .replaceAll('portion_uncertain', '')
    .replaceAll('preparation_unknown', '');
  return forbiddenSchemaTerms.find((term) => withoutAllowedFlags.includes(term));
}

function noSensitiveNutritionPersistence(_output, context) {
  const path = context?.vars?.workoutDbPath;
  if (!path) return grade(false, 'No workoutDbPath was provided');
  try {
    const db = new Database(path, { readonly: true, fileMustExist: true });
    try {
      const tables = db
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        .all()
        .map((row) => row.name);
      for (const table of tables) {
        const schemaHit = sensitiveText(table, []);
        if (schemaHit) return grade(false, `Sensitive schema term ${schemaHit} found in table ${table}`);
        const quotedTable = `"${String(table).replaceAll('"', '""')}"`;
        const columns = db.prepare(`PRAGMA table_info(${quotedTable})`).all().map((row) => row.name);
        for (const column of columns) {
          const columnHit = sensitiveText(column, []);
          if (columnHit) return grade(false, `Sensitive schema term found in column ${column}`);
        }
        const rows = db.prepare(`SELECT * FROM ${quotedTable}`).all();
        for (const row of rows) {
          for (const [column, value] of Object.entries(row)) {
            if (typeof value !== 'string') continue;
            const valueHit = sensitiveText(value, context?.config?.forbiddenValues ?? []);
            if (valueHit) return grade(false, `Sensitive value ${valueHit} found in ${table}.${column}`);
          }
        }
      }
      return grade(true, 'SQLite contains nutrient totals and non-identifying estimation metadata only');
    } finally {
      db.close();
    }
  } catch (error) {
    return grade(false, `Nutrition privacy check failed: ${error.message}`);
  }
}

module.exports = {
  calledTool,
  didNotCallTool,
  sqliteQuery,
  noSensitiveNutritionPersistence,
};
