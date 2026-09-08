"""Offline tests for the thread-page enrichment hook (enrich()) - real
network calls mocked out, HTML fragments modeled on the live XenForo/MyBB
pages fetched during development (see underground_forums.py's
POST_BODY_SELECTORS comment)."""
from unittest.mock import MagicMock, patch

from forumtracker.sources.underground_forums import UndergroundForumSource

XENFORO_THREAD_HTML = """
<html><body>
<article class="message">
  <div class="message-body">
    <div class="bbWrapper">Full first post text with much more detail than any RSS teaser would carry, including pricing and contact info.</div>
  </div>
</article>
</body></html>
"""

MYBB_THREAD_HTML = """
<html><body>
<div class="post_body">SHIBA SHOP full listing: dozens of streaming accounts, prices, and a Discord contact for orders.</div>
<div class="post_body">This is a reply, not the first post.</div>
</body></html>
"""

GATED_THREAD_HTML = """
<html><body>
<article class="message">
  <div class="message-body">
    <div class="bbWrapper">To view the content, you need to Sign In or Register.</div>
  </div>
</article>
</body></html>
"""

XENFORO_WITH_LDJSON_HTML = """
<html><body>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "DiscussionForumPosting",
 "interactionStatistic": {"@type": "InteractionCounter", "interactionType": "https://schema.org/ReplyAction", "userInteractionCount": 444}}
</script>
<article class="message">
  <div class="message-body">
    <div class="bbWrapper">Full first post text with much more detail than any RSS teaser would carry.</div>
  </div>
</article>
</body></html>
"""


def _incident(description=None, post_url="https://forum.example/Thread-x"):
    return {
        "incident_id": "abc123",
        "description": description,
        "evidence": {"post_url": post_url},
        "extra": {},
    }


def test_extract_post_body_xenforo_selector():
    body = UndergroundForumSource._extract_post_body(XENFORO_THREAD_HTML)
    assert "Full first post text" in body


def test_extract_post_body_mybb_selector_first_match_only():
    body = UndergroundForumSource._extract_post_body(MYBB_THREAD_HTML)
    assert "SHIBA SHOP" in body
    assert "This is a reply" not in body


def test_extract_post_body_returns_none_on_no_selector_match():
    assert UndergroundForumSource._extract_post_body("<html><body>nothing here</body></html>") is None


def test_extract_reply_count_reads_ldjson_interaction_statistic():
    assert UndergroundForumSource._extract_reply_count(XENFORO_WITH_LDJSON_HTML) == 444


def test_extract_reply_count_none_when_no_ldjson():
    assert UndergroundForumSource._extract_reply_count(XENFORO_THREAD_HTML) is None


@patch("forumtracker.sources.underground_forums.requests.get")
def test_fetch_thread_page_returns_none_on_non_200(mock_get):
    mock_get.return_value = MagicMock(status_code=403, text="")
    assert UndergroundForumSource._fetch_thread_page("https://forum.example/thread") is None


@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_replaces_short_rss_teaser_with_full_body(mock_get):
    mock_get.return_value = MagicMock(status_code=200, text=XENFORO_THREAD_HTML)
    source = UndergroundForumSource()
    incident = _incident(description="short teaser")

    out = source.enrich(incident)

    assert "Full first post text" in out["description"]
    assert out["extra"]["content_source"] == "thread_page"


@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_stores_reply_count_when_ldjson_present(mock_get):
    mock_get.return_value = MagicMock(status_code=200, text=XENFORO_WITH_LDJSON_HTML)
    source = UndergroundForumSource()
    incident = _incident(description="short teaser")

    out = source.enrich(incident)

    assert out["extra"]["reply_count"] == 444
    assert "Full first post text" in out["description"]


@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_keeps_rss_description_when_thread_page_is_gated(mock_get):
    mock_get.return_value = MagicMock(status_code=200, text=GATED_THREAD_HTML)
    source = UndergroundForumSource()
    long_teaser = "A much longer RSS teaser than the gated placeholder text on the live page."
    incident = _incident(description=long_teaser)

    out = source.enrich(incident)

    assert out["description"] == long_teaser
    assert "content_source" not in out["extra"]


def test_enrich_noop_without_post_url():
    source = UndergroundForumSource()
    incident = _incident(description="teaser", post_url=None)
    incident["evidence"] = {"post_url": None}

    out = source.enrich(incident)

    assert out["description"] == "teaser"


@patch("forumtracker.sources.underground_forums.requests.get", side_effect=Exception("network down"))
def test_enrich_returns_incident_unchanged_on_fetch_exception(mock_get):
    source = UndergroundForumSource()
    incident = _incident(description="teaser")

    out = source.enrich(incident)

    assert out["description"] == "teaser"


def _mock_playwright(mock_sp, page=None):
    """Wires up sync_playwright() as p; browser = p.chromium.launch();
    browser.new_page() -> page (default: a fresh MagicMock) - matching the
    `with sync_playwright() as p: browser = p.chromium.launch(); ...` shape
    enrich_batch() uses."""
    mock_browser = MagicMock()
    mock_page = page or MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_sp.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    return mock_browser, mock_page


@patch("playwright.sync_api.sync_playwright")
@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_batch_sets_screenshot_url_for_each_incident(mock_get, mock_sp):
    mock_get.return_value = MagicMock(status_code=200, text=XENFORO_THREAD_HTML)
    _mock_playwright(mock_sp)
    source = UndergroundForumSource()
    incidents = [_incident(description="teaser 1", post_url="https://forum.example/Thread-1"),
                 _incident(description="teaser 2", post_url="https://forum.example/Thread-2")]

    out = source.enrich_batch(incidents)

    assert all(inc["evidence"].get("screenshot_url") for inc in out)
    # text enrichment still ran as part of the same batch
    assert all("Full first post text" in inc["description"] for inc in out)


@patch("playwright.sync_api.sync_playwright")
@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_batch_launches_browser_once_for_whole_batch(mock_get, mock_sp):
    mock_get.return_value = MagicMock(status_code=200, text=XENFORO_THREAD_HTML)
    _mock_playwright(mock_sp)
    source = UndergroundForumSource()
    incidents = [_incident(post_url=f"https://forum.example/Thread-{i}") for i in range(5)]

    source.enrich_batch(incidents)

    launch = mock_sp.return_value.__enter__.return_value.chromium.launch
    assert launch.call_count == 1


@patch("playwright.sync_api.sync_playwright")
@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_batch_skips_incident_without_post_url(mock_get, mock_sp):
    mock_get.return_value = MagicMock(status_code=200, text=XENFORO_THREAD_HTML)
    mock_browser, _ = _mock_playwright(mock_sp)
    source = UndergroundForumSource()
    incident = _incident(post_url=None)
    incident["evidence"] = {"post_url": None}

    out = source.enrich_batch([incident])

    assert "screenshot_url" not in out[0]["evidence"]
    mock_browser.new_page.assert_not_called()


@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_batch_skips_screenshots_when_playwright_not_installed(mock_get):
    mock_get.return_value = MagicMock(status_code=200, text=XENFORO_THREAD_HTML)
    source = UndergroundForumSource()
    incident = _incident(description="teaser")

    with patch.dict("sys.modules", {"playwright.sync_api": None}):
        out = source.enrich_batch([incident])

    assert "screenshot_url" not in out[0]["evidence"]
    # text enrichment still happened even though screenshots were skipped
    assert "Full first post text" in out[0]["description"]


@patch("playwright.sync_api.sync_playwright")
@patch("forumtracker.sources.underground_forums.requests.get")
def test_enrich_batch_one_failing_screenshot_does_not_block_the_rest(mock_get, mock_sp):
    mock_get.return_value = MagicMock(status_code=200, text=XENFORO_THREAD_HTML)
    mock_browser = MagicMock()
    bad_page = MagicMock()
    bad_page.goto.side_effect = Exception("nav timeout")
    good_page = MagicMock()
    mock_browser.new_page.side_effect = [bad_page, good_page]
    mock_sp.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    source = UndergroundForumSource()
    incidents = [_incident(post_url="https://forum.example/Thread-bad"),
                 _incident(post_url="https://forum.example/Thread-good")]

    out = source.enrich_batch(incidents)

    assert "screenshot_url" not in out[0]["evidence"]
    assert out[1]["evidence"].get("screenshot_url")
