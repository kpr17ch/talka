from app import main


def test_extract_wake_remainder_exact_match_with_remainder():
    detected, remainder = main._extract_wake_remainder("Hey Al, eine Frage", "hey al")
    assert detected is True
    assert remainder == "eine frage"


def test_extract_wake_remainder_fuzzy_second_token():
    detected, remainder = main._extract_wake_remainder("hey el eine frage", "hey al")
    assert detected is True
    assert remainder == "eine frage"


def test_extract_wake_remainder_with_small_leading_prefix():
    detected, remainder = main._extract_wake_remainder("yo hey al wie spaet ist es", "hey al")
    assert detected is True
    assert remainder == "wie spaet ist es"


def test_extract_wake_remainder_wake_only():
    detected, remainder = main._extract_wake_remainder("hey al", "hey al")
    assert detected is True
    assert remainder == ""


def test_extract_wake_remainder_no_wake_detected():
    detected, remainder = main._extract_wake_remainder("kannst du mir helfen", "hey al")
    assert detected is False
    assert remainder == ""
