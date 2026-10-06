"""Loads content/books/*.json and content/lessons/<book_id>/*.json into
Postgres. JSON files are the source of truth; the database is a rebuildable
cache. Safe to re-run: every write is an idempotent upsert.

Usage (run from ela_read/):
    python -m app.content_loader --all
    python -m app.content_loader fwtbt
"""
import argparse, json, sys, asyncio
import selectors
from pathlib import Path

from . import db

APP_DIR = Path(__file__).resolve().parent
CONTENT_DIR = APP_DIR / "content"
BOOKS_DIR = CONTENT_DIR / "books"
LESSONS_DIR = CONTENT_DIR / "lessons"


def _load_book(conn, book_path: Path) -> str:
    book = json.loads(book_path.read_text(encoding="utf-8"))
    book_id = book["book_id"]
    conn.execute(
        """
        INSERT INTO books (book_id, title, author, source_pdf, chapter_count)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (book_id) DO UPDATE SET
            title = EXCLUDED.title,
            author = EXCLUDED.author,
            source_pdf = EXCLUDED.source_pdf,
            chapter_count = EXCLUDED.chapter_count
        """,
        (book_id, book["title"], book["author"], book.get("source_pdf"),
         book.get("chapter_count")),
    )
    return book_id


def _load_lessons(conn, book_id: str) -> int:
    lesson_dir = LESSONS_DIR / book_id
    if not lesson_dir.is_dir():
        return 0

    lesson_files = sorted(lesson_dir.glob("*.json"))
    lessons = [json.loads(p.read_text(encoding="utf-8")) for p in lesson_files]
    # Global reading order within the book: by chapter, then part.
    lessons.sort(key=lambda l: (l["chapter"], l["part"]))

    for ord_, lesson in enumerate(lessons, start=1):
        lesson_id = lesson["lesson_id"]
        conn.execute(
            """
            INSERT INTO lessons
                (lesson_id, book_id, chapter, part, parts_in_chapter, title,
                 word_count, estimated_minutes, ord, paragraphs_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (lesson_id) DO UPDATE SET
                chapter = EXCLUDED.chapter,
                part = EXCLUDED.part,
                parts_in_chapter = EXCLUDED.parts_in_chapter,
                title = EXCLUDED.title,
                word_count = EXCLUDED.word_count,
                estimated_minutes = EXCLUDED.estimated_minutes,
                ord = EXCLUDED.ord,
                paragraphs_json = EXCLUDED.paragraphs_json
            """,
            (lesson_id, book_id, lesson["chapter"], lesson["part"],
             lesson["parts_in_chapter"], lesson["title"], lesson["word_count"],
             lesson["estimated_minutes"], ord_,
             json.dumps(lesson["paragraphs"], ensure_ascii=False)),
        )

        # Question ids are derived from lesson_id + ord (not the JSON's own
        # "q1"/"or1" ids) so the DB primary key never depends on how the
        # content file happens to number things.
        conn.execute("DELETE FROM mc_questions WHERE lesson_id = %s", (lesson_id,))
        for q_ord, q in enumerate(lesson.get("questions", []), start=1):
            conn.execute(
                """
                INSERT INTO mc_questions
                    (id, lesson_id, ord, skill, paragraph_refs_json, prompt,
                     choices_json, correct, rationale)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (f"{lesson_id}-mc{q_ord}", lesson_id, q_ord, q["skill"],
                 json.dumps(q["paragraph_refs"]), q["prompt"],
                 json.dumps(q["choices"], ensure_ascii=False),
                 q["correct"], q["rationale"]),
            )

        conn.execute("DELETE FROM open_questions WHERE lesson_id = %s", (lesson_id,))
        for q_ord, q in enumerate(lesson.get("open_response_questions", []), start=1):
            conn.execute(
                """
                INSERT INTO open_questions
                    (id, lesson_id, ord, skill, prompt, guidance_json)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (f"{lesson_id}-or{q_ord}", lesson_id, q_ord, q["skill"],
                 q["prompt"], json.dumps(q["guidance"], ensure_ascii=False)),
            )

    return len(lessons)


def load_book(conn, book_path: Path) -> None:
    book_id = _load_book(conn, book_path)
    n = _load_lessons(conn, book_id)
    conn.commit()
    print(f"{book_id}: {n} lesson(s) loaded")


def main(argv=None) -> bool:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_ids", nargs="*", help="book_id(s) to load, e.g. fwtbt")
    parser.add_argument("--all", action="store_true", help="load every book under content/books/")
    args = parser.parse_args(argv)

    if not args.all and not args.book_ids:
        parser.error("pass --all or one or more book ids")

    if args.all:
        book_paths = sorted(BOOKS_DIR.glob("*.json"))
    else:
        book_paths = [BOOKS_DIR / f"{b}.json" for b in args.book_ids]

    missing = [p for p in book_paths if not p.is_file()]
    for p in missing:
        print(f"skipping {p}: not found", file=sys.stderr)
    book_paths = [p for p in book_paths if p.is_file()]

    if not book_paths:
        print("nothing to load", file=sys.stderr)
        return False

    
    conn = db.connect_sync()
    try:
        for p in book_paths:
            load_book(conn, p)
    finally:
        conn.close()

    return not missing


if __name__ == "__main__":
    main()