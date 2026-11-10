"""Live thread/post scraper for underground hacking & cracking forums.

Targets pulled from the community-maintained deepdarkCTI list
(github.com/fastfire/deepdarkCTI, forum.md) and validated live across
several sessions (most recently 2026-11-01, re-diffing the then-current
list against this pool - see the "re-diffed" comment below FORUM_POOL):
real .onion-only forums are out of scope here (Tor egress is not available
from this environment; see README for the workaround), and so are
Cloudflare/login-gated ones (verified live, not assumed - see README's
pwnforums.st section) and off-topic ones (adult content, game cheats/
botting, breach-news blogs rather than forum chatter). FORUM_POOL below is
the living result of that ongoing review, currently 36 entries (some are
a specific subforum of an already-pooled site, not a wholly separate
forum - see the "breachforums-leads"/"darkforums-leads" comment below).

RSS auto-discovery instead of guessed URLs: forum software (XenForo, MyBB,
IPB, and others) all advertise their feed via
`<link rel="alternate" type="application/rss+xml" href="...">` on the
homepage - that's what `discover_rss_url` reads, so a site changing its
feed path (or a new pool entry with only a homepage URL) still works without
a code change. `known_rss_url` is just a cache to skip that extra fetch.

Both XenForo's `content:encoded` and MyBB/IPB's `description` carry the
*actual first-post body*, not just the thread title - that is the "forum
chat/content" this source is for, not merely a title/metadata list.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..schema import Evidence, Incident, ThreatActor, Victim
from ..schema import Source as SourceMeta
from .base import Source
from .browser_forum import SCREENSHOT_DIR

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}

# Shared across every GET in this module (RSS fetch, homepage RSS-link
# discovery, full thread-body fetch) instead of a bare `requests.get()` per
# call - gets us connection pooling/keep-alive for free, plus automatic
# bounded retry with exponential backoff + jitter on 429/5xx (never on
# other 4xx - a 403/404 is a correctness signal, not transient, so retrying
# it would just make a bot-blocked site see 3x the request volume for the
# same eventual "no"). `respect_retry_after_header` honors a real
# rate-limit's own requested delay over our own backoff guess.
_RETRY = Retry(
    total=3,
    backoff_factor=1.0,
    backoff_jitter=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
    respect_retry_after_header=True,
)
_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY))
_SESSION.mount("http://", HTTPAdapter(max_retries=_RETRY))


def _log_http_status(what: str, url: str, status_code: int) -> None:
    """Called only once Retry has already exhausted its attempts for a
    retryable code (or immediately for a non-retryable one) - distinguishes
    the three operationally-different outcomes the old bare
    `if status != 200: return None` collapsed into one, so logs actually
    say whether a site is rate-limiting us, blocking us, or just down,
    instead of all three reading identically as "fetch failed".
    """
    if status_code == 429:
        log.warning("underground_forums: %s rate-limited (429, retries exhausted) for %s", what, url)
    elif status_code == 403:
        log.warning("underground_forums: %s forbidden (403, likely bot-blocked) for %s", what, url)
    elif 500 <= status_code < 600:
        log.warning("underground_forums: %s server error (%d, retries exhausted) for %s", what, status_code, url)
    else:
        log.info("underground_forums: %s returned %d for %s", what, status_code, url)

RSS_LINK_RE = re.compile(
    r'<link[^>]+type=["\']application/(?:rss\+xml|atom\+xml)["\'][^>]*href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
ITEM_RE = re.compile(r"<item>(?P<body>.*?)</item>", re.DOTALL)
TITLE_RE = re.compile(r"<title>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</title>", re.DOTALL)
LINK_RE = re.compile(r"<link>(.*?)</link>")
PUBDATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>")
CREATOR_RE = re.compile(r"<dc:creator>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</dc:creator>", re.DOTALL)
AUTHOR_RE = re.compile(r"<author>(.*?)</author>")
CATEGORY_RE = re.compile(r"<category[^>]*>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</category>", re.DOTALL)
CONTENT_ENCODED_RE = re.compile(r"<content:encoded>(?:<!\[CDATA\[(.*?)\]\]>)</content:encoded>", re.DOTALL)
DESCRIPTION_RE = re.compile(r"<description>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</description>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
AUTHOR_EMAIL_RE = re.compile(r"^.*?\(([^)]+)\)\s*$")  # "invalid@example.com (display name)"
LD_JSON_RE = re.compile(r'<script type=["\']application/ld\+json["\']>(.*?)</script>', re.DOTALL)

MAX_ITEMS_PER_SITE = 30
MAX_BODY_CHARS = 800

# Tried in order against the real thread page (not the RSS feed) to pull the
# FULL first-post text, not just whatever teaser length the forum's own RSS
# generator happens to include. Covers the two forum families this pool is
# built from: XenForo (`article.message .bbWrapper`, falling back to the
# looser `.message-body`) and MyBB (`div.post_body`) - verified live against
# a real thread on 20 of the 22 pool forums (2026-09-08); `div.post_body`
# also happened to match toolbase.bz's custom board software.
POST_BODY_SELECTORS = ["article.message .bbWrapper", ".message-body", "div.post_body"]
# Real first posts run much longer than the 800-char RSS-teaser cap above
# (a "shop" thread's product list can run 20k+ chars) - this is the cap for
# genuinely-fetched full content, not the RSS fallback.
ENRICHED_BODY_MAX_CHARS = 2000

# Screenshot evidence: every enriched incident gets one (not gated on
# alert-worthiness like browser_forum.py's, since the pool here is small
# enough and the user asked for full coverage) - one shared browser
# instance per enrich_batch() call, not one launch per incident, since a
# fresh Chromium launch is the expensive part (~0.5-1s), not the
# navigation. `evidence.screenshot_url` is a local file path, same
# unhosted-for-now caveat as browser_forum.py's - see README.
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]")
SCREENSHOT_NAV_TIMEOUT_MS = 15_000
SCREENSHOT_CONTENT_TIMEOUT_MS = 8_000
SCREENSHOT_OVERLAY_SETTLE_MS = 3_000

# Live-observed (2026-11-01): a single slow/overloaded forum can make
# several consecutive _save_screenshot() calls each burn close to their
# full ~26s worst case (15s nav + 8s selector-wait + 3s settle), with
# nothing logged in between since only failures are logged - from the
# outside this is indistinguishable from a genuine hang. After a 6-week
# collection gap, one real run spent 25+ minutes in this loop alone. Since
# this now runs on a 30-minute recurring local schedule (see
# scripts/run_cycle.ps1), an unbounded screenshot phase risks the *rest*
# of the cycle (emit, alerts, health-check, mirror-adoption, dashboard
# rebuild - all cheap/fast) never running at all. A wall-clock budget on
# the screenshot phase - not per-item, the whole batch - caps that risk:
# once exceeded, remaining incidents simply keep their RSS-teaser
# `description` and no `screenshot_url`, same as if Playwright were never
# installed, rather than blocking the cycle indefinitely.
SCREENSHOT_BATCH_BUDGET_SECONDS = 600

# name -> (homepage_url, known_rss_url or None to force autodiscovery)
FORUM_POOL: dict[str, dict[str, Optional[str]]] = {
    "turkhackteam": {"homepage": "https://www.turkhackteam.org", "rss": "https://www.turkhackteam.org/forumlar/-/index.rss"},
    "spyhackerz": {"homepage": "https://spyhackerz.org", "rss": "https://spyhackerz.org/forum/forums/-/index.rss"},
    "sinister.ly": {"homepage": "https://sinister.ly", "rss": "https://sinister.ly/syndication.php"},
    # "altenens" deliberately excluded: live content skews to adult-content sharing
    # (some titles reference minors) rather than CTI-relevant hacking/fraud chatter.
    "bdfclub": {"homepage": "https://bdfclub.com", "rss": "https://bdfclub.com/forums/-/index.rss"},
    "bhcforums": {"homepage": "https://bhcforums.cc", "rss": "https://bhcforums.cc/syndication.php"},
    "carders.biz": {"homepage": "https://carders.biz", "rss": "https://carders.biz/forums/-/index.rss"},
    "leetforums": {"homepage": "https://leetforums.cc", "rss": "https://leetforums.cc/forums/-/index.rss"},
    "mmgp": {"homepage": "https://mmgp.com", "rss": "https://mmgp.com/forums/-/index.rss"},
    "probiv": {"homepage": "https://probiv.one", "rss": "https://probiv.one/forums/-/index.rss"},
    "xreactor": {"homepage": "https://xreactor.org", "rss": "https://xreactor.org/forums/-/index.rss"},
    "leakforum.io": {"homepage": "https://leakforum.io", "rss": "https://leakforum.io/syndication.php"},
    # --- added from the deepdarkCTI-derived RSS discovery batch (2026-09-06) ---
    # 11 of 17 re-verified-live candidates passed a title-sample content review;
    # 6 were dropped: "bits.media" / "mipped" / "openssource" are off-topic
    # (mainstream crypto/coupon/course-piracy forums, not underground hacking/fraud),
    # "hard-tm" mirrors the altenens problem (leaked/adult personal content), and
    # "dublikat.club" / "seopirat" both surfaced the same recurring "intimate
    # archives (18+)" ad in their sampled items alongside otherwise-CTI content.
    "breachforums": {"homepage": "https://bf.st", "rss": "https://bf.st/syndication.php"},
    "darkforums": {"homepage": "https://darkforums.as", "rss": "https://darkforums.as/syndication.php"},
    # The site-wide feeds above only carry the latest ~30 posts ACROSS every
    # subforum (MAX_ITEMS_PER_SITE), so a specific, lower-traffic subforum
    # like "Leads Market" (marketing/contact-lead sales - e.g. "Turkey
    # Betting Leads", "700k Coinbase Crypto Leads" - a different genre from
    # database-breach dumps, closer to the "[TURKEY] Ziraat Bank Promotion
    # Leads" case that motivated this search) gets crowded out and almost
    # never shows up. Found live (2026-11-01): both forums' MyBB software
    # exposes a per-subforum feed via `syndication.php?fid=N`, confirmed
    # with a real shared cross-post ("Turkey Betting Leads" appears on
    # both) - same two already-working forums, just their dedicated feed
    # instead of being diluted into the general one. Checked whether this
    # fid-based per-subforum pattern exists on the pool's other MyBB-style
    # forums (bhcforums, leakforum.io) - it doesn't, this is specific to
    # these two.
    "breachforums-leads": {"homepage": "https://bf.st/Forum-Leads-Market", "rss": "https://bf.st/syndication.php?fid=42"},
    "darkforums-leads": {"homepage": "https://darkforums.as/Forum-Leads-Market", "rss": "https://darkforums.as/syndication.php?fid=83"},
    "blackbones": {"homepage": "https://blackbones.cc", "rss": "https://blackbones.cc/forums/-/index.rss"},
    "blackhatpakistan": {"homepage": "https://blackhatpakistan.net", "rss": "https://blackhatpakistan.net/forums/-/index.rss"},
    "forumteam.top": {"homepage": "https://forumteam.top", "rss": "https://forumteam.top/forums/-/index.rss"},
    "niflheim": {"homepage": "https://niflheim.world", "rss": "https://niflheim.world/forums/-/index.rss"},
    "nodo313": {"homepage": "https://www.nodo313.net", "rss": "https://www.nodo313.net/foros/-/index.rss"},
    "piratebuhta": {"homepage": "https://piratebuhta.pw", "rss": "https://piratebuhta.pw/forums/-/index.rss"},
    "spear": {"homepage": "https://spear.cx", "rss": "https://spear.cx/syndication.php"},
    "toolbase": {"homepage": "https://www.toolbase.bz", "rss": "https://www.toolbase.bz/board/rss/forums/1-tools/"},
    "warriors": {"homepage": "https://warriors.to", "rss": "https://warriors.to/forums/-/index.rss"},
    # --- re-diffed against the current (2026-11-01) deepdarkCTI forum.md, 167
    # not-yet-in-pool ONLINE+clearnet candidates probed. Most were dead
    # (DNS/connect failures - the list churns fast), Cloudflare/login-gated
    # (xssf.net's "/feed" is actually its login page, same HUMINT-only
    # category as pwnforums.st - see README), off-topic (zismo.biz: VK
    # account farming, rf-cheats.ru: game cheats/botting, korovka.cc: a
    # breach-NEWS blog, not forum chatter - out of scope per this module's
    # own docstring), or duplicates of a pool forum under another domain
    # (blackbones.net's RSS literally links back to blackbones.cc;
    # leakforum.st's and cracked.sh's <link> tags point at leakforum.io and
    # a dead cracked.st respectively). One real, live, content-reviewed
    # addition survived:
    "ascarding": {"homepage": "https://ascarding.com", "rss": "https://ascarding.com/forums/-/index.rss"},
    # --- global expansion batch (2026-09-12): candidates sourced from threat-intel
    # write-ups/forum directories, each live-verified twice (once by research, once
    # independently here) with a real 200 + genuine <item>-bearing RSS body before
    # being added. Dropped from this same research pass, and NOT added: forums that
    # were live but had no working RSS (cracked.st, wwh-club.net, omerta.cc, greysec.net,
    # carder.uk, cardingsecrets.is), forums blocked by Cloudflare/bot-check from this
    # environment (hackforums.net, ezcarder.is, procrd.cc, dublikat.cc, exploit.in),
    # forums seized/dead/squatted (leakbase.la, nulled.cc, omerta.vc, xss.is/.ac), and
    # crdpro.at (RSS body is genuine but the endpoint 404s instead of returning 200 -
    # fails this pool's own liveness bar, revisit if it starts returning 200).
    "darknetarmy": {"homepage": "https://darknetarmy.st", "rss": "https://darknetarmy.st/forums/-/index.rss"},
    "carding.pw": {"homepage": "https://carding.pw", "rss": "https://carding.pw/forums/-/index.rss"},
    "validmarket": {"homepage": "https://validmarket.io", "rss": "https://validmarket.io/forums/-/index.rss"},
    "cardinggame": {"homepage": "https://cardinggame.site", "rss": "https://cardinggame.site/forums/-/index.rss"},
    "darkwebmafias": {"homepage": "https://darkwebmafias.net", "rss": "https://darkwebmafias.net/forums/-/index.rss"},
    # antichat.io doesn't follow the XenForo `/forums/-/index.rss` convention -
    # its real feed is at `/news.rss` (found via the homepage's own <link> tag).
    # FORUM_POOL's rss value is just fetched literally, no code change needed.
    "antichat": {"homepage": "https://forum.antichat.io", "rss": "https://forum.antichat.io/news.rss"},
    # --- deepdarkCTI merge batch (2026-09-12): user asked to reconcile the
    # project's original deepdarkCTI-derived seed list (see module docstring)
    # against the CURRENT github.com/fastfire/deepdarkCTI/forum.md. Of 134
    # clearnet "ONLINE"-labeled candidates not already in this pool or already
    # excluded, 2 turned out to be mirror domains of forums already here
    # (leakforum.st serves byte-identical content to leakforum.io; toolba.se
    # 301-redirects to the already-pooled www.toolbase.bz) - not re-added.
    # These 3 are genuinely new, live-verified (RSS 200 + real <item> content,
    # on-topic sampled) forums:
    "0x00sec": {"homepage": "https://0x00sec.org", "rss": "https://0x00sec.org/rss/"},
    "nulledbb": {"homepage": "https://nulledbb.com", "rss": "https://nulledbb.com/syndication.php"},
    "voided": {"homepage": "https://voided.to", "rss": "https://voided.to/syndication.php"},
    # --- GitHub-sourced batch (2026-11-06): candidates surfaced not from
    # deepdarkCTI but from a third-party tracker's own config
    # (github.com/adminlove520/DarkWeb-Forums-Tracker's rss_dataleak.yaml),
    # live-tested here one by one:
    "cardforum": {"homepage": "https://cardforum.cc", "rss": "https://cardforum.cc/syndication.php?limit=50"},
    # "dublikat" is at.dublikat.club, NOT the bare dublikat.club already
    # rejected above (2026-09-06 batch comment: "recurring 'intimate
    # archives (18+)' ad"). Different host behind the same brand - bare
    # dublikat.club now 403s outright, while this "at." subdomain returned
    # a clean 25-item feed; re-checked specifically for that 18+ ad marker
    # in every item's title AND description (not just a title skim) and it
    # is genuinely absent here - real content is document forgery/SIM-card/
    # crypto-exchange/verification services, on-topic. Not re-adding the
    # bare domain; this is a distinct, separately-verified host.
    "dublikat": {"homepage": "https://at.dublikat.club", "rss": "https://at.dublikat.club/forums/-/index.rss"},
    # Checked and NOT added from the same source list: "hard-tm" (hard-tm.su)
    # re-confirms the existing 2026-09-06 rejection above almost verbatim -
    # live sample still shows explicit adult-content thread titles mixed
    # into the feed, same problem, not a mistaken exclusion. "xforums.st" /
    # "gerki" (forum.gerki.ws) are both behind a Cloudflare browser-check
    # wall (302/503), same already-documented category as pwnforums.st -
    # not attempted past that. "leakbase.la" now serves a law-enforcement
    # seizure page ("This Website Has Been Seized"), confirmed dead.
    # "htdark.com"/"mipped.com"/"ipbmafia.ru" could not be reached at all
    # from this environment (TLS handshake / connection timeout) - inconclusive,
    # not added on an unverified guess. "sinister.ly" in that config uses a
    # subforum-specific feed (`?fid=9`) instead of the site-wide one already
    # in this pool - same pattern as the Leads Market discovery above - but
    # the fid=9 URL could not be reached from this environment either
    # (DNS/connect failure, repeatable); worth retrying later, not added yet.
}


class UndergroundForumSource(Source):
    name = "underground_forums"
    poll_interval_seconds = 900

    def __init__(self, sites: Optional[dict] = None) -> None:
        # `sites or FORUM_POOL` would be wrong here: an explicitly empty
        # dict (e.g. pipeline.build_sources()'s only_forums filter matching
        # nothing) is falsy and would silently fall back to the FULL pool
        # instead of meaning "no sites" - only a real None (no argument at
        # all) should default to FORUM_POOL.
        self.sites = FORUM_POOL if sites is None else sites

    def fetch(self) -> Iterable[Incident]:
        now = datetime.now(timezone.utc).isoformat()
        incidents: list[Incident] = []
        # Per-site outcome for this cycle, read by pipeline.py (via
        # getattr, so a source without this attribute degrades silently)
        # to populate collection_health.py's per-source observability
        # record. This is what tells "0 new posts, feed reachable, 30
        # items parsed cleanly" (status=healthy) apart from "0 new posts,
        # feed unreachable this cycle" (status=unreachable) and "site
        # raised" (status=error) - before this, all three collapsed into
        # the same silent empty list, indistinguishable from each other or
        # from a dead parser selector.
        self.last_cycle_health: dict[str, dict] = {}
        for site_name, cfg in self.sites.items():
            try:
                site_incidents, fetched_ok, parse_errors = self._fetch_site(site_name, cfg, now)
                incidents.extend(site_incidents)
                self.last_cycle_health[site_name] = {
                    "last_attempt": now,
                    "last_success": now if fetched_ok else None,
                    "posts_discovered": len(site_incidents),
                    "parse_errors": parse_errors,
                    "http_errors": 0 if fetched_ok else 1,
                    "status": "healthy" if fetched_ok else "unreachable",
                }
            except Exception:
                log.exception("underground_forums: site %s failed entirely, skipping", site_name)
                self.last_cycle_health[site_name] = {
                    "last_attempt": now,
                    "last_success": None,
                    "posts_discovered": 0,
                    "parse_errors": 0,
                    "http_errors": 1,
                    "status": "error",
                }
        return incidents

    def _fetch_site(self, site_name: str, cfg: dict, ingested_at: str) -> tuple[list[Incident], bool, int]:
        """Returns (incidents, fetched_ok, parse_errors) - `fetched_ok` is
        False only when no RSS content could be obtained at all (feed
        down/blocked/not found this cycle), never when the feed was
        reachable but genuinely empty or 100% malformed items (that's
        still `fetched_ok=True` with posts_discovered=0 or
        parse_errors>0 - a reachable-but-broken-parser site is a
        different, more specific signal than an unreachable one).
        """
        rss_url = cfg.get("rss")
        rss_text = self._try_fetch_rss(rss_url) if rss_url else None
        if rss_text is None:
            discovered = self._discover_rss_url(cfg["homepage"])
            if not discovered:
                log.warning("underground_forums: no RSS feed found for %s", site_name)
                return [], False, 0
            rss_text = self._try_fetch_rss(discovered)
            rss_url = discovered
            if rss_text is None:
                return [], False, 0

        out = []
        parse_errors = 0
        for match in list(ITEM_RE.finditer(rss_text))[:MAX_ITEMS_PER_SITE]:
            try:
                out.append(self._normalize(site_name, match.group("body"), ingested_at))
            except Exception:
                parse_errors += 1
                log.exception("underground_forums: skipping malformed item from %s", site_name)
        return out, True, parse_errors

    def enrich(self, incident: dict) -> dict:
        """Visits the thread page ONCE and pulls two things off it: the
        full first-post text (replacing `description` when it's a real
        improvement over the RSS teaser) and, where the forum exposes it,
        a reply count for popularity ranking (see `alerting.compute_
        popularity_score`).

        Many of these forums gate full content behind a reply-to-unlock
        wall ("[HIDE]" BBCode) - for a guest fetch that just renders as a
        short "you must register/reply to view this content" placeholder,
        which is shorter than the RSS teaser almost every time, so it never
        overwrites it. No special-casing needed: the length comparison
        alone does the right thing for both the gated and the empty-match
        case (e.g. an image-only first post with no text at all).
        """
        url = (incident.get("evidence") or {}).get("post_url")
        if not url:
            return incident
        try:
            html_text = self._fetch_thread_page(url)
        except Exception:
            log.exception("underground_forums: enrich failed for %s", url)
            return incident
        if not html_text:
            return incident

        body = self._extract_post_body(html_text)
        if body:
            if len(body) > ENRICHED_BODY_MAX_CHARS:
                body = body[:ENRICHED_BODY_MAX_CHARS].rsplit(" ", 1)[0] + "…"
            existing = incident.get("description") or ""
            if len(body) > len(existing):
                incident["description"] = body
                incident.setdefault("extra", {})["content_source"] = "thread_page"

        reply_count = self._extract_reply_count(html_text)
        if reply_count is not None:
            incident.setdefault("extra", {})["reply_count"] = reply_count

        return incident

    def enrich_batch(self, incidents: list[dict]) -> list[dict]:
        """Runs the existing per-item text/reply-count enrich() first (cheap,
        plain `requests`), then makes one pass over the batch with a single
        shared Playwright browser to capture a screenshot per incident -
        batching the browser launch is what makes "a screenshot for every
        new post across every forum" affordable instead of one Chromium
        process per incident.
        """
        out = incidents
        for i, inc in enumerate(out):
            out[i] = self.enrich(inc)

        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            log.warning("underground_forums: playwright not installed, skipping screenshots this cycle")
            return out

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    batch_start = time.monotonic()
                    skipped = 0
                    for i, inc in enumerate(out):
                        if time.monotonic() - batch_start > SCREENSHOT_BATCH_BUDGET_SECONDS:
                            skipped = len(out) - i
                            log.warning(
                                "underground_forums: screenshot batch budget (%ds) exceeded, "
                                "skipping remaining %d incident(s) this cycle (no screenshot, "
                                "RSS-teaser description kept)",
                                SCREENSHOT_BATCH_BUDGET_SECONDS, skipped,
                            )
                            break
                        url = (inc.get("evidence") or {}).get("post_url")
                        if not url:
                            continue
                        path = self._save_screenshot(browser, url)
                        if path:
                            inc.setdefault("evidence", {})["screenshot_url"] = path
                finally:
                    browser.close()
        except Exception:
            log.exception("underground_forums: could not start a browser for screenshots, skipping this cycle")
        return out

    @staticmethod
    def _save_screenshot(browser, url: str) -> Optional[str]:
        try:
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
        except Exception:
            log.exception("underground_forums: could not open a page for screenshot of %s", url)
            return None
        try:
            page.goto(url, timeout=SCREENSHOT_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            # Several pool forums (carders.biz confirmed live) show a
            # transient JS splash/interstitial ("Bookmark our domains") on
            # top of the real thread for a couple seconds. This is NOT a
            # content-not-loaded-yet problem - the post body is already in
            # the DOM at domcontentloaded, `wait_for_selector` on it returns
            # immediately, and a screenshot taken right then still shows the
            # overlay (verified live: selector present + count>0 but the
            # captured frame is 100% splash). The overlay dismisses itself
            # on a timer, not on a DOM event, so there's nothing to wait on
            # except the clock - a flat pause is the actual fix.
            try:
                page.wait_for_selector(", ".join(POST_BODY_SELECTORS), timeout=SCREENSHOT_CONTENT_TIMEOUT_MS)
            except Exception:
                pass
            page.wait_for_timeout(SCREENSHOT_OVERLAY_SETTLE_MS)
            # Most pool forums stack ad banners above the fold - scroll the
            # actual post into view so the captured frame is the thread
            # content itself, not a wall of unrelated shop ads.
            for selector in POST_BODY_SELECTORS:
                try:
                    locator = page.locator(selector).first
                    if locator.count():
                        locator.scroll_into_view_if_needed(timeout=2_000)
                        break
                except Exception:
                    continue
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            slug = _SLUG_RE.sub("_", url.rstrip("/").rsplit("/", 1)[-1])[:120] or "thread"
            path = SCREENSHOT_DIR / f"{slug}.png"
            page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            log.exception("underground_forums: screenshot failed for %s", url)
            return None
        finally:
            page.close()

    @staticmethod
    def _fetch_thread_page(url: str) -> Optional[str]:
        try:
            resp = _SESSION.get(url, headers=HEADERS, timeout=12)
        except requests.exceptions.Timeout:
            log.warning("underground_forums: thread fetch timed out for %s", url)
            return None
        except requests.exceptions.RequestException:
            log.exception("underground_forums: thread fetch failed for %s", url)
            return None
        except Exception:
            log.exception("underground_forums: thread fetch raised unexpectedly for %s", url)
            return None
        if resp.status_code != 200:
            _log_http_status("thread fetch", url, resp.status_code)
            return None
        return resp.text

    @staticmethod
    def _extract_post_body(html_text: str) -> Optional[str]:
        soup = BeautifulSoup(html_text, "html.parser")
        for selector in POST_BODY_SELECTORS:
            el = soup.select_one(selector)
            if el:
                text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
                if text:
                    return text
        return None

    @staticmethod
    def _extract_reply_count(html_text: str) -> Optional[int]:
        """XenForo's schema.org `DiscussionForumPosting` JSON-LD block (an
        SEO add-on some - not all - sites in the pool have installed)
        carries a real reply count as `interactionStatistic.
        userInteractionCount`. Verified live across 21 of the 22 pool
        forums (2026-09-08): present on 8/16 XenForo sites, absent on the
        rest and on every MyBB site (no equivalent markup there at all) -
        so this is a bonus signal when present, not something every forum
        is expected to provide.
        """
        for match in LD_JSON_RE.finditer(html_text):
            try:
                data = json.loads(match.group(1))
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            stat = data.get("interactionStatistic")
            if isinstance(stat, dict) and isinstance(stat.get("userInteractionCount"), int):
                return stat["userInteractionCount"]
        return None

    @staticmethod
    def _discover_rss_url(homepage: str) -> Optional[str]:
        try:
            resp = _SESSION.get(homepage, headers=HEADERS, timeout=10)
        except requests.exceptions.Timeout:
            log.warning("underground_forums: homepage fetch timed out for %s", homepage)
            return None
        except requests.exceptions.RequestException:
            log.exception("underground_forums: homepage fetch failed for %s", homepage)
            return None
        except Exception:
            log.exception("underground_forums: homepage fetch raised unexpectedly for %s", homepage)
            return None
        if resp.status_code != 200:
            _log_http_status("homepage fetch", homepage, resp.status_code)
            return None
        m = RSS_LINK_RE.search(resp.text)
        if not m:
            return None
        href = m.group(1)
        return urljoin(resp.url, href) if href.startswith("/") else href

    @staticmethod
    def _try_fetch_rss(url: str) -> Optional[str]:
        try:
            resp = _SESSION.get(url, headers=HEADERS, timeout=12)
        except requests.exceptions.Timeout:
            log.warning("underground_forums: RSS fetch timed out for %s", url)
            return None
        except requests.exceptions.RequestException:
            log.exception("underground_forums: RSS fetch failed for %s", url)
            return None
        except Exception:
            log.exception("underground_forums: RSS fetch raised unexpectedly for %s", url)
            return None
        if resp.status_code != 200:
            _log_http_status("RSS fetch", url, resp.status_code)
            return None
        if "<item>" not in resp.text:
            return None
        return resp.text

    @staticmethod
    def _extract(pattern: re.Pattern, body: str) -> Optional[str]:
        m = pattern.search(body)
        if not m:
            return None
        groups = [g for g in m.groups() if g is not None]
        return groups[0].strip() if groups else None

    @classmethod
    def _normalize(cls, site_name: str, body: str, ingested_at: str) -> Incident:
        title = html.unescape(cls._extract(TITLE_RE, body) or "unknown")
        link = cls._extract(LINK_RE, body)
        pub_raw = cls._extract(PUBDATE_RE, body)
        published_at = None
        if pub_raw:
            try:
                published_at = parsedate_to_datetime(pub_raw).isoformat()
            except (TypeError, ValueError):
                published_at = pub_raw

        creator = cls._extract(CREATOR_RE, body)
        if creator:
            creator = TAG_RE.sub("", creator).strip()
        else:
            raw_author = cls._extract(AUTHOR_RE, body)
            if raw_author:
                em = AUTHOR_EMAIL_RE.match(raw_author)
                creator = em.group(1).strip() if em else raw_author.strip()
        actor = html.unescape(creator) if creator else "unknown"

        category = cls._extract(CATEGORY_RE, body)
        if category:
            category = html.unescape(category)

        raw_body = cls._extract(CONTENT_ENCODED_RE, body) or cls._extract(DESCRIPTION_RE, body) or ""
        text_body = html.unescape(TAG_RE.sub(" ", raw_body))
        text_body = re.sub(r"[​\s]+", " ", text_body).strip()
        if len(text_body) > MAX_BODY_CHARS:
            text_body = text_body[:MAX_BODY_CHARS].rsplit(" ", 1)[0] + "…"

        return Incident(
            detected_at=ingested_at,
            published_at=published_at,
            source=SourceMeta(type="forum", name=f"forum:{site_name}", url=link),
            threat_actor=ThreatActor(name=actor),
            # victim.sector is NOT this forum's own subforum/board category
            # (that's a navigation label, not a victim industry - it's
            # already preserved below in `tags`) - real sector
            # classification happens later, in pipeline._apply_ransomware_intel,
            # see sector_classification.py's docstring.
            victim=Victim(name=title),
            category="forum_post",
            title=title,
            description=text_body or None,
            evidence=Evidence(post_url=link),
            confidence="claimed",
            tags=["forum", site_name] + ([category.lower()] if category else []),
            extra={},
            stable_key=link,
        )
