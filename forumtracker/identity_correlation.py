"""Cross-forum username correlation ("v2" feature): the same nickname
posting on multiple pool forums is treated as one real actor operating
across platforms.

**Active, unauthenticated member-search against other forums does not
work on this pool** - verified live (2026-09-11) across all 21 XenForo/
MyBB forums. XenForo's `/search/member?q=` route either 404s (route
disabled) or resolves to a login wall (turkhackteam); its `/find-member`
autocomplete route 404s everywhere tried (needs a logged-in session +
CSRF token); MyBB's `member.php?action=search` returns an empty 200
without a real logged-in POST. Most underground forums deliberately gate
member search behind login specifically to resist this kind of automated
enumeration - this isn't a bug to work around, it's the forums' own
anti-OSINT posture.

**What genuinely works, with zero new network calls:** every incident this
pipeline already ingests carries `threat_actor.name` (the poster's forum
username) and `source.name` (which forum, as `"forum:<site_name>"`).
`IdentityStore` builds a (username, forum) index from that as incidents
arrive and flags any username that turns up on 2+ different forums as a
cross-platform match. This gets *stronger* over time as the pipeline keeps
polling - unlike a one-off active search limited to whichever forums
happen to be scrapeable at query time, every new cycle can surface new
matches from names we didn't have before. `backfill_from_feed()` seeds the
store from the existing `incidents.jsonl` - run once, it already finds 59
real matches from data already on disk (2026-09-11 run).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

# Role/placeholder names that trivially appear on every forum and would
# otherwise "match" as if they were one real actor - not usernames at all.
_GENERIC_NAMES = {
    "unknown", "admin", "administrator", "mod", "moderator", "system",
    "deleted", "guest", "anonymous", "support", "staff", "bot",
}


def normalize_username(name: str) -> Optional[str]:
    normalized = (name or "").strip().lower()
    if not normalized or normalized in _GENERIC_NAMES:
        return None
    return normalized


def _forum_from_source_name(source_name: str) -> Optional[str]:
    if not source_name or not source_name.startswith("forum:"):
        return None
    return source_name.split(":", 1)[1]


@dataclass
class CrossMatch:
    username_norm: str
    forums: list[str]
    display_names: list[str]
    first_matched_at: str


class IdentityStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS username_forums (
                username_norm TEXT,
                forum TEXT,
                display_name TEXT,
                first_seen TEXT,
                last_seen TEXT,
                PRIMARY KEY (username_norm, forum)
            );
            CREATE TABLE IF NOT EXISTS cross_matches (
                username_norm TEXT PRIMARY KEY,
                forums TEXT,
                first_matched_at TEXT
            );
            """
        )
        self._conn.commit()

    def _forums_for(self, username_norm: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT forum FROM username_forums WHERE username_norm = ? ORDER BY forum", (username_norm,)
        ).fetchall()
        return [r[0] for r in rows]

    def record(self, username: str, forum: str, seen_at: Optional[str] = None) -> Optional[CrossMatch]:
        """Records one (username, forum) sighting. Returns a CrossMatch iff
        this sighting is what pushed the username's distinct-forum count
        from 1 to 2+ for the FIRST time (i.e. a newly discovered
        cross-platform identity, not a re-confirmation of an already-known
        one) - callers use this to notify/emit exactly once per discovery.
        """
        username_norm = normalize_username(username)
        if username_norm is None or not forum:
            return None
        seen_at = seen_at or datetime.now(timezone.utc).isoformat()

        already_matched = self._conn.execute(
            "SELECT 1 FROM cross_matches WHERE username_norm = ?", (username_norm,)
        ).fetchone() is not None

        self._conn.execute(
            """
            INSERT INTO username_forums (username_norm, forum, display_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username_norm, forum) DO UPDATE SET
                display_name=excluded.display_name, last_seen=excluded.last_seen
            """,
            (username_norm, forum, username.strip(), seen_at, seen_at),
        )
        self._conn.commit()

        if already_matched:
            return None

        forums = self._forums_for(username_norm)
        if len(forums) < 2:
            return None

        self._conn.execute(
            "INSERT OR IGNORE INTO cross_matches (username_norm, forums, first_matched_at) VALUES (?, ?, ?)",
            (username_norm, json.dumps(forums), seen_at),
        )
        self._conn.commit()

        display_names = [
            r[0] for r in self._conn.execute(
                "SELECT DISTINCT display_name FROM username_forums WHERE username_norm = ?", (username_norm,)
            ).fetchall()
        ]
        return CrossMatch(username_norm, forums, display_names, seen_at)

    def record_incidents(self, incidents: Iterable[dict]) -> list[CrossMatch]:
        """Same as record(), applied to a batch of Incident dicts - the
        normal way this gets called, once per pipeline cycle on that
        cycle's new incidents. Never raises: a malformed incident is
        logged and skipped, same discipline as Source.fetch().
        """
        new_matches: list[CrossMatch] = []
        for inc in incidents:
            if not isinstance(inc, dict):
                log.warning("identity_correlation: skipping non-dict incident %r", inc)
                continue
            try:
                forum = _forum_from_source_name((inc.get("source") or {}).get("name") or "")
                username = (inc.get("threat_actor") or {}).get("name") or ""
                if forum is None:
                    continue
                match = self.record(username, forum, seen_at=inc.get("detected_at"))
                if match is not None:
                    new_matches.append(match)
            except Exception:
                log.exception("identity_correlation: failed to record incident %s", inc.get("incident_id"))
        return new_matches

    def all_matches(self) -> list[CrossMatch]:
        """`cross_matches.forums` is only a snapshot of the forum list at
        the moment a username FIRST crossed 2 forums - a later 3rd/4th
        forum sighting updates `username_forums` (via `record()`) without
        rewriting that snapshot, since `record()` intentionally returns
        early once a username is already matched (see its docstring: fire
        once per discovery, not once per sighting). So the forum list
        reported here is always recomputed live from `username_forums`
        instead of trusting the stale snapshot.
        """
        rows = self._conn.execute(
            "SELECT username_norm, first_matched_at FROM cross_matches ORDER BY first_matched_at DESC"
        ).fetchall()
        out = []
        for username_norm, first_matched_at in rows:
            forums = self._forums_for(username_norm)
            display_names = [
                r[0] for r in self._conn.execute(
                    "SELECT DISTINCT display_name FROM username_forums WHERE username_norm = ?", (username_norm,)
                ).fetchall()
            ]
            out.append(CrossMatch(username_norm, forums, display_names, first_matched_at))
        return out

    def close(self) -> None:
        self._conn.close()


def backfill_from_feed(store: IdentityStore, feed_path: Path) -> list[CrossMatch]:
    """One-time seed from an existing incidents.jsonl (or any JSONL of
    Incident dicts) - lets the correlator see everything the pipeline has
    already collected instead of only matches found from this point
    forward. Skips malformed lines rather than failing the whole backfill.
    """
    if not feed_path.exists():
        return []
    incidents = []
    with feed_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                incidents.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return store.record_incidents(incidents)
