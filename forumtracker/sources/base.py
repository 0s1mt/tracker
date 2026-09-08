"""Common interface every ingestion source must implement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..schema import Incident


class Source(ABC):
    name: str
    poll_interval_seconds: int = 300

    @abstractmethod
    def fetch(self) -> Iterable[Incident]:
        """Return the current batch of incidents as normalized Incident objects.

        Must not raise on network/parsing errors for individual items - log and skip
        instead, so one bad record never breaks the whole poll cycle.
        """
        raise NotImplementedError

    def enrich(self, incident: dict) -> dict:
        """Optional post-dedup hook, called once per NEW incident (after
        dedup, so a re-seen item never triggers an extra network call).
        Default is a no-op passthrough; sources that can fetch a fuller
        record (e.g. visiting a thread page for the real post body)
        override this. Must not raise - log and return `incident`
        unchanged on failure.
        """
        return incident

    def enrich_batch(self, incidents: list[dict]) -> list[dict]:
        """Same contract as `enrich()`, applied to the whole cycle's new
        incidents at once. Default just calls `enrich()` per item - override
        this instead when enrichment needs a resource that's expensive to
        set up per-item (e.g. one shared browser instance for a batch of
        screenshots) rather than per-incident. Must not raise.
        """
        out = incidents
        for i, inc in enumerate(out):
            out[i] = self.enrich(inc)
        return out
