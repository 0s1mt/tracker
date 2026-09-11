"""Posts rendered alert cards to a real Telegram channel/chat via the Bot
API - the one piece of the "Telegram entegrasyonu" that genuinely needs
something from the user first: a bot token from @BotFather and the chat_id
of the channel to post into (see README for the two-minute setup).

Deliberately a thin wrapper around `requests.post` (no python-telegram-bot
dependency) - sendMessage is one HTTP call, and the pipeline already treats
every source/sink failure as "log and continue", so pulling in a whole
client library for this would be dead weight.

Reads credentials from environment variables so no token ever lands in code
or in a committed config file:

    TELEGRAM_BOT_TOKEN   - from @BotFather after /newbot
    TELEGRAM_CHAT_ID     - the channel/group/user to post into (see README
                            for how to find this - it's not the @username)

If either is unset, `notify_alerts()` is a silent no-op - running the
pipeline without Telegram configured must not change its behavior.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from .alerting import render_telegram_message

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 10


def send_alert(card: dict, token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
    """Renders one alert card and posts it as a Telegram message.

    Returns True on success, False on any failure (bad token, chat not
    found, network error, rate limit) - never raises, so one bad send
    doesn't take down a whole pipeline cycle.
    """
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    text = render_telegram_message(card)
    try:
        resp = requests.post(
            API_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        log.exception("telegram sendMessage failed for incident %s", card.get("incident_id"))
        return False

    if resp.status_code != 200:
        log.warning(
            "telegram sendMessage non-200 for incident %s: %s %s",
            card.get("incident_id"), resp.status_code, resp.text[:300],
        )
        return False
    return True


def notify_health_alert(
    forum_name: str, mirror_candidates: list[dict], token: Optional[str] = None, chat_id: Optional[str] = None
) -> bool:
    """Plain-text ping for a pool forum that just crossed
    `health.DEAD_THRESHOLD` consecutive unreachable checks - a probable
    address rotation, not a content alert, so it's a hand-built message
    rather than going through `render_telegram_message` (built for
    Incident alert cards). Same env-var/no-op contract as
    `notify_alerts()`: silently does nothing if Telegram isn't configured.
    """
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    lines = [
        f"⚠️ Forum muhtemelen adres degistirdi: {forum_name}",
        "Art arda birden fazla kontrolde ulasilamadi (DNS/baglanti hatasi, sunucu hatasi degil).",
    ]
    if mirror_candidates:
        lines.append("\nOlasi yeni adresler (Telegram'da bahsedilen, henuz dogrulanmadi - manuel incele):")
        for c in mirror_candidates[:5]:
            lines.append(f"  - {c['hostname']} (benzerlik={c['similarity']}, mentions={c['mentions']})")
    else:
        lines.append("\nHenuz olasi bir mirror bulunamadi.")
    text = "\n".join(lines)

    try:
        resp = requests.post(
            API_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        log.exception("telegram health alert failed for %s", forum_name)
        return False

    if resp.status_code != 200:
        log.warning(
            "telegram health alert non-200 for %s: %s %s", forum_name, resp.status_code, resp.text[:300]
        )
        return False
    return True


def notify_identity_match(match, token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
    """Plain-text ping for a newly discovered cross-forum username match
    (see identity_correlation.py) - `match` is an identity_correlation.
    CrossMatch. Same env-var/no-op contract as the other notify_* helpers.
    """
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    names = "/".join(sorted(set(match.display_names))) or match.username_norm
    text = (
        f"🔗 Capraz platform eslesmesi: {names}\n"
        f"Forumlar: {', '.join(sorted(match.forums))}"
    )
    try:
        resp = requests.post(
            API_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        log.exception("telegram identity-match alert failed for %s", match.username_norm)
        return False

    if resp.status_code != 200:
        log.warning(
            "telegram identity-match alert non-200 for %s: %s %s",
            match.username_norm, resp.status_code, resp.text[:300],
        )
        return False
    return True


def notify_alerts(alerts: list[dict]) -> int:
    """Sends every alert card in `alerts` to Telegram. Silent no-op if
    TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID aren't set. Returns the number of
    cards actually sent.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return 0

    sent = 0
    for card in alerts:
        if send_alert(card, token=token, chat_id=chat_id):
            sent += 1
    if sent:
        log.info("telegram: sent %d/%d alert card(s)", sent, len(alerts))
    return sent
