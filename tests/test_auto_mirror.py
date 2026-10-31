"""Offline tests for automatic mirror adoption - no network; UndergroundForumSource.fetch
is monkeypatched so these exercise the adoption logic, not the real RSS scraper."""
from forumtracker.auto_mirror import AutoMirrorStore, try_adopt_mirror
from forumtracker.schema import Evidence, Incident, ThreatActor, Victim
from forumtracker.schema import Source as SourceMeta
from forumtracker.sources.underground_forums import UndergroundForumSource


def _fake_incident() -> Incident:
    return Incident(
        detected_at="2026-09-08T00:00:00+00:00",
        published_at=None,
        source=SourceMeta(type="forum", name="forum:darkforums", url=None),
        threat_actor=ThreatActor(name="someone"),
        victim=Victim(name="whatever"),
        category="forum_post",
        title="whatever",
        description=None,
        evidence=Evidence(post_url=None),
        confidence="claimed",
        tags=[],
        extra={},
    )


def test_adopts_high_similarity_candidate_that_verifies_live(tmp_path, monkeypatch):
    monkeypatch.setattr(UndergroundForumSource, "fetch", lambda self: [_fake_incident()])
    store = AutoMirrorStore(tmp_path / "auto_mirrors.json")
    candidates = [{"hostname": "darkforums-official.cc", "mentions": 3, "example_url": "https://x", "similarity": 0.85}]

    adopted = try_adopt_mirror("darkforums", store, candidates=candidates)

    assert adopted["hostname"] == "darkforums-official.cc"
    assert adopted["incidents_seen"] == 1
    assert store.already_adopted_for("darkforums")["hostname"] == "darkforums-official.cc"


def test_never_adopts_below_similarity_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(UndergroundForumSource, "fetch", lambda self: [_fake_incident()])
    store = AutoMirrorStore(tmp_path / "auto_mirrors.json")
    candidates = [{"hostname": "unrelated-site.example", "mentions": 20, "example_url": "https://x", "similarity": 0.5}]

    adopted = try_adopt_mirror("darkforums", store, candidates=candidates)

    assert adopted is None
    assert store.already_adopted_for("darkforums") is None


def test_never_adopts_a_candidate_that_fails_live_verification(tmp_path, monkeypatch):
    # high similarity but the "forum" turns out to have no real RSS/content -
    # a parked domain or phishing clone must not be adopted just on name match.
    monkeypatch.setattr(UndergroundForumSource, "fetch", lambda self: [])
    store = AutoMirrorStore(tmp_path / "auto_mirrors.json")
    candidates = [{"hostname": "darkforums-parked.cc", "mentions": 1, "example_url": "https://x", "similarity": 0.9}]

    adopted = try_adopt_mirror("darkforums", store, candidates=candidates)

    assert adopted is None


def test_idempotent_skips_already_adopted_forum(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(UndergroundForumSource, "fetch", lambda self: calls.append(1) or [_fake_incident()])
    store = AutoMirrorStore(tmp_path / "auto_mirrors.json")
    candidates = [{"hostname": "darkforums-official.cc", "mentions": 3, "example_url": "https://x", "similarity": 0.85}]

    try_adopt_mirror("darkforums", store, candidates=candidates)
    second = try_adopt_mirror("darkforums", store, candidates=candidates)

    assert second is None
    assert len(calls) == 1  # no re-verification fetch on the second call


def test_falls_through_to_next_candidate_when_first_fails_verification(tmp_path, monkeypatch):
    def fake_fetch(self):
        if "fails" in list(self.sites.values())[0]["homepage"]:
            return []
        return [_fake_incident()]

    monkeypatch.setattr(UndergroundForumSource, "fetch", fake_fetch)
    store = AutoMirrorStore(tmp_path / "auto_mirrors.json")
    candidates = [
        {"hostname": "darkforums-fails.cc", "mentions": 1, "example_url": "https://x", "similarity": 0.95},
        {"hostname": "darkforums-official.cc", "mentions": 2, "example_url": "https://x", "similarity": 0.85},
    ]

    adopted = try_adopt_mirror("darkforums", store, candidates=candidates)

    assert adopted["hostname"] == "darkforums-official.cc"


def test_sites_returns_shape_expected_by_underground_forum_source(tmp_path):
    store = AutoMirrorStore(tmp_path / "auto_mirrors.json")
    store.record("darkforums", {"hostname": "darkforums-official.cc", "similarity": 0.85})

    sites = store.sites()

    assert sites == {"darkforums": {"homepage": "https://darkforums-official.cc", "rss": None}}


def test_store_persists_across_instances(tmp_path):
    path = tmp_path / "auto_mirrors.json"
    AutoMirrorStore(path).record("darkforums", {"hostname": "darkforums-official.cc", "similarity": 0.85})

    reloaded = AutoMirrorStore(path)

    assert reloaded.already_adopted_for("darkforums")["hostname"] == "darkforums-official.cc"
