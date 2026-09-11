"""Entrypoint: `python -m forumtracker.cli --once` or `--loop`."""
from __future__ import annotations

import argparse
import json
import logging

from .discover import find_mirror_candidates
from .forum_stats import StatsStore, collect_snapshots
from .health import DEAD_THRESHOLD, HealthStore, check_all, pool_urls
from .identity_correlation import IdentityStore, backfill_from_feed
from .pipeline import DATA_DIR, OUTPUT_FEED, build_sources, loop, open_stores, run_full_cycle
from .sources.browser_forum import BROWSER_FORUM_POOL
from .sources.underground_forums import FORUM_POOL

HEALTH_DB = DATA_DIR / "health.sqlite3"
STATS_DB = DATA_DIR / "forum_stats.sqlite3"
IDENTITY_DB = DATA_DIR / "identity.sqlite3"


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


def run_identity_report(backfill: bool = False) -> dict:
    """Reports every username currently known to have posted on 2+ pool
    forums (see identity_correlation.py). `--identity-backfill` seeds this
    from the existing incidents.jsonl first - otherwise the report only
    reflects whatever --once/--loop runs have recorded since IdentityStore
    was first created.
    """
    store = IdentityStore(IDENTITY_DB)
    try:
        if backfill:
            backfill_from_feed(store, OUTPUT_FEED)
        matches = store.all_matches()
        return {
            "cross_forum_matches": len(matches),
            "matches": [
                {"username": m.display_names, "forums": m.forums, "first_matched_at": m.first_matched_at}
                for m in matches
            ],
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
    parser.add_argument(
        "--identity", action="store_true",
        help="report every username currently matched across 2+ pool forums",
    )
    parser.add_argument(
        "--identity-backfill", action="store_true",
        help="with --identity, first seed the identity store from the existing incidents.jsonl",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.health:
        print(json.dumps(run_health_check(), ensure_ascii=False, indent=2))
        return

    if args.stats:
        print(json.dumps(run_stats_snapshot(), ensure_ascii=False, indent=2))
        return

    if args.identity:
        print(json.dumps(run_identity_report(backfill=args.identity_backfill), ensure_ascii=False, indent=2))
        return

    if args.loop:
        loop(poll_seconds=args.interval)
        return

    # Default action (also `--once`): one full cycle - ingest, alert,
    # identity-correlate, health-check, stats-snapshot - via the same
    # run_full_cycle() a --loop iteration uses, so a one-shot CI/scheduled
    # run and a long-lived local loop never drift apart. See
    # pipeline.run_full_cycle's docstring.
    sources = build_sources()
    store, health_store, stats_store, identity_store = open_stores()
    try:
        summary = run_full_cycle(sources, store, health_store, stats_store, identity_store)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        store.close()
        health_store.close()
        stats_store.close()
        identity_store.close()


if __name__ == "__main__":
    main()
