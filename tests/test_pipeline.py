"""Offline tests for pipeline-level orchestration logic (no network)."""
from forumtracker.pipeline import _apply_ransomware_intel, build_alerts


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
