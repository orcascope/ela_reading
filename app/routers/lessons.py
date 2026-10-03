from fastapi import APIRouter, Depends
from app.deps import get_db, touch_student, strip_prefix
from fastapi.exceptions import HTTPException
from typing import Optional
import json

from app.schemas import *

router= APIRouter(prefix="/lessons")


@router.get("/api/lessons/{lesson_id}")
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


@router.post("/api/lessons/{lesson_id}/progress")
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