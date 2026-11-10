"""Per-cycle collection observability - distinguishes "healthy, genuinely
zero new posts this cycle" from "something's actually broken" at the
individual-forum level.

Before this module, `UndergroundForumSource.fetch()` collapsed three very
different outcomes into the same silent empty-list-for-this-site result:
the feed being unreachable this cycle, the feed being reachable but every
item failing to parse (e.g. the site changed its RSS/HTML structure), and
the feed being reachable with genuinely nothing new to report. All three
read identically in `incidents.jsonl` (nothing appended) and in the old
per-cycle log line (just a fetched/new count for the whole 36-site pool,
not broken out per site) - a dead parser selector was indistinguishable
from a quiet night. See the 2026-11 collection-reliability audit this
closes gap #4 of.

`record_cycle` is fed `UndergroundForumSource.last_cycle_health` (see that
class's `fetch()`) merged with the dedup counts `pipeline.run_once`
already computes for the source as a whole - per-site new/duplicate
counts aren't tracked separately since dedup runs once over the whole
source's combined incident batch, not per site.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class CollectionHealthStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_health (
                site TEXT PRIMARY KEY,
                status TEXT,
                last_attempt TEXT,
                last_success TEXT,
                posts_discovered INTEGER DEFAULT 0,
                new_records INTEGER DEFAULT 0,
                duplicates INTEGER DEFAULT 0,
                parse_errors INTEGER DEFAULT 0,
                http_errors INTEGER DEFAULT 0
            )
            """
        )
        self._conn.commit()

    def record(self, site: str, snapshot: dict) -> None:
        """`snapshot` keys: status, last_attempt, last_success (optional -
        only set on a healthy fetch), posts_discovered, new_records,
        duplicates, parse_errors, http_errors - all optional except
        status/last_attempt, matching `UndergroundForumSource.
        last_cycle_health`'s per-site dict shape.
        """
        self._conn.execute(
            """
            INSERT INTO collection_health
                (site, status, last_attempt, last_success, posts_discovered,
                 new_records, duplicates, parse_errors, http_errors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(site) DO UPDATE SET
                status=excluded.status,
                last_attempt=excluded.last_attempt,
                last_success=COALESCE(excluded.last_success, collection_health.last_success),
                posts_discovered=excluded.posts_discovered,
                new_records=excluded.new_records,
                duplicates=excluded.duplicates,
                parse_errors=excluded.parse_errors,
                http_errors=excluded.http_errors
            """,
            (
                site,
                snapshot.get("status", "unknown"),
                snapshot.get("last_attempt"),
                snapshot.get("last_success"),
                snapshot.get("posts_discovered", 0),
                snapshot.get("new_records", 0),
                snapshot.get("duplicates", 0),
                snapshot.get("parse_errors", 0),
                snapshot.get("http_errors", 0),
            ),
        )
        self._conn.commit()

    def record_cycle(self, per_site: dict[str, dict]) -> None:
        for site, snapshot in per_site.items():
            self.record(site, snapshot)

    def report(self) -> list[dict]:
        cols = [
            "site", "status", "last_attempt", "last_success", "posts_discovered",
            "new_records", "duplicates", "parse_errors", "http_errors",
        ]
        rows = self._conn.execute(
            f"SELECT {', '.join(cols)} FROM collection_health ORDER BY site"
        ).fetchall()
        return [dict(zip(cols, row)) for row in rows]

    def close(self) -> None:
        self._conn.close()
