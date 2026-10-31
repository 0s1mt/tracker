"""Offline tests for dashboard.py (no network - builds from fixture data/ state)."""
import json

from forumtracker.dashboard import build_dashboard, build_dashboard_data, render_dashboard
from forumtracker.forum_stats import StatsStore
from forumtracker.health import CheckResult, HealthStore
from forumtracker.identity_correlation import IdentityStore
from forumtracker.member_directory import MemberDirectoryStore
from forumtracker.sources.underground_forums import FORUM_POOL


def test_build_dashboard_data_on_empty_data_dir_returns_zeroed_bundle(tmp_path):
    bundle = build_dashboard_data(tmp_path)

    assert bundle["kpi"]["total_incidents"] == 0
    assert bundle["kpi"]["total_alerts"] == 0
    assert bundle["kpi"]["identity_total_users"] == 0
    assert bundle["kpi"]["member_directory_total"] == 0
    # every pool forum still shows up, just with "unknown" status and no history
    assert bundle["kpi"]["forums_monitored"] == len(bundle["forums"])
    assert all(f["status"] == "unknown" for f in bundle["forums"])
    assert all(f["history"] == [] for f in bundle["forums"])


def test_build_dashboard_data_aggregates_real_fixture_state(tmp_path):
    any_pool_forum = next(iter(FORUM_POOL))

    health_store = HealthStore(tmp_path / "health.sqlite3")
    health_store.record(CheckResult(any_pool_forum, "https://example.test", "up", 200, None, "2026-09-20T00:00:00+00:00"))
    health_store.close()

    stats_store = StatsStore(tmp_path / "forum_stats.sqlite3")
    stats_store.record(any_pool_forum, {"threads": 10, "messages": 100, "members": 5}, "2026-09-19T00:00:00+00:00")
    stats_store.record(any_pool_forum, {"threads": 12, "messages": 130, "members": 5}, "2026-09-20T00:00:00+00:00")
    stats_store.close()

    identity_store = IdentityStore(tmp_path / "identity.sqlite3")
    identity_store.record("EvilGhost", "darkforums", seen_at="2026-09-20T00:00:00+00:00")
    identity_store.record("EvilGhost", "carders.biz", seen_at="2026-09-20T00:01:00+00:00")
    identity_store.close()

    member_store = MemberDirectoryStore(tmp_path / "member_directory.sqlite3")
    member_store.record_members("carders.biz", [{"username": "shadowfox", "member_id": "1"}])
    member_store.record_members("leetforums", [{"username": "shadowfox", "member_id": "9"}])
    member_store.close()

    (tmp_path / "incidents.jsonl").write_text(
        json.dumps({
            "incident_id": "abc123",
            "detected_at": "2026-09-20T00:00:00+00:00",
            "source": {"type": "forum", "name": f"forum:{any_pool_forum}", "url": None},
            "threat_actor": {"name": "EvilGhost"},
            "victim": {"name": "victim", "country_code": "FR"},
            "category": "forum_post",
            "title": "test thread",
            "description": None,
            "evidence": {"post_url": "https://example.test/t/1"},
            "confidence": "claimed",
            "tags": ["forum"],
            "extra": {},
        }) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "alerts.jsonl").write_text(
        json.dumps({
            "incident_id": "abc123",
            "rich": {
                "target_title": "test thread", "threat_actor": "EvilGhost",
                "intel_source": f"forum:{any_pool_forum}", "severity": "HIGH",
                "risk_score": 9, "popularity_score": 5, "reply_count": 42,
                "matched_entities": ["fullz"], "detection_date": "2026-09-20T00:00:00+00:00",
                "source_link": "https://example.test/t/1",
            },
        }) + "\n",
        encoding="utf-8",
    )

    bundle = build_dashboard_data(tmp_path)

    assert bundle["kpi"]["total_incidents"] == 1
    assert bundle["kpi"]["total_alerts"] == 1
    assert bundle["kpi"]["identity_cross_forum"] == 1
    assert bundle["kpi"]["member_directory_cross_forum"] == 1
    assert bundle["kpi"]["member_directory_total"] == 2
    assert bundle["top_actors"] == [{"name": "EvilGhost", "count": 1}]
    assert bundle["top_countries"] == [{"code": "FR", "count": 1}]
    assert bundle["alerts"][0]["cc"] == "FR"  # joined back from the matching incident

    forum_entry = next(f for f in bundle["forums"] if f["name"] == any_pool_forum)
    assert forum_entry["status"] == "up"
    assert forum_entry["incident_count"] == 1
    assert len(forum_entry["history"]) == 2
    assert forum_entry["latest"]["messages"] == 130

    assert bundle["identity_matches"][0]["forums"] == ["carders.biz", "darkforums"]
    assert bundle["member_directory_cross_forum"][0]["forums"] == ["carders.biz", "leetforums"]


def test_render_dashboard_escapes_closing_script_tags_in_embedded_data(tmp_path):
    (tmp_path / "incidents.jsonl").write_text(
        json.dumps({
            "incident_id": "x", "detected_at": "2026-09-20T00:00:00+00:00",
            "source": {"type": "forum", "name": "forum:test", "url": None},
            "threat_actor": {"name": "a"}, "victim": {"name": "v"},
            "category": "forum_post", "title": "</script><script>alert(1)</script>",
            "description": None, "evidence": {"post_url": None},
            "confidence": "claimed", "tags": [], "extra": {},
        }) + "\n",
        encoding="utf-8",
    )

    html = render_dashboard(tmp_path)

    assert "</script><script>alert(1)</script>" not in html
    assert "<\\/script>" in html


def test_build_dashboard_writes_a_real_file(tmp_path):
    out = build_dashboard(tmp_path, tmp_path / "reports" / "dashboard.html")

    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "DARKWEB FORUM TRACKER" in content
    assert "dashboard-data" in content
