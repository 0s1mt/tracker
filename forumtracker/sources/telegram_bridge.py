"""Reads gated (Telegram "sensitive content"-flagged) channels via a free,
public, unauthenticated Telegram->RSS bridge (tg.i-c-a.su) - no Telethon
session, no phone-number OTP login needed.

Why this exists: `t.me/breachdetect` ("Data Leak Monitor" - one of the exact
channels the user referenced with a screenshot) is flagged sensitive, so the
unauthenticated `t.me/s/<channel>` approach used by DarkWebFeed's
`telegram_public.py` gets redirected to a login wall and returns nothing.
Verified live (2026-09-08): `t.me/s/breachdetect` -> redirects away, 0
messages. `tg.i-c-a.su/rss/breachdetect` -> 200, real messages, same content
a logged-in user would see. It's a small third-party service (not
Anthropic/us-run) with a shared, aggressive rate limit - a burst of requests
across different channels returns 429/403 for a short cooldown ("Time to
unlock access: NNs"), so `fetch()` treats those as this-cycle-only failures,
same as any other source's network hiccup.

Each breachdetect post is already the exact
Source/Content/author/"Detection Date"/Type JSON shape from the user's
screenshot, double-HTML-escaped inside the RSS <description>. `_parse_item`
decodes and regex-extracts those five fields into a plain dict; the
`referenced_source_url` (breachdetect's own "Source" field) is kept in
`extra` - `forumtracker/discover.py` mines that field across a batch of
these to surface new forum candidates, which is the actual "enrich the forum
pool from Telegram" mechanism the user asked for.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

from ..schema import Evidence, Incident, ThreatActor, Victim
from ..schema import Source as SourceMeta
from .base import Source

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
BRIDGE_URL = "https://tg.i-c-a.su/rss/{channel}"
MAX_ITEMS_PER_CHANNEL = 30

# channel -> config. Only breachdetect is confirmed working through this
# bridge right now - ctifeeds/DarkWebInformer both hit the bridge's
# per-new-channel cooldown when tried live and need a retry later (see
# README). Add them here once confirmed; fetch() skips failures per-channel
# so one bad entry never blocks the others.
TELEGRAM_BRIDGE_POOL: dict[str, dict] = {
    "breachdetect": {},
}

ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
DESCRIPTION_RE = re.compile(r"<description>(.*?)</description>", re.DOTALL)
LINK_RE = re.compile(r"<link>(.*?)</link>")
TAG_RE = re.compile(r"<[^>]+>")

SOURCE_FIELD_RE = re.compile(
    r'"Source":\s*"(?:<a[^>]*href="([^"]*)"[^>]*>[^<]*</a>|([^"]*))"'
)
FIELD_RE = {
    "content": re.compile(r'"Content":\s*"([^"]*)"'),
    "author": re.compile(r'"author":\s*"([^"]*)"'),
    "detection_date": re.compile(r'"Detection Date":\s*"([^"]*)"'),
    "type": re.compile(r'"Type":\s*"([^"]*)"'),
}

_CATEGORY_MAP = {
    "data leak": "data_leak",
    "data breach": "data_breach",
    "ransomware": "ransomware_leak",
    "phishing": "phishing",
    "defacement": "defacement",
}

# Live-verified (2026-09-08): every single item on t.me/breachdetect's own
# feed carries Type="Data leak", regardless of actual content - genuine
# combolists and access sales get it, but so do plain consumer-tech news
# reposts ("Lenovo выпустила 4K-монитор ThinkVision...", "MediaTek Helio
# G99 получил..."). The channel's self-reported Type is not a reliable
# signal, so a "data leak"/"data breach" mapping (or an unrecognized Type
# falling through to the old blanket default) is only honored when the
# post's own content actually looks like underground/CTI chatter - EN+RU
# vocabulary covering leaks, credential/access sales, carding, malware,
# and marketplace activity. Content that matches nothing here is
# downgraded to "forum_post" (neutral chatter) instead of a category that
# implies a confirmed leak/breach. Explicit rarer Types (ransomware,
# phishing, defacement) are trusted as-is - they're more specific and
# ransomware claims get a second, independent check in
# `pipeline._apply_ransomware_intel`.
_RELEVANCE_TERMS = [
    "leak", "dump", "database", "combo", "fullz", "cvv", "dork", "carding",
    "hack", "breach", "stolen", "credential", "login", "pass", "rdp", "ssh",
    "ftp", "cpanel", "webmail", "mail", "access", "vpn", "exploit", "0day",
    "cve", "ransomware", "ddos", "botnet", "spam", "phishing", "malware",
    "stealer", "cookie", "session", "token", "panel", "shell", "backdoor",
    "source code", "config", "checker", "brute", "proxy", "sql injection",
    "api key", "account", "fraud", "warez", "crypter", "keylogger",
    "skimmer", "smtp", "passport", "iban", "swift", "wallet", "escrow",
    "vouch", "shop", "scam", "ulp", "log:pass", "url:pass", "mail:pass",
    "утечка", "взлом", "аккаунт", "база", "продажа", "куплю", "продам",
    "слив", "логи", "пароль", "почта", "магазин", "скам", "чек", "карта",
    "карты", "банк", "кабинет", "доступ",
]


def _looks_underground_relevant(text: Optional[str]) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in _RELEVANCE_TERMS)


def _parse_item(item_xml: str, link: Optional[str] = None) -> Optional[dict]:
    """Pure function: one <item> block -> a flat dict, or None if it doesn't
    match the expected Source/Content/author/Detection Date/Type shape
    (some channel posts are plain announcements, not structured leak posts).
    """
    desc_m = DESCRIPTION_RE.search(item_xml)
    if not desc_m:
        return None
    decoded = html.unescape(html.unescape(desc_m.group(1)))

    source_m = SOURCE_FIELD_RE.search(decoded)
    if not source_m:
        return None
    source_url = (source_m.group(1) or source_m.group(2) or "").strip()

    stripped = TAG_RE.sub("", decoded)
    row: dict = {"source_url": source_url}
    for key, pattern in FIELD_RE.items():
        m = pattern.search(stripped)
        row[key] = html.unescape(m.group(1)).strip() if m else None

    if link is None:
        link_m = LINK_RE.search(item_xml)
        link = link_m.group(1) if link_m else None
    row["link"] = link
    return row


def _parse_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d %b %Y").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return raw


class TelegramBridgeSource(Source):
    name = "telegram_bridge"
    poll_interval_seconds = 1800

    def __init__(self, channels: Optional[dict] = None) -> None:
        self.channels = channels or TELEGRAM_BRIDGE_POOL

    def fetch(self) -> Iterable[Incident]:
        now = datetime.now(timezone.utc).isoformat()
        incidents: list[Incident] = []
        for channel in self.channels:
            try:
                incidents.extend(self._fetch_channel(channel, now))
            except Exception:
                log.exception("telegram_bridge: channel %s failed entirely, skipping", channel)
        return incidents

    def _fetch_channel(self, channel: str, ingested_at: str) -> list[Incident]:
        url = BRIDGE_URL.format(channel=channel)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
        except Exception:
            log.exception("telegram_bridge: request failed for %s", channel)
            return []
        if resp.status_code != 200:
            log.warning("telegram_bridge: %s returned %s (rate-limited or gated), skipping this cycle",
                        channel, resp.status_code)
            return []

        out = []
        for item_xml in ITEM_RE.findall(resp.text)[:MAX_ITEMS_PER_CHANNEL]:
            row = _parse_item(item_xml)
            if row is None:
                continue
            out.append(self._to_incident(channel, row, ingested_at))
        return out

    @staticmethod
    def _to_incident(channel: str, row: dict, ingested_at: str) -> Incident:
        content = row.get("content") or "unknown"
        author = (row.get("author") or "").strip("() ") or "unknown"
        mapped_category = _CATEGORY_MAP.get((row.get("type") or "").strip().lower())
        if mapped_category in (None, "data_leak", "data_breach") and not _looks_underground_relevant(content):
            category = "forum_post"
        else:
            category = mapped_category or "data_leak"

        return Incident(
            detected_at=ingested_at,
            published_at=_parse_date(row.get("detection_date")),
            source=SourceMeta(type="telegram", name=f"t.me/{channel}", url=row.get("link")),
            threat_actor=ThreatActor(name=author),
            victim=Victim(name=content[:200]),
            category=category,
            title=content[:200],
            description=content,
            evidence=Evidence(post_url=row.get("link")),
            confidence="claimed",
            tags=["telegram", channel],
            extra={"referenced_source_url": row.get("source_url"), "raw_type": row.get("type")},
        )
