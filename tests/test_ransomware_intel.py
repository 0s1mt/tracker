"""Offline tests for ransomware group/pattern classification."""
from forumtracker.ransomware_intel import classify, has_ransom_note_pattern, match_ransomware_group


def test_match_distinctive_group_name():
    assert match_ransomware_group("LockBit claims a new victim, example.com") == "lockbit"


def test_match_case_and_alias_insensitive():
    assert match_ransomware_group("posted by an ALPHV affiliate") == "blackcat"


def test_ambiguous_group_name_not_matched_standalone():
    # "play" alone must never match - it's an ordinary English word
    assert match_ransomware_group("we had fun playing games all weekend") is None


def test_ambiguous_group_name_matched_with_qualifier():
    assert match_ransomware_group("Play ransomware gang leaks 500GB from VictimCorp") == "play"


def test_no_match_on_unrelated_text():
    assert match_ransomware_group("just a friendly hello thread, nothing special") is None


def test_has_ransom_note_pattern_true_on_typical_phrasing():
    text = "We have encrypted your network. Pay us within 72 hours or data will be published."
    assert has_ransom_note_pattern(text) is True


def test_has_ransom_note_pattern_false_on_unrelated_text():
    assert has_ransom_note_pattern("check out this cool music remix") is False


def test_classify_returns_both_signals():
    result = classify("Akira group breached the network, negotiation chat link inside")
    assert result["ransomware_group"] == "akira"
    assert result["ransom_note_pattern"] is True
