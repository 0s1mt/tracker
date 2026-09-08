"""Offline test for forum-candidate discovery - no network, a fake source
stub stands in for TelegramBridgeSource.fetch()."""
from forumtracker.discover import discover_candidates
from forumtracker.schema import Evidence, Incident, ThreatActor, Victim
from forumtracker.schema import Source as SourceMeta


def _incident(referenced_url: str) -> Incident:
    return Incident(
        detected_at="2026-09-08T00:00:00+00:00",
        published_at=None,
        source=SourceMeta(type="telegram", name="t.me/breachdetect", url=None),
        threat_actor=ThreatActor(name="unknown"),
        victim=Victim(name="whatever"),
        category="data_leak",
        title="whatever",
        description=None,
        evidence=Evidence(post_url=None),
        confidence="claimed",
        tags=[],
        extra={"referenced_source_url": referenced_url},
    )


class _FakeSource:
    def __init__(self, incidents):
        self._incidents = incidents

    def fetch(self):
        return self._incidents


def test_discover_ranks_new_hostnames_by_mentions():
    incidents = [
        _incident("https://ascarding.net/thread1"),
        _incident("https://ascarding.net/thread2"),
        _incident("https://ascarding.net/thread3"),
        _incident("https://onenewsite.example/thread"),
    ]
    candidates = discover_candidates(_FakeSource(incidents))
    hosts = [c["hostname"] for c in candidates]
    assert hosts[0] == "ascarding.net"
    assert candidates[0]["mentions"] == 3
    assert "onenewsite.example" in hosts


def test_discover_excludes_already_known_forums():
    # bdfclub.com is already in FORUM_POOL - must never show up as "new"
    incidents = [_incident("https://bdfclub.com/some-thread")]
    candidates = discover_candidates(_FakeSource(incidents))
    assert candidates == []


def test_discover_handles_missing_referenced_url():
    incident = _incident("https://x.example/y")
    incident.extra = {}
    candidates = discover_candidates(_FakeSource([incident]))
    assert candidates == []
