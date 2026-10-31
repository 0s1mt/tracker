"""Offline tests for the activity/reputation ranking layer - pure functions over
plain dicts, no stores or network involved."""
from datetime import datetime, timezone

from forumtracker.ranking import actor_reputation, forum_activity, per_forum_top_actors, top_actors, top_countries, top_sectors

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _inc(src, actor, days_ago, cc=None, sector=None):
    dt = datetime(2026, 11, 1, tzinfo=timezone.utc).fromtimestamp(NOW.timestamp() - days_ago * 86400, tz=timezone.utc)
    return {"src": src, "a": actor, "d": dt.isoformat(), "cc": cc, "sector": sector}


def test_forum_activity_weights_recent_week_over_older_activity():
    incidents = [
        _inc("forum:hot", "x", days_ago=1),
        _inc("forum:hot", "x", days_ago=2),
        _inc("forum:hot", "x", days_ago=3),
        _inc("forum:cold", "y", days_ago=25),
        _inc("forum:cold", "y", days_ago=26),
        _inc("forum:cold", "y", days_ago=27),
        _inc("forum:cold", "y", days_ago=28),
    ]
    ranked = forum_activity(incidents, now=NOW)
    names = [r["forum"] for r in ranked]
    assert names[0] == "hot"  # 3 incidents this week outrank 4 incidents three weeks ago
    hot = next(r for r in ranked if r["forum"] == "hot")
    cold = next(r for r in ranked if r["forum"] == "cold")
    assert hot["recent_7d"] == 3
    assert hot["activity_score"] == 3 * 3 + 3
    assert cold["recent_7d"] == 0
    assert cold["recent_30d"] == 4
    assert cold["activity_score"] == 4


def test_forum_activity_ignores_incidents_older_than_30_days():
    incidents = [_inc("forum:stale", "x", days_ago=45)]
    ranked = forum_activity(incidents, now=NOW)
    row = next(r for r in ranked if r["forum"] == "stale")
    assert row["recent_7d"] == 0
    assert row["recent_30d"] == 0
    assert row["activity_score"] == 0
    assert row["total"] == 1  # lifetime total is unaffected by the recency windows


def test_forum_activity_ignores_non_forum_sources():
    incidents = [{"src": "t.me/breachdetect", "a": "x", "d": NOW.isoformat(), "cc": None}]
    assert forum_activity(incidents, now=NOW) == []


def test_per_forum_top_actors_ranks_within_each_forum_separately():
    incidents = [
        _inc("forum:a", "Alice", days_ago=1), _inc("forum:a", "Alice", days_ago=1), _inc("forum:a", "Bob", days_ago=1),
        _inc("forum:b", "Bob", days_ago=1), _inc("forum:b", "Bob", days_ago=1), _inc("forum:b", "Bob", days_ago=1),
    ]
    result = per_forum_top_actors(incidents)
    assert result["a"][0] == {"name": "Alice", "count": 2}
    assert result["b"][0] == {"name": "Bob", "count": 3}


def test_per_forum_top_actors_excludes_generic_names():
    incidents = [_inc("forum:a", "unknown", days_ago=1), _inc("forum:a", "Admin", days_ago=1)]
    assert per_forum_top_actors(incidents) == {}


def test_actor_reputation_rewards_cross_forum_presence_over_raw_count():
    incidents = (
        [_inc("forum:a", "SingleForumSpammer", days_ago=1) for _ in range(5)]
        + [_inc("forum:a", "CrossForumActor", days_ago=1), _inc("forum:b", "CrossForumActor", days_ago=1), _inc("forum:c", "CrossForumActor", days_ago=1)]
    )
    rep = actor_reputation(incidents)
    assert rep["SingleForumSpammer"]["total"] == 5
    assert rep["SingleForumSpammer"]["forums"] == 1
    assert rep["CrossForumActor"]["total"] == 3
    assert rep["CrossForumActor"]["forums"] == 3
    # 3 posts on 3 distinct forums (3 + 2*2=7) outranks 5 posts on one forum (5 + 0)
    assert rep["CrossForumActor"]["score"] > rep["SingleForumSpammer"]["score"]


def test_actor_reputation_stars_are_bucketed_and_deterministic():
    incidents = [_inc("forum:a", "BigActor", days_ago=1) for _ in range(20)]
    rep = actor_reputation(incidents)
    assert rep["BigActor"]["score"] == 20
    assert rep["BigActor"]["stars"] == 5


def test_actor_reputation_excludes_generic_names():
    incidents = [_inc("forum:a", "admin", days_ago=1)]
    assert actor_reputation(incidents) == {}


def test_top_actors_and_top_countries_still_work_as_before():
    incidents = [_inc("forum:a", "Alice", days_ago=1, cc="TR"), _inc("forum:a", "Alice", days_ago=1, cc="TR")]
    assert top_actors(incidents) == [{"name": "Alice", "count": 2}]
    assert top_countries(incidents) == [{"code": "TR", "count": 2}]


def test_top_sectors_ranks_by_count():
    incidents = [
        _inc("forum:a", "x", days_ago=1, sector="finance"),
        _inc("forum:a", "x", days_ago=1, sector="finance"),
        _inc("forum:a", "x", days_ago=1, sector="telecom"),
    ]
    assert top_sectors(incidents) == [{"sector": "finance", "count": 2}, {"sector": "telecom", "count": 1}]
