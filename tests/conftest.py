"""
conftest.py — keep tests away from the real instance/ folder.

Runs before any test module is imported, so server.py (which opens its SQLite
store and audit log at import time) uses a throw-away directory that is
re-seeded from users.DEFAULT_ACCOUNTS on every run.
"""
import os
import tempfile

os.environ["FETCH_INSTANCE_DIR"] = tempfile.mkdtemp(prefix="fetch-test-")
