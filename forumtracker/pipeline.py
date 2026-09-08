"""Orchestrates the forum source: fetch -> normalize (done by the source) ->
dedup -> emit as JSON.

Scoped to a single source on purpose - this tracker was split out of the main
DarkWebFeed pipeline so it can run, version, and be tested independently.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from .alerting import is_alert_worthy, to_alert_card
from .dedup import SeenStore
from .entity_extraction import extract_victim_domain
from .notify_telegram import notify_alerts
from .ransomware_intel import classify as classify_ransomware
from .sources.base import Source
from .sources.browser_forum import BrowserForumSource
from .sources.telegram_bridge import TelegramBridgeSource
from .sources.underground_forums import UndergroundForumSource

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEEN_DB = DATA_DIR / "seen.sqlite3"
OUTPUT_FEED = DATA_DIR / "incidents.jsonl"
ALERTS_FEED = DATA_DIR / "alerts.jsonl"


def build_sources() -> list[Source]:
    return [
        UndergroundForumSource(),
        BrowserForumSource(),
        TelegramBridgeSource(),
    ]


def run_once(sources: list[Source], store: SeenStore) -> list[dict]:
    all_new: list[dict] = []
    for source in sources:
        try:
            incidents = [inc.to_dict() for inc in source.fetch()]
        except Exception:
            log.exception("source %s raised unexpectedly, skipping this cycle", source.name)
            continue

        new_incidents = store.filter_new(incidents)
        try:
            new_incidents = source.enrich_batch(new_incidents)
        except Exception:
            log.exception("source %s enrich_batch raised, keeping incidents unenriched", source.name)
        for inc in new_incidents:
            try:
                _apply_ransomware_intel(inc)
            except Exception:
                log.exception("ransomware_intel failed for %s, leaving unclassified", inc.get("incident_id"))
        store.mark_seen(new_incidents)
        log.info("%s: %d fetched, %d new", source.name, len(incidents), len(new_incidents))
        all_new.extend(new_incidents)
    return all_new


def _apply_ransomware_intel(incident: dict) -> None:
    """Cross-source post-processing (runs after enrich(), so it sees the
    fuller thread-page text where available, not just the RSS teaser):
    tags a known RaaS group / ransom-note pattern (see ransomware_intel.py)
    and pulls a real victim domain out of the text (see
    entity_extraction.py) into `victim.website`, which the schema already
    has but nothing populated until now.

    Only upgrades `category` from the generic "forum_post" default - never
    downgrades a source's own more specific classification (e.g.
    telegram_bridge's own category mapping, or a future source that already
    knows it's looking at a verified breach).
    """
    text = f"{incident.get('title', '')} {incident.get('description') or ''}"

    intel = classify_ransomware(text)
    if intel["ransomware_group"] or intel["ransom_note_pattern"]:
        extra = incident.setdefault("extra", {})
        if intel["ransomware_group"]:
            extra["ransomware_group"] = intel["ransomware_group"]
        if intel["ransom_note_pattern"]:
            extra["ransom_note_pattern"] = True
        if incident.get("category") == "forum_post":
            incident["category"] = "ransomware_leak"
        if "ransomware" not in incident.get("tags", []):
            incident.setdefault("tags", []).append("ransomware")

    source_hostname = urlparse(incident.get("source", {}).get("url") or "").hostname
    domain = extract_victim_domain(text, exclude_hostname=source_hostname)
    if domain:
        incident.setdefault("victim", {})["website"] = domain


def emit(incidents: list[dict]) -> None:
    if not incidents:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FEED.open("a", encoding="utf-8") as f:
        for inc in incidents:
            f.write(json.dumps(inc, ensure_ascii=False) + "\n")


def build_alerts(incidents: list[dict]) -> list[dict]:
    """Keyword/risk-score filter over new incidents - see alerting.py.
    Every incident still goes to incidents.jsonl; only the ones judged
    alert-worthy also get a rendered card here, sorted most-popular-first
    (`rich.popularity_score`, which blends real forum engagement with CTI
    risk in equal proportion - see `alerting.compute_popularity_score`) so
    the threads most worth an analyst's attention lead the feed instead of
    being buried in arrival order.
    """
    cards = [to_alert_card(inc) for inc in incidents if is_alert_worthy(inc)]
    cards.sort(key=lambda c: c["rich"]["popularity_score"], reverse=True)
    return cards


def emit_alerts(alerts: list[dict]) -> None:
    if not alerts:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with ALERTS_FEED.open("a", encoding="utf-8") as f:
        for alert in alerts:
            f.write(json.dumps(alert, ensure_ascii=False) + "\n")


def loop(poll_seconds: int = 900) -> None:
    sources = build_sources()
    store = SeenStore(SEEN_DB)
    try:
        while True:
            new_incidents = run_once(sources, store)
            emit(new_incidents)
            alerts = build_alerts(new_incidents)
            emit_alerts(alerts)
            notify_alerts(alerts)
            time.sleep(poll_seconds)
    finally:
        store.close()
