"""Unified incident JSON schema for the forum tracker.

Same envelope shape as the parent DarkWebFeed pipeline (kept compatible on
purpose, so output from this standalone tracker can still be merged back into
a shared incident stream downstream), but this copy only needs to serve
forum-sourced records - see ../schemas/incident.schema.json for the formal,
narrowed contract.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
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
    incident_id: str = field(init=False, default="")

    def __post_init__(self) -> None:
        basis = f"{self.source.name}|{self.threat_actor.name}|{self.victim.name}|{self.published_at}"
        self.incident_id = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["incident_id"] = self.incident_id
        return d
