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
from .discover import find_mirror_candidates
from .entity_extraction import extract_victim_domain
from .health import HealthStore, check_all, pool_urls
from .notify_telegram import notify_alerts, notify_health_alert
from .ransomware_intel import classify as classify_ransomware
from .sources.base import Source
from .sources.browser_forum import BROWSER_FORUM_POOL, BrowserForumSource
from .sources.telegram_bridge import TelegramBridgeSource
from .sources.underground_forums import FORUM_POOL, UndergroundForumSource

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEEN_DB = DATA_DIR / "seen.sqlite3"
HEALTH_DB = DATA_DIR / "health.sqlite3"
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


def check_forum_health(health_store: HealthStore) -> list[str]:
    """Runs one liveness pass over FORUM_POOL + BROWSER_FORUM_POOL and
    returns only the forum names that *just* crossed
    `health.DEAD_THRESHOLD` consecutive unreachable checks this cycle -
    i.e. forums already past the threshold before this call are excluded,
    so a caller notifying on the return value fires once per outage
    instead of every cycle the forum stays down.
    """
    before = set(health_store.probably_dead())
    check_all(pool_urls(FORUM_POOL, BROWSER_FORUM_POOL), health_store)
    after = set(health_store.probably_dead())
    return sorted(after - before)


def loop(poll_seconds: int = 900) -> None:
    sources = build_sources()
    store = SeenStore(SEEN_DB)
    health_store = HealthStore(HEALTH_DB)
    try:
        while True:
            new_incidents = run_once(sources, store)
            emit(new_incidents)
            alerts = build_alerts(new_incidents)
            emit_alerts(alerts)
            notify_alerts(alerts)

            try:
                newly_dead = check_forum_health(health_store)
                for name in newly_dead:
                    log.warning("health: %s crossed the dead-mirror threshold", name)
                    candidates = find_mirror_candidates(name)
                    notify_health_alert(name, candidates)
            except Exception:
                log.exception("health check raised, skipping this cycle")

            time.sleep(poll_seconds)
    finally:
        store.close()
        health_store.close()
