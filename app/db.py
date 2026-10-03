"""Database connection (PostgreSQL).

Two ways to connect:
- Locally: DATABASE_URL from the environment or from a .env file at
  ela_read/.env, for example
      DATABASE_URL=postgresql://propane:propane@localhost:55432/amc
- Inside a Databricks App (future deployment): DATABASE_URL names the
  Lakebase host, and the password is a short-lived OAuth token from the
  Databricks SDK. Mirrors app/db.py in the sibling AMC app.

Rows come back as dicts. Queries use %s placeholders (psycopg style). All
tables live in the ela_read schema, separate from the AMC app's amc_app
schema in the same database.
"""
import os
import re
from sys import maxsize
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import conninfo
from psycopg.rows import dict_row

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
SEARCH_PATH = "-c search_path=ela_read"

LAKEBASE_ENDPOINT = os.environ.get("LAKEBASE_ENDPOINT",
                                   "projects/hobby/branches/production/endpoints/primary")
TOKEN_REUSE_SECONDS = 45 * 60

ON_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_PORT"))
ON_DATABRICKS_JOB = bool(os.environ.get("USE_LAKEBASE"))

if not (ON_DATABRICKS_APP or ON_DATABRICKS_JOB):
    load_dotenv(ROOT / ".env")

_schema_ready = False
_workspace = None
_token = None           # (token, fetched_at)


from psycopg_pool import AsyncConnectionPool

async def open_pool()->AsyncConnectionPool:
    pool=AsyncConnectionPool(
        conninfo=database_url(),
        kwargs={"row_factory" : dict_row, "options":SEARCH_PATH,
                "dbname": "databricks_postgres"},
        min_size=1, max_size=10,
        check=AsyncConnectionPool.check_connection,
        open=False
    )
    await pool.open(wait=True)
    async with pool.connection() as conn:
        await conn.execute((APP_DIR / "schema.sql").read_text(encoding="utf-8"))
    return pool

def database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    print(url)
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add a line like\n"
            "  DATABASE_URL=postgresql://propane:propane@localhost:55432/amc\n"
            f"to {ROOT / '.env'} (see .env.example).")
    return url


async def lakebase_token():
    global _workspace, _token
    if _token and time.monotonic() - _token[1] < TOKEN_REUSE_SECONDS:
        return _token[0]
    if _workspace is None:
        from databricks.sdk import AsyncWorkspaceClient as WorkspaceClient
        _workspace = WorkspaceClient()
    credential = await _workspace.postgres.generate_database_credential(endpoint=LAKEBASE_ENDPOINT)
    _token = (credential.token, time.monotonic())
    print(f"Lakebase token refreshed (expires {credential.expire_time})")
    return _token[0]


async def connect():
    """A new connection; the schema is created the first time in each process."""
    global _schema_ready
    conn = await psycopg.AsyncConnection.connect(database_url(), row_factory=dict_row, options=SEARCH_PATH,
                dbname="databricks_postgres",
                # user=os.environ.get("DATABRICKS_CLIENT_ID") or "propane",
                # password=lakebase_token(),                           
        )
    if not _schema_ready:
        await conn.execute((APP_DIR / "schema.sql").read_text(encoding="utf-8"))
        await conn.commit()
        _schema_ready = True
    return conn


def connect_sync():
    """Blocking connection for scripts (content_loader). The server uses connect()."""
    conn = psycopg.connect(database_url(), row_factory=dict_row, options=SEARCH_PATH,
                           dbname="databricks_postgres")
    conn.execute((APP_DIR / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    return conn
