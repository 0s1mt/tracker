"""Forum-pool discovery: mines the `extra.referenced_source_url` field that
`telegram_bridge.py` attaches to every incident (breachdetect's own "Source"
field - which forum each leak post came from) and ranks the hostnames that
aren't already in our pools. This is the actual "pull from Telegram and
enrich the forum category" mechanism - Telegram itself is never treated as
a forum source, only as a discovery signal for which forums to look at next.

Deliberately does NOT auto-add anything to FORUM_POOL/BROWSER_FORUM_POOL -
every prior addition (see underground_forums.py's docstring) went through a
manual live content/safety review first (that's how altenens/hard-tm/etc.
got excluded), and a hostname mentioned in one Telegram post is not enough
evidence on its own to skip that step.

Usage: `python -m forumtracker.discover`
"""
from __future__ import annotations

import logging
from collections import Counter
from urllib.parse import urlparse

from .sources.browser_forum import BROWSER_FORUM_POOL
from .sources.telegram_bridge import TelegramBridgeSource
from .sources.underground_forums import FORUM_POOL

log = logging.getLogger(__name__)


def _known_hostnames() -> set[str]:
    known = set()
    for pool in (FORUM_POOL, BROWSER_FORUM_POOL):
        for cfg in pool.values():
            homepage = cfg.get("homepage") or cfg.get("board_url")
            if homepage:
                known.add(urlparse(homepage).hostname or "")
    return known


def discover_candidates(source: TelegramBridgeSource | None = None) -> list[dict]:
    """Returns [{"hostname": ..., "mentions": N, "example_url": ...}, ...],
    sorted by mention count descending, excluding hostnames already present
    in FORUM_POOL or BROWSER_FORUM_POOL.
    """
    source = source or TelegramBridgeSource()
    known = _known_hostnames()

    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for incident in source.fetch():
        url = (incident.extra or {}).get("referenced_source_url")
        if not url:
            continue
        hostname = urlparse(url).hostname
        if not hostname or hostname in known:
            continue
        counts[hostname] += 1
        examples.setdefault(hostname, url)

    return [
        {"hostname": host, "mentions": n, "example_url": examples[host]}
        for host, n in counts.most_common()
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    candidates = discover_candidates()
    if not candidates:
        print("No new forum candidates found this cycle.")
        return
    print(f"{len(candidates)} candidate forum(s) not yet in FORUM_POOL/BROWSER_FORUM_POOL:\n")
    for c in candidates:
        print(f"  {c['hostname']:<30} mentions={c['mentions']:<3} e.g. {c['example_url']}")
    print("\nReview each site's live content before adding it - see underground_forums.py's "
          "docstring for the review discipline (altenens/hard-tm/etc. were excluded this way).")


if __name__ == "__main__":
    main()
