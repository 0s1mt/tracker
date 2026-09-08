"""Pulls a real victim domain out of forum title/content text.

Why this exists: `Victim.name` is set at RSS-normalize time to the raw
thread title (see `underground_forums.py`'s docstring: "genelde thread
başlığının kendisidir") - that's necessarily noisy, marketing-styled
("Top Trending SELLER Carder Forum..."), not a clean victim identifier a
CTI product can key off of. A domain mentioned in the same post
("breached example.com", "500k users from mycompany.com leaked") is a much
better anchor - `Victim.website` already exists in the schema for exactly
this and was unused until now.

Deliberately conservative: matches only a curated TLD allowlist (not any
`word.word` pattern, which would also catch "v2.0", "config.io" the
software, price notation, etc.) and excludes the forum's own domain and a
small stoplist of hosting/link-shortener/leak-mirror domains that show up
constantly in these posts but are never the actual victim (mega.nz,
anonfiles.com, t.me, ...).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# Common enough to show up as a real victim's domain across the forum pool's
# mixed English/Russian/Turkish/Spanish/Portuguese-speaking membership.
# Deliberately excludes single/double-letter or purely numeric-looking TLDs
# that would inflate false positives on version numbers and prices.
_TLDS = (
    "com", "net", "org", "gov", "edu", "io", "co", "info", "biz", "app",
    "ru", "su", "cn", "de", "fr", "tr", "uk", "nl", "se", "no", "es", "it",
    "pl", "br", "in", "au", "ca", "jp", "kr", "mx", "ar", "za", "ch", "at",
    "be", "dk", "fi", "ie", "pt", "gr", "cz", "ro", "hu", "il", "ae", "sa",
    "id", "th", "vn", "ph", "sg", "my", "nz", "gouv", "mil", "ac", "edu.tr",
    "com.tr", "gov.tr", "co.uk", "org.uk", "com.br", "gov.br", "com.au",
)
_TLD_PATTERN = "|".join(t.replace(".", r"\.") for t in _TLDS)

_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:" + _TLD_PATTERN + r")\b",
    re.IGNORECASE,
)

# Hosting/paste/leak-mirror/messaging domains that appear constantly in
# these posts as the DELIVERY mechanism, never as the victim itself.
_EXCLUDED_DOMAINS = {
    "mega.nz", "anonfiles.com", "t.me", "telegram.me", "telegram.org",
    "pastebin.com", "gofile.io", "mediafire.com", "drive.google.com",
    "discord.com", "discord.gg", "bit.ly", "tinyurl.com", "imgur.com",
    "youtube.com", "youtu.be", "twitter.com", "x.com",
    # file/image hosts seen live in real leak posts as the DOWNLOAD LINK,
    # never the breached organization (2026-09-08 sample: ibb.co,
    # pixeldrain.com, limewire.com all showed up this way)
    "ibb.co", "pixeldrain.com", "limewire.com", "krakenfiles.com",
    "send.now", "buzzheavier.com", "1fichier.com", "rapidgator.net",
    "dropmefiles.com", "file.io", "mega.io", "anonfile.com",
}


def extract_victim_domain(text: str, exclude_hostname: Optional[str] = None) -> Optional[str]:
    """Returns the first plausible victim domain found in `text`, or None.

    `exclude_hostname` should be the forum's own hostname (e.g.
    "carders.biz") so a self-referential mention ("posted on carders.biz")
    never gets mistaken for the victim.
    """
    if not text:
        return None
    exclude = {exclude_hostname.lower()} if exclude_hostname else set()
    for match in _DOMAIN_RE.finditer(text):
        domain = match.group(0).lower()
        if domain in _EXCLUDED_DOMAINS or domain in exclude:
            continue
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    return None
