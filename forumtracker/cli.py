"""Entrypoint: `python -m forumtracker.cli --once` or `--loop`."""
from __future__ import annotations

import argparse
import json
import logging

from .dedup import SeenStore
from .notify_telegram import notify_alerts
from .pipeline import SEEN_DB, build_alerts, build_sources, emit, emit_alerts, loop, run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Darkweb Forum Tracker ingestion pipeline")
    parser.add_argument("--once", action="store_true", help="run a single fetch cycle and print new incidents")
    parser.add_argument("--loop", action="store_true", help="poll continuously (Ctrl+C to stop)")
    parser.add_argument("--interval", type=int, default=900, help="seconds between polls in --loop mode")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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
