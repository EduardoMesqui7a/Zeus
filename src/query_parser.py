from __future__ import annotations

import re
from dataclasses import dataclass


MINUTE_PATTERN = re.compile(r"(?i)\bm(\d{1,3}|500)\.")


@dataclass(frozen=True)
class QueryMinuteInfo:
    minute_refs: tuple[int, ...]
    entry_minute: int


def extract_minute_refs(query: str) -> list[int]:
    refs: list[int] = []
    for match in MINUTE_PATTERN.finditer(query or ""):
        refs.append(int(match.group(1)))
    return sorted(set(refs))


def infer_entry_minute(query: str) -> int:
    refs = extract_minute_refs(query)
    if not refs:
        return 1

    non_final = [minute for minute in refs if minute != 500]
    if non_final:
        return max(non_final)
    return 500


def infer_final_minute(query: str) -> int:
    refs = extract_minute_refs(query)
    if not refs:
        return 500

    non_final = [minute for minute in refs if minute != 500]
    if non_final:
        return max(non_final)
    return 500


def absolute_to_period_minute(minute: int) -> tuple[int, int]:
    if minute <= 0:
        return 1, 1
    if minute == 500:
        return 2, 90
    if minute <= 45:
        return 1, minute
    return 2, minute


def period_minute_to_absolute(period: int, minute: int) -> int:
    return minute
