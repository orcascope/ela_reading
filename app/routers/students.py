from fastapi import APIRouter, Depends
from app.deps import get_db, touch_student, strip_prefix
from fastapi.exceptions import HTTPException
from typing import Optional
import json

from app.schemas import *

router= APIRouter(prefix="/students")


@router.post("/api/students")
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

@router.get("/api/books")
async def list_books(conn=Depends(get_db)):
    cur = await conn.execute("SELECT book_id, title, author, chapter_count FROM books ORDER BY title")
    return await cur.fetchall()


@router.get("/api/books/{book_id}/resume")
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

