"""Test isolation helper: never open the live workout database.

Tests create their own temporary databases. As a safety net, WORKOUT_DB points at a throwaway file for the
whole run (also inherited by CLI subprocesses), and db.connect / db.connect_readonly refuse the live default
path (db.DEFAULT_DB) outright.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="workout-tests-")
os.environ["WORKOUT_DB"] = os.path.join(_TMP, "unused-default.db")
LIVE_DB = os.path.realpath(db.DEFAULT_DB)


def _guard(path):
    resolved = os.path.realpath(path or os.environ.get("WORKOUT_DB") or db.DEFAULT_DB)
    if resolved == LIVE_DB:
        raise RuntimeError(f"tests must not open the live workout database {LIVE_DB}")


def _wrap(fn):
    def guarded(path=None, *args, **kwargs):
        _guard(path)
        return fn(path, *args, **kwargs)
    guarded.__wrapped__ = fn
    return guarded


if not hasattr(db.connect, "__wrapped__"):
    db.connect = _wrap(db.connect)
    db.connect_readonly = _wrap(db.connect_readonly)
