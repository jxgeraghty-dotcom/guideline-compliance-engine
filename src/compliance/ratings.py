"""Credit-rating scale utilities.

Ratings are mapped onto an ordinal "notch" scale where a *lower* number is a
*better* rating (AAA = 1). This lets rules compare quality with simple integer
comparisons and lets us compute a market-value-weighted average rating.

Both S&P/Fitch (``AAA``, ``AA+`` …) and Moody's (``Aaa``, ``Aa1`` …) notation
are accepted; Moody's grades are normalised onto the S&P scale on the way in.
"""

from __future__ import annotations

# S&P / Fitch long-term scale, best to worst.
SP_SCALE: list[str] = [
    "AAA",
    "AA+", "AA", "AA-",
    "A+", "A", "A-",
    "BBB+", "BBB", "BBB-",
    "BB+", "BB", "BB-",
    "B+", "B", "B-",
    "CCC+", "CCC", "CCC-",
    "CC",
    "C",
    "D",
]

# notch: AAA -> 1 ... D -> 22 (lower is better)
_RATING_TO_NOTCH: dict[str, int] = {r: i + 1 for i, r in enumerate(SP_SCALE)}

# Moody's -> S&P equivalent.
_MOODYS_TO_SP: dict[str, str] = {
    "AAA": "AAA",
    "AA1": "AA+", "AA2": "AA", "AA3": "AA-",
    "A1": "A+", "A2": "A", "A3": "A-",
    "BAA1": "BBB+", "BAA2": "BBB", "BAA3": "BBB-",
    "BA1": "BB+", "BA2": "BB", "BA3": "BB-",
    "B1": "B+", "B2": "B", "B3": "B-",
    "CAA1": "CCC+", "CAA2": "CCC", "CAA3": "CCC-",
    "CA": "CC",
    # "C" and "D" already coincide with the S&P scale.
}

# Values that explicitly mean "no rating available".
_UNRATED_TOKENS = {"", "NR", "N/A", "NA", "UNRATED", "WR", "NONE"}

# First speculative-grade notch (BB+); anything at or below is high yield.
_FIRST_HIGH_YIELD_NOTCH = _RATING_TO_NOTCH["BB+"]


def normalize(rating: str | None) -> str | None:
    """Return a canonical S&P-style rating, or ``None`` if unrated/unknown.

    Handles casing, whitespace, common "not rated" tokens and Moody's
    notation. Unknown strings return ``None`` rather than raising, so a single
    malformed cell never aborts a whole compliance run.
    """
    if rating is None:
        return None
    token = str(rating).strip().upper().replace(" ", "")
    if token in _UNRATED_TOKENS:
        return None
    if token in _RATING_TO_NOTCH:
        return token
    if token in _MOODYS_TO_SP:
        return _MOODYS_TO_SP[token]
    return None


def notch(rating: str | None) -> int | None:
    """Ordinal position of ``rating`` (AAA = 1), or ``None`` if unrated."""
    canonical = normalize(rating)
    if canonical is None:
        return None
    return _RATING_TO_NOTCH[canonical]


def is_investment_grade(rating: str | None) -> bool | None:
    """``True`` if BBB- or better, ``False`` if below, ``None`` if unrated."""
    n = notch(rating)
    if n is None:
        return None
    return n < _FIRST_HIGH_YIELD_NOTCH


def is_below_floor(rating: str | None, floor: str) -> bool | None:
    """Whether ``rating`` sits below the minimum acceptable ``floor``.

    Returns ``None`` when the holding is unrated, so callers can decide how to
    treat "cannot verify" separately from a confirmed breach.
    """
    n = notch(rating)
    if n is None:
        return None
    floor_notch = notch(floor)
    if floor_notch is None:
        raise ValueError(f"Invalid rating floor: {floor!r}")
    return n > floor_notch


def rating_from_notch(value: float) -> str:
    """Map a (possibly fractional) notch back to the nearest S&P grade."""
    idx = min(max(int(round(value)) - 1, 0), len(SP_SCALE) - 1)
    return SP_SCALE[idx]


def weighted_average_rating(
    pairs: list[tuple[str | None, float]],
) -> tuple[str, float] | None:
    """Market-value-weighted average rating over ``(rating, weight)`` pairs.

    Unrated holdings are excluded. Returns ``(rating_label, average_notch)`` or
    ``None`` if nothing in the list carries a usable rating.
    """
    weighted_notch = 0.0
    total_weight = 0.0
    for rating, weight in pairs:
        n = notch(rating)
        if n is None or weight <= 0:
            continue
        weighted_notch += n * weight
        total_weight += weight
    if total_weight == 0:
        return None
    avg = weighted_notch / total_weight
    return rating_from_notch(avg), avg
