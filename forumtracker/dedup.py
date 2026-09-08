"""SQLite-backed "have we already emitted this incident_id" store.

Kept deliberately tiny (stdlib sqlite3 only) so the tracker has no external
state dependency beyond a single file on disk.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class SeenStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS seen (incident_id TEXT PRIMARY KEY, first_seen TEXT)"
        )
        self._conn.commit()

    def filter_new(self, incidents: list[dict]) -> list[dict]:
        new = []
        for inc in incidents:
            cur = self._conn.execute(
                "SELECT 1 FROM seen WHERE incident_id = ?", (inc["incident_id"],)
            )
            if cur.fetchone() is None:
                new.append(inc)
        return new

    def mark_seen(self, incidents: list[dict]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen (incident_id, first_seen) VALUES (?, ?)",
            [(inc["incident_id"], inc["detected_at"]) for inc in incidents],
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
