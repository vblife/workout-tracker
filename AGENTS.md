# Agent rules for vblife/workout-tracker

These rules apply to every coding agent (Grok Bot, Cursor, or any other) working in this repo.

## Git and PRs
- Never push to `main`. Work on a feature branch and open a pull request.
- Never merge your own PR. The reviewer agent merges automatically once tests pass and its review is clean.
- Keep each PR to one change. Do not mix unrelated edits.
- Do not post GitHub comments or replies unless the repo owner explicitly asked for that exact comment.

## Tests
- Run the full test suite before pushing: `python -m pytest -q` (or `python3 -m pytest -q`) from the repo root (needs `pytest` and `mcp`).
- Put the test command and its result (e.g. `90 passed`) in the PR description.
- Add or update tests for any behavior change.

## Content
- Files under `skills/` must be English only (enforced by `test_skill_files_are_english_only`).
- Do not hard-code machine-specific absolute paths; resolve paths relative to the repo.

## Contracts
- Do not invent APIs, tool names, or data fields. Read the existing code in `server/` (and any docs) first and follow what is there.
