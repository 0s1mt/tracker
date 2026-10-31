"""Derived "what matters right now" views over the raw incident feed:
which forums are currently active (not just how many incidents they've
ever produced), which actors are posting where, and a simple reputation
score per actor. None of this is collected separately - it's all computed
from the same incidents.jsonl the rest of the pipeline already writes, the
same way dashboard.py's top_actors/top_countries aggregates already did
before this module existed (moved here, unchanged, so every "aggregate
over incidents" function lives in one place).

Deliberately NOT a machine-learned or LLM-scored "importance" - every
formula here is a small, fixed, documented weighting, same philosophy as
the rest of this project (see ransomware_intel.py/alerting.py for the same
style of plain keyword/threshold scoring, not a model call).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# Role/placeholder actor names that would otherwise dominate any
# actor-facing ranking without being a real identity.
_GENERIC_ACTOR_NAMES = {
    "unknown", "admin", "administrator", "mod", "moderator", "system",
    "deleted", "guest", "anonymous", "support", "staff", "bot",
}

_FORUM_PREFIX = "forum:"


def _forum_name(src: Optional[str]) -> Optional[str]:
    if not src or not src.startswith(_FORUM_PREFIX):
        return None
    return src[len(_FORUM_PREFIX):]


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def top_actors(incidents: list[dict], limit: int = 15) -> list[dict]:
    counts: dict[str, int] = {}
    for inc in incidents:
        name = (inc.get("a") or "").strip()
        if not name or name.lower() in _GENERIC_ACTOR_NAMES:
            continue
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"name": name, "count": count} for name, count in ranked]


def top_countries(incidents: list[dict], limit: int = 12) -> list[dict]:
    counts: dict[str, int] = {}
    for inc in incidents:
        cc = inc.get("cc")
        if not cc:
            continue
        counts[cc] = counts.get(cc, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"code": code, "count": count} for code, count in ranked]


def top_sectors(incidents: list[dict], limit: int = 12) -> list[dict]:
    counts: dict[str, int] = {}
    for inc in incidents:
        sector = inc.get("sector")
        if not sector:
            continue
        counts[sector] = counts.get(sector, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"sector": sector, "count": count} for sector, count in ranked]


def forum_activity(incidents: list[dict], now: Optional[datetime] = None) -> list[dict]:
    """Ranks forums by RECENT activity, not lifetime incident count - a
    forum with 400 incidents collected over a year but nothing in the last
    month should rank below one with 60 incidents this week. "importance"
    is exactly that recency-weighted signal (the thing problem #1 asked
    for: "hangi forumlar en aktif").

    activity_score = recent_7 * 3 + recent_30 - a deliberately simple,
    fixed weighting (incidents from the last 7 days count 3x as much as
    the 8-30 day window, days 31+ don't count at all) so "active this
    week" dominates "was active last month", without needing a decaying
    half-life function to get that ordering right.
    """
    now = now or datetime.now(timezone.utc)
    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)

    totals: dict[str, int] = {}
    recent_7: dict[str, int] = {}
    recent_30: dict[str, int] = {}
    last_seen: dict[str, str] = {}

    for inc in incidents:
        forum = _forum_name(inc.get("src"))
        if not forum:
            continue
        totals[forum] = totals.get(forum, 0) + 1
        dt = _parse_dt(inc.get("d"))
        if dt is None:
            continue
        if last_seen.get(forum) is None or inc["d"] > last_seen[forum]:
            last_seen[forum] = inc["d"]
        if dt >= cutoff_30:
            recent_30[forum] = recent_30.get(forum, 0) + 1
            if dt >= cutoff_7:
                recent_7[forum] = recent_7.get(forum, 0) + 1

    rows = []
    for forum, total in totals.items():
        r7 = recent_7.get(forum, 0)
        r30 = recent_30.get(forum, 0)
        rows.append({
            "forum": forum,
            "total": total,
            "recent_7d": r7,
            "recent_30d": r30,
            "activity_score": r7 * 3 + r30,
            "last_seen": last_seen.get(forum),
        })
    rows.sort(key=lambda r: (r["activity_score"], r["total"]), reverse=True)
    return rows


def per_forum_top_actors(incidents: list[dict], limit_per_forum: int = 5) -> dict[str, list[dict]]:
    """{forum_name: [{"name", "count"}, ...]} - the per-forum equivalent of
    top_actors(), so "who's most active ON THIS forum" (what vecert-style
    tools show per-forum) is answerable, not just a single global list.
    """
    by_forum: dict[str, dict[str, int]] = {}
    for inc in incidents:
        forum = _forum_name(inc.get("src"))
        name = (inc.get("a") or "").strip()
        if not forum or not name or name.lower() in _GENERIC_ACTOR_NAMES:
            continue
        by_forum.setdefault(forum, {})
        by_forum[forum][name] = by_forum[forum].get(name, 0) + 1

    result = {}
    for forum, counts in by_forum.items():
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit_per_forum]
        result[forum] = [{"name": name, "count": count} for name, count in ranked]
    return result


def _stars_from_score(score: int) -> int:
    if score >= 20:
        return 5
    if score >= 10:
        return 4
    if score >= 5:
        return 3
    if score >= 2:
        return 2
    return 1


def actor_reputation(incidents: list[dict]) -> dict[str, dict]:
    """A simple, fixed-formula reputation score per actor - NOT a claim
    about real-world skill or threat level, just a deterministic ranking
    signal: total posts, plus a bonus for appearing on multiple forums
    (cross-forum presence is a stronger "this is a real recurring actor,
    not a one-off" signal than raw post count alone - same intuition
    identity_correlation.py's cross-forum matching is built on).

    score = total_posts + 2 * (distinct_forums - 1)
    stars = bucketed 1-5 from that score (see _stars_from_score) - a crude
    but transparent mapping, not a percentile/z-score model, so the same
    actor always gets the same star rating regardless of what else is in
    the dataset that day.
    """
    posts: dict[str, int] = {}
    forums: dict[str, set[str]] = {}
    for inc in incidents:
        name = (inc.get("a") or "").strip()
        if not name or name.lower() in _GENERIC_ACTOR_NAMES:
            continue
        posts[name] = posts.get(name, 0) + 1
        forum = _forum_name(inc.get("src"))
        if forum:
            forums.setdefault(name, set()).add(forum)

    result = {}
    for name, total in posts.items():
        distinct_forums = len(forums.get(name, set()))
        score = total + 2 * max(distinct_forums - 1, 0)
        result[name] = {"score": score, "stars": _stars_from_score(score), "forums": distinct_forums, "total": total}
    return result
