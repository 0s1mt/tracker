"""Offline tests for victim-domain extraction from forum text."""
from forumtracker.entity_extraction import country_from_domain, country_from_text, extract_victim_domain


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


def test_country_from_domain_simple_cctld():
    assert country_from_domain("lesecologistes.fr") == "FR"


def test_country_from_domain_compound_cctld():
    assert country_from_domain("snu.gouv.fr") == "FR"
    assert country_from_domain("nhs.co.uk") == "GB"


def test_country_from_domain_generic_tld_returns_none():
    assert country_from_domain("example.com") is None
    assert country_from_domain("example.io") is None


def test_country_from_domain_su_maps_to_russia():
    assert country_from_domain("example.su") == "RU"


def test_country_from_domain_none_input():
    assert country_from_domain(None) is None


def test_country_from_text_matches_country_name_with_no_domain():
    assert country_from_text("Turkey GSM Database - 11,000,000 rows") == "TR"


def test_country_from_text_matches_demonym():
    assert country_from_text("Russian banking portal breached") == "RU"


def test_country_from_text_prefers_longest_phrase():
    assert country_from_text("United Arab Emirates telecom leak") == "AE"


def test_country_from_text_returns_none_when_no_known_country_mentioned():
    assert country_from_text("Top Trending SELLER Carder Forum") is None


def test_country_from_text_none_input():
    assert country_from_text(None) is None
