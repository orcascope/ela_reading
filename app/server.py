"""FastAPI app: API routes + the static reader UI."""
from contextlib import asynccontextmanager
import json, os, logging
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_openai import ChatOpenAI

from . import db, llm
from app.routers import explain, lessons, students
from app.deps import get_db


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app:FastAPI):
    MODEL = "system.ai.meta-llama-3-3-70b-instruct"
    DATABRICKS_TOKEN = os.getenv("LLM_ACCESS_KEY")
    app.state.llm_client=llm.get_client()
    app.state.pool = await db.open_pool()
    print("lifespan llm_client created")      
    yield
    await app.state.pool.close()

app = FastAPI(title="ela_read", lifespan=lifespan)
app.include_router(explain.router)
app.include_router(students.router)
app.include_router(lessons.router)

# ------------------------------------------------------------------ static --

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")