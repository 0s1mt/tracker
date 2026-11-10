"""Offline tests for pipeline-level orchestration logic (no network)."""
import forumtracker.pipeline as pipeline
from forumtracker.collection_health import CollectionHealthStore
from forumtracker.dedup import SeenStore
from forumtracker.forum_stats import StatsStore
from forumtracker.health import HealthStore
from forumtracker.identity_correlation import IdentityStore
from forumtracker.member_directory import MemberDirectoryStore
from forumtracker.pipeline import _apply_ransomware_intel, build_alerts, build_sources, run_full_cycle
from forumtracker.sources.browser_forum import BrowserForumSource
from forumtracker.sources.ransomware_live import RansomwareLiveSource
from forumtracker.sources.underground_forums import UndergroundForumSource
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


def test_apply_ransomware_intel_derives_country_code_from_victim_domain():
    incident = {
        **_incident("r4", category="forum_post"),
        "title": "Selling database of mairie-exemple.fr, 50k residents",
        "description": None,
    }

    _apply_ransomware_intel(incident)

    assert incident["victim"]["website"] == "mairie-exemple.fr"
    assert incident["victim"]["country_code"] == "FR"


def test_apply_ransomware_intel_derives_country_code_from_title_text_when_no_domain_present():
    incident = {
        **_incident("r5", category="forum_post"),
        "title": "Turkey GSM Database - 11,000,000 rows",
        "description": None,
    }

    _apply_ransomware_intel(incident)

    assert "website" not in incident["victim"]
    assert incident["victim"]["country_code"] == "TR"


def test_apply_ransomware_intel_derives_sector_from_domain_label():
    incident = {
        **_incident("r6", category="forum_post"),
        "title": "Government portal spk.gov.tr leaked, 2M citizen records",
        "description": None,
    }

    _apply_ransomware_intel(incident)

    assert incident["victim"]["website"] == "spk.gov.tr"
    assert incident["victim"]["sector"] == "government"


def test_apply_ransomware_intel_derives_sector_from_text_when_no_domain_present():
    incident = {
        **_incident("r7", category="forum_post"),
        "title": "Turkey GSM Database - 11,000,000 rows",
        "description": None,
    }

    _apply_ransomware_intel(incident)

    assert "website" not in incident["victim"]
    assert incident["victim"]["sector"] == "telecom"


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
        MemberDirectoryStore(tmp_path / "member_directory.sqlite3"),
        CollectionHealthStore(tmp_path / "collection_health.sqlite3"),
    )


def _stub_network_touching_steps(monkeypatch, tmp_path):
    # health/stats/member-directory all touch real forums over the network
    # - stub them out so these tests stay offline and fast, same discipline
    # as test_discover.py's _FakeSource for TelegramBridgeSource.
    monkeypatch.setattr(pipeline, "check_forum_health", lambda health_store: [])
    monkeypatch.setattr(pipeline, "collect_snapshots", lambda urls, store: {})
    monkeypatch.setattr(pipeline, "crawl_batch", lambda store, pool=None, pages_per_forum=3: {})
    monkeypatch.setattr(pipeline, "OUTPUT_FEED", tmp_path / "incidents.jsonl")
    monkeypatch.setattr(pipeline, "ALERTS_FEED", tmp_path / "alerts.jsonl")


def test_run_full_cycle_wires_identity_correlation(tmp_path, monkeypatch):
    _stub_network_touching_steps(monkeypatch, tmp_path)

    sources = [_FakeSource([
        _forum_incident("post-a", "darkforums", "EvilGhost"),
        _forum_incident("post-b", "carders.biz", "EvilGhost"),
    ])]
    stores = _stores(tmp_path)
    store, health_store, stats_store, identity_store, member_directory_store, collection_health_store = stores

    summary = run_full_cycle(
        sources, store, health_store, stats_store, identity_store, member_directory_store, collection_health_store,
    )

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
    assert summary["member_directory_crawl"] == {}
    assert summary["member_directory_total"] == 0
    assert summary["registered_cross_forum"] == []


def test_run_full_cycle_second_call_does_not_duplicate_incidents(tmp_path, monkeypatch):
    _stub_network_touching_steps(monkeypatch, tmp_path)

    incidents = [_forum_incident("post-a", "darkforums", "EvilGhost")]
    sources = [_FakeSource(incidents)]
    stores = _stores(tmp_path)
    store, health_store, stats_store, identity_store, member_directory_store, collection_health_store = stores

    first = run_full_cycle(
        sources, store, health_store, stats_store, identity_store, member_directory_store, collection_health_store,
    )
    second = run_full_cycle(
        sources, store, health_store, stats_store, identity_store, member_directory_store, collection_health_store,
    )

    assert first["new_incidents"] == 1
    assert second["new_incidents"] == 0  # SeenStore dedup, same as before this refactor


def test_run_full_cycle_survives_a_broken_source(tmp_path, monkeypatch):
    _stub_network_touching_steps(monkeypatch, tmp_path)

    class _BrokenSource:
        name = "broken"

        def fetch(self):
            raise RuntimeError("network exploded")

    stores = _stores(tmp_path)
    store, health_store, stats_store, identity_store, member_directory_store, collection_health_store = stores
    summary = run_full_cycle(
        [_BrokenSource()], store, health_store, stats_store, identity_store, member_directory_store,
        collection_health_store,
    )

    assert summary["new_incidents"] == 0


def test_run_full_cycle_reports_collection_health_for_sources_that_track_it(tmp_path, monkeypatch):
    _stub_network_touching_steps(monkeypatch, tmp_path)

    class _FakeUndergroundSource:
        """Mimics UndergroundForumSource's last_cycle_health contract (set
        inside its own fetch()) without touching the network - verifies
        run_full_cycle/run_once's merge of per-site fetch outcome with
        real new/duplicate counts derived from the incident batch.
        """
        name = "underground_forums"

        def fetch(self):
            self.last_cycle_health = {
                "darkforums": {
                    "status": "healthy", "last_attempt": "2026-11-07T00:00:00+00:00",
                    "last_success": "2026-11-07T00:00:00+00:00", "posts_discovered": 2, "parse_errors": 0,
                    "http_errors": 0,
                },
                "deadforum": {
                    "status": "unreachable", "last_attempt": "2026-11-07T00:00:00+00:00",
                    "last_success": None, "posts_discovered": 0, "parse_errors": 0, "http_errors": 1,
                },
            }
            return [
                _forum_incident("post-a", "darkforums", "actor1"),
                _forum_incident("post-b", "darkforums", "actor2"),
            ]

        def enrich_batch(self, incidents):
            return incidents

    stores = _stores(tmp_path)
    store, health_store, stats_store, identity_store, member_directory_store, collection_health_store = stores

    summary = run_full_cycle(
        [_FakeUndergroundSource()], store, health_store, stats_store, identity_store, member_directory_store,
        collection_health_store,
    )

    by_site = {row["site"]: row for row in summary["collection_health"]}
    assert by_site["darkforums"]["status"] == "healthy"
    assert by_site["darkforums"]["new_records"] == 2
    assert by_site["darkforums"]["duplicates"] == 0
    assert by_site["deadforum"]["status"] == "unreachable"
    assert by_site["deadforum"]["new_records"] == 0
    assert by_site["deadforum"]["http_errors"] == 1

    # A second cycle with the same two incidents: darkforums is still
    # "healthy" (feed reachable), but both posts are now duplicates, not
    # new - the thing collection_health must NOT confuse with a failure.
    second = run_full_cycle(
        [_FakeUndergroundSource()], store, health_store, stats_store, identity_store, member_directory_store,
        collection_health_store,
    )
    second_by_site = {row["site"]: row for row in second["collection_health"]}
    assert second_by_site["darkforums"]["status"] == "healthy"
    assert second_by_site["darkforums"]["new_records"] == 0
    assert second_by_site["darkforums"]["duplicates"] == 2


def test_build_sources_default_includes_all_source_types():
    sources = build_sources()
    names = {s.name for s in sources}
    assert names == {"underground_forums", "browser_forums", "telegram_bridge", "ransomware_live"}


def test_build_sources_only_forums_restricts_to_underground_forums_alone():
    sources = build_sources(only_forums=["darkforums", "breachforums"])
    assert len(sources) == 1
    assert isinstance(sources[0], UndergroundForumSource)
    assert not any(isinstance(s, RansomwareLiveSource) for s in sources)


def test_build_sources_only_forums_matches_leads_subforum_variants():
    sources = build_sources(only_forums=["darkforums"])
    site_names = set(sources[0].sites.keys())
    assert "darkforums" in site_names
    assert "darkforums-leads" in site_names
    assert "breachforums" not in site_names


def test_build_sources_only_forums_with_no_match_yields_empty_pool():
    # A name matching neither FORUM_POOL nor BROWSER_FORUM_POOL must degrade
    # gracefully to a single empty-site UndergroundForumSource, not raise,
    # matching the rest of this pipeline's never-raise discipline.
    sources = build_sources(only_forums=["totally-unknown-forum-xyz"])
    assert len(sources) == 1
    assert sources[0].sites == {}


def test_build_sources_only_forums_matches_browser_pool_too():
    # "pwnforums" matches BROWSER_FORUM_POOL's "pwnforums.st" (Cloudflare/
    # Anubis-gated, browser-based - see browser_forum.py) even though it
    # matches nothing in FORUM_POOL (RSS-based) - a previous version of
    # build_sources only ever consulted FORUM_POOL here, which meant
    # test_3forum.yml's "--only-forums darkforums,breachforums,pwnforums"
    # could never actually collect pwnforums regardless of cookie validity.
    sources = build_sources(only_forums=["pwnforums"])
    assert len(sources) == 2
    assert isinstance(sources[0], UndergroundForumSource)
    assert sources[0].sites == {}
    assert isinstance(sources[1], BrowserForumSource)
    assert set(sources[1].sites.keys()) == {"pwnforums.st"}
