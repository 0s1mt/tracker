"""Normalization tests for the underground-forum RSS scraper - no network required."""
from forumtracker.sources.underground_forums import UndergroundForumSource

# Recorded fragment matching the real XenForo RSS structure fetched live from turkhackteam.org
XENFORO_ITEM_BODY = """
<title>Visual Studio Code #DOWN</title>
<pubDate>Sat, 05 Sep 2026 20:05:00 +0000</pubDate>
<link>https://www.turkhackteam.org/konular/visual-studio-code-down.2086298/</link>
<guid isPermaLink="false">urn:xenforo:thread:2086298</guid>
<author>invalid@example.com (Egemen.)</author>
<category domain="https://www.turkhackteam.org/forumlar/dos-ddos.959/"><![CDATA[Dos &amp; Ddos]]></category>
<dc:creator>Egemen.</dc:creator>
<content:encoded><![CDATA[<div class="bbWrapper">HEDEF: VİSUAL STUDİO CODE&#8203;<br />WAF: Microsoft Corporation</div>]]></content:encoded>
"""

# Recorded fragment matching the real MyBB syndication.php structure fetched live from sinister.ly
MYBB_ITEM_BODY = """
<title><![CDATA[Spanish Music Remix]]></title>
<link>https://sinister.ly/Thread-Spanish-Music-Remix</link>
<pubDate>Sat, 05 Sep 2026 16:27:54 +0000</pubDate>
<dc:creator><![CDATA[<a href="https://sinister.ly/member.php?action=profile&uid=282187">Snickerdoodle</a>]]></dc:creator>
<guid isPermaLink="false">https://sinister.ly/Thread-Spanish-Music-Remix</guid>
<description><![CDATA[<div>No Sientes Nada (Remix) &amp; more</div>]]></description>
"""


def test_xenforo_item_normalize_extracts_body_and_category():
    inc = UndergroundForumSource._normalize("turkhackteam", XENFORO_ITEM_BODY, "2026-09-05T21:00:00+00:00")
    d = inc.to_dict()

    assert d["threat_actor"]["name"] == "Egemen."
    assert d["title"] == "Visual Studio Code #DOWN"
    assert d["victim"]["sector"] == "Dos & Ddos"
    assert "HEDEF: VİSUAL STUDİO CODE" in d["description"]
    assert d["source"]["name"] == "forum:turkhackteam"
    assert d["category"] == "forum_post"


def test_mybb_item_normalize_strips_html_from_creator_and_body():
    inc = UndergroundForumSource._normalize("sinister.ly", MYBB_ITEM_BODY, "2026-09-05T21:00:00+00:00")
    d = inc.to_dict()

    assert d["threat_actor"]["name"] == "Snickerdoodle"
    assert d["title"] == "Spanish Music Remix"
    assert d["description"] == "No Sientes Nada (Remix) & more"
    assert "<a href" not in d["threat_actor"]["name"]
