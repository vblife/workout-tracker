"""pytest hook: install the live-database guard before any test module imports the server code."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import live_db_guard  # noqa: E402,F401
