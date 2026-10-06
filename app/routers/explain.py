
from fastapi import APIRouter, Depends, Request
from app.deps import *
from fastapi.exceptions import HTTPException
from typing import Optional
import json
from fastapi.responses import StreamingResponse
from app import llm
from pydantic import BaseModel

from app.schemas import *
from app.llm import make_sse_packet

router= APIRouter()

@router.post("/api/explain")
async def explain(body: ExplainRequest, 
                  request: Request,
                  conn=Depends(get_db), 
                  client = Depends(get_openai_client))->StreamingResponse:
    
    #Form cache_key_for the selected text and test 
    #check if cache_key present in table and return
    cache_key = make_explain_cache_key(body.student, body.selected_text)
    cache_result = await check_selected_text_in_cache(conn, cache_key)
    print(f"cache_result----{cache_result}")
    async def save_to_cache(result):
        async with request.app.state.pool.connection() as conn:
            await insert_seltext_meaning_in_cache(conn, cache_key, 
                                                    body.selected_text.lower().strip(), 
                                                    result)
    if not cache_result:
        print("No Cache"    )   
        await touch_student(conn, body.student)
        selected = body.selected_text.strip()
        if not selected:
            raise HTTPException(400, "selected_text is required")
        try:
            # return StreamingResponse(llm.explain_selection(client, selected, body.context)
            #                          , media_type="application/x-ndjson")
            return StreamingResponse(llm.explain_selection_sse(client, selected, body.context, on_done=save_to_cache)
                                        , media_type="text/event-stream",
                                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        except llm.ExplainError as exc:
            raise HTTPException(502, str(exc))

    return StreamingResponse(make_sse_packet("done", cache_result), 
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    


class VocabularySave(BaseModel):
    student: int
    lesson_id: str
    selected_text: str
    meaning: str
    context_note: str


@router.post("/api/vocabulary")
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


@router.get("/api/vocabulary", response_model=VocabularyList)
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