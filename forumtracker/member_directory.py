"""Global username pool, seeded from a source active member-search
couldn't reach: each forum's own public member DIRECTORY (not search).

`identity_correlation.py` already proved active guest member-*search*
doesn't work on this pool (login wall / 404 everywhere, 2026-09-11). A
member *directory* - the plain "browse all members" page every XenForo
install ships - is a different feature with a different guest-access
default, verified live and separately (2026-09-11):

    /members/                14/21 forums 403/404, 8 return 200 with a
                              real, paginated, alphabetically-indexed
                              member list (carders.biz, leetforums,
                              xreactor, blackhatpakistan, forumteam.top,
                              niflheim, piratebuhta, warriors)

MyBB's equivalent (`memberlist.php`, tried on leakforum.io) is a bare
search FORM by default - no listing until a query is submitted - a
different-enough shape (and, on that one forum, 4119 result pages for a
single letter) that it's deliberately left out of scope here rather than
half-implemented; see this module's `NOT_YET_SUPPORTED` note.

**Why this needs to be resumable, not a one-shot crawl:** the 8 working
forums range from a few hundred members (xreactor: single-page alpha
buckets) to 179,007 (piratebuhta) - a full crawl of the largest ones is
thousands of pages. `MemberDirectoryStore` checkpoints (forum, alpha
bucket, page) after every fetch, so `crawl_batch()` can be called once
per pipeline cycle (a few pages per forum at a time) and pick up exactly
where the last cycle left off - the same "small bounded increment every
cycle" shape as forum_stats.py's snapshots, needed to fit a CI job's time
budget rather than one call blocking for hours.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
TIMEOUT = (8, 15)

# Verified live 2026-09-11: guest GET on {homepage}/members/ returns a real
# alphabetical member directory (not a login wall or 404). Forums NOT here
# (turkhackteam, spyhackerz, bdfclub, mmgp, probiv, blackbones, nodo313)
# all returned 403 on this exact check.
MEMBER_DIRECTORY_POOL: dict[str, str] = {
    "carders.biz": "https://carders.biz",
    "leetforums": "https://leetforums.cc",
    "xreactor": "https://xreactor.org",
    "blackhatpakistan": "https://blackhatpakistan.net",
    "forumteam.top": "https://forumteam.top",
    "niflheim": "https://niflheim.world",
    "piratebuhta": "https://piratebuhta.pw",
    "warriors": "https://warriors.to",
    # global expansion batch (2026-09-12): of the 6 new FORUM_POOL entries added
    # alongside this, only cardinggame.site's /members/ returned a real,
    # guest-accessible alphabetical listing (200 + genuine profile links) - the
    # other 5 either 403 (carding.pw, validmarket.io, darkwebmafias.net), 404
    # (forum.antichat.io), or serve a different "notable members" leaderboard
    # template instead of the alpha-indexed listing this crawler expects
    # (darknetarmy.st) - all verified live, not guessed.
    "cardinggame": "https://cardinggame.site",
}

# MyBB's memberlist.php (tried on leakforum.io) defaults to a bare search
# form, not a listing - needs a submitted username_match=begins&username=X
# query per letter, and paginates in the thousands even per letter. Real,
# but a different enough shape from the XenForo alpha-index below that it's
# tracked as future work rather than guessed at.
NOT_YET_SUPPORTED = ["leakforum.io (MyBB memberlist needs a submitted query, not a bare GET)"]

ALPHA_BUCKETS: tuple[str, ...] = ("0-9",) + tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

_MEMBER_LINK_RE = re.compile(r'href="/members/([a-zA-Z0-9_\-.]+)\.(\d+)/"')


def _has_next_page(html: str, alpha: str, next_page: int) -> bool:
    return f"?alpha={alpha}&page={next_page}" in html or f"alpha={alpha}&amp;page={next_page}" in html


def extract_members(html: str) -> list[dict]:
    """Returns [{"username": ..., "member_id": ...}, ...], deduplicated -
    each profile link appears twice per row (avatar + name) in the real
    markup verified live.
    """
    seen: dict[str, str] = {}
    for username, member_id in _MEMBER_LINK_RE.findall(html):
        seen.setdefault(username, member_id)
    return [{"username": u, "member_id": mid} for u, mid in seen.items()]


def fetch_alpha_page(homepage: str, alpha: str, page: int) -> Optional[str]:
    url = f"{homepage.rstrip('/')}/members/?alpha={alpha}&page={page}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.RequestException:
        log.debug("member_directory: fetch failed for %s", url, exc_info=True)
        return None


@dataclass
class CrawlProgress:
    forum: str
    alpha_index: int
    page: int
    done: bool


class MemberDirectoryStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS members (
                forum TEXT,
                username_norm TEXT,
                display_name TEXT,
                profile_url TEXT,
                first_seen TEXT,
                last_seen TEXT,
                PRIMARY KEY (forum, username_norm)
            );
            CREATE TABLE IF NOT EXISTS crawl_progress (
                forum TEXT PRIMARY KEY,
                alpha_index INTEGER DEFAULT 0,
                page INTEGER DEFAULT 1,
                done INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """
        )
        self._conn.commit()

    def get_progress(self, forum: str) -> CrawlProgress:
        row = self._conn.execute(
            "SELECT alpha_index, page, done FROM crawl_progress WHERE forum = ?", (forum,)
        ).fetchone()
        if row is None:
            return CrawlProgress(forum, 0, 1, False)
        return CrawlProgress(forum, row[0], row[1], bool(row[2]))

    def save_progress(self, progress: CrawlProgress) -> None:
        self._conn.execute(
            """
            INSERT INTO crawl_progress (forum, alpha_index, page, done, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(forum) DO UPDATE SET
                alpha_index=excluded.alpha_index, page=excluded.page,
                done=excluded.done, updated_at=excluded.updated_at
            """,
            (progress.forum, progress.alpha_index, progress.page, int(progress.done),
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def record_members(self, forum: str, members: list[dict]) -> int:
        """Upserts a batch of {"username", "member_id"} dicts, returns how
        many were genuinely new (not just a re-crawl re-touching the same
        row - a member seen again just bumps last_seen).
        """
        now = datetime.now(timezone.utc).isoformat()
        new_count = 0
        for m in members:
            username_norm = m["username"].strip().lower()
            if not username_norm:
                continue
            existed = self._conn.execute(
                "SELECT 1 FROM members WHERE forum = ? AND username_norm = ?", (forum, username_norm)
            ).fetchone()
            if existed is None:
                new_count += 1
            self._conn.execute(
                """
                INSERT INTO members (forum, username_norm, display_name, profile_url, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(forum, username_norm) DO UPDATE SET last_seen=excluded.last_seen
                """,
                (forum, username_norm, m["username"], f"/members/{m['username']}.{m['member_id']}/", now, now),
            )
        self._conn.commit()
        return new_count

    def count(self, forum: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM members WHERE forum = ?", (forum,)
        ).fetchone()[0]

    def total_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]

    def counts_by_forum(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT forum, COUNT(*) FROM members GROUP BY forum ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {forum: count for forum, count in rows}

    def forums_for_username(self, username: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT forum FROM members WHERE username_norm = ? ORDER BY forum",
            (username.strip().lower(),),
        ).fetchall()
        return [r[0] for r in rows]

    def cross_forum_registered(self, min_forums: int = 2) -> list[dict]:
        """Usernames with a registered account on 2+ crawled forums -
        a WEAKER signal than identity_correlation.IdentityStore's matches
        (those require an actual observed POST on each forum; this only
        means the account exists, it may never have posted anything we've
        seen). Kept as a separate, clearly-labeled query rather than
        merged into IdentityStore's confidence-carrying matches, so a
        caller always knows which kind of evidence it's looking at.
        """
        rows = self._conn.execute(
            """
            SELECT username_norm, GROUP_CONCAT(DISTINCT display_name), GROUP_CONCAT(DISTINCT forum)
            FROM members
            GROUP BY username_norm
            HAVING COUNT(DISTINCT forum) >= ?
            ORDER BY COUNT(DISTINCT forum) DESC, username_norm
            """,
            (min_forums,),
        ).fetchall()
        return [
            {"username": display.split(","), "forums": sorted(forums.split(","))}
            for _, display, forums in rows
        ]

    def close(self) -> None:
        self._conn.close()


def crawl_batch(store: MemberDirectoryStore, pool: dict[str, str] = None, pages_per_forum: int = 3) -> dict:
    """One bounded increment of the resumable crawl: up to `pages_per_forum`
    page-fetches per forum, continuing from each forum's saved checkpoint.
    Forums already fully crawled (`done`) are skipped for free. Safe to
    call every pipeline cycle - see module docstring.
    """
    pool = MEMBER_DIRECTORY_POOL if pool is None else pool
    report: dict[str, dict] = {}
    for forum, homepage in pool.items():
        progress = store.get_progress(forum)
        if progress.done:
            report[forum] = {"pages_fetched": 0, "done": True, "total_members": store.count(forum)}
            continue

        pages_fetched = 0
        new_members = 0
        while pages_fetched < pages_per_forum and not progress.done:
            alpha = ALPHA_BUCKETS[progress.alpha_index]
            html = fetch_alpha_page(homepage, alpha, progress.page)
            pages_fetched += 1
            if html is None:
                # Transient failure - stop this forum for this batch, retry
                # the same (alpha, page) next cycle rather than skipping it.
                break

            members = extract_members(html)
            new_members += store.record_members(forum, members)

            if _has_next_page(html, alpha, progress.page + 1):
                progress = CrawlProgress(forum, progress.alpha_index, progress.page + 1, False)
            elif progress.alpha_index + 1 < len(ALPHA_BUCKETS):
                progress = CrawlProgress(forum, progress.alpha_index + 1, 1, False)
            else:
                progress = CrawlProgress(forum, progress.alpha_index, progress.page, True)

            store.save_progress(progress)

        report[forum] = {
            "pages_fetched": pages_fetched,
            "new_members": new_members,
            "done": progress.done,
            "total_members": store.count(forum),
        }
    return report
