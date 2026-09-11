"""Offline tests for cross-forum username correlation - no network."""
import json

from forumtracker.identity_correlation import IdentityStore, backfill_from_feed, normalize_username


def test_normalize_username_lowercases_and_strips():
    assert normalize_username("  EvilGhost  ") == "evilghost"


def test_normalize_username_rejects_generic_role_names():
    assert normalize_username("Admin") is None
    assert normalize_username("unknown") is None
    assert normalize_username("") is None
    assert normalize_username(None) is None


def test_record_returns_none_for_a_single_forum_sighting(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    match = store.record("EvilGhost", "darkforums")
    assert match is None
    store.close()


def test_record_returns_match_on_second_distinct_forum(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    assert store.record("EvilGhost", "darkforums") is None
    match = store.record("evilghost", "carders.biz")  # case-insensitive same person
    assert match is not None
    assert match.username_norm == "evilghost"
    assert set(match.forums) == {"darkforums", "carders.biz"}
    store.close()


def test_record_fires_only_once_per_username_not_every_subsequent_sighting(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    store.record("EvilGhost", "darkforums")
    first = store.record("EvilGhost", "carders.biz")
    assert first is not None
    # A third forum sighting still updates the index but must not re-fire -
    # callers notify once per discovery, not once per sighting after that.
    second = store.record("EvilGhost", "mmgp")
    assert second is None
    assert set(store.all_matches()[0].forums) == {"darkforums", "carders.biz", "mmgp"}
    store.close()


def test_record_incidents_extracts_forum_and_username_from_incident_shape(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    incidents = [
        {"incident_id": "a", "source": {"name": "forum:darkforums"}, "threat_actor": {"name": "EvilGhost"},
         "detected_at": "2026-09-10T00:00:00+00:00"},
        {"incident_id": "b", "source": {"name": "forum:carders.biz"}, "threat_actor": {"name": "evilghost"},
         "detected_at": "2026-09-11T00:00:00+00:00"},
    ]
    matches = store.record_incidents(incidents)
    assert len(matches) == 1
    assert matches[0].username_norm == "evilghost"
    store.close()


def test_record_incidents_skips_non_forum_sources(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    incidents = [
        {"incident_id": "a", "source": {"name": "t.me/breachdetect"}, "threat_actor": {"name": "EvilGhost"}},
    ]
    matches = store.record_incidents(incidents)
    assert matches == []
    assert store.all_matches() == []
    store.close()


def test_record_incidents_never_raises_on_malformed_incident(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    matches = store.record_incidents([{"incident_id": "broken"}, None])
    assert matches == []
    store.close()


def test_backfill_from_feed_seeds_matches_from_jsonl(tmp_path):
    feed = tmp_path / "incidents.jsonl"
    rows = [
        {"incident_id": "a", "source": {"name": "forum:darkforums"}, "threat_actor": {"name": "EvilGhost"}},
        {"incident_id": "b", "source": {"name": "forum:carders.biz"}, "threat_actor": {"name": "EvilGhost"}},
        {"incident_id": "c", "source": {"name": "forum:mmgp"}, "threat_actor": {"name": "SoloUser"}},
    ]
    feed.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    store = IdentityStore(tmp_path / "identity.sqlite3")
    matches = backfill_from_feed(store, feed)
    assert len(matches) == 1
    assert matches[0].username_norm == "evilghost"
    store.close()


def test_backfill_from_feed_missing_file_returns_empty(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    assert backfill_from_feed(store, tmp_path / "nope.jsonl") == []
    store.close()
