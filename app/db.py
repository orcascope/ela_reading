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
import os, re,  time
from dotenv import load_dotenv
load_dotenv()
from sys import maxsize
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from databricks.sdk import WorkspaceClient

APP_DIR = Path(__file__).resolve().parent
SEARCH_PATH = "-c search_path=ela_read"


TOKEN_REUSE_SECONDS = 45 * 60

_schema_ready = False
_workspace = None
_token = None           # (token, fetched_at)


from psycopg_pool import AsyncConnectionPool

async def open_pool()->AsyncConnectionPool:
    w=WorkspaceClient(
        # host=os.getenv("DATABRICKS_HOST"),
        # client_id=os.getenv("DATABRICKS_CLIENT_ID"),
        # client_secret=os.getenv("DATABRICKS_CLIENT_SECRET_DB")
    )
    me = w.current_user.me()

    print(f"Name: {me.display_name}")
    print(f"Email: {me.emails}")
    print(f"ID: {me.id}")
    print(f"Active: {me.active}")
    credential = w.postgres.generate_database_credential(endpoint=os.getenv("LAKEBASE_ENDPOINT"))

    pool=AsyncConnectionPool(
        conninfo=f"host={os.environ['LAKEBASE_HOST']} dbname={os.environ.get('LAKEBASE_DB', 'databricks_postgres')} user={os.environ['DATABRICKS_CLIENT_ID']} port=5432 sslmode=require",
        kwargs={"password": credential.token,
                "row_factory" : dict_row, 
                "options":SEARCH_PATH
                },               
        min_size=1, max_size=10,
        check=AsyncConnectionPool.check_connection,
        open=False
    )
    await pool.open(wait=True)
    async with pool.connection() as conn:
        await conn.execute((APP_DIR / "schema.sql").read_text(encoding="utf-8"))
    return pool

async def lakebase_token():
    global _workspace, _token
    if _token and time.monotonic() - _token[1] < TOKEN_REUSE_SECONDS:
        return _token[0]
    if _workspace is None:
        from databricks.sdk import AsyncWorkspaceClient as WorkspaceClient
        _workspace = WorkspaceClient()
    credential = await _workspace.postgres.generate_database_credential(endpoint=os.getenv("LAKEBASE_ENDPOINT"))
    _token = (credential.token, time.monotonic())
    print(f"Lakebase token refreshed (expires {credential.expire_time})")
    return _token[0]


async def connect():
    """A new connection; the schema is created the first time in each process."""
    global _schema_ready
    conn = await psycopg.AsyncConnection.connect("<database_url>", row_factory=dict_row, options=SEARCH_PATH,
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
    w=WorkspaceClient()
    credential = w.postgres.generate_database_credential(endpoint=os.getenv("LAKEBASE_ENDPOINT"))

    conn = psycopg.connect(
        conninfo=f"host={os.environ['PGHOST']} dbname={os.environ.get('PGDATABASE')} user={os.environ['PGUSER']} port={os.getenv('PGPORT')} sslmode=require",
        password=credential.token,
        row_factory=dict_row,
        options=SEARCH_PATH,
    )
    conn.execute((APP_DIR / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    return conn


if __name__ == "__main__":
    databricks_pool()