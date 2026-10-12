# Workout Tracker MCP (Phases 1-4)
Launch (stdio): command `/usr/bin/python3`, args `["/home/box/workout/server/server.py","--stdio"]`
Optional env: WORKOUT_DB (default /home/box/workout/workout.db), WORKOUT_FALLBACK_BASE (https base of the static site; or settings.fallback_base_url)
Code: server.py (protocol/tools), core.py (log/settings/HIIT/activities), coach.py (suggest_next, daily_plan_context), reports.py (report, show_progress), fallback.py, ui/{hiit,charts}.html
Static fallback site: `server/build_site.sh` -> /home/box/workout/site (NOT published yet)
Tests: python3 server/tests/test_core.py; python3 server/tests/test_phase234.py; .venv-test/bin/python server/tests/e2e_sdk.py; .venv-test/bin/python server/tests/e2e_phase234.py
