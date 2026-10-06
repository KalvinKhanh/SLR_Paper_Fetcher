"""Small SQLite catalog: DOI resolution, file ownership, and download states."""

import json
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from config import settings
from paper_io import public_url, safe_message, valid_pdf_file

STATES = {
    "PENDING", "PROCESSING", "OPEN_ACCESS", "LOGIN_REQUIRED", "AUTHENTICATED",
    "SESSION_EXPIRED", "INSTITUTION_ACCESS", "NO_ACCESS", "MFA_REQUIRED",
    "CAPTCHA_REQUIRED", "INVALID_DOI", "INVALID_PDF", "RESOLUTION_FAILED",
    "DOWNLOADED", "FAILED", "CANCELLED",
}


class PaperStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS papers (
                    doi TEXT PRIMARY KEY, status TEXT NOT NULL, resolved_url TEXT,
                    hostname TEXT, provider TEXT, filename TEXT, file_path TEXT,
                    source TEXT, message TEXT, metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, doi TEXT NOT NULL, status TEXT NOT NULL,
                    message TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS papers_filename ON papers(filename);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, doi):
        with self.connect() as db:
            row = db.execute("SELECT * FROM papers WHERE doi=?", (doi,)).fetchone()
        return dict(row) if row else None

    def update(self, doi, status, **fields):
        if status not in STATES:
            raise ValueError("Trạng thái bài báo không hợp lệ.")
        now = datetime.now(timezone.utc).isoformat()
        if "resolved_url" in fields:
            fields["resolved_url"] = public_url(fields["resolved_url"])
        if "message" in fields:
            fields["message"] = safe_message(fields["message"])
        if "metadata" in fields:
            fields["metadata"] = json.dumps(fields["metadata"], ensure_ascii=False)
        allowed = {"resolved_url", "hostname", "provider", "filename", "file_path", "source", "message", "metadata"}
        if set(fields) - allowed:
            raise ValueError("Trường dữ liệu bài báo không hợp lệ.")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO papers(doi,status,updated_at) VALUES(?,?,?)", (doi, status, now))
            names = ["status", "updated_at"] + list(fields)
            db.execute("UPDATE papers SET " + ",".join(name + "=?" for name in names) + " WHERE doi=?",
                       [status, now] + list(fields.values()) + [doi])
            db.execute("INSERT INTO events(doi,status,message,created_at) VALUES(?,?,?,?)",
                       (doi, status, fields.get("message", ""), now))

    def downloaded(self, doi):
        record = self.get(doi)
        return record if record and record["status"] == "DOWNLOADED" and valid_pdf_file(record["file_path"] or "") else None

    def file_owner(self, path):
        with self.connect() as db:
            row = db.execute("SELECT doi FROM papers WHERE file_path=? COLLATE NOCASE", (str(Path(path).resolve()),)).fetchone()
        return row["doi"] if row else None

    def list(self, limit=200):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM papers ORDER BY updated_at DESC LIMIT ?", (limit,))]


@lru_cache(maxsize=4)
def _store(path):
    return PaperStore(path)


_STORE_LOCK = Lock()


def get_store():
    # lru_cache permits simultaneous calls on a cache miss. Serialize catalog
    # initialization so scan workers do not race PRAGMA/schema creation.
    with _STORE_LOCK:
        return _store(str(settings.DB_PATH.resolve()))
