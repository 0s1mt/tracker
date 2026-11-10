# Darkweb Forum Tracker

A lightweight, dependency-minimal Python pipeline that polls underground/dark-web-adjacent forums, Telegram channels, and ransomware-leak feeds, normalizes everything into one `Incident` schema, deduplicates it, and renders a static HTML dashboard. No LLM/AI in the collection path — parsing is deterministic (RSS + regex/BeautifulSoup), by design.

**Why this README exists:** this is a snapshot pushed for external code review, specifically to get a second opinion on one unsolved problem — see [The one open problem: pwnforums.st](#the-one-open-problem-pwnforumsst) below. If that's what you're here for, jump straight there; the rest is architecture context.

## What it does

- Polls **36 RSS-syndicated forums** (`forumtracker/sources/underground_forums.py`) — auto-discovers each site's RSS feed, falls back to scraping a thread-list page when no feed exists.
- Polls **2 browser-rendered forums** (`forumtracker/sources/browser_forum.py`, Playwright/Chromium) for sites with no RSS and JS-rendered content — including a shoutbox/chatbox scraper.
- Pulls real victim intelligence from **ransomware.live**'s public API (`forumtracker/sources/ransomware_live.py`) — actual victim/country/sector data from a CTI team, not text-guessed.
- Bridges a **Telegram channel feed** (`forumtracker/sources/telegram_bridge.py`) via RSS, with a generic fallback parser for posts that don't match any bot's structured format.
- Normalizes all of the above into one `Incident` dataclass (`forumtracker/schema.py`): source, threat actor, victim, category, evidence URL, confidence, tags.
- Deduplicates via a SQLite "seen" store keyed on a content-derived `incident_id` (forum-native thread permalink when available, falls back to a title/author/date hash).
- Runs health-checks, auto-mirror-adoption (when a forum's domain changes), cross-forum username correlation, and a resumable member-directory crawl.
- Renders everything to a single searchable `reports/dashboard.html`.

## Architecture

Every source implements one interface (`forumtracker/sources/base.py`):

```python
class Source(ABC):
    def fetch(self) -> Iterable[Incident]: ...      # must never raise
    def enrich(self, incident: dict) -> dict: ...    # optional, post-dedup, per-item
    def enrich_batch(self, incidents: list[dict]) -> list[dict]: ...  # same, batched
```

`forumtracker/pipeline.py`'s `run_full_cycle()` ties it together: fetch all sources → dedup against `seen.sqlite3` → `enrich_batch` on genuinely new incidents only → append to `data/incidents.jsonl` (append-only, never rewritten) → alert (Telegram) → health-check / auto-mirror-adoption → record per-site collection health → stats/identity bookkeeping.

`forumtracker/cli.py` exposes it:

```
python -m forumtracker.cli --once                 # one fetch cycle
python -m forumtracker.cli --loop --interval 900   # poll continuously
python -m forumtracker.cli --collection-health      # per-site last-cycle outcome, no network calls
python -m forumtracker.cli --health                 # pool liveness, dead-forum detection
python -m forumtracker.cli --build-dashboard        # render reports/dashboard.html from current data/
python -m forumtracker.cli --only-forums darkforums,breachforums  # scoped test run
```

In production this runs on a 30-minute schedule — either `.github/workflows/pipeline.yml` (GitHub Actions) for every source that doesn't need a persistent IP, or a local Windows Scheduled Task (`scripts/run_cycle.ps1`) for the one source that does (see below).

## Reliability (recently hardened)

The collection layer was recently audited end-to-end and hardened:

- Shared `requests.Session` with bounded exponential backoff + jitter, retrying only on 429/5xx (never on 403/404 — those are correctness signals, not transient faults), with `Retry-After` support.
- Per-site collection-health observability (`forumtracker/collection_health.py`): every cycle records, per forum, `healthy` / `unreachable` / `error`, posts discovered, new vs. duplicate counts, and parse/HTTP error counts — so a quiet forum (genuinely no new posts) is no longer indistinguishable from a broken one.
- Incident IDs for forum threads now hash on the thread's own stable permalink instead of title/author/published-date — fixes a false-duplicate bug where a bumped thread (RSS `pubDate` often reflects last-reply time, not creation time) could otherwise mint a new ID — and therefore a false "new incident" alert — on every reply.
- Full test suite: 225 tests, all green (`pytest -q`).

## Data model

```python
Incident(
    detected_at, published_at, source, threat_actor, victim,
    category,       # "forum_post" | "ransomware_leak" | "data_leak" | "data_breach" | "phishing" | "defacement"
    title, description, evidence,  # evidence.post_url, evidence.screenshot_url
    confidence,     # "claimed" | "verified"
    tags, extra,
)
```

See `schemas/incident.schema.json` for the formal contract and `schemas/examples/` for real examples.

## The one open problem: pwnforums.st

This is the reason this repo is being shared for review.

**The site:** `pwnforums.st`, structured listing format (country/category/market fields), real relevant content. Gated by **Cloudflare + a secondary "Anubis" proof-of-work anti-bot layer**.

**What was tried and ruled out:**
- Plain `requests` and a real Playwright/Chromium browser (with a wait) both get stuck on the Cloudflare interactive challenge ("Just a moment...").
- General-purpose Cloudflare-bypass tools (FlareSolverr, cloudscraper, undetected-chromedriver, playwright-stealth) were researched and deliberately **not** adopted: current reporting says they're unreliable against 2026-era Turnstile/Managed Challenge variants, and they fall into bot-detection-evasion territory, which is out of scope for this project on principle — not just a reliability call.
- A human-solved session's cookies *can* be replayed (`cookies_env` config in `browser_forum.py` — the cookie value is read from an environment variable, never hardcoded, and the mechanism is a plain session replay, not an automated bypass). This part works: a real cookie set (`cf_clearance`, `mybbuser`, and an `anubis.pwnforums-auth` JWT) was obtained from a real, human-solved browser session and verified to attach correctly to the Playwright context (9/9 checks passed).

**The actual blocker:** both `cf_clearance` and the Anubis JWT appear to be **bound to the IP address they were solved from**. A cookie solved on the user's home connection does not work when replayed from a different egress IP (verified live: replaying from a different IP still showed the challenge page). Concretely, this means:
- It will **never** work from GitHub Actions or any other environment with a rotating/shared IP.
- It **does** work from a fixed-IP machine, but only until the cookie expires (the Anubis JWT appears to carry roughly a 24h validity from its `iat`/`exp` claims), at which point a human has to re-solve it from that same IP and refresh the stored cookie value.

**Where it stands:** this is being run as a separate local-only job (`scripts/run_cycle.ps1` + a Windows Scheduled Task) on a fixed-IP machine, with the cookie refreshed manually and periodically via `setx`. This works but isn't elegant and doesn't scale past "one person's machine."

**What would help:** any legitimate approach to either (a) make periodic re-solving cheaper/more reliable without it becoming automated bot-detection evasion, or (b) architecturally decouple "needs a stable, pre-cleared IP" from the rest of the pipeline (e.g. a small persistent-IP VPS as a dedicated collector for just this source, feeding its output back into the same `Incident` stream). Ideas on either front are exactly what's being asked for here.

## Running it

```bash
pip install -r requirements.txt
python -m playwright install chromium   # only needed for browser_forum.py sources
python -m forumtracker.cli --once
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q
```

225 tests, fixture-based (no live-network calls during the test run itself).

## Relationship to DarkWebFeed

This module is deliberately split out from the parent `DarkWebFeed` ingestion pipeline — its own release cycle, its own dependencies (`requests`, `playwright`, `beautifulsoup4`), its own dedup state — but shares the same `Incident` envelope shape, so its output can be merged into the same stream downstream if needed.
