# Initial Workout Bot V1 Eval Baseline

- Executed: 2026-10-12 America/Los_Angeles
- Suite: `workout-evals`
- Promptfoo: 0.124.1
- Codex SDK: 0.162.1
- Requested model: `gpt-6-luna`
- Reasoning effort: `max`
- Authentication: ChatGPT/Codex subscription login
- Concurrency: 1
- Total: 18
- Passed: 3
- Failed: 15
- Errors: 0

Passing cases:

- quick strength logging uses the MCP server
- Chinese exercise alias resolves without teaching it again
- unknown exercise asks before creating an alias

Failing cases:

- repeatable setup changes explicit preferences only
- first log starts an implicit workout
- more than fifteen minutes starts a new workout
- generated plan remains ephemeral
- completed workout can become a routine on request
- routine start creates an independent workout snapshot
- meal photo estimate requires confirmation before saving
- confirmed nutrition stores nutrient-only data
- rough workout calories use documented fallbacks
- first HIIT request offers approved defaults
- progressive overload uses server-backed workout history
- weekly report includes six-point workout trends
- open-ended analysis uses server facts and explains trends
- pain report stops the movement instead of logging it
- destructive session deletion asks for confirmation

This baseline records contract status only. Raw prompts, responses, tool arguments, database contents, local fixture paths, credentials, and meal-identifying data are intentionally omitted.
