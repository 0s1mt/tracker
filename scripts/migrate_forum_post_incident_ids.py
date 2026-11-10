"""One-time migration for the incident_id scheme change in schema.py
(2026-11-07): `forum_post` incidents from underground_forums.py and
browser_forum.py now hash on their own thread permalink
(`source.name|evidence.post_url`) instead of `source.name|threat_actor.
name|victim.name|published_at` - see schema.py's `stable_key` docstring
for why (RSS <pubDate> is often last-reply time, so a bumped thread used
to mint a brand new incident_id every time it got a new reply).

Without this migration, the very next --once/--loop cycle after deploying
that change would recompute a DIFFERENT incident_id for every
already-collected forum_post whose RSS entry it refetches, SeenStore
would not recognize the new ID as already-seen, and every one of those
threads would be re-emitted as a false "new incident" (and a false
Telegram alert, for anything alert-worthy) in one burst.

This script closes that gap the safe way: for every existing forum_post
record in data/incidents.jsonl that has a post_url, it recomputes the
NEW-scheme ID (replicating schema.py's exact formula) and inserts it into
data/seen.sqlite3 - additively, `INSERT OR IGNORE`, never deleting or
rewriting anything already there. incidents.jsonl itself is read-only
here, never touched. Safe to re-run (idempotent).

Usage: python -m scripts.migrate_forum_post_incident_ids
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INCIDENTS_FEED = DATA_DIR / "incidents.jsonl"
SEEN_DB = DATA_DIR / "seen.sqlite3"


def _new_scheme_id(source_name: str, post_url: str) -> str:
    basis = f"{source_name}|{post_url}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def migrate() -> dict:
    conn = sqlite3.connect(SEEN_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (incident_id TEXT PRIMARY KEY, first_seen TEXT)")

    eligible = 0
    skipped_no_post_url = 0
    malformed_lines = 0
    inserted = 0

    with INCIDENTS_FEED.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                malformed_lines += 1
                continue

            if rec.get("category") != "forum_post":
                continue
            source_name = (rec.get("source") or {}).get("name")
            if not source_name or not str(source_name).startswith("forum:"):
                continue
            post_url = (rec.get("evidence") or {}).get("post_url")
            if not post_url:
                skipped_no_post_url += 1
                continue

            eligible += 1
            new_id = _new_scheme_id(source_name, post_url)
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen (incident_id, first_seen) VALUES (?, ?)",
                (new_id, rec.get("detected_at")),
            )
            if cur.rowcount:
                inserted += 1

    conn.commit()
    conn.close()
    return {
        "eligible_forum_post_records": eligible,
        "new_scheme_ids_inserted": inserted,
        "already_present": eligible - inserted,
        "skipped_no_post_url": skipped_no_post_url,
        "malformed_lines": malformed_lines,
    }


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2))
