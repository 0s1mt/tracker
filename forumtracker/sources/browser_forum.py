"""Headless-browser fallback for forums with no RSS feed.

`underground_forums.py` covers 22 forums via RSS auto-discovery. This module
covers the remainder of the deepdarkCTI candidate list that returned no RSS
feed at all (`forum_rss_discovery.json` from the earlier research session
listed 6: comfybox.floofey.dog, cryptogid.org, enclave.cc, nulledbb.com,
patched.to, voided.to). Live review of each (2026-09-06) found:

- comfybox.floofey.dog: reachable, but it's a general furry/hobby community
  forum - no CTI-relevant content. Off-topic, not added.
- cryptogid.org: reachable, but a mainstream Russian crypto/Bitcoin
  discussion forum (same profile as the already-excluded bits.media) -
  off-topic, not added.
- enclave.cc: real carding content exists (a "CC/FULLZ & Non-VBV Dumps"
  thread was visible as a homepage banner ad), but the homepage isn't a
  genuine "latest threads" index - it's a rotating sponsor banner, not
  something we can reliably poll for new posts - and the thread itself
  returned "Sorry, you do not have permission for that!" to a guest
  session. Not a scrapable source, not added. Its permission-denied page
  is kept as a fixture (`enclave_thread_permission_denied.html`) purely to
  test `_classify_blocked()`'s login-wall detection.
- nulledbb.com / voided.to: both reachable, both real MyBB-family forums,
  but their homepages only surface board/category names ("The Lounge",
  "Giveaways", "Upgraded Tools", ...), not thread listings - reaching real
  content needs a per-board review (which board is actually CTI-relevant)
  that hasn't been done yet. Left for a future addition once reviewed.
- patched.to: reachable, no login wall, and a specific board
  (`/Forum-refunding-services`) has genuine, paginated, CTI-relevant
  thread listings (refund-fraud-as-a-service ads) with full first-post
  bodies visible to a guest session. The one concretely wired site below.

Unlike `underground_forums.py` (plain `requests` + regex against clean RSS
XML), this source drives a real headless Chromium via Playwright - these
pages are ordinary server-rendered HTML today, but the whole point of a
browser-based fallback is to keep working if a target later turns on
JS-rendering or a Cloudflare/DDoS-Guard challenge that a plain HTTP request
can't get past. Parsing itself is done with BeautifulSoup against
`page.content()`, kept in pure functions so it's unit-testable offline
against the recorded fixtures in `tests/fixtures/` - no browser needed to
run the test suite.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..alerting import is_alert_worthy
from ..schema import Evidence, Incident, ThreatActor, Victim
from ..schema import Source as SourceMeta
from .base import Source

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
NAV_TIMEOUT_MS = 20_000
MAX_THREADS_PER_SITE = 15
MAX_THREAD_BODIES_PER_SITE = 8
MAX_BODY_CHARS = 800
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")

# name -> site config. "parser" selects which _parse_*/_extract_* pair below to use.
BROWSER_FORUM_POOL: dict[str, dict[str, str]] = {
    "patched.to": {
        "board_url": "https://patched.to/Forum-refunding-services",
        "base_url": "https://patched.to",
        "parser": "mybb_seo",
    },
}

# Signatures checked against full page HTML/text to classify a fetch as blocked
# rather than silently misparsed - mirrors the failed_cause idea from the
# n8n-based reference project (brunosergi/darkweb-forums-tracker), but as a
# simple substring/heuristic check instead of an LLM judgment call.
_CAPTCHA_SIGNATURES = (
    "just a moment",
    "checking your browser",
    "attention required",
    "cf-browser-verification",
    "ddos-guard",
    "verify you are human",
)
_LOGIN_WALL_SIGNATURES = (
    "you do not have permission for that",
    "you must be logged in",
    "please log in to continue",
    "you must be registered",
    "sign in to view",
)


def _classify_blocked(html: str) -> Optional[str]:
    """Returns 'bot_captcha' | 'login_needed' | None (not blocked)."""
    lowered = html.lower()
    if any(sig in lowered for sig in _CAPTCHA_SIGNATURES):
        return "bot_captcha"
    if any(sig in lowered for sig in _LOGIN_WALL_SIGNATURES):
        return "login_needed"
    return None


def _parse_mybb_seo_board(html: str, base_url: str) -> list[dict]:
    """Extracts (title, link, author) rows from a MyBB "SEO URL" style
    board/thread-list page (patched.to, voided.to and similar reskins use
    `Thread-<slug>` URLs instead of `showthread.php?tid=`). Each thread is a
    `<tr>` containing one title anchor plus a `<span class="author">Started
    by <a>...</a></span>` - pagination anchors reuse the same
    `Thread-<slug>?page=N` href with just a page-number as link text, so we
    scope the search to one row at a time instead of scanning all `<a>` tags
    on the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen_paths: set[str] = set()
    threads: list[dict] = []
    for row in soup.find_all("tr"):
        title_a = next(
            (a for a in row.find_all("a", href=True)
             if "Thread-" in a["href"] and "?page=" not in a["href"] and "#" not in a["href"]),
            None,
        )
        if title_a is None:
            continue
        href = title_a["href"]
        title = title_a.get_text(strip=True)
        if not title or href in seen_paths:
            continue
        seen_paths.add(href)

        author_span = row.find("span", class_="author")
        author_a = author_span.find("a") if author_span else None
        author = author_a.get_text(strip=True) if author_a else "unknown"

        threads.append({"title": title, "link": urljoin(base_url, href), "author": author})
        if len(threads) >= MAX_THREADS_PER_SITE:
            break
    return threads


def _extract_mybb_post_body(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    body_div = soup.find("div", class_="post_body")
    if body_div is None:
        return None
    text = body_div.get_text(" ", strip=True)
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS].rsplit(" ", 1)[0] + "…"
    return text or None


_PARSERS = {
    "mybb_seo": (_parse_mybb_seo_board, _extract_mybb_post_body),
}


class BrowserForumSource(Source):
    name = "browser_forums"
    poll_interval_seconds = 1800

    def __init__(self, sites: Optional[dict] = None) -> None:
        self.sites = sites or BROWSER_FORUM_POOL

    def fetch(self) -> Iterable[Incident]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            log.warning("browser_forums: playwright not installed, skipping this cycle")
            return []

        now = datetime.now(timezone.utc).isoformat()
        incidents: list[Incident] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    for site_name, cfg in self.sites.items():
                        try:
                            incidents.extend(self._fetch_site(browser, site_name, cfg, now))
                        except Exception:
                            log.exception("browser_forums: site %s failed entirely, skipping", site_name)
                finally:
                    browser.close()
        except Exception:
            log.exception("browser_forums: could not start a browser (is chromium installed? "
                           "run `playwright install chromium`), skipping this cycle")
            return []
        return incidents

    def _fetch_site(self, browser, site_name: str, cfg: dict, ingested_at: str) -> list[Incident]:
        parser_name = cfg["parser"]
        if parser_name not in _PARSERS:
            log.warning("browser_forums: unknown parser %r for site %s", parser_name, site_name)
            return []
        parse_board, extract_body = _PARSERS[parser_name]

        page = browser.new_page(user_agent=UA)
        try:
            page.goto(cfg["board_url"], timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            html = page.content()
        finally:
            page.close()

        blocked = _classify_blocked(html)
        if blocked:
            log.warning("browser_forums: %s blocked (%s), skipping this cycle", site_name, blocked)
            return []

        threads = parse_board(html, cfg["base_url"])
        out: list[Incident] = []
        for thread in threads:
            body, screenshot_path = None, None
            if len(out) < MAX_THREAD_BODIES_PER_SITE:
                body, screenshot_path = self._fetch_thread(browser, thread["link"], thread["title"], extract_body)
            out.append(Incident(
                detected_at=ingested_at,
                published_at=None,
                source=SourceMeta(type="forum", name=f"forum:{site_name}", url=thread["link"]),
                threat_actor=ThreatActor(name=thread.get("author", "unknown")),
                victim=Victim(name=thread["title"]),
                category="forum_post",
                title=thread["title"],
                description=body,
                evidence=Evidence(post_url=thread["link"], screenshot_url=screenshot_path),
                confidence="claimed",
                tags=["forum", site_name],
                extra={},
            ))
        return out

    @staticmethod
    def _fetch_thread(browser, url: str, title: str, extract_body) -> tuple[Optional[str], Optional[str]]:
        """Fetches one thread page: always returns its body text (if parseable),
        and additionally saves a screenshot - but only when the thread is
        judged alert-worthy (see alerting.is_alert_worthy), to keep the
        common case cheap on disk I/O. `evidence.screenshot_url` here is a
        local file path, not a hosted URL - there's no image host wired up
        yet, see README.
        """
        page = browser.new_page(user_agent=UA)
        try:
            page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            html = page.content()
        except Exception:
            log.exception("browser_forums: thread fetch failed for %s", url)
            page.close()
            return None, None

        if _classify_blocked(html):
            page.close()
            return None, None

        body = extract_body(html)
        screenshot_path = None
        if is_alert_worthy({"title": title, "description": body, "category": "forum_post", "confidence": "claimed"}):
            screenshot_path = BrowserForumSource._save_screenshot(page, url)
        page.close()
        return body, screenshot_path

    @staticmethod
    def _save_screenshot(page, url: str) -> Optional[str]:
        try:
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            slug = _SLUG_RE.sub("_", url.rstrip("/").rsplit("/", 1)[-1])[:120] or "thread"
            path = SCREENSHOT_DIR / f"{slug}.png"
            page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            log.exception("browser_forums: screenshot failed for %s", url)
            return None
