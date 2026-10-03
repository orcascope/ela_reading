"""FastAPI app: API routes + the static reader UI."""
from contextlib import asynccontextmanager
import json, os
from pathlib import Path
from typing import Optional



from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_openai import ChatOpenAI

from . import db, llm


async def get_db(request:Request):
    async with request.app.state.pool.connection() as conn:
        yield conn

def get_openai_client(request: Request):
    return request.app.state.llm_client


async def touch_student(conn, student_id: Optional[int]) -> int:
    """Validates a student id against the DB and bumps last_seen. Raises 400
    if missing or unknown, mirroring the AMC app's student_id() helper."""
    if student_id is None:
        raise HTTPException(400, "unknown student")
    cur = await conn.execute("SELECT id FROM students WHERE id = %s", (student_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(400, "unknown student")
    await conn.execute("UPDATE students SET last_seen = now() WHERE id = %s", (student_id,))
    await conn.commit()
    return row["id"]


def strip_prefix(full_id: str, lesson_id: str) -> str:
    return full_id[len(lesson_id) + 1:]




@app.get("/api/test_route")
async def run_test(conn=Depends(get_db)):
    cur = await conn.execute("SELECT count(*) FROM vocabulary")
    row= await cur.fetchall()
    print(row)
    return row

# ------------------------------------------------------------------ static --

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
