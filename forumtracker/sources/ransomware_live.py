"""Structured ransomware-victim feed from ransomware.live's free v2 API -
a fundamentally different kind of source from this module's others: those
scrape forum/Telegram TEXT and have to guess at victim/country/sector from
whatever a post happens to mention (often nothing - see pipeline.py's
_apply_ransomware_intel). ransomware.live is itself a maintained CTI
aggregator: its own researchers already resolved the real victim name,
country, sector and domain behind each group's claim, so this source
reports those fields directly instead of re-deriving them.

Found via deepdarkCTI's ransomware_gang.md (not forum.md, which this
module's other sources were sourced from) - 2026-11-01 research pass
triggered by a user-reported gap (searching "ziraat" surfaced nothing in
our own data despite 187 real Turkish ransomware victims existing in
ransomware.live's history alone, confirmed live via
GET /v2/countryvictims/TR).

ToS note (api.ransomware.live/api, read 2026-11-01): the free v2 tier is
documented as "Personal use only... not intended for corporate or business
use", rate-limited to ~1 request/minute/endpoint. This source calls exactly
one endpoint (/v2/recentvictims) once per pipeline cycle (every 30 minutes
via scripts/run_cycle.ps1), nowhere near that limit - but the "personal use"
clause itself is a business-model question, not a technical one; revisit
if this tracker's output is ever used commercially (a paid PRO key exists
for that case - see api-pro.ransomware.live/docs).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

from ..schema import Evidence, Incident, ThreatActor, Victim
from ..schema import Source as SourceMeta
from .base import Source

log = logging.getLogger(__name__)

API_BASE = "https://api.ransomware.live/v2"
RECENT_VICTIMS_URL = f"{API_BASE}/recentvictims"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

# ransomware.live's own "activity" taxonomy -> this project's sector
# vocabulary (see sector_classification.py). Deliberately conservative:
# only maps values with an unambiguous match, same discipline as
# sector_classification.py's own keyword tables - "Other"/"Not Found"/
# anything unmapped is left as None rather than guessed.
_ACTIVITY_TO_SECTOR = {
    "technology": "technology",
    "information technology": "technology",
    "it services": "technology",
    "manufacturing": "manufacturing",
    "education": "education",
    "retail & e-commerce": "retail",
    "retail": "retail",
    "e-commerce": "retail",
    "transportation": "transportation",
    "logistics": "transportation",
    "finance": "finance",
    "financial services": "finance",
    "banking": "finance",
    "insurance": "finance",
    "healthcare": "healthcare",
    "medical": "healthcare",
    "government": "government",
    "public sector": "government",
    "telecommunications": "telecom",
    "energy": "energy",
    "oil & gas": "energy",
    "utilities": "energy",
}


def _sector_from_activity(activity: Optional[str]) -> Optional[str]:
    if not activity:
        return None
    return _ACTIVITY_TO_SECTOR.get(activity.strip().lower())


def _parse_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


class RansomwareLiveSource(Source):
    name = "ransomware_live"
    # ransomware.live's own API is rate-limited to ~1 req/min/endpoint, but
    # this source only ever calls it once per fetch() - the pipeline's own
    # 30-minute cycle (scripts/run_cycle.ps1) is already far looser than
    # that, so no extra throttling is needed here.
    poll_interval_seconds = 1800

    def fetch(self) -> Iterable[Incident]:
        ingested_at = datetime.now(timezone.utc).isoformat()
        try:
            resp = requests.get(RECENT_VICTIMS_URL, headers=HEADERS, timeout=15)
        except Exception:
            log.exception("ransomware_live: request to %s failed", RECENT_VICTIMS_URL)
            return []
        if resp.status_code != 200:
            log.warning("ransomware_live: unexpected status %d from %s", resp.status_code, RECENT_VICTIMS_URL)
            return []
        try:
            rows = resp.json()
        except Exception:
            log.exception("ransomware_live: response was not valid JSON")
            return []
        if not isinstance(rows, list):
            log.warning("ransomware_live: expected a JSON list, got %s", type(rows).__name__)
            return []

        out = []
        for row in rows:
            try:
                inc = self._to_incident(row, ingested_at)
                if inc:
                    out.append(inc)
            except Exception:
                log.exception("ransomware_live: skipping malformed record")
        return out

    @staticmethod
    def _to_incident(row: dict, ingested_at: str) -> Optional[Incident]:
        # /recentvictims and /countryvictims/<cc> use different key names
        # for the same fields (live-verified 2026-11-01) - accept both.
        victim_name = row.get("victim") or row.get("post_title")
        group_name = row.get("group") or row.get("group_name")
        if not victim_name or not group_name:
            return None
        domain = row.get("domain") or row.get("website")
        claim_url = row.get("claim_url") or row.get("post_url")
        page_url = row.get("url")
        country = row.get("country") or None
        sector = _sector_from_activity(row.get("activity"))
        published_at = _parse_date(row.get("attackdate") or row.get("published"))
        description = row.get("description")
        if description in (None, "N/A", ""):
            description = None

        extra: dict = {"ransomware_group": group_name}
        if row.get("data_size"):
            extra["data_size"] = row["data_size"]
        if row.get("ransom"):
            extra["ransom"] = row["ransom"]

        return Incident(
            detected_at=ingested_at,
            published_at=published_at,
            source=SourceMeta(type="ransomware_tracker", name="ransomware.live", url=page_url),
            threat_actor=ThreatActor(name=group_name),
            victim=Victim(name=victim_name, website=domain, country_code=country, sector=sector),
            category="ransomware_leak",
            title=victim_name,
            description=description,
            evidence=Evidence(post_url=claim_url or page_url),
            confidence="claimed",
            tags=["ransomware", "ransomware_live"],
            extra=extra,
        )
