"""Unified incident JSON schema for the forum tracker.

Same envelope shape as the parent DarkWebFeed pipeline (kept compatible on
purpose, so output from this standalone tracker can still be merged back into
a shared incident stream downstream), but this copy only needs to serve
forum-sourced records - see ../schemas/incident.schema.json for the formal,
narrowed contract.
"""
from __future__ import annotations

import hashlib
from dataclasses import InitVar, dataclass, field, asdict
from typing import Optional


@dataclass
class Source:
    type: str          # "forum" for this tracker
    name: str          # e.g. "forum:turkhackteam"
    url: Optional[str] = None


@dataclass
class ThreatActor:
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Victim:
    name: str
    website: Optional[str] = None
    country_code: Optional[str] = None
    sector: Optional[str] = None


@dataclass
class Evidence:
    post_url: Optional[str] = None
    screenshot_url: Optional[str] = None


@dataclass
class Incident:
    detected_at: str                 # ISO8601 - when our pipeline ingested it
    published_at: Optional[str]      # ISO8601 - original source timestamp
    source: Source
    threat_actor: ThreatActor
    victim: Victim
    category: str                    # "forum_post" for this tracker
    title: str
    description: Optional[str]
    evidence: Evidence
    confidence: str                  # "claimed" | "verified"
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    # Not a stored field (InitVar - absent from asdict()/to_dict(), so it
    # never leaks into the JSON schema) - an opt-in, source-verified
    # forum-native identifier (e.g. a thread's RSS/page permalink) a
    # caller passes when it KNOWS that value is stable and unique per
    # item. Deliberately not inferred from `evidence.post_url` generically:
    # some sources (ransomware_live.py's claim_url-less fallback to a
    # shared group page_url; browser_forum.py's chat_message, which has no
    # per-message permalink at all) reuse the same post_url across
    # genuinely distinct incidents, where keying on it alone would
    # silently collapse them. Only underground_forums.py and
    # browser_forum.py's forum_post construction pass this today - see
    # their `_normalize`/`_fetch_site`, both using each item's own
    # verified-unique thread permalink.
    stable_key: InitVar[Optional[str]] = None
    incident_id: str = field(init=False, default="")

    def __post_init__(self, stable_key: Optional[str]) -> None:
        # Preferring a real forum-native ID over title/author/date fixes a
        # genuine false-duplicate risk the old scheme had: many forums'
        # RSS <pubDate> is last-reply time, not thread-creation time, so
        # the same thread getting a new reply between polls changed
        # `published_at` and therefore silently minted a brand new
        # incident_id for an already-seen thread. A thread's own permalink
        # doesn't change when it gets bumped, so this basis doesn't either.
        if stable_key:
            basis = f"{self.source.name}|{stable_key}"
        else:
            basis = f"{self.source.name}|{self.threat_actor.name}|{self.victim.name}|{self.published_at}"
        self.incident_id = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["incident_id"] = self.incident_id
        return d
