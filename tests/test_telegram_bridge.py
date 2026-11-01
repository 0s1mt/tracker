"""Offline tests for the Telegram bridge source - no network needed, uses a
real RSS response recorded live from tg.i-c-a.su/rss/breachdetect
(2026-09-08)."""
import os
import re

from forumtracker.sources.telegram_bridge import (
    ITEM_RE,
    TelegramBridgeSource,
    _parse_date,
    _parse_generic_item,
    _parse_item,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_items():
    with open(os.path.join(FIXTURES_DIR, "breachdetect_bridge.xml"), encoding="utf-8") as f:
        text = f.read()
    return ITEM_RE.findall(text)


def test_parses_all_real_items():
    items = _load_items()
    assert len(items) >= 5
    rows = [_parse_item(it) for it in items]
    assert all(r is not None for r in rows)


def test_extracts_source_url_from_nested_anchor():
    rows = [_parse_item(it) for it in _load_items()]
    urls = {r["source_url"] for r in rows}
    assert "https://ascarding.net/" in urls
    assert "https://bdfclub.com/" in urls


def test_content_survives_embedded_html_tags():
    rows = [_parse_item(it) for it in _load_items()]
    # one real item's Content contains a nested <a href="Checkermax.pro">...</a> -
    # must not truncate at the tag's own quote
    matches = [r for r in rows if r["content"] and "Checkermax" in r["content"]]
    assert matches
    assert "Hidden assets seeker" in matches[0]["content"]


def test_author_and_type_and_link_extracted():
    rows = [_parse_item(it) for it in _load_items()]
    named = [r for r in rows if r["author"]]
    assert named
    assert all(r["type"] == "Data leak" for r in rows if r["type"])
    assert all(r["link"] and r["link"].startswith("https://t.me/breachdetect/") for r in rows)


def test_parse_date_converts_real_format():
    iso = _parse_date("08 Sep 2026")
    assert iso is not None
    assert iso.startswith("2026-09-08")


def test_parse_date_none_passthrough():
    assert _parse_date(None) is None


def _row(content, type_="Data leak"):
    return {"content": content, "author": "someone", "detection_date": "08 Sep 2026",
            "type": type_, "link": "https://t.me/breachdetect/1", "source_url": None}


def test_plain_tech_news_downgraded_to_forum_post():
    # Real example from the live channel: Type is "Data leak" like every
    # other post, but the content is just consumer-tech news with no
    # underground/CTI vocabulary at all - must not be labeled a data leak.
    row = _row("Lenovo выпустила 4K-монитор ThinkVision P27UD-40 с интерфейсом Thunderbolt")
    inc = TelegramBridgeSource._to_incident("breachdetect", row, "2026-09-08T00:00:00+00:00")
    assert inc.category == "forum_post"


def test_genuine_leak_content_keeps_data_leak_category():
    row = _row("704K Url:Log:Pass Lines FRESH AND NEW @SkyBases")
    inc = TelegramBridgeSource._to_incident("breachdetect", row, "2026-09-08T00:00:00+00:00")
    assert inc.category == "data_leak"


def test_webmail_access_listing_keeps_data_leak_category():
    row = _row("lineaccess.net Webmail Login")
    inc = TelegramBridgeSource._to_incident("breachdetect", row, "2026-09-08T00:00:00+00:00")
    assert inc.category == "data_leak"


def test_explicit_ransomware_type_not_downgraded_even_without_keywords():
    row = _row("Acme Corp - 48 hours", type_="ransomware")
    inc = TelegramBridgeSource._to_incident("breachdetect", row, "2026-09-08T00:00:00+00:00")
    assert inc.category == "ransomware_leak"


# --- _parse_generic_item: the fallback for a plain (non-bot) channel post -
# live-verified 2026-11-01 that SliceForLifeee, a real active channel with
# no "Source"/"Content"/"Type" fields, parsed to 0 incidents through
# _parse_item alone (every post silently dropped).

def _generic_item_xml(title="", description="", link="https://t.me/SliceForLifeee/1", pubdate=None):
    pub = f"<pubDate>{pubdate}</pubDate>" if pubdate else ""
    return f"<item><title>{title}</title><description>{description}</description><link>{link}</link>{pub}</item>"


def test_parse_generic_item_extracts_description_as_content():
    xml = _generic_item_xml(title="A caption", description="The real post body with details.")
    row = _parse_generic_item(xml)
    assert row["content"] == "The real post body with details."
    assert row["link"] == "https://t.me/SliceForLifeee/1"
    assert row["author"] is None
    assert row["source_url"] is None


def test_parse_generic_item_falls_back_to_title_when_no_description():
    xml = _generic_item_xml(title="Only a caption, no body")
    row = _parse_generic_item(xml)
    assert row["content"] == "Only a caption, no body"


def test_parse_generic_item_drops_media_only_title_with_no_description():
    # a bare photo/video post with no caption - real example from
    # SliceForLifeee's own feed
    xml = _generic_item_xml(title="[Photo]", description="")
    row = _parse_generic_item(xml)
    assert row is None


def test_parse_generic_item_returns_none_when_totally_empty():
    xml = "<item><title></title><description></description><link>https://t.me/x/1</link></item>"
    assert _parse_generic_item(xml) is None


def test_parse_generic_item_double_unescapes_html_before_stripping_tags():
    # same double-HTML-escaping the bridge uses for breachdetect - a naive
    # strip-then-unescape order leaves literal "<a href=...>" text behind
    escaped_once = "&amp;lt;a href=&amp;quot;https://x&amp;quot;&amp;gt;link&amp;lt;/a&amp;gt; and more text"
    xml = _generic_item_xml(description=escaped_once)
    row = _parse_generic_item(xml)
    assert "<a href=" not in row["content"]
    assert "link" in row["content"]
    assert "and more text" in row["content"]


def test_parse_generic_item_reads_pubdate_as_detection_date():
    xml = _generic_item_xml(description="some content", pubdate="Sun, 01 Nov 2026 10:00:00 GMT")
    row = _parse_generic_item(xml)
    assert row["detection_date"] == "Sun, 01 Nov 2026 10:00:00 GMT"


def test_parse_date_converts_standard_rfc822_pubdate_format():
    iso = _parse_date("Sun, 01 Nov 2026 10:00:00 GMT")
    assert iso is not None
    assert iso.startswith("2026-11-01")


def test_fetch_channel_uses_generic_fallback_when_structured_parse_fails(monkeypatch):
    import forumtracker.sources.telegram_bridge as tb

    xml = (
        "<rss><channel>"
        + _generic_item_xml(description="A real plain channel post about a CVE exploit chain.")
        + "</channel></rss>"
    )

    class _Resp:
        status_code = 200
        text = xml

    monkeypatch.setattr(tb.requests, "get", lambda *a, **k: _Resp())
    source = TelegramBridgeSource(channels={"SliceForLifeee": {}})

    incidents = list(source.fetch())

    assert len(incidents) == 1
    assert "CVE exploit chain" in incidents[0].description
