"""Offline tests for forum liveness tracking - no network, requests.Session.get
is monkeypatched per test."""
import requests

from forumtracker.health import DEAD_THRESHOLD, CheckResult, HealthStore, check_one, pool_urls


def test_pool_urls_reads_homepage_or_board_url():
    urls = pool_urls(
        {"a": {"homepage": "https://a.example", "rss": "https://a.example/rss"}},
        {"b": {"board_url": "https://b.example/board", "base_url": "https://b.example"}},
        {"c": {}},  # no usable URL - must be skipped, not KeyError
    )
    assert urls == {"a": "https://a.example", "b": "https://b.example/board"}


def _resp(status_code):
    class _R:
        pass
    r = _R()
    r.status_code = status_code
    return r


def test_check_one_classifies_2xx_as_up(monkeypatch):
    monkeypatch.setattr(requests.Session, "get", lambda self, *a, **k: _resp(200))
    result = check_one("forumx", "https://forumx.example")
    assert result.status == "up"
    assert result.http_status == 200
    assert result.error is None


def test_check_one_classifies_5xx_as_degraded_not_unreachable(monkeypatch):
    monkeypatch.setattr(requests.Session, "get", lambda self, *a, **k: _resp(503))
    result = check_one("forumx", "https://forumx.example")
    assert result.status == "degraded"


def test_check_one_classifies_connection_error_as_unreachable(monkeypatch):
    def _raise(self, *a, **k):
        raise requests.exceptions.ConnectionError("Name or service not known")
    monkeypatch.setattr(requests.Session, "get", _raise)
    result = check_one("forumx", "https://forumx.example")
    assert result.status == "unreachable"
    assert "not known" in result.error


def test_check_one_routes_onion_hosts_through_tor_proxy(monkeypatch):
    captured = {}

    def _fake_get(self, url, **kwargs):
        captured["proxies"] = self.proxies
        return _resp(200)

    monkeypatch.setattr(requests.Session, "get", _fake_get)
    check_one("onionforum", "http://abcdefghijklmnop.onion")
    assert captured["proxies"] == {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}


def _result(name, status, checked_at="2026-09-11T00:00:00+00:00"):
    return CheckResult(name=name, url="https://x.example", status=status, http_status=None, error=None, checked_at=checked_at)


def test_health_store_increments_consecutive_failures_on_unreachable(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    n1 = store.record(_result("forumx", "unreachable"))
    n2 = store.record(_result("forumx", "unreachable"))
    n3 = store.record(_result("forumx", "unreachable"))
    assert (n1, n2, n3) == (1, 2, 3)
    store.close()


def test_health_store_resets_failures_on_success(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    store.record(_result("forumx", "unreachable"))
    store.record(_result("forumx", "unreachable"))
    reset = store.record(_result("forumx", "up"))
    assert reset == 0
    store.close()


def test_health_store_degraded_also_resets_failures(tmp_path):
    # A 503 still proves the address itself resolves/connects - only a
    # connection-level failure should count toward the dead-mirror threshold.
    store = HealthStore(tmp_path / "health.sqlite3")
    store.record(_result("forumx", "unreachable"))
    reset = store.record(_result("forumx", "degraded"))
    assert reset == 0
    store.close()


def test_probably_dead_only_after_threshold(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    for _ in range(DEAD_THRESHOLD - 1):
        store.record(_result("forumx", "unreachable"))
    assert store.probably_dead() == []
    store.record(_result("forumx", "unreachable"))
    assert store.probably_dead() == ["forumx"]
    store.close()


def test_address_history_recorded_only_on_success(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    store.record(_result("forumx", "unreachable"))
    assert store.address_history("forumx") == []
    store.record(_result("forumx", "up"))
    history = store.address_history("forumx")
    assert len(history) == 1
    assert history[0]["url"] == "https://x.example"
    store.close()


def test_report_reflects_latest_status(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    store.record(_result("forumx", "up"))
    report = store.report()
    assert len(report) == 1
    assert report[0]["name"] == "forumx"
    assert report[0]["status"] == "up"
    store.close()
