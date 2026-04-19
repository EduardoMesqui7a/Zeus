from __future__ import annotations

import math
import re
from itertools import product
from math import log1p
from typing import Any, Iterable


QUERY_VARIANT_SPLIT = r"(?:\n\s*---+\s*\n|\n{2,})"


def split_query_variants(text: str | None) -> list[str]:
    blocks = [block.strip() for block in re.split(QUERY_VARIANT_SPLIT, text or "") if block.strip()]
    return blocks or [""]


def build_int_range(start: int, end: int, step: int) -> list[int]:
    if step <= 0:
        raise ValueError("step must be positive")
    if end < start:
        return []
    return list(range(start, end + 1, step))


def normalize_date_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def append_query_filters(query: str, filters: Iterable[str]) -> str:
    terms = [term.strip() for term in filters if str(term).strip()]
    if query and query.strip():
        terms.insert(0, query.strip())
    return " and ".join(f"({term})" for term in terms if term)


def add_date_filter(query: str, start_date: Any = None, end_date: Any = None) -> str:
    filters: list[str] = []
    if start_date:
        filters.append(f'm500.DataJogo >= "{normalize_date_value(start_date)}"')
    if end_date:
        filters.append(f'm500.DataJogo <= "{normalize_date_value(end_date)}"')
    return append_query_filters(query, filters)


def candidate_product(
    base_queries: list[str],
    final_filters: list[str],
    entry_minutes: list[int],
    final_minutes: list[int],
) -> list[dict[str, Any]]:
    combos: list[dict[str, Any]] = []
    for base_query, final_filter, entry_minute, final_minute in product(
        base_queries or [""],
        final_filters or [""],
        entry_minutes or [0],
        final_minutes or [500],
    ):
        combos.append(
            {
                "base_query": base_query.strip(),
                "final_filter": final_filter.strip(),
                "entry_minute": int(entry_minute),
                "final_minute": int(final_minute),
            }
        )
    return combos


def optimization_score(metrics: dict[str, Any]) -> float:
    bets = float(metrics.get("bets") or 0.0)
    roi = float(metrics.get("roi") or 0.0)
    win_rate = float(metrics.get("win_rate") or 0.0)
    drawdown = abs(float(metrics.get("worst_curve") or metrics.get("max_drawdown") or 0.0))
    return (roi * log1p(bets + 1.0)) + (win_rate * 0.05) - (drawdown * 0.02)


def sort_optimization_records(records: list[dict[str, Any]], validation_available: bool) -> list[dict[str, Any]]:
    def _metric(record: dict[str, Any], key: str) -> float:
        value = record.get(key)
        if value is None:
            return float("-inf")
        try:
            metric = float(value)
            return float("-inf") if math.isnan(metric) else metric
        except (TypeError, ValueError):
            return float("-inf")

    if validation_available:
        sort_keys = [
            "validation_roi",
            "validation_win_rate",
            "validation_bets",
            "train_roi",
            "train_win_rate",
            "train_bets",
        ]
    else:
        sort_keys = [
            "train_roi",
            "train_win_rate",
            "train_bets",
            "train_drawdown",
        ]

    return sorted(
        records,
        key=lambda record: tuple(_metric(record, key) for key in sort_keys),
        reverse=True,
    )
