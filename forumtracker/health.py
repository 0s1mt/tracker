"""Forum liveness monitoring + address-rotation detection.

Underground forums rotate domains often (takedowns, DDoS, voluntary
migration) - unlike a "down for maintenance" blip, a rotated forum's old
address stops resolving/connecting entirely rather than serving an error
page. This module tells those two cases apart (`_classify` below) and
persists a small history so `python -m forumtracker.cli --health` can
report, for each pool entry: still up, temporarily erroring, or "probably
rotated - go find the new mirror". For that last case,
`discover.find_mirror_candidates` is the next step, reusing the existing
Telegram-mention signal from discover.py rather than standing up a
separate trusted-aggregator source.

Kept to stdlib + requests (already a dependency), matching the project's
"no infra beyond a Python process and a SQLite file" stance (see
dedup.py). A `.onion` homepage routes through the local Tor SOCKS proxy
the same way robin-test's scrape.py does; that needs PySocks installed
(`pip install PySocks`, not in requirements.txt yet since no pool entry
is .onion-only today) - without it, an .onion check fails with a
ConnectionError that this module reports as "unreachable" same as any
other connection failure, which is the safe default (never silently
"up").
"""
from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

CLEARNET_TIMEOUT = (6, 10)  # (connect, read) seconds
ONION_TIMEOUT = (15, 30)
TOR_PROXY = "socks5h://127.0.0.1:9050"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

# Consecutive "unreachable" checks (DNS/connect/timeout failure, not just an
# HTTP error status) before a forum is flagged as probably having rotated
# its address. A one-off blip (a restart, a transient DDoS-Guard timeout)
# should not trigger a mirror hunt - only a sustained outage should.
DEAD_THRESHOLD = 3


@dataclass
class CheckResult:
    name: str
    url: str
    status: str  # "up" | "degraded" | "unreachable"
    http_status: Optional[int]
    error: Optional[str]
    checked_at: str


def pool_urls(*pools: dict) -> dict[str, str]:
    """Flattens any number of {name: cfg} pools (FORUM_POOL,
    BROWSER_FORUM_POOL, a future onion pool, ...) into {name: url},
    reading whichever of `homepage`/`board_url` that pool's cfg shape
    uses - same lookup discover.py's `_known_hostnames` already does.
    """
    out: dict[str, str] = {}
    for pool in pools:
        for name, cfg in pool.items():
            url = cfg.get("homepage") or cfg.get("board_url")
            if url:
                out[name] = url
    return out


def _is_onion(url: str) -> bool:
    return (urlparse(url).hostname or "").lower().endswith(".onion")


def _session_for(url: str) -> requests.Session:
    session = requests.Session()
    if _is_onion(url):
        session.proxies = {"http": TOR_PROXY, "https": TOR_PROXY}
    return session


def check_one(name: str, url: str) -> CheckResult:
    checked_at = datetime.now(timezone.utc).isoformat()
    timeout = ONION_TIMEOUT if _is_onion(url) else CLEARNET_TIMEOUT
    try:
        session = _session_for(url)
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        status = "up" if resp.status_code < 500 else "degraded"
        return CheckResult(name, url, status, resp.status_code, None, checked_at)
    except requests.exceptions.RequestException as exc:
        return CheckResult(name, url, "unreachable", None, str(exc)[:200], checked_at)


class HealthStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS forum_health (
                name TEXT PRIMARY KEY,
                homepage TEXT,
                status TEXT,
                consecutive_failures INTEGER DEFAULT 0,
                last_checked_at TEXT,
                last_success_at TEXT,
                last_http_status INTEGER,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS address_history (
                name TEXT,
                url TEXT,
                first_seen TEXT,
                last_seen TEXT,
                PRIMARY KEY (name, url)
            );
            """
        )
        self._conn.commit()

    def record(self, result: CheckResult) -> int:
        """Persists one check result, returns the forum's updated
        consecutive_failures count (0 whenever this check wasn't
        "unreachable" - a degraded/error-page response still proves the
        address itself is alive, so it resets the counter same as "up").
        """
        row = self._conn.execute(
            "SELECT consecutive_failures FROM forum_health WHERE name = ?", (result.name,)
        ).fetchone()
        prev_failures = row[0] if row else 0
        new_failures = prev_failures + 1 if result.status == "unreachable" else 0

        last_success_at = result.checked_at if result.status == "up" else None
        self._conn.execute(
            """
            INSERT INTO forum_health (name, homepage, status, consecutive_failures,
                last_checked_at, last_success_at, last_http_status, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                homepage=excluded.homepage,
                status=excluded.status,
                consecutive_failures=excluded.consecutive_failures,
                last_checked_at=excluded.last_checked_at,
                last_success_at=COALESCE(excluded.last_success_at, forum_health.last_success_at),
                last_http_status=excluded.last_http_status,
                last_error=excluded.last_error
            """,
            (
                result.name, result.url, result.status, new_failures,
                result.checked_at, last_success_at, result.http_status, result.error,
            ),
        )
        if result.status == "up":
            self._conn.execute(
                """
                INSERT INTO address_history (name, url, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name, url) DO UPDATE SET last_seen=excluded.last_seen
                """,
                (result.name, result.url, result.checked_at, result.checked_at),
            )
        self._conn.commit()
        return new_failures

    def probably_dead(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM forum_health WHERE consecutive_failures >= ?", (DEAD_THRESHOLD,)
        ).fetchall()
        return [r[0] for r in rows]

    def report(self) -> list[dict]:
        cols = ["name", "homepage", "status", "consecutive_failures", "last_checked_at",
                "last_success_at", "last_http_status", "last_error"]
        rows = self._conn.execute(
            f"SELECT {', '.join(cols)} FROM forum_health ORDER BY consecutive_failures DESC, name"
        ).fetchall()
        return [dict(zip(cols, row)) for row in rows]

    def address_history(self, name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT url, first_seen, last_seen FROM address_history WHERE name = ? ORDER BY last_seen DESC",
            (name,),
        ).fetchall()
        return [{"url": r[0], "first_seen": r[1], "last_seen": r[2]} for r in rows]

    def close(self) -> None:
        self._conn.close()


def check_all(urls: dict[str, str], store: HealthStore, max_workers: int = 8) -> list[CheckResult]:
    results: list[CheckResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(check_one, name, url): name for name, url in urls.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception:
                log.exception("health: check crashed for %s", name)
                continue
            store.record(result)
            results.append(result)
    return results
