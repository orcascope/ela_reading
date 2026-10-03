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
from app.routers import explain, lessons, students

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

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


# ---------------------------------------------------------------- students --

class StudentCreate(BaseModel):
    name: str


@app.post("/api/students")
async def create_or_get_student(body: StudentCreate, conn=Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    cur = await conn.execute("SELECT id, name FROM students WHERE lower(name) = lower(%s)", (name,))
    row = await cur.fetchone()
    if row:
        await conn.execute("UPDATE students SET last_seen = now() WHERE id = %s", (row["id"],))
        await conn.commit()
        return row
    cur = await conn.execute(
        "INSERT INTO students (name) VALUES (%s) RETURNING id, name", (name,)
    )
    row = await cur.fetchone()
    await conn.commit()
    return row


# ------------------------------------------------------------------- books --

@app.get("/api/books")
async def list_books(conn=Depends(get_db)):
    cur = await conn.execute("SELECT book_id, title, author, chapter_count FROM books ORDER BY title")
    return await cur.fetchall()


@app.get("/api/books/{book_id}/resume")
async def resume_book(book_id: str, student: Optional[int] = None, conn=Depends(get_db)):
    await touch_student(conn, student)

    _bookmark = await conn.execute(
        "SELECT current_lesson_id FROM bookmarks WHERE student_id = %s AND book_id = %s",
        (student, book_id),
    )
    bookmark = await _bookmark.fetchone()
    if bookmark:
        return {"lesson_id": bookmark["current_lesson_id"]}

    _first = await conn.execute(
        "SELECT lesson_id FROM lessons WHERE book_id = %s ORDER BY ord ASC LIMIT 1",
        (book_id,),
    )
    first = await _first.fetchone()
    if not first:
        raise HTTPException(404, "no lessons available for this book yet")
    return {"lesson_id": first["lesson_id"]}


# ----------------------------------------------------------------- lessons --

@app.get("/api/lessons/{lesson_id}")
async def get_lesson(lesson_id: str, student: Optional[int] = None, conn=Depends(get_db)):
    _lesson = await conn.execute("SELECT * FROM lessons WHERE lesson_id = %s", (lesson_id,))
    lesson = await _lesson.fetchone()
    if not lesson:
        raise HTTPException(404, "lesson not found")

    cur = await conn.execute(
        "SELECT id, ord, skill, paragraph_refs_json, prompt, choices_json "
        "FROM mc_questions WHERE lesson_id = %s ORDER BY ord",
        (lesson_id,),
    )
    mc_rows = await cur.fetchall()
    cur = await conn.execute(
        "SELECT id, ord, skill, prompt, guidance_json "
        "FROM open_questions WHERE lesson_id = %s ORDER BY ord",
        (lesson_id,),
    )
    or_rows = await cur.fetchall()
    cur = await conn.execute(
        "SELECT lesson_id FROM lessons WHERE book_id = %s AND ord = %s",
        (lesson["book_id"], lesson["ord"] + 1),
    )
    next_lesson = await cur.fetchone()

    progress = None
    if student is not None:
        await touch_student(conn, student)
        cur = await conn.execute(
            "SELECT mc_answers_json, open_responses_json, completed "
            "FROM lesson_progress WHERE student_id = %s AND lesson_id = %s",
            (student, lesson_id),
        )
        progress = await cur.fetchone()

    return {
        "lesson_id": lesson["lesson_id"],
        "book_id": lesson["book_id"],
        "chapter": lesson["chapter"],
        "part": lesson["part"],
        "parts_in_chapter": lesson["parts_in_chapter"],
        "title": lesson["title"],
        "word_count": lesson["word_count"],
        "estimated_minutes": lesson["estimated_minutes"],
        "paragraphs": json.loads(lesson["paragraphs_json"]),
        "next_lesson_id": next_lesson["lesson_id"] if next_lesson else None,
        # correct/rationale withheld until grading, see POST .../progress
        "questions": [
            {
                "id": strip_prefix(r["id"], lesson_id),
                "skill": r["skill"],
                "paragraph_refs": json.loads(r["paragraph_refs_json"]),
                "prompt": r["prompt"],
                "choices": json.loads(r["choices_json"]),
            }
            for r in mc_rows
        ],
        "open_response_questions": [
            {
                "id": strip_prefix(r["id"], lesson_id),
                "skill": r["skill"],
                "prompt": r["prompt"],
                "guidance": json.loads(r["guidance_json"]),
            }
            for r in or_rows
        ],
        "progress": {
            "mc_answers": json.loads(progress["mc_answers_json"]),
            "open_responses": json.loads(progress["open_responses_json"]),
            "completed": bool(progress["completed"]),
        } if progress else None,
    }


class ProgressUpdate(BaseModel):
    student: int
    mc_answers: dict[str, str] = {}
    open_responses: dict[str, str] = {}
    completed: bool = False


@app.post("/api/lessons/{lesson_id}/progress")
async def update_progress(lesson_id: str, body: ProgressUpdate, conn=Depends(get_db)):
    await touch_student(conn, body.student)

    cur = await conn.execute("SELECT * FROM lessons WHERE lesson_id = %s", (lesson_id,))
    lesson = await cur.fetchone()
    if not lesson:
        raise HTTPException(404, "lesson not found")

    await conn.execute(
        """
        INSERT INTO lesson_progress (student_id, lesson_id, mc_answers_json, open_responses_json, completed, updated_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (student_id, lesson_id) DO UPDATE SET
            mc_answers_json = EXCLUDED.mc_answers_json,
            open_responses_json = EXCLUDED.open_responses_json,
            completed = EXCLUDED.completed,
            updated_at = now()
        """,
        (body.student, lesson_id, json.dumps(body.mc_answers), json.dumps(body.open_responses),
         int(body.completed)),
    )

    cur = await conn.execute(
        "SELECT id, correct, rationale FROM mc_questions WHERE lesson_id = %s ORDER BY ord",
        (lesson_id,),
    )
    mc_rows = await cur.fetchall()
    grading = {}
    for r in mc_rows:
        qid = strip_prefix(r["id"], lesson_id)
        given = body.mc_answers.get(qid)
        grading[qid] = {
            "correct": given is not None and given == r["correct"],
            "correct_answer": r["correct"],
            "rationale": r["rationale"],
        }

    next_lesson_id = None
    if body.completed:
        cur = await conn.execute(
            "SELECT lesson_id FROM lessons WHERE book_id = %s AND ord = %s",
            (lesson["book_id"], lesson["ord"] + 1),
        )
        next_row = await cur.fetchone()
        next_lesson_id = next_row["lesson_id"] if next_row else None
        advance_to = next_lesson_id or lesson_id
        await conn.execute(
            """
            INSERT INTO bookmarks (student_id, book_id, current_lesson_id, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (student_id, book_id) DO UPDATE SET
                current_lesson_id = EXCLUDED.current_lesson_id,
                updated_at = now()
            """,
            (body.student, lesson["book_id"], advance_to),
        )

    await conn.commit()
    return {"grading": grading, "next_lesson_id": next_lesson_id}


# ---------------------------------------------------------- explain & vocab --

class ExplainRequest(BaseModel):
    student: int
    lesson_id: str
    selected_text: str
    context: str


@app.post("/api/explain")
async def explain(body: ExplainRequest, 
                  conn=Depends(get_db), 
                  client = Depends(get_openai_client))->StreamingResponse:
    await touch_student(conn, body.student)
    selected = body.selected_text.strip()
    if not selected:
        raise HTTPException(400, "selected_text is required")
    try:
        # return StreamingResponse(llm.explain_selection(client, selected, body.context)
        #                          , media_type="application/x-ndjson")
        return StreamingResponse(llm.explain_selection_sse(client, selected, body.context)
                                 , media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except llm.ExplainError as exc:
        raise HTTPException(502, str(exc))


class VocabularySave(BaseModel):
    student: int
    lesson_id: str
    selected_text: str
    meaning: str
    context_note: str


@app.post("/api/vocabulary")
async def save_vocabulary(body: VocabularySave, conn=Depends(get_db)):
    await touch_student(conn, body.student)
    cur = await conn.execute("SELECT lesson_id FROM lessons WHERE lesson_id = %s", (body.lesson_id,))
    lesson = await cur.fetchone()
    if not lesson:
        raise HTTPException(404, "lesson not found")
    cur = await conn.execute(
        """
        INSERT INTO vocabulary (student_id, lesson_id, selected_text, meaning, context_note)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, lesson_id, selected_text, meaning, context_note, created_at
        """,
        (body.student, body.lesson_id, body.selected_text.strip(), body.meaning, body.context_note),
    )
    row = await cur.fetchone()
    await conn.commit()
    return row


@app.get("/api/vocabulary")
async def list_vocabulary(student: Optional[int] = None, conn=Depends(get_db)):
    await touch_student(conn, student)
    cur = await conn.execute(
        """
        SELECT v.id, v.lesson_id, v.selected_text, v.meaning, v.context_note, v.created_at,
               l.title AS lesson_title
        FROM vocabulary v JOIN lessons l ON l.lesson_id = v.lesson_id
        WHERE v.student_id = %s
        ORDER BY v.created_at DESC
        """,
        (student,),
    )
    return await cur.fetchall()


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
