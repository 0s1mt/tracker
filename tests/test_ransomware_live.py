"""Offline tests for the ransomware.live source - real network mocked out.
Record shapes below are modeled on live responses captured 2026-11-01 from
GET /v2/recentvictims (the endpoint this source actually polls) and
GET /v2/countryvictims/TR (same underlying data, different key names -
_to_incident() has to accept both, see its docstring)."""
from unittest.mock import MagicMock, patch

from forumtracker.sources.ransomware_live import RansomwareLiveSource, _sector_from_activity

RECENT_VICTIMS_ROW = {
    "activity": "Technology",
    "attackdate": "2026-11-01T01:50:30.711414+00:00",
    "claim_url": "http://qr6uopkqxmq254osyct3oibil32xp3qsimkkbqdscsrsb4zqbagnifad.onion/",
    "country": "TR",
    "data_size": None,
    "description": "Evinoks is a Turkish company that produces and sells furniture.",
    "discovered": "2026-11-01T01:50:47.238640+00:00",
    "domain": "evinoks.com",
    "group": "Barracuda",
    "press": None,
    "ransom": None,
    "screenshot": "https://images.ransomware.live/victims/x.png",
    "url": "https://www.ransomware.live/id/RVZJTk9LU0BCYXJyYWN1ZGE=",
    "victim": "EVINOKS",
}

COUNTRY_VICTIMS_ROW = {
    "activity": "Finance",
    "country": "TR",
    "data_size": None,
    "description": "A Turkish bank.",
    "discovered": "2026-11-01T01:50:47.238640+00:00",
    "group_name": "Barracuda",
    "post_title": "ZIRAAT BANKASI",
    "post_url": "http://example.onion/",
    "published": "2026-11-01T01:50:30.711414+00:00",
    "ransom": None,
    "website": "ziraatbank.com.tr",
}


def _mock_response(json_body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    return resp


@patch("forumtracker.sources.ransomware_live.requests.get")
def test_fetch_maps_recentvictims_fields_into_incident(mock_get):
    mock_get.return_value = _mock_response([RECENT_VICTIMS_ROW])
    source = RansomwareLiveSource()

    incidents = [inc.to_dict() for inc in source.fetch()]

    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["victim"]["name"] == "EVINOKS"
    assert inc["victim"]["country_code"] == "TR"
    assert inc["victim"]["website"] == "evinoks.com"
    assert inc["victim"]["sector"] == "technology"
    assert inc["threat_actor"]["name"] == "Barracuda"
    assert inc["category"] == "ransomware_leak"
    assert inc["source"]["name"] == "ransomware.live"
    assert inc["evidence"]["post_url"] == RECENT_VICTIMS_ROW["claim_url"]
    assert inc["extra"]["ransomware_group"] == "Barracuda"


@patch("forumtracker.sources.ransomware_live.requests.get")
def test_fetch_accepts_countryvictims_key_variants(mock_get):
    mock_get.return_value = _mock_response([COUNTRY_VICTIMS_ROW])
    source = RansomwareLiveSource()

    incidents = [inc.to_dict() for inc in source.fetch()]

    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["victim"]["name"] == "ZIRAAT BANKASI"
    assert inc["victim"]["website"] == "ziraatbank.com.tr"
    assert inc["victim"]["sector"] == "finance"
    assert inc["threat_actor"]["name"] == "Barracuda"


@patch("forumtracker.sources.ransomware_live.requests.get")
def test_fetch_skips_record_missing_victim_or_group(mock_get):
    bad = dict(RECENT_VICTIMS_ROW)
    bad["victim"] = None
    mock_get.return_value = _mock_response([bad, RECENT_VICTIMS_ROW])
    source = RansomwareLiveSource()

    incidents = list(source.fetch())

    assert len(incidents) == 1


@patch("forumtracker.sources.ransomware_live.requests.get")
def test_fetch_returns_empty_on_non_200(mock_get):
    mock_get.return_value = _mock_response([], status=429)
    source = RansomwareLiveSource()

    assert list(source.fetch()) == []


@patch("forumtracker.sources.ransomware_live.requests.get")
def test_fetch_returns_empty_on_non_list_json(mock_get):
    mock_get.return_value = _mock_response({"error": "rate limited"})
    source = RansomwareLiveSource()

    assert list(source.fetch()) == []


@patch("forumtracker.sources.ransomware_live.requests.get", side_effect=Exception("network down"))
def test_fetch_returns_empty_on_request_exception(mock_get):
    source = RansomwareLiveSource()

    assert list(source.fetch()) == []


@patch("forumtracker.sources.ransomware_live.requests.get")
def test_fetch_treats_na_description_as_none(mock_get):
    row = dict(RECENT_VICTIMS_ROW)
    row["description"] = "N/A"
    mock_get.return_value = _mock_response([row])
    source = RansomwareLiveSource()

    inc = next(iter(source.fetch())).to_dict()

    assert inc["description"] is None


def test_sector_from_activity_maps_known_values():
    assert _sector_from_activity("Technology") == "technology"
    assert _sector_from_activity("Retail & E-Commerce") == "retail"
    assert _sector_from_activity("Finance") == "finance"


def test_sector_from_activity_unmapped_and_none():
    assert _sector_from_activity("Other") is None
    assert _sector_from_activity(None) is None
