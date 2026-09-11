"""Offline tests for the forum-statistics widget parser and activity-rate
store - no network. HTML fixtures below are trimmed excerpts of real
widgets verified live on turkhackteam.org, carders.biz and bdfclub.com
(2026-09-11)."""
from forumtracker.forum_stats import StatsStore, extract_stats

TURKHACKTEAM_HTML = """
<div class="block" data-widget-key="forum_stats" data-widget-definition="forum_statistics">
    <div class="block-container">
        <h3 class="block-minorHeader">Forum istatistikleri</h3>
        <div class="block-body block-row">
            <dl class="pairs pairs--justified count--threads">
                <dt>Konular</dt>
                <dd>1,463,871</dd>
            </dl>
            <dl class="pairs pairs--justified count--messages">
                <dt>Mesajlar</dt>
                <dd>7,931,264</dd>
            </dl>
            <dl class="pairs pairs--justified count--users">
                <dt>Kullanıcılar</dt>
                <dd>927,937</dd>
            </dl>
            <dl class="pairs pairs--justified">
                <dt>Son üye</dt>
                <dd><a href="/uye/x.104/">aykan_345</a></dd>
            </dl>
        </div>
    </div>
</div>
"""

CARDERS_BIZ_HTML = """
<div class="block" data-widget-definition="forum_statistics">
    <div class="block-container">
        <h3 class="block-minorHeader">Forum statistics</h3>
        <div class="block-body block-row">
            <dl class="pairs pairs--justified"><dt>Threads</dt><dd>14,077</dd></dl>
            <dl class="pairs pairs--justified"><dt>Messages</dt><dd>68,487</dd></dl>
            <dl class="pairs pairs--justified"><dt>Members</dt><dd>64,642</dd></dl>
            <dl class="pairs pairs--justified"><dt>Latest member</dt><dd><a href="/members/x.877/">Bryancc1</a></dd></dl>
        </div>
    </div>
</div>
"""

BDFCLUB_HTML = """
<div class="block" data-widget-definition="forumStatistics">
    <div class="block-container">
        <h3 class="block-minorHeader">Статистика форума</h3>
        <div class="block-body block-row">
            <dl class="pairs pairs--justified"><dt>Uptime сервера</dt><dd>99.98%</dd></dl>
            <dl class="pairs pairs--justified"><dt>Темы</dt><dd>153.298</dd></dl>
            <dl class="pairs pairs--justified"><dt>За 24 часа</dt><dd>23</dd></dl>
            <dl class="pairs pairs--justified"><dt>Сообщения</dt><dd>326.731</dd></dl>
            <dl class="pairs pairs--justified"><dt>За 24 часа</dt><dd>203</dd></dl>
        </div>
    </div>
</div>
"""

NO_WIDGET_HTML = "<html><body><p>Nothing statistics-shaped here.</p></body></html>"

# bdfclub.com live layout (2026-09-11): a "top posters" carousel widget
# whose key also happens to contain "statistics" but has no dt/dd pairs at
# all, positioned BEFORE the real counters widget in document order.
MULTI_WIDGET_HTML = """
<div data-widget-definition="live_forum_statistics">
    <div class="tabGroup"><span class="title">Top posters</span></div>
</div>
<div data-widget-definition="forumStatistics">
    <dl><dt>Threads</dt><dd>500</dd></dl>
    <dl><dt>Messages</dt><dd>9,000</dd></dl>
</div>
"""


def test_extract_stats_turkish_labels():
    stats = extract_stats(TURKHACKTEAM_HTML)
    assert stats == {"threads": 1463871, "messages": 7931264, "members": 927937}


def test_extract_stats_english_labels():
    stats = extract_stats(CARDERS_BIZ_HTML)
    assert stats == {"threads": 14077, "messages": 68487, "members": 64642}


def test_extract_stats_russian_labels_and_dotted_thousands():
    stats = extract_stats(BDFCLUB_HTML)
    # "За 24 часа" matches none of our labels and must not clobber the real
    # Темы/Сообщения totals with its much smaller daily-delta numbers.
    assert stats == {"threads": 153298, "messages": 326731}


def test_extract_stats_returns_none_when_widget_missing():
    assert extract_stats(NO_WIDGET_HTML) is None


def test_extract_stats_skips_an_earlier_empty_statistics_widget():
    # Regression: soup.find() (first match only) returned None here because
    # the first "*statistic*"-keyed widget on the real bdfclub.com page has
    # no dt/dd pairs at all - must fall through to the next candidate.
    stats = extract_stats(MULTI_WIDGET_HTML)
    assert stats == {"threads": 500, "messages": 9000}


def test_extract_stats_ignores_non_numeric_dd_like_latest_member():
    stats = extract_stats(CARDERS_BIZ_HTML)
    assert "latest_member" not in stats


def test_stats_store_activity_per_day_needs_two_snapshots(tmp_path):
    store = StatsStore(tmp_path / "stats.sqlite3")
    store.record("forumx", {"messages": 1000}, captured_at="2026-09-10T00:00:00+00:00")
    assert store.activity_per_day("forumx") is None
    store.close()


def test_stats_store_activity_per_day_computes_real_rate(tmp_path):
    store = StatsStore(tmp_path / "stats.sqlite3")
    store.record("forumx", {"messages": 1000}, captured_at="2026-09-10T00:00:00+00:00")
    store.record("forumx", {"messages": 1240}, captured_at="2026-09-12T00:00:00+00:00")
    # 240 new messages over 2 days = 120/day
    assert store.activity_per_day("forumx") == 120.0
    store.close()


def test_stats_store_activity_per_day_none_for_too_short_a_span(tmp_path):
    store = StatsStore(tmp_path / "stats.sqlite3")
    store.record("forumx", {"messages": 1000}, captured_at="2026-09-10T00:00:00+00:00")
    store.record("forumx", {"messages": 1010}, captured_at="2026-09-10T00:10:00+00:00")
    assert store.activity_per_day("forumx") is None


def test_stats_store_activity_per_day_none_on_counter_reset(tmp_path):
    store = StatsStore(tmp_path / "stats.sqlite3")
    store.record("forumx", {"messages": 5000}, captured_at="2026-09-10T00:00:00+00:00")
    store.record("forumx", {"messages": 200}, captured_at="2026-09-12T00:00:00+00:00")
    assert store.activity_per_day("forumx") is None


def test_stats_store_latest_returns_most_recent_snapshot(tmp_path):
    store = StatsStore(tmp_path / "stats.sqlite3")
    store.record("forumx", {"messages": 1000}, captured_at="2026-09-10T00:00:00+00:00")
    store.record("forumx", {"messages": 1100}, captured_at="2026-09-11T00:00:00+00:00")
    latest = store.latest("forumx")
    assert latest.messages == 1100
