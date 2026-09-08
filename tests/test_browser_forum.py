"""Offline tests for the browser-based fallback source's parsing logic.

No Playwright/browser/network needed - these test the pure parsing functions
against real HTML recorded live from patched.to (2026-09-06) and a real
"you do not have permission" page recorded from enclave.cc, which is used
only to exercise the login-wall classifier.
"""
import os

from forumtracker.sources.browser_forum import (
    _classify_blocked,
    _extract_mybb_post_body,
    _parse_mybb_seo_board,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def test_parse_mybb_seo_board_extracts_real_threads():
    html = _read_fixture("patched_board.html")
    threads = _parse_mybb_seo_board(html, "https://patched.to")

    assert len(threads) >= 2
    titles = [t["title"] for t in threads]
    assert any("AMAZON GOD" in t for t in titles)
    assert any("HULK REFUNDS" in t for t in titles)

    amazon = next(t for t in threads if "AMAZON GOD" in t["title"])
    assert amazon["author"] == "H3RMES"
    assert amazon["link"] == "https://patched.to/Thread-amazon-god-hermes-top-selling-refund-service-interesting-read-here"

    # pagination anchors ("1", "2", "3"...) must never be mistaken for threads
    assert all(t["title"] not in ("1", "2", "3") for t in threads)


def test_parse_mybb_seo_board_dedupes_pagination_links():
    html = _read_fixture("patched_board.html")
    threads = _parse_mybb_seo_board(html, "https://patched.to")
    links = [t["link"] for t in threads]
    assert len(links) == len(set(links))


def test_extract_mybb_post_body_gets_real_first_post_text():
    html = _read_fixture("patched_thread.html")
    body = _extract_mybb_post_body(html)

    assert body is not None
    assert "Hermes Refunds" in body or "refund" in body.lower()


def test_classify_blocked_detects_real_login_wall():
    html = _read_fixture("enclave_thread_permission_denied.html")
    assert _classify_blocked(html) == "login_needed"


def test_classify_blocked_returns_none_for_normal_page():
    html = _read_fixture("patched_board.html")
    assert _classify_blocked(html) is None
