"""Tests for the credit-rating scale utilities."""

from __future__ import annotations

import pytest

from compliance import ratings


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AAA", "AAA"),
        (" bbb- ", "BBB-"),
        ("Baa2", "BBB"),      # Moody's -> S&P
        ("Aa1", "AA+"),
        ("Caa1", "CCC+"),
        ("NR", None),
        ("", None),
        (None, None),
        ("not-a-rating", None),
    ],
)
def test_normalize(raw, expected):
    assert ratings.normalize(raw) == expected


def test_notch_ordering():
    assert ratings.notch("AAA") == 1
    assert ratings.notch("AAA") < ratings.notch("BBB-") < ratings.notch("D")
    assert ratings.notch("NR") is None


def test_investment_grade_boundary():
    assert ratings.is_investment_grade("BBB-") is True
    assert ratings.is_investment_grade("BB+") is False
    assert ratings.is_investment_grade("NR") is None


def test_is_below_floor():
    assert ratings.is_below_floor("BB+", "BBB-") is True
    assert ratings.is_below_floor("BBB-", "BBB-") is False
    assert ratings.is_below_floor("A", "BBB-") is False
    assert ratings.is_below_floor("NR", "BBB-") is None


def test_is_below_floor_rejects_bad_floor():
    with pytest.raises(ValueError):
        ratings.is_below_floor("A", "ZZZ")


def test_weighted_average_rating():
    label, avg = ratings.weighted_average_rating([("AAA", 1.0), ("A", 1.0)])
    # AAA notch 1, A notch 6 -> average 3.5 -> rounds to AA- (notch 4)
    assert avg == pytest.approx(3.5)
    assert label == "AA-"


def test_weighted_average_rating_all_unrated():
    assert ratings.weighted_average_rating([("NR", 1.0), (None, 2.0)]) is None
