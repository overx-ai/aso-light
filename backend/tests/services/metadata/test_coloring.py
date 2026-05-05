"""Tests for `app.services.metadata.coloring.classify_keyword`."""
from __future__ import annotations

from app.services.metadata.coloring import classify_keyword


def test_keyword_in_title_only_returns_title() -> None:
    assert classify_keyword("yoga", "Daily Yoga", "Fitness app", "meditation,sleep") == "title"


def test_keyword_in_subtitle_only_returns_subtitle() -> None:
    assert classify_keyword("fitness", "Daily Yoga", "Fitness app", "meditation,sleep") == "subtitle"


def test_keyword_as_exact_comma_token_returns_keywords() -> None:
    assert classify_keyword("meditation", "Daily Yoga", "Workouts", "meditation,sleep,calm") == "keywords"


def test_keyword_substring_inside_token_does_not_match_keywords_field() -> None:
    # "run" is a substring of "running" but not an exact token -> none
    assert classify_keyword("run", "Daily Yoga", "Workouts", "running,fitness") == "none"


def test_keyword_absent_everywhere_returns_none() -> None:
    assert classify_keyword("zebra", "Daily Yoga", "Fitness app", "meditation,sleep") == "none"


def test_case_insensitive_title_match() -> None:
    assert classify_keyword("Yoga", "Daily yoga", "Fitness app", "meditation,sleep") == "title"


def test_case_insensitive_keywords_match() -> None:
    assert classify_keyword("Meditation", "Daily Yoga", "Fitness app", "MEDITATION,sleep") == "keywords"


def test_whitespace_tolerance_in_keywords_field() -> None:
    assert classify_keyword("run", "Daily Yoga", "Workouts", "run, fitness, fast") == "keywords"


def test_precedence_title_over_keywords() -> None:
    # Present in BOTH title and keywords field -> title wins
    assert classify_keyword("yoga", "Yoga Daily", "Workouts", "yoga,fitness") == "title"


def test_precedence_subtitle_over_keywords() -> None:
    # Present in BOTH subtitle and keywords field -> subtitle wins
    assert classify_keyword("fitness", "Daily Yoga", "Fitness pro", "fitness,run") == "subtitle"


def test_precedence_title_over_subtitle() -> None:
    # Present in title and subtitle -> title wins
    assert classify_keyword("yoga", "Yoga", "Yoga app", "meditation") == "title"


def test_all_none_inputs_returns_none() -> None:
    assert classify_keyword("yoga", None, None, None) == "none"


def test_empty_string_inputs_returns_none() -> None:
    assert classify_keyword("yoga", "", "", "") == "none"


def test_empty_keyword_returns_none() -> None:
    assert classify_keyword("", "Daily Yoga", "Fitness", "yoga,run") == "none"


def test_whitespace_only_keyword_returns_none() -> None:
    assert classify_keyword("   ", "Daily Yoga", "Fitness", "yoga,run") == "none"


def test_keyword_with_surrounding_whitespace_normalized() -> None:
    assert classify_keyword("  yoga  ", "Daily Yoga", "Fitness", "meditation") == "title"


def test_multiword_keyword_substring_match_in_title() -> None:
    assert classify_keyword("daily yoga", "My Daily Yoga App", "Fitness", "meditation") == "title"


def test_multiword_keyword_does_not_match_keywords_field_via_substring() -> None:
    # multi-word keyword present as substring within keywords blob but not as a full token
    assert classify_keyword("daily yoga", None, None, "daily,yoga,meditation") == "none"
