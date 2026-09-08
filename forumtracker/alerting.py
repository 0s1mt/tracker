"""Deterministic (no LLM) alerting layer on top of forum Incidents.

Two things this gives the pipeline that a raw Incident doesn't:

1. `match_entities()` - the same normalize+match algorithm used by
   brunosergi/darkweb-forums-tracker's "Entity Matching Code" n8n node
   (canonical name + variant list, accent-stripped text, word-boundary
   matching for short terms, substring matching for multi-word terms, both
   for single long words) - ported to plain Python so it runs offline, with
   no API key and no per-call cost.

2. `to_alert_card()` - renders one Incident into two shapes from the same
   data: a flat one (`simple`) matching the "Data Leak Monitor"-style
   Telegram posts (Source/Content/author/Detection Date/Type), and a richer
   one (`rich`, Target/Threat Actor/Country/Intel Source/Detection Date/
   Risk Score/Brief Summary/Source Link) closer to an OsintCTI-style card -
   so a non-technical reader gets a quick read and a CTI analyst gets the
   same underlying numbers to check, without asking an LLM to grade its own
   homework on every single forum post.

KEYWORD_GROUPS below is a small starter set, not a finished watchlist - add
your own groups (brand names, org names, CVEs, whatever you actually want
paged on) the same way FORUM_POOL gets extended: edit the list, no other
code changes needed.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Optional

# canonical_name -> [canonical_name, variant, variant, ...]. The first
# element of each group is what shows up in `matched_entities` - keep it
# lowercase so it stays stable if variants are added later.
KEYWORD_GROUPS: list[list[str]] = [
    ["ransomware"],
    ["database", "db dump", "sql dump"],
    ["combolist", "combo list", "mail:pass", "url:pass"],
    ["fullz", "cvv", "dump"],
    ["ssn", "social security"],
    ["breach", "leaked", "leak"],
    ["government", ".gov", "gouv"],
    ["bank", "banking"],
]

_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")

_CATEGORY_BASE_SCORE = {
    "ransomware_leak": 6,
    "data_breach": 6,
    "data_leak": 5,
    "exploited_vulnerability": 5,
    "phishing": 4,
    "forum_post": 3,
    "defacement": 3,
}

_CATEGORY_LABELS = {
    "ransomware_leak": "ransomware sızıntısı",
    "data_leak": "veri sızıntısı",
    "data_breach": "doğrulanmış veri ihlali",
    "forum_post": "forum gönderisi",
    "defacement": "web sitesi deface",
    "phishing": "phishing kampanyası",
    "exploited_vulnerability": "istismar edilen zafiyet",
}


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = _NON_WORD_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def match_entities(text: str, groups: list[list[str]] = KEYWORD_GROUPS) -> list[str]:
    """Returns the canonical names of every keyword group with a hit in `text`."""
    if not text:
        return []
    normalized = _normalize_text(text)
    matched: list[str] = []
    for group in groups:
        canonical = group[0]
        for variant in group:
            term = _normalize_text(variant)
            if not term:
                continue
            if len(term) <= 3:
                hit = re.search(rf"\b{re.escape(term)}\b", normalized) is not None
            elif " " in term:
                hit = term in normalized
            else:
                hit = term in normalized or re.search(rf"\b{re.escape(term)}\b", normalized) is not None
            if hit:
                matched.append(canonical)
                break
    return matched


def _incident_text(incident: dict) -> str:
    return f"{incident.get('title', '')} {incident.get('description') or ''}"


def compute_risk_score(incident: dict, matched_entities: Optional[list[str]] = None) -> int:
    """1-10, deterministic: category base + keyword-hit bonus + verified
    bonus + named-RaaS-group bonus.

    Transparent on purpose - an analyst can see exactly why a score landed
    where it did instead of trusting an opaque model judgment.
    """
    if matched_entities is None:
        matched_entities = match_entities(_incident_text(incident))
    score = _CATEGORY_BASE_SCORE.get(incident.get("category"), 3)
    score += min(len(matched_entities), 3)
    if incident.get("confidence") == "verified":
        score += 1
    # A named, recognized RaaS group (ransomware_intel.py) is a stronger
    # signal than a generic "ransomware" keyword hit - the post isn't just
    # ransomware-adjacent chatter, it's attributed to a specific active
    # operation.
    if (incident.get("extra") or {}).get("ransomware_group"):
        score += 1
    return max(1, min(score, 10))


def _engagement_score(reply_count: int) -> int:
    """Maps a raw reply count to 1-10 on a log scale - reply counts in the
    wild are extremely skewed (0 to 6800+ seen live across the pool), so a
    linear scale would put almost everything at the bottom.
    """
    return max(1, min(10, round(1 + math.log10(reply_count + 1) * 3)))


def compute_popularity_score(incident: dict, risk_score: Optional[int] = None) -> int:
    """1-10: how much this thread should be surfaced/prioritized, combining
    two signals in equal proportion - real forum engagement (reply count,
    where the forum exposes it - see underground_forums.py's
    `_extract_reply_count`) and CTI risk (`compute_risk_score`).

    Reply count is only available for a minority of forums (verified live:
    8 of 21 reachable pool forums exposed it, all XenForo - MyBB never
    does), so when it's missing this falls back to risk score alone rather
    than averaging against a fabricated zero, which would unfairly punish
    threads on forums that simply don't publish the metric.
    """
    if risk_score is None:
        risk_score = compute_risk_score(incident)
    reply_count = (incident.get("extra") or {}).get("reply_count")
    if reply_count is None:
        return risk_score
    return round((risk_score + _engagement_score(reply_count)) / 2)


def brief_summary(incident: dict) -> str:
    actor = (incident.get("threat_actor") or {}).get("name", "unknown")
    label = _CATEGORY_LABELS.get(incident.get("category"), incident.get("category") or "olay")
    victim = (incident.get("victim") or {}).get("name") or incident.get("title", "")
    actor_part = f"{actor} imzalı " if actor and actor != "unknown" else ""
    return f"{actor_part}bir {label}: {victim}".strip()


def is_alert_worthy(incident: dict, min_risk: int = 6) -> bool:
    matched = match_entities(_incident_text(incident))
    if matched:
        return True
    return compute_risk_score(incident, matched_entities=matched) >= min_risk


def _severity_label(risk_score: int) -> str:
    if risk_score >= 9:
        return "CRITICAL"
    if risk_score >= 7:
        return "HIGH"
    if risk_score >= 4:
        return "MEDIUM"
    return "LOW"


def to_alert_card(incident: dict) -> dict:
    """Renders both card shapes from one Incident dict - see module docstring."""
    matched = match_entities(_incident_text(incident))
    risk = compute_risk_score(incident, matched_entities=matched)
    popularity = compute_popularity_score(incident, risk_score=risk)
    source = incident.get("source") or {}
    victim = incident.get("victim") or {}
    evidence = incident.get("evidence") or {}
    actor = (incident.get("threat_actor") or {}).get("name", "unknown")
    post_url = evidence.get("post_url") or source.get("url")
    # For telegram_bridge incidents, extra.referenced_source_url is the
    # ORIGIN forum the channel post pointed at (e.g. "https://ascarding.net/")
    # - that's what "Source" means in the reference screenshot, not our own
    # post_url (which would just be the t.me/breachdetect/NNN message link).
    display_source = (incident.get("extra") or {}).get("referenced_source_url") or post_url
    content = (incident.get("description") or incident.get("title") or "")[:280]

    return {
        "incident_id": incident.get("incident_id"),
        "simple": {
            "Source": display_source,
            "Content": content,
            "author": actor,
            "Detection Date": incident.get("detected_at"),
            "Type": incident.get("category"),
        },
        "rich": {
            "target_title": victim.get("name") or incident.get("title"),
            "victim_website": victim.get("website"),
            "threat_actor": actor,
            "country": victim.get("country_code"),
            "intel_source": source.get("name"),
            "detection_date": incident.get("detected_at"),
            "risk_score": risk,
            "severity": _severity_label(risk),
            "popularity_score": popularity,
            "reply_count": (incident.get("extra") or {}).get("reply_count"),
            "ransomware_group": (incident.get("extra") or {}).get("ransomware_group"),
            "brief_summary": brief_summary(incident),
            "source_link": post_url,
            "matched_entities": matched,
        },
    }


def render_telegram_message(card: dict) -> str:
    """Renders the `rich` half of an alert card as literal display text,
    laid out to match the OsintCTI-style card the user shared as a
    reference screenshot (severity banner, then a labeled field list) -
    this is what actually gets posted if/when a Telegram delivery bot is
    added (see README's "Telegram - henüz yapılmadı" section), and what a
    dashboard would render one card as today.
    """
    r = card["rich"]
    lines = [f"⚠️ {r['severity']} SEVERITY LEAK DETECTED ⚠️", ""]
    lines.append(f"• Target/Title: {r['target_title']}")
    if r.get("victim_website"):
        lines.append(f"🌍 Victim: {r['victim_website']}")
    if r.get("ransomware_group"):
        lines.append(f"🔒 RaaS Group: {r['ransomware_group'].upper()}")
    lines.append(f"👤 Threat Actor: {r['threat_actor']}")
    if r.get("country"):
        lines.append(f"📍 Country: {r['country']}")
    lines.append(f"🌐 Intel Source: {r['intel_source']}")
    lines.append(f"📅 Detection Date: {r['detection_date']}")
    lines.append(f"📊 Risk Score: {r['risk_score']}/10")
    if r.get("reply_count") is not None:
        lines.append(f"🔥 Popularity: {r['popularity_score']}/10 ({r['reply_count']} replies)")
    lines.append("")
    lines.append(f"📝 Brief Summary: {r['brief_summary']}")
    if r.get("matched_entities"):
        lines.append(f"🏷️ Matched: {', '.join(r['matched_entities'])}")
    lines.append("")
    lines.append(f"🔗 Source Intelligence Link: {r['source_link']}")
    return "\n".join(lines)
