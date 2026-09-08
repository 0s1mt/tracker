"""Offline tests for the Telegram delivery sink - no real network call,
requests.post is mocked so these run without a bot token."""
from unittest.mock import MagicMock, patch

from forumtracker.notify_telegram import notify_alerts, send_alert

CARD = {
    "incident_id": "abc123",
    "simple": {
        "Source": "https://darkforums.as/Thread-x",
        "Content": "leak claim",
        "author": "LunarisSec",
        "Detection Date": "2026-09-08T10:00:00+00:00",
        "Type": "forum_post",
    },
    "rich": {
        "target_title": "250k snu.gouv.fr database leak",
        "threat_actor": "LunarisSec",
        "country": "FR",
        "intel_source": "forum:darkforums",
        "detection_date": "2026-09-08T10:00:00+00:00",
        "risk_score": 7,
        "severity": "HIGH",
        "brief_summary": "LunarisSec imzali bir forum gonderisi",
        "source_link": "https://darkforums.as/Thread-x",
        "matched_entities": ["database"],
    },
}


def test_send_alert_returns_false_without_credentials():
    assert send_alert(CARD, token=None, chat_id=None) is False


@patch("forumtracker.notify_telegram.requests.post")
def test_send_alert_posts_rendered_text_to_bot_api(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    ok = send_alert(CARD, token="123:FAKE", chat_id="-100999")

    assert ok is True
    mock_post.assert_called_once()
    url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
    assert url == "https://api.telegram.org/bot123:FAKE/sendMessage"
    assert kwargs["json"]["chat_id"] == "-100999"
    assert "HIGH SEVERITY LEAK DETECTED" in kwargs["json"]["text"]
    assert "LunarisSec" in kwargs["json"]["text"]


@patch("forumtracker.notify_telegram.requests.post")
def test_send_alert_returns_false_on_non_200(mock_post):
    mock_post.return_value = MagicMock(status_code=403, text="Forbidden: bot was blocked")
    assert send_alert(CARD, token="123:FAKE", chat_id="-100999") is False


@patch("forumtracker.notify_telegram.requests.post")
def test_send_alert_returns_false_on_network_error(mock_post):
    import requests
    mock_post.side_effect = requests.ConnectionError("boom")
    assert send_alert(CARD, token="123:FAKE", chat_id="-100999") is False


def test_notify_alerts_is_noop_without_env_vars(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify_alerts([CARD]) == 0


@patch("forumtracker.notify_telegram.requests.post")
def test_notify_alerts_sends_each_card_when_configured(mock_post, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:FAKE")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    mock_post.return_value = MagicMock(status_code=200)

    sent = notify_alerts([CARD, CARD])

    assert sent == 2
    assert mock_post.call_count == 2
