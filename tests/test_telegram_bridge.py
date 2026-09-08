"""Offline tests for the Telegram bridge source - no network needed, uses a
real RSS response recorded live from tg.i-c-a.su/rss/breachdetect
(2026-09-08)."""
import os
import re

from forumtracker.sources.telegram_bridge import ITEM_RE, TelegramBridgeSource, _parse_date, _parse_item

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
