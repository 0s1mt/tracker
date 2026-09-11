"""Forum-level activity score, fed from a source we already fetch: the
homepage HTML itself.

XenForo (and its rebrands) ships a standard "Forum İstatistikleri" /
"Forum statistics" / "Статистика форума" sidebar widget
(`data-widget-definition="forum_statistics"` or a themed variant like
bdfclub's `forumStatistics`/`live_forum_statistics`) showing cumulative
thread/message/member counts as plain `<dt>label</dt><dd>number</dd>`
pairs - verified live on turkhackteam, carders.biz and bdfclub
(2026-09-11). No new dependency, no new scrape target: `underground_forums.
_discover_rss_url` already GETs this same homepage every cycle for RSS
auto-discovery; this module just also reads the numbers already sitting in
that response.

Labels vary by forum language/theme (Konular/Konu, Threads, Темы, ...) so
`extract_stats` matches against a small multilingual keyword table instead
of relying on any one exact string or CSS class name - the class names
themselves differ per theme too (`count--threads` on turkhackteam, absent
on bdfclub/carders.biz).

A single snapshot only gives a forum's total size, not its current activity
- "how active is this forum RIGHT NOW" needs at least two snapshots spaced
apart, so `StatsStore` persists every reading and `activity_per_day`
derives a real messages/day rate from the oldest-to-newest delta, the same
"never fabricate a number we don't have real signal for" discipline as
`alerting.compute_popularity_score`'s reply_count fallback.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
TIMEOUT = (6, 12)
TOR_PROXY = "socks5h://127.0.0.1:9050"

# dt-label (lowercased, accent/locale variants) -> which count it is.
_LABEL_MAP: dict[str, tuple[str, ...]] = {
    "threads": ("threads", "topics", "konular", "konu", "темы", "тем"),
    "messages": ("messages", "posts", "mesajlar", "сообщения", "сообщений"),
    "members": ("members", "users", "kullanıcılar", "üyeler", "пользователи", "пользователей"),
}

_DIGITS_RE = re.compile(r"\d")


def _classify_label(label: str) -> Optional[str]:
    lowered = label.strip().lower()
    for key, variants in _LABEL_MAP.items():
        if any(v in lowered for v in variants):
            return key
    return None


def _parse_count(text: str) -> Optional[int]:
    """"1,463,871" / "153.298" / "68 487" -> int. Both ',' and '.' are used
    as thousands separators across these forums' locales (no pool site has
    shown a genuinely fractional stat here), so digits are just
    concatenated. A value with no digits at all (an avatar/username cell
    like "Latest member") returns None.
    """
    digits = "".join(_DIGITS_RE.findall(text))
    return int(digits) if digits else None


def _extract_from_widget(widget) -> dict:
    result: dict[str, int] = {}
    for dl in widget.find_all("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt is None or dd is None:
            continue
        key = _classify_label(dt.get_text(strip=True))
        if key is None or key in result:
            continue
        count = _parse_count(dd.get_text(strip=True))
        if count is not None:
            result[key] = count
    return result


def extract_stats(html: str) -> Optional[dict]:
    """Returns {"threads": N, "messages": N, "members": N} using whichever
    of those three the page's stats widget actually exposes (never all
    three on every forum - e.g. bdfclub's variant seen live didn't label a
    members count), or None if no stats widget was found at all.

    A page can carry more than one `data-widget-definition="*statistic*"`
    element - bdfclub ships both `live_forum_statistics` (a "top posters"
    carousel with the word "statistics" in its key but no dt/dd pairs at
    all) and `forumStatistics` (the real counters) - so every match is
    tried in document order and the first one that actually yields a
    result wins, instead of trusting the first match found.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.find_all(attrs={"data-widget-definition": re.compile("statistic", re.IGNORECASE)})
    for widget in candidates:
        result = _extract_from_widget(widget)
        if result:
            return result
    return None


def _is_onion(url: str) -> bool:
    return (urlparse(url).hostname or "").lower().endswith(".onion")


def fetch_stats(url: str) -> Optional[dict]:
    session = requests.Session()
    if _is_onion(url):
        session.proxies = {"http": TOR_PROXY, "https": TOR_PROXY}
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        log.debug("forum_stats: fetch failed for %s", url, exc_info=True)
        return None
    return extract_stats(resp.text)


@dataclass
class StatsSnapshot:
    name: str
    captured_at: str
    threads: Optional[int]
    messages: Optional[int]
    members: Optional[int]


class StatsStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_stats (
                name TEXT,
                captured_at TEXT,
                threads INTEGER,
                messages INTEGER,
                members INTEGER,
                PRIMARY KEY (name, captured_at)
            )
            """
        )
        self._conn.commit()

    def record(self, name: str, stats: dict, captured_at: Optional[str] = None) -> None:
        captured_at = captured_at or datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO forum_stats (name, captured_at, threads, messages, members) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, captured_at, stats.get("threads"), stats.get("messages"), stats.get("members")),
        )
        self._conn.commit()

    def history(self, name: str) -> list[StatsSnapshot]:
        rows = self._conn.execute(
            "SELECT name, captured_at, threads, messages, members FROM forum_stats "
            "WHERE name = ? ORDER BY captured_at ASC",
            (name,),
        ).fetchall()
        return [StatsSnapshot(*row) for row in rows]

    def latest(self, name: str) -> Optional[StatsSnapshot]:
        history = self.history(name)
        return history[-1] if history else None

    def activity_per_day(self, name: str, min_hours_span: float = 1.0) -> Optional[float]:
        """Real messages/day, derived from this forum's own oldest-to-newest
        recorded delta - not a single self-reported "posts today" number
        (only one pool forum, bdfclub, even exposes that, and not in a
        consistent field across forums). Returns None until at least two
        snapshots exist far enough apart to make the rate meaningful -
        mirrors compute_popularity_score's "don't fabricate a rate from
        data we don't have" rule.
        """
        history = [h for h in self.history(name) if h.messages is not None]
        if len(history) < 2:
            return None
        first, last = history[0], history[-1]
        elapsed_hours = (
            datetime.fromisoformat(last.captured_at) - datetime.fromisoformat(first.captured_at)
        ).total_seconds() / 3600
        if elapsed_hours < min_hours_span:
            return None
        delta_messages = last.messages - first.messages
        if delta_messages < 0:
            # Counter reset/forum reinstall/mis-scrape - don't report a
            # negative activity rate.
            return None
        return round(delta_messages / (elapsed_hours / 24), 2)

    def close(self) -> None:
        self._conn.close()


def collect_snapshots(urls: dict[str, str], store: StatsStore) -> dict[str, Optional[dict]]:
    results: dict[str, Optional[dict]] = {}
    for name, url in urls.items():
        stats = fetch_stats(url)
        results[name] = stats
        if stats:
            store.record(name, stats)
    return results
