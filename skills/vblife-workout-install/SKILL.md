---
name: vblife-workout-install
description: Use when the user asks to install, reinstall, or update Workout Bot, or to create a new Workout Bot from the vblife/workout-tracker repository.
---
# Install Workout Bot from the repository

The repository is the single source of truth. Never write the bot's instructions, routines, or skills from memory; read them from the files below.

1. Code: if `/home/vblife/workout` is missing, clone `https://github.com/vblife/workout-tracker` there; otherwise `git pull --ff-only`. Run `python3 -m unittest discover -s server/tests -p 'test_*.py'` and stop on failure.
2. Data: create `/workspace/vblife/workout-data/`. Never overwrite or delete an existing `workout.db` there.
3. Connector: read `bot/connector.json`. If a connector with that name exists, restart it; otherwise add it with exactly that command, args, and env. Confirm its tools load.
4. Skills: for every folder in `skills/` except this one, save the skill to the shared library with its frontmatter name and description and the body after the frontmatter. Delete library skills with the `vblife-workout-` prefix that no longer exist in the repository. Save this install skill too, so the library copy stays current.
5. Bot: create the agent named "Workout Bot" with the full text of `bot/instructions.md` as its description. When reinstalling, update the existing bot's description instead of creating a second bot.
6. Routines: routines belong to the bot itself, so send the new bot the full contents of `bot/routines.yaml` and ask it to confirm the user's timezone and training days first. The bot saves the confirmed timezone as a valid IANA name with `update_settings` (never assume Pacific time or UTC), fills `{training_days}`, and creates each routine with its schedule exactly as a plain 5-field cron in user-local time. Do not add a `CRON_TZ=` prefix or any other timezone field to the schedule. Keep the routines proactive by default; the user can turn any of them off.
7. Verify and report: the connector is connected, every skill is in the library, the bot exists, and the routines were handed off. Give the commit hash that was installed.
