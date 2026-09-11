"""Entrypoint: `python -m forumtracker.cli --once` or `--loop`."""
from __future__ import annotations

import argparse
import json
import logging

from .dedup import SeenStore
from .discover import find_mirror_candidates
from .forum_stats import StatsStore, collect_snapshots
from .health import DEAD_THRESHOLD, HealthStore, check_all, pool_urls
from .notify_telegram import notify_alerts
from .pipeline import DATA_DIR, SEEN_DB, build_alerts, build_sources, emit, emit_alerts, loop, run_once
from .sources.browser_forum import BROWSER_FORUM_POOL
from .sources.underground_forums import FORUM_POOL

HEALTH_DB = DATA_DIR / "health.sqlite3"
STATS_DB = DATA_DIR / "forum_stats.sqlite3"


def run_health_check() -> dict:
    """Checks every pool forum's homepage once, persists the result, and
    for any forum that has now crossed DEAD_THRESHOLD consecutive
    unreachable checks, looks for a same-named replacement among
    Telegram-mentioned hostnames (see discover.find_mirror_candidates).
    """
    store = HealthStore(HEALTH_DB)
    try:
        urls = pool_urls(FORUM_POOL, BROWSER_FORUM_POOL)
        check_all(urls, store)
        dead = store.probably_dead()
        mirror_suggestions = {name: find_mirror_candidates(name) for name in dead}
        return {"report": store.report(), "probably_dead": dead, "mirror_suggestions": mirror_suggestions}
    finally:
        store.close()


def run_stats_snapshot() -> dict:
    """Fetches every pool forum's homepage once, parses its "Forum
    İstatistikleri" widget (see forum_stats.py), records the snapshot, and
    reports each forum's real messages/day rate wherever at least two
    snapshots spanning >=1h now exist. A single run only grows the history
    by one point - `activity_per_day` stays null until a second run (e.g.
    the next --loop cycle, or --stats run again later) gives it a real
    delta to measure.
    """
    store = StatsStore(STATS_DB)
    try:
        urls = pool_urls(FORUM_POOL, BROWSER_FORUM_POOL)
        snapshots = collect_snapshots(urls, store)
        activity = {
            name: store.activity_per_day(name)
            for name, stats in snapshots.items() if stats is not None
        }
        return {
            "snapshots": snapshots,
            "messages_per_day": {k: v for k, v in activity.items() if v is not None},
            "no_stats_widget_found": [name for name, stats in snapshots.items() if stats is None],
        }
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Darkweb Forum Tracker ingestion pipeline")
    parser.add_argument("--once", action="store_true", help="run a single fetch cycle and print new incidents")
    parser.add_argument("--loop", action="store_true", help="poll continuously (Ctrl+C to stop)")
    parser.add_argument("--interval", type=int, default=900, help="seconds between polls in --loop mode")
    parser.add_argument(
        "--health", action="store_true",
        help=f"check pool liveness once and report forums with {DEAD_THRESHOLD}+ consecutive "
             "unreachable checks, plus any Telegram-mentioned hostname that looks like a replacement",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="snapshot each pool forum's own thread/message/member counters and report real "
             "messages/day activity once enough history has accumulated",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.health:
        print(json.dumps(run_health_check(), ensure_ascii=False, indent=2))
        return

    if args.stats:
        print(json.dumps(run_stats_snapshot(), ensure_ascii=False, indent=2))
        return

    if args.loop:
        loop(poll_seconds=args.interval)
        return

    sources = build_sources()
    store = SeenStore(SEEN_DB)
    try:
        new_incidents = run_once(sources, store)
        emit(new_incidents)
        alerts = build_alerts(new_incidents)
        emit_alerts(alerts)
        sent = notify_alerts(alerts)
        print(json.dumps({"incidents": new_incidents, "alerts": alerts, "telegram_sent": sent}, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
