"""Offline tests for the deterministic keyword/risk-score alerting layer."""
from forumtracker.alerting import (
    compute_popularity_score,
    compute_risk_score,
    is_alert_worthy,
    match_entities,
    render_telegram_message,
    to_alert_card,
)


def test_match_entities_word_boundary_avoids_false_substring_hit():
    # "ssn" must not match inside an unrelated longer word
    assert match_entities("this is about assnormal things") == []
    assert "ssn" in match_entities("leaked SSN and DOB for 4000 users")


def test_match_entities_multiword_variant_uses_substring():
    assert "combolist" in match_entities("fresh mail:pass combo list for sale")


def test_match_entities_accent_insensitive():
    assert "government" in match_entities("Portail du GOUV français piraté")


def test_match_entities_no_hit_returns_empty_list():
    assert match_entities("just a friendly hello thread, nothing special") == []


def test_compute_risk_score_scales_with_category_and_matches():
    forum_post = {"category": "forum_post", "confidence": "claimed"}
    ransomware = {"category": "ransomware_leak", "confidence": "claimed"}
    assert compute_risk_score(forum_post, matched_entities=[]) < compute_risk_score(ransomware, matched_entities=[])
    assert compute_risk_score(forum_post, matched_entities=["breach"]) > compute_risk_score(forum_post, matched_entities=[])


def test_compute_risk_score_is_capped_1_to_10():
    high = {"category": "ransomware_leak", "confidence": "verified"}
    assert 1 <= compute_risk_score(high, matched_entities=["a", "b", "c", "d", "e"]) <= 10


def test_compute_risk_score_bonus_for_named_ransomware_group():
    plain = {"category": "ransomware_leak", "confidence": "claimed", "extra": {}}
    named = {"category": "ransomware_leak", "confidence": "claimed", "extra": {"ransomware_group": "lockbit"}}
    assert compute_risk_score(named, matched_entities=[]) > compute_risk_score(plain, matched_entities=[])


def test_compute_popularity_score_falls_back_to_risk_score_without_reply_count():
    incident = {"category": "forum_post", "confidence": "claimed", "extra": {}}
    risk = compute_risk_score(incident)
    assert compute_popularity_score(incident, risk_score=risk) == risk


def test_compute_popularity_score_blends_reply_count_with_risk_score():
    low_engagement = {"category": "forum_post", "confidence": "claimed", "extra": {"reply_count": 1}}
    high_engagement = {"category": "forum_post", "confidence": "claimed", "extra": {"reply_count": 5000}}
    assert compute_popularity_score(low_engagement, risk_score=3) < compute_popularity_score(high_engagement, risk_score=3)


def test_compute_popularity_score_is_capped_1_to_10():
    incident = {"category": "ransomware_leak", "confidence": "verified", "extra": {"reply_count": 999999}}
    assert 1 <= compute_popularity_score(incident, risk_score=10) <= 10


def test_is_alert_worthy_true_on_keyword_hit_regardless_of_category():
    incident = {"title": "combolist mail:pass fresh dump", "description": None, "category": "forum_post", "confidence": "claimed"}
    assert is_alert_worthy(incident) is True


def test_is_alert_worthy_false_for_bland_low_risk_forum_post():
    incident = {"title": "welcome new members thread", "description": None, "category": "forum_post", "confidence": "claimed"}
    assert is_alert_worthy(incident) is False


def test_to_alert_card_produces_both_shapes_from_real_incident():
    incident = {
        "incident_id": "abc123",
        "detected_at": "2026-09-08T10:00:00+00:00",
        "source": {"type": "forum", "name": "forum:darkforums", "url": None},
        "threat_actor": {"name": "LunarisSec", "aliases": []},
        "victim": {"name": "250k snu.gouv.fr", "country_code": None, "sector": None},
        "category": "forum_post",
        "title": "250k snu.gouv.fr database leak",
        "description": "We breached snu.gouv.fr and leaked the database of 250k users.",
        "evidence": {"post_url": "https://darkforums.as/Thread-x", "screenshot_url": None},
        "confidence": "claimed",
        "tags": [],
        "extra": {},
    }
    card = to_alert_card(incident)

    assert card["incident_id"] == "abc123"
    assert card["simple"]["Source"] == "https://darkforums.as/Thread-x"
    assert card["simple"]["author"] == "LunarisSec"
    assert card["simple"]["Type"] == "forum_post"
    assert "database" in card["rich"]["matched_entities"]
    assert card["rich"]["threat_actor"] == "LunarisSec"
    assert 1 <= card["rich"]["risk_score"] <= 10
    assert "LunarisSec" in card["rich"]["brief_summary"]


def test_render_telegram_message_matches_reference_layout():
    incident = {
        "incident_id": "abc123",
        "detected_at": "2026-09-08T10:00:00+00:00",
        "source": {"type": "forum", "name": "forum:darkforums", "url": None},
        "threat_actor": {"name": "LunarisSec", "aliases": []},
        "victim": {"name": "250k snu.gouv.fr", "country_code": "FR", "sector": None},
        "category": "forum_post",
        "title": "250k snu.gouv.fr database leak",
        "description": "We breached snu.gouv.fr and leaked the database of 250k users.",
        "evidence": {"post_url": "https://darkforums.as/Thread-x", "screenshot_url": None},
        "confidence": "claimed",
        "tags": [],
        "extra": {},
    }
    text = render_telegram_message(to_alert_card(incident))

    assert "SEVERITY LEAK DETECTED" in text
    assert "Threat Actor: LunarisSec" in text
    assert "Country: FR" in text
    assert "Intel Source: forum:darkforums" in text
    assert "Risk Score:" in text and "/10" in text
    assert "Source Intelligence Link: https://darkforums.as/Thread-x" in text
