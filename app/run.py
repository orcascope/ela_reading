"""Starts the ela_read server.

Usage (from ela_read/): python -m app.run
"""
import os
from dotenv import load_dotenv
load_dotenv()

import uvicorn

if __name__ == "__main__":
    on_databricks = bool(os.environ.get("DATABRICKS_APP_PORT"))
    host = "0.0.0.0" if on_databricks else "127.0.0.1"
    port = int(os.environ.get("DATABRICKS_APP_PORT", 8010))
    print(f"ela_read serving2 at http://{host}:{port}")
    uvicorn.run("app.server:app", host=host, port=port, reload=not on_databricks)
