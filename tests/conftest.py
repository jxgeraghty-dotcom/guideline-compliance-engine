"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest

from compliance.models import Portfolio, Position


def pos(security_id: str, issuer: str, market_value: float, **kwargs) -> Position:
    """Terse Position factory for tests."""
    return Position(security_id=security_id, issuer=issuer, market_value=market_value, **kwargs)


@pytest.fixture
def simple_portfolio() -> Portfolio:
    """A $100 portfolio (weights read as whole-percent) with a clear structure."""
    return Portfolio(
        name="Test Portfolio",
        base_currency="USD",
        positions=[
            pos("A1", "Alpha Corp", 6, sector="Financials", rating="A", duration=5.0),
            pos("A2", "Alpha Corp", 2, sector="Financials", rating="A", duration=4.0),
            pos("B1", "Beta Inc", 4, sector="Financials", rating="BBB", duration=6.0),
            pos("C1", "Gamma Ltd", 3, sector="Industrials", rating="BB+", duration=3.0),
            pos("D1", "US Treasury", 20, sector="Government", rating="AA+", duration=7.0),
            pos("E1", "Delta SA", 5, sector="Utilities", rating="BBB-", duration=8.0),
            pos("F1", "Epsilon", 10, sector="Technology", rating="AAA", duration=2.0),
            pos("G1", "Zeta", 50, sector="Government", rating="AA+", duration=6.0),
        ],
    )
