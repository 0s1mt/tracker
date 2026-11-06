"""Offline tests for the browser-based fallback source's parsing logic.

No Playwright/browser/network needed - these test the pure parsing functions
against real HTML recorded live from patched.to (2026-09-06) and a real
"you do not have permission" page recorded from enclave.cc, which is used
only to exercise the login-wall classifier.
"""
import os
from unittest.mock import MagicMock, patch

from forumtracker.sources.browser_forum import (
    BrowserForumSource,
    _classify_blocked,
    _extract_mybb_post_body,
    _parse_cookie_string,
    _parse_latest_posts,
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


# --- _parse_cookie_string: turns a devtools-exported `Cookie:` header value
# into Playwright's add_cookies() shape, for session-reuse against
# Cloudflare/login-gated forums (see BROWSER_FORUM_POOL's "cookies_env" comment).

def test_parse_cookie_string_splits_name_value_pairs():
    cookies = _parse_cookie_string("session=abc123; cf_clearance=xyz789", ".pwnforums.st")
    assert {"name": "session", "value": "abc123", "domain": ".pwnforums.st", "path": "/"} in cookies
    assert {"name": "cf_clearance", "value": "xyz789", "domain": ".pwnforums.st", "path": "/"} in cookies


def test_parse_cookie_string_skips_malformed_segments():
    cookies = _parse_cookie_string("session=abc123; garbage; =noname; ", ".pwnforums.st")
    assert len(cookies) == 1
    assert cookies[0]["name"] == "session"


def test_parse_cookie_string_empty_input_returns_empty_list():
    assert _parse_cookie_string("", ".pwnforums.st") == []


# --- fetch()-level: cookies_env wiring through to context.add_cookies()

def _mock_playwright_context(mock_sp):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_page.content.return_value = "<html><body>no threads here</body></html>"
    mock_context.new_page.return_value = mock_page
    mock_browser.new_context.return_value = mock_context
    mock_sp.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    return mock_browser, mock_context, mock_page


@patch("playwright.sync_api.sync_playwright")
def test_fetch_applies_cookies_when_env_var_is_set(mock_sp, monkeypatch):
    monkeypatch.setenv("TEST_PWN_COOKIES", "session=abc123")
    _, mock_context, _ = _mock_playwright_context(mock_sp)
    source = BrowserForumSource(sites={
        "pwnforums.st": {
            "board_url": "https://pwnforums.st/board",
            "base_url": "https://pwnforums.st",
            "parser": "mybb_seo",
            "cookies_env": "TEST_PWN_COOKIES",
        },
    })

    list(source.fetch())

    mock_context.add_cookies.assert_called_once()
    applied = mock_context.add_cookies.call_args[0][0]
    assert applied[0]["name"] == "session"
    assert applied[0]["domain"] == ".pwnforums.st"


@patch("playwright.sync_api.sync_playwright")
def test_fetch_skips_cookies_when_env_var_unset(mock_sp, monkeypatch):
    monkeypatch.delenv("TEST_PWN_COOKIES_UNSET", raising=False)
    _, mock_context, _ = _mock_playwright_context(mock_sp)
    source = BrowserForumSource(sites={
        "pwnforums.st": {
            "board_url": "https://pwnforums.st/board",
            "base_url": "https://pwnforums.st",
            "parser": "mybb_seo",
            "cookies_env": "TEST_PWN_COOKIES_UNSET",
        },
    })

    list(source.fetch())

    mock_context.add_cookies.assert_not_called()


@patch("playwright.sync_api.sync_playwright")
def test_fetch_without_cookies_env_behaves_as_before(mock_sp):
    """A site with no cookies_env key at all (patched.to) must not even
    look at os.environ. Scoped to just that one site explicitly, not the
    full default BROWSER_FORUM_POOL, so this stays true regardless of what
    else gets added to the pool later (e.g. pwnforums.st, which does carry
    cookies_env)."""
    _, mock_context, _ = _mock_playwright_context(mock_sp)
    source = BrowserForumSource(sites={
        "patched.to": {
            "board_url": "https://patched.to/Forum-refunding-services",
            "base_url": "https://patched.to",
            "parser": "mybb_seo",
        },
    })

    list(source.fetch())

    mock_context.add_cookies.assert_not_called()


# --- _parse_latest_posts: pwnforums.st's sitewide "Latest Posts" sidebar -
# real authenticated HTML provided by the user (2026-11-06), trimmed into
# tests/fixtures/pwnforums_latest_posts.html (no session tokens).

def test_parse_latest_posts_extracts_real_items():
    html = _read_fixture("pwnforums_latest_posts.html")
    threads = _parse_latest_posts(html, "https://pwnforums.st")

    assert len(threads) == 3
    titles = [t["title"] for t in threads]
    assert any("Climb Up - 151K" in t for t in titles)
    assert any("ChatGPT jailbreak unlock" == t for t in titles)


def test_parse_latest_posts_keeps_type_prefix_label_in_title():
    html = _read_fixture("pwnforums_latest_posts.html")
    threads = _parse_latest_posts(html, "https://pwnforums.st")

    prefixed = [t for t in threads if "SELLING" in t["title"] or "DATABASE" in t["title"]]
    assert len(prefixed) == 2
    assert any("DATABASE" in t["title"] and "Philippines" in t["title"] for t in prefixed)


def test_parse_latest_posts_resolves_relative_thread_link():
    html = _read_fixture("pwnforums_latest_posts.html")
    threads = _parse_latest_posts(html, "https://pwnforums.st")

    links = [t["link"] for t in threads]
    assert "https://pwnforums.st/Thread-ChatGPT-jailbreak-unlock" in links


def test_parse_latest_posts_extracts_author_not_type_prefix():
    html = _read_fixture("pwnforums_latest_posts.html")
    threads = _parse_latest_posts(html, "https://pwnforums.st")

    authors = {t["author"] for t in threads}
    assert authors == {"Alduin", "NoUsername010", "colleenoakes"}


def test_parse_latest_posts_empty_html_returns_empty_list():
    assert _parse_latest_posts("<html><body>nothing here</body></html>", "https://pwnforums.st") == []


def test_browser_forum_source_explicit_empty_sites_stays_empty():
    # Same falsy-empty-dict bug class as UndergroundForumSource.__init__ -
    # `sites or BROWSER_FORUM_POOL` would silently fall back to the full
    # pool for sites={}.
    source = BrowserForumSource(sites={})
    assert source.sites == {}


def test_browser_forum_source_none_defaults_to_full_pool():
    source = BrowserForumSource()
    assert "patched.to" in source.sites
    assert "pwnforums.st" in source.sites
