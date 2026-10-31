"""Offline tests for deterministic victim-industry (sector) classification."""
from forumtracker.sector_classification import sector_from_domain, sector_from_text


def test_sector_from_domain_government_label():
    assert sector_from_domain("spk.gov.tr") == "government"
    assert sector_from_domain("whitehouse.gov") == "government"


def test_sector_from_domain_military_label():
    assert sector_from_domain("example.mil") == "government"


def test_sector_from_domain_education_label():
    assert sector_from_domain("university.edu") == "education"
    assert sector_from_domain("example.ac.uk") == "education"


def test_sector_from_domain_generic_tld_returns_none():
    assert sector_from_domain("example.com") is None


def test_sector_from_domain_none_input():
    assert sector_from_domain(None) is None


def test_sector_from_text_matches_telecom_keyword():
    assert sector_from_text("Turkey GSM Database - 11,000,000 rows") == "telecom"


def test_sector_from_text_matches_finance_keyword():
    assert sector_from_text("Ziraat Bank customer database leaked") == "finance"


def test_sector_from_text_matches_turkish_keyword():
    assert sector_from_text("T.C. Sağlık Bakanlığı veritabanı sızdırıldı") == "government"


def test_sector_from_text_returns_none_when_no_keyword_present():
    assert sector_from_text("Top Trending SELLER Carder Forum") is None


def test_sector_from_text_none_input():
    assert sector_from_text(None) is None
