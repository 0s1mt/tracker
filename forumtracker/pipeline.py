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
from .forum_stats import StatsStore, collect_snapshots
from .health import HealthStore, check_all, pool_urls
from .identity_correlation import IdentityStore
from .notify_telegram import notify_alerts, notify_health_alert, notify_identity_match
from .ransomware_intel import classify as classify_ransomware
from .sources.base import Source
from .sources.browser_forum import BROWSER_FORUM_POOL, BrowserForumSource
from .sources.telegram_bridge import TelegramBridgeSource
from .sources.underground_forums import FORUM_POOL, UndergroundForumSource

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEEN_DB = DATA_DIR / "seen.sqlite3"
HEALTH_DB = DATA_DIR / "health.sqlite3"
STATS_DB = DATA_DIR / "forum_stats.sqlite3"
IDENTITY_DB = DATA_DIR / "identity.sqlite3"
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


def run_full_cycle(
    sources: list[Source],
    store: SeenStore,
    health_store: HealthStore,
    stats_store: StatsStore,
    identity_store: IdentityStore,
) -> dict:
    """One complete cycle: ingest -> dedup -> alert -> identity-correlate ->
    health-check -> stats-snapshot. This is the single source of truth for
    "what does one tracker cycle do" - `loop()` calls it repeatedly with a
    sleep between calls, and a CI/scheduled-job run (no long-lived process,
    one Python invocation per workflow run) calls it exactly once via
    `cli.py`'s default action. Before this refactor those two entrypoints
    had started to drift (identity/health/stats were wired into `loop()`
    only, so a one-shot CI run would have silently skipped all three) -
    one function used by both closes that gap for good instead of needing
    the next feature to remember to wire itself into two places.

    Every sub-step is independently try/excepted (already true before this
    refactor, preserved here) so one failing piece - e.g. the health check
    can't reach the network - never blocks incident ingestion or vice
    versa. Returns a small JSON-able summary, used both for `--once`'s
    printed output and as the basis for the CI workflow's committed
    reports/*.json.
    """
    new_incidents = run_once(sources, store)
    emit(new_incidents)
    alerts = build_alerts(new_incidents)
    emit_alerts(alerts)
    telegram_sent = notify_alerts(alerts)

    new_identity_matches: list[dict] = []
    try:
        for match in identity_store.record_incidents(new_incidents):
            log.info("identity: %s matched across %s", match.username_norm, match.forums)
            notify_identity_match(match)
            new_identity_matches.append({"username": match.display_names, "forums": match.forums})
    except Exception:
        log.exception("identity correlation raised, skipping this cycle")

    newly_dead: list[str] = []
    try:
        newly_dead = check_forum_health(health_store)
        for name in newly_dead:
            log.warning("health: %s crossed the dead-mirror threshold", name)
            candidates = find_mirror_candidates(name)
            notify_health_alert(name, candidates)
    except Exception:
        log.exception("health check raised, skipping this cycle")

    stats_snapshots: dict = {}
    try:
        stats_snapshots = collect_snapshots(pool_urls(FORUM_POOL, BROWSER_FORUM_POOL), stats_store)
    except Exception:
        log.exception("forum_stats snapshot raised, skipping this cycle")

    # Full current-state reads below are local SQLite queries, not new
    # network calls - cheap enough to include every cycle so this one
    # return value is a complete rollup a CI job can write straight to
    # reports/*.json without a second pass over --health/--stats/--identity.
    try:
        health_report = health_store.report()
    except Exception:
        log.exception("health report read raised")
        health_report = []

    try:
        identity_matches = [
            {"username": m.display_names, "forums": m.forums, "first_matched_at": m.first_matched_at}
            for m in identity_store.all_matches()
        ]
    except Exception:
        log.exception("identity report read raised")
        identity_matches = []

    return {
        "new_incidents": len(new_incidents),
        "alerts": len(alerts),
        "telegram_sent": telegram_sent,
        "new_identity_matches": new_identity_matches,
        "newly_dead_forums": newly_dead,
        "stats_forums_found": sum(1 for v in stats_snapshots.values() if v),
        "health_report": health_report,
        "identity_matches": identity_matches,
        "stats_snapshots": stats_snapshots,
    }


def open_stores() -> tuple[SeenStore, HealthStore, StatsStore, IdentityStore]:
    return SeenStore(SEEN_DB), HealthStore(HEALTH_DB), StatsStore(STATS_DB), IdentityStore(IDENTITY_DB)


def loop(poll_seconds: int = 900) -> None:
    sources = build_sources()
    store, health_store, stats_store, identity_store = open_stores()
    try:
        while True:
            run_full_cycle(sources, store, health_store, stats_store, identity_store)
            time.sleep(poll_seconds)
    finally:
        store.close()
        health_store.close()
        stats_store.close()
        identity_store.close()
