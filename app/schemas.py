from datetime import datetime

from pydantic import BaseModel

class StudentCreate(BaseModel):
    name: str


class ExplainRequest(BaseModel):
    student: int
    lesson_id: str
    selected_text: str
    context: str

class ProgressUpdate(BaseModel):
    student: int
    mc_answers: dict[str, str] = {}
    open_responses: dict[str, str] = {}
    completed: bool = False


class VocabularyList(BaseModel):
    id: int 
    lesson_id: int
    selected_text: str 
    meaning:str 
    context_note:str 
    created_at:datetime
    lesson_title:str 


class Book(BaseModel):
    book_id: int 
    title: str 
    author:str 
    chapter_count: int

class BookList(BaseModel):
    pass