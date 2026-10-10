"""Historial persistente de operaciones (SQLite, solo biblioteca estándar)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

# Estados posibles de una operación. "unverified" significa que se intentó pero no se pudo comprobar.
STATUSES = ("planned", "running", "done", "verified", "unverified", "failed", "skipped", "cancelled")


class OperationLog:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                project TEXT,
                category TEXT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT
            )"""
        )
        self._conn.commit()

    def record(self, action: str, status: str, *, project: str = "", category: str = "", detail=None) -> int:
        if status not in STATUSES:
            raise ValueError(f"estado desconocido: {status}")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO operations (ts, project, category, action, status, detail) VALUES (?,?,?,?,?,?)",
                (time.time(), project, category, action, status, json.dumps(detail, ensure_ascii=False, default=str)),
            )
            self._conn.commit()
            return cur.lastrowid

    def update(self, op_id: int, status: str, detail=None) -> None:
        if status not in STATUSES:
            raise ValueError(f"estado desconocido: {status}")
        with self._lock:
            if detail is None:
                self._conn.execute("UPDATE operations SET status=? WHERE id=?", (status, op_id))
            else:
                self._conn.execute(
                    "UPDATE operations SET status=?, detail=? WHERE id=?",
                    (status, json.dumps(detail, ensure_ascii=False, default=str), op_id),
                )
            self._conn.commit()

    def recent(self, limit: int = 100, project: str | None = None) -> list[dict]:
        q = "SELECT id, ts, project, category, action, status, detail FROM operations"
        args: tuple = ()
        if project is not None:
            q += " WHERE project=?"
            args = (project,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(q, args + (limit,)).fetchall()
        out = []
        for r in rows:
            out.append(dict(id=r[0], ts=r[1], project=r[2], category=r[3], action=r[4], status=r[5],
                            detail=json.loads(r[6]) if r[6] else None))
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
