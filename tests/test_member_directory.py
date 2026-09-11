"""Offline tests for the resumable member-directory crawler - no network,
requests.get is monkeypatched per test. HTML fixtures are trimmed to the
real markup shape verified live on xreactor.org (2026-09-11)."""
import requests

from forumtracker.member_directory import (
    ALPHA_BUCKETS,
    MemberDirectoryStore,
    crawl_batch,
    extract_members,
)

PAGE_A_NO_MORE = """
<div class="pageNav-main">
  <a href="/members/?alpha=A" rel="nofollow">A</a>
  <a href="/members/?alpha=B" rel="nofollow">B</a>
</div>
<a href="/members/evilghost.241/">avatar</a>
<a href="/members/evilghost.241/">EvilGhost</a>
<a href="/members/anothername.999/">avatar</a>
<a href="/members/anothername.999/">AnotherName</a>
"""

def _page_has_more(alpha: str) -> str:
    return f"""
<a href="/members/one.1/">one</a>
<a href="?alpha={alpha}&page=2">Next</a>
"""


PAGE_A_PAGE2 = """
<a href="/members/two.2/">two</a>
"""


def test_extract_members_dedupes_avatar_and_name_links():
    members = extract_members(PAGE_A_NO_MORE)
    usernames = {m["username"] for m in members}
    assert usernames == {"evilghost", "anothername"}
    assert len(members) == 2  # not 4 - each real link appears twice


def test_crawl_batch_advances_to_next_alpha_when_no_more_pages(tmp_path, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        class R:
            status_code = 200
            text = PAGE_A_NO_MORE
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(requests, "get", fake_get)
    store = MemberDirectoryStore(tmp_path / "members.sqlite3")
    report = crawl_batch(store, pool={"testforum": "https://testforum.example"}, pages_per_forum=1)

    assert report["testforum"]["pages_fetched"] == 1
    assert report["testforum"]["new_members"] == 2
    progress = store.get_progress("testforum")
    assert progress.alpha_index == 1  # advanced from bucket 0 ("0-9") to bucket 1 ("A")
    store.close()


def test_crawl_batch_paginates_within_the_same_alpha_bucket(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        alpha = url.split("alpha=")[1].split("&")[0]
        class R:
            status_code = 200
            text = PAGE_A_PAGE2 if "page=2" in url else _page_has_more(alpha)
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(requests, "get", fake_get)
    store = MemberDirectoryStore(tmp_path / "members.sqlite3")
    report = crawl_batch(store, pool={"testforum": "https://testforum.example"}, pages_per_forum=2)

    assert report["testforum"]["pages_fetched"] == 2
    assert report["testforum"]["new_members"] == 2  # "one" then "two"
    progress = store.get_progress("testforum")
    # PAGE_A_PAGE2 (page 2) has no further "next" link, so the crawl moved
    # on to the next alpha bucket rather than trying a page 3.
    assert (progress.alpha_index, progress.page) == (1, 1)
    store.close()


def test_crawl_batch_resumes_from_saved_checkpoint(tmp_path, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        class R:
            status_code = 200
            text = PAGE_A_NO_MORE
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(requests, "get", fake_get)
    store = MemberDirectoryStore(tmp_path / "members.sqlite3")
    pool = {"testforum": "https://testforum.example"}

    crawl_batch(store, pool=pool, pages_per_forum=1)
    progress_after_first = store.get_progress("testforum")

    crawl_batch(store, pool=pool, pages_per_forum=1)
    progress_after_second = store.get_progress("testforum")

    # Second call must continue from where the first left off, not restart
    # at alpha_index 0 again.
    assert progress_after_second.alpha_index == progress_after_first.alpha_index + 1
    store.close()


def test_crawl_batch_marks_forum_done_after_last_alpha_bucket(tmp_path, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        class R:
            status_code = 200
            text = "<html>no members here</html>"
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(requests, "get", fake_get)
    store = MemberDirectoryStore(tmp_path / "members.sqlite3")
    pool = {"testforum": "https://testforum.example"}

    # Drain every alpha bucket (27 of them) in one-page-per-call batches.
    for _ in range(len(ALPHA_BUCKETS)):
        crawl_batch(store, pool=pool, pages_per_forum=1)

    progress = store.get_progress("testforum")
    assert progress.done is True

    # A done forum must be a free no-op on the next call, not re-fetch.
    report = crawl_batch(store, pool=pool, pages_per_forum=1)
    assert report["testforum"]["pages_fetched"] == 0
    assert report["testforum"]["done"] is True
    store.close()


def test_crawl_batch_stops_forum_on_fetch_failure_without_advancing(tmp_path, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("host unreachable")

    monkeypatch.setattr(requests, "get", fake_get)
    store = MemberDirectoryStore(tmp_path / "members.sqlite3")
    report = crawl_batch(store, pool={"testforum": "https://testforum.example"}, pages_per_forum=3)

    assert report["testforum"]["pages_fetched"] == 1
    progress = store.get_progress("testforum")
    assert progress.alpha_index == 0 and progress.page == 1  # retry the same spot next time
    store.close()


def test_forums_for_username_after_recording(tmp_path):
    store = MemberDirectoryStore(tmp_path / "members.sqlite3")
    store.record_members("forumx", [{"username": "EvilGhost", "member_id": "1"}])
    store.record_members("forumy", [{"username": "evilghost", "member_id": "2"}])
    assert store.forums_for_username("EVILGHOST") == ["forumx", "forumy"]
    store.close()


def test_cross_forum_registered_excludes_single_forum_accounts(tmp_path):
    store = MemberDirectoryStore(tmp_path / "members.sqlite3")
    store.record_members("forumx", [{"username": "EvilGhost", "member_id": "1"}])
    store.record_members("forumy", [{"username": "EvilGhost", "member_id": "2"}])
    store.record_members("forumx", [{"username": "SoloUser", "member_id": "3"}])

    matches = store.cross_forum_registered()

    assert len(matches) == 1
    assert matches[0]["forums"] == ["forumx", "forumy"]
    store.close()
