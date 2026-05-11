"""Percent conversion helpers for canonical percent-point config values."""

from __future__ import annotations


def pct_to_ratio(percent_points: float | int | None, default: float = 0.0) -> float:
    """Convert percent points to ratio.

    Example: ``25`` -> ``0.25``.
    """
    if percent_points is None:
        return float(default)
    return float(percent_points) / 100.0


def pct_to_multiplier(percent_points: float | int | None, default: float = 1.0) -> float:
    """Convert percent points to multiplier.

    Example: ``15`` -> ``1.15`` and ``-10`` -> ``0.90``.
    """
    if percent_points is None:
        return float(default)
    return 1.0 + (float(percent_points) / 100.0)


def ratio_to_pct(ratio: float | int | None, default: float = 0.0) -> float:
    """Convert ratio to percent points.

    Example: ``0.25`` -> ``25``.
    """
    if ratio is None:
        return float(default)
    return float(ratio) * 100.0


def multiplier_to_pct(multiplier: float | int | None, default: float = 0.0) -> float:
    """Convert multiplier to percent points.

    Example: ``1.15`` -> ``15`` and ``0.9`` -> ``-10``.
    """
    if multiplier is None:
        return float(default)
    return (float(multiplier) - 1.0) * 100.0
