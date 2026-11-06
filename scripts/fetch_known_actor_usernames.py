"""One-time/periodic refresh: pulls the "active forum" username lists from
github.com/spmedia/Threat-Actor-Usernames-Scrape (MIT licensed - see that
repo's LICENSE; attribution kept here and in README.md per its terms) and
writes a local `{username: [forum_name, ...]}` JSON cache for
identity_correlation.seed_external_usernames() to consume.

This is a manual/periodic step, NOT part of the automatic pipeline cycle -
the source repo is a static dump that gets re-scraped by its own maintainer
every so often, not a live feed, so there is nothing to poll every 30
minutes. Re-run this script whenever you want a fresher snapshot.

Only the "active forum" files are pulled (the repo prefixes ones for
forums confirmed dead/seized with "z_dead_..." - those usernames can't
cross-match against anything we could still observe live, so pulling them
would only bloat the cache for no correlation benefit). The repo's own
`dnm_users_drughub.txt` (no "forum_users_" prefix, same active-forum
meaning) is included too.

Usage:
    python scripts/fetch_known_actor_usernames.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO = "spmedia/Threat-Actor-Usernames-Scrape"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main/"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "known_actor_usernames.json"

HEADERS = {"User-Agent": "Mozilla/5.0"}

# Hardcoded rather than listed via the GitHub Contents API: that API is
# aggressively rate-limited unauthenticated (60 req/hour, shared across
# anything else hitting api.github.com from this machine) and the repo's
# "active forum" file list changes rarely - confirmed live 2026-11-06.
# Re-check github.com/spmedia/Threat-Actor-Usernames-Scrape's file list
# occasionally and update this by hand if a forum is added/goes dead
# (dead ones get a "z_dead_" prefix there and should NOT be added here -
# see module docstring).
ACTIVE_USERNAME_FILES = [
    "dnm_users_drughub.txt",
    "forum_users_altenen.txt",
    "forum_users_ascarding.txt",
    "forum_users_blackhatworld.txt",
    "forum_users_breached2026.txt",
    "forum_users_cracked.txt",
    "forum_users_craxpro.txt",
    "forum_users_darkforums.txt",
    "forum_users_darknetarmy.txt",
    "forum_users_dread.txt",
    "forum_users_hackforums.txt",
    "forum_users_oguser.txt",
    "forum_users_patched.txt",
    "forum_users_pwnforums.txt",
    "forum_users_rehubcom.txt",
    "forum_users_tierone.txt",
    "forum_users_xreactor.txt",
    "forum_users_xss.txt",
]


def _forum_label(filename: str) -> str:
    name = filename
    for prefix in ("forum_users_", "dnm_users_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name[:-4] if name.endswith(".txt") else name


def list_active_username_files() -> list[str]:
    return ACTIVE_USERNAME_FILES


def fetch_usernames(filename: str) -> list[str]:
    resp = requests.get(RAW_BASE + filename, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return [line.strip() for line in resp.text.splitlines() if line.strip()]


def build_mapping() -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for filename in list_active_username_files():
        forum = _forum_label(filename)
        try:
            usernames = fetch_usernames(filename)
        except Exception as exc:
            print(f"skipping {filename}: {exc}", file=sys.stderr)
            continue
        for username in usernames:
            mapping.setdefault(username, [])
            if forum not in mapping[username]:
                mapping[username].append(forum)
        print(f"{filename}: {len(usernames)} usernames -> forum {forum!r}", file=sys.stderr)
    return mapping


def main() -> None:
    mapping = build_mapping()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": f"https://github.com/{REPO}",
        "license": "MIT (see source repo's LICENSE)",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "username_count": len(mapping),
        "usernames": mapping,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(mapping)} usernames to {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
