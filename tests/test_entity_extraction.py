"""Offline tests for victim-domain extraction from forum text."""
from forumtracker.entity_extraction import extract_victim_domain


def test_extracts_first_plausible_domain():
    text = "We breached snu.gouv.fr and leaked the database of 250k users."
    assert extract_victim_domain(text) == "snu.gouv.fr"


def test_excludes_forum_own_hostname():
    text = "Top Trending SELLER Carder Forum posted on carders.biz today"
    assert extract_victim_domain(text, exclude_hostname="carders.biz") is None


def test_excludes_known_hosting_and_delivery_domains():
    text = "Full dump on mega.nz/file/abc123, join our channel t.me/leakchannel"
    assert extract_victim_domain(text) is None


def test_strips_www_prefix():
    assert extract_victim_domain("hacked www.example.com database") == "example.com"


def test_returns_none_when_no_domain_present():
    assert extract_victim_domain("no domain mentioned here at all") is None


def test_returns_none_on_empty_text():
    assert extract_victim_domain("") is None
