"""Automatic mirror adoption for a forum that just crossed the dead-mirror
threshold (see health.py's DEAD_THRESHOLD / pipeline.py's
check_forum_health).

Problem this closes: discover.find_mirror_candidates() already finds
textually-similar new hostnames from Telegram chatter when a forum dies,
but until now that was purely advisory - a human had to notice the
Telegram alert and manually edit FORUM_POOL to actually resume tracking.
In practice that gap could run for days, during which the tracker simply
stops collecting anything for that forum - exactly the "forum kapandiginda
cop oluyoruz" problem. This module closes the gap automatically, with two
hard requirements before anything is trusted:

1. A high textual-similarity floor (see ADOPTION_SIMILARITY_FLOOR) - the
   new hostname has to literally contain the dead forum's name, the
   strongest real-world mirror signal (same discipline
   find_mirror_candidates already uses to floor its own score at 0.8).
2. A LIVE verification fetch that returns real incidents - reuses
   UndergroundForumSource's actual RSS-autodiscovery + parse path (not a
   bare reachability ping), so a parked domain, a phishing clone with no
   real forum software behind it, or a squatted name with an empty board
   all fail this and are never adopted.

What this still does NOT do: silently fold the candidate into FORUM_POOL
as a permanent, fully-trusted entry. Adoption is recorded separately (see
AutoMirrorStore) and every incident it produces is still visibly a mirror
guess, not a reviewed pool member - discover.py's docstring explains why
that distinction matters (a Telegram mention plus a live RSS feed is
good enough evidence to resume DATA COLLECTION immediately, but not
enough on its own to call a domain permanently safe without a human
eventually looking at it).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .discover import find_mirror_candidates
from .sources.underground_forums import UndergroundForumSource

log = logging.getLogger(__name__)

ADOPTION_SIMILARITY_FLOOR = 0.8


class AutoMirrorStore:
    """Tiny JSON-backed record of auto-adopted mirrors, keyed by the dead
    forum's pool name. Not SQLite like the other *Store classes in this
    project - this is at most a handful of rows (one per forum that has
    ever died and been auto-replaced), read in full every cycle, never
    queried at volume.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def all(self) -> dict[str, dict]:
        return dict(self._data)

    def sites(self) -> dict[str, dict]:
        """{candidate_name: {"homepage": ..., "rss": None}} for every
        currently-adopted mirror - the exact shape
        UndergroundForumSource(sites=...)/pool_urls() expect, so callers
        can merge this straight into FORUM_POOL.
        """
        return {name: {"homepage": row["homepage"], "rss": None} for name, row in self._data.items()}

    def already_adopted_for(self, dead_name: str) -> Optional[dict]:
        return self._data.get(dead_name)

    def record(self, dead_name: str, candidate: dict) -> None:
        self._data[dead_name] = {
            "hostname": candidate["hostname"],
            "homepage": f"https://{candidate['hostname']}",
            "similarity": candidate["similarity"],
            "adopted_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")


def try_adopt_mirror(
    dead_name: str, store: AutoMirrorStore, candidates: Optional[list[dict]] = None
) -> Optional[dict]:
    """Attempts to automatically adopt a live-verified mirror for a forum
    that just crossed the dead threshold. Returns the adopted candidate
    dict (with an added "incidents_seen" count from the verification
    fetch) on success, None if nothing cleared both the similarity floor
    and the live-content check. Idempotent: a forum already adopted once
    is skipped without re-fetching, so this is cheap to call every cycle.
    """
    if store.already_adopted_for(dead_name):
        return None

    candidates = find_mirror_candidates(dead_name) if candidates is None else candidates
    for candidate in candidates:
        if candidate["similarity"] < ADOPTION_SIMILARITY_FLOOR:
            continue
        probe_sites = {f"{dead_name}_candidate": {"homepage": f"https://{candidate['hostname']}", "rss": None}}
        try:
            incidents = list(UndergroundForumSource(sites=probe_sites).fetch())
        except Exception:
            log.exception("auto_mirror: verification fetch raised for %s", candidate["hostname"])
            continue
        if not incidents:
            continue
        store.record(dead_name, candidate)
        log.warning(
            "auto_mirror: adopted %s as live mirror of dead forum %s (similarity=%.2f, %d incidents on first fetch)",
            candidate["hostname"], dead_name, candidate["similarity"], len(incidents),
        )
        return {**candidate, "incidents_seen": len(incidents)}
    return None
