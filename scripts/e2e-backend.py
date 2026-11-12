#!/usr/bin/env python3
"""Serve a disposable API for the browser tests on http://127.0.0.1:8001.

It empties the Mac host's isolated *_test database (never the application database),
migrates it, and runs the API against it until stopped:

    python3 scripts/e2e-backend.py &
    cd frontend && API_URL=http://127.0.0.1:8001 npx playwright test
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/macos"))
import host  # noqa: E402

os.environ.setdefault("LC_ALL", "en_US.UTF-8")
host.init_database()
env = host.environment(host.config()["db_name"] + "_test")
assert env["PGDATABASE"].endswith("_test")
subprocess.run(["psql", "-q", "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"],
               env={**env, "PATH": str(host.pg_bin()) + ":" + env["PATH"]}, check=True)
subprocess.run([host.python_bin(), "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", env=env, check=True)
os.chdir(ROOT / "backend")
os.execve(host.python_bin(), [host.python_bin(), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001"], env)
