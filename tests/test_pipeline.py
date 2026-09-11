"""Offline tests for pipeline-level orchestration logic (no network)."""
import forumtracker.pipeline as pipeline
from forumtracker.dedup import SeenStore
from forumtracker.forum_stats import StatsStore
from forumtracker.health import HealthStore
from forumtracker.identity_correlation import IdentityStore
from forumtracker.pipeline import _apply_ransomware_intel, build_alerts, run_full_cycle
from forumtracker.schema import Evidence, Incident, ThreatActor, Victim
from forumtracker.schema import Source as SourceMeta


def _incident(incident_id, reply_count=None, category="ransomware_leak"):
    return {
        "incident_id": incident_id,
        "detected_at": "2026-09-08T10:00:00+00:00",
        "source": {"type": "forum", "name": "forum:darkforums", "url": None},
        "threat_actor": {"name": "actor", "aliases": []},
        "victim": {"name": "victim", "country_code": None, "sector": None},
        "category": category,
        "title": "title",
        "description": "description",
        "evidence": {"post_url": "https://darkforums.as/Thread-x", "screenshot_url": None},
        "confidence": "claimed",
        "tags": [],
        "extra": {} if reply_count is None else {"reply_count": reply_count},
    }


def test_build_alerts_sorts_most_popular_first():
    incidents = [
        _incident("low", reply_count=0),
        _incident("high", reply_count=5000),
        _incident("mid", reply_count=50),
    ]

    alerts = build_alerts(incidents)

    ids_in_order = [a["incident_id"] for a in alerts]
    assert ids_in_order == ["high", "mid", "low"]


def test_build_alerts_only_includes_alert_worthy_incidents():
    incidents = [_incident("worthy"), {**_incident("bland", category="forum_post"), "title": "welcome thread"}]
    alerts = build_alerts(incidents)
    assert [a["incident_id"] for a in alerts] == ["worthy"]


def test_apply_ransomware_intel_upgrades_forum_post_category():
    incident = {
        **_incident("r1", category="forum_post"),
        "title": "LockBit breached victimcorp.com, negotiation chat link inside",
        "description": None,
    }

    _apply_ransomware_intel(incident)

    assert incident["category"] == "ransomware_leak"
    assert incident["extra"]["ransomware_group"] == "lockbit"
    assert "ransomware" in incident["tags"]
    assert incident["victim"]["website"] == "victimcorp.com"


def test_apply_ransomware_intel_never_downgrades_existing_category():
    incident = {**_incident("r2", category="data_breach"), "title": "LockBit involved somehow", "description": None}

    _apply_ransomware_intel(incident)

    assert incident["category"] == "data_breach"


def test_apply_ransomware_intel_noop_on_unrelated_forum_post():
    incident = {**_incident("r3", category="forum_post"), "title": "welcome new members", "description": None}

    _apply_ransomware_intel(incident)

    assert incident["category"] == "forum_post"
    assert "ransomware_group" not in incident["extra"]


class _FakeSource:
    """Minimal Source stand-in - no real Source.fetch() ever touches the
    network for one item, but run_full_cycle must work against the real
    Source contract (fetch/enrich_batch), not a shortcut."""
    name = "fake_forum"

    def __init__(self, incidents):
        self._incidents = incidents

    def fetch(self):
        return self._incidents

    def enrich_batch(self, incidents):
        return incidents


def _forum_incident(incident_id_seed, forum, author):
    return Incident(
        detected_at="2026-09-11T00:00:00+00:00",
        published_at=None,
        source=SourceMeta(type="forum", name=f"forum:{forum}", url=f"https://{forum}/{incident_id_seed}"),
        threat_actor=ThreatActor(name=author),
        victim=Victim(name=incident_id_seed),
        category="forum_post",
        title=incident_id_seed,
        description="irrelevant body text",
        evidence=Evidence(post_url=f"https://{forum}/{incident_id_seed}"),
        confidence="claimed",
        tags=["forum", forum],
        extra={},
    )


def _stores(tmp_path):
    return (
        SeenStore(tmp_path / "seen.sqlite3"),
        HealthStore(tmp_path / "health.sqlite3"),
        StatsStore(tmp_path / "stats.sqlite3"),
        IdentityStore(tmp_path / "identity.sqlite3"),
    )


def test_run_full_cycle_wires_identity_correlation(tmp_path, monkeypatch):
    # health/stats both touch real forums over the network - stub them out
    # so this test stays offline and fast, same discipline as
    # test_discover.py's _FakeSource for TelegramBridgeSource.
    monkeypatch.setattr(pipeline, "check_forum_health", lambda health_store: [])
    monkeypatch.setattr(pipeline, "collect_snapshots", lambda urls, store: {})
    monkeypatch.setattr(pipeline, "OUTPUT_FEED", tmp_path / "incidents.jsonl")
    monkeypatch.setattr(pipeline, "ALERTS_FEED", tmp_path / "alerts.jsonl")

    sources = [_FakeSource([
        _forum_incident("post-a", "darkforums", "EvilGhost"),
        _forum_incident("post-b", "carders.biz", "EvilGhost"),
    ])]
    store, health_store, stats_store, identity_store = _stores(tmp_path)

    summary = run_full_cycle(sources, store, health_store, stats_store, identity_store)

    assert summary["new_incidents"] == 2
    assert len(summary["new_identity_matches"]) == 1
    assert summary["new_identity_matches"][0]["username"] == ["EvilGhost"]
    assert set(summary["new_identity_matches"][0]["forums"]) == {"darkforums", "carders.biz"}
    # identity_matches is the full current-state rollup (adds
    # first_matched_at); new_identity_matches is just this cycle's delta.
    assert len(summary["identity_matches"]) == 1
    assert summary["identity_matches"][0]["username"] == ["EvilGhost"]
    assert summary["newly_dead_forums"] == []
    assert summary["health_report"] == []


def test_run_full_cycle_second_call_does_not_duplicate_incidents(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "check_forum_health", lambda health_store: [])
    monkeypatch.setattr(pipeline, "collect_snapshots", lambda urls, store: {})
    monkeypatch.setattr(pipeline, "OUTPUT_FEED", tmp_path / "incidents.jsonl")
    monkeypatch.setattr(pipeline, "ALERTS_FEED", tmp_path / "alerts.jsonl")

    incidents = [_forum_incident("post-a", "darkforums", "EvilGhost")]
    sources = [_FakeSource(incidents)]
    store, health_store, stats_store, identity_store = _stores(tmp_path)

    first = run_full_cycle(sources, store, health_store, stats_store, identity_store)
    second = run_full_cycle(sources, store, health_store, stats_store, identity_store)

    assert first["new_incidents"] == 1
    assert second["new_incidents"] == 0  # SeenStore dedup, same as before this refactor


def test_run_full_cycle_survives_a_broken_source(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "check_forum_health", lambda health_store: [])
    monkeypatch.setattr(pipeline, "collect_snapshots", lambda urls, store: {})
    monkeypatch.setattr(pipeline, "OUTPUT_FEED", tmp_path / "incidents.jsonl")
    monkeypatch.setattr(pipeline, "ALERTS_FEED", tmp_path / "alerts.jsonl")

    class _BrokenSource:
        name = "broken"

        def fetch(self):
            raise RuntimeError("network exploded")

    store, health_store, stats_store, identity_store = _stores(tmp_path)
    summary = run_full_cycle([_BrokenSource()], store, health_store, stats_store, identity_store)

    assert summary["new_incidents"] == 0
