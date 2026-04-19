from __future__ import annotations

import math
import re
from decimal import Decimal, ROUND_HALF_UP
from itertools import product
from math import log1p
from typing import Any, Iterable, Sequence


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


def build_float_range(start: float, end: float, step: float, *, precision: int = 2) -> list[float]:
    if step <= 0:
        raise ValueError("step must be positive")
    if end < start:
        return []
    quant = Decimal("1").scaleb(-precision)
    current = Decimal(str(start))
    limit = Decimal(str(end))
    step_decimal = Decimal(str(step))
    values: list[float] = []
    while current <= limit + Decimal("1e-12"):
        values.append(float(current.quantize(quant, rounding=ROUND_HALF_UP)))
        current += step_decimal
        if len(values) > 5000:
            break
    return values


def format_query_number(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isfinite(numeric) and numeric.is_integer():
        return str(int(numeric))
    text = f"{numeric:.4f}".rstrip("0").rstrip(".")
    return text or "0"


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


SNAPSHOT_FIELD_PRESETS: list[dict[str, Any]] = [
    {
        "key": "m20_gols_total",
        "label": "Gols total no minuto de entrada",
        "expression": "m20.GolsTotal",
        "operator": "<=",
        "default_start": 0.0,
        "default_end": 3.0,
        "default_step": 1.0,
        "precision": 0,
        "default_tokens": ["m20.golstotal"],
    },
    {
        "key": "m20_pressao1_total",
        "label": "Pressão 1 total no minuto de entrada",
        "expression": "m20.Pressao1Casa + m20.Pressao1Visitante",
        "operator": "<=",
        "default_start": 10.0,
        "default_end": 25.0,
        "default_step": 1.0,
        "precision": 0,
        "default_tokens": ["pressao1casa", "pressao1visitante"],
    },
    {
        "key": "m20_pressao2_total",
        "label": "Pressão 2 total no minuto de entrada",
        "expression": "m20.Pressao2Casa + m20.Pressao2Visitante",
        "operator": "<=",
        "default_start": 10.0,
        "default_end": 30.0,
        "default_step": 1.0,
        "precision": 0,
        "default_tokens": ["pressao2casa", "pressao2visitante"],
    },
    {
        "key": "m20_chutes_no_gol_total",
        "label": "Chutes no gol totais no minuto de entrada",
        "expression": "m20.ChutesNoGolCasaC + m20.ChutesNoGolVisitanteC",
        "operator": "<=",
        "default_start": 0.0,
        "default_end": 5.0,
        "default_step": 1.0,
        "precision": 0,
        "default_tokens": ["chutesnogolcasac", "chutesnogolvisitantec"],
    },
    {
        "key": "m20_grafico_total",
        "label": "Gráfico total no minuto de entrada",
        "expression": "m20.GraficoCasa + m20.GraficoVisitante",
        "operator": "<=",
        "default_start": 0.0,
        "default_end": 30.0,
        "default_step": 1.0,
        "precision": 0,
        "default_tokens": ["graficocasa", "graficovisitante"],
    },
    {
        "key": "m20_escanteios_total",
        "label": "Escanteios totais no minuto de entrada",
        "expression": "m20.EscanteiosCasaC + m20.EscanteiosVisitanteC",
        "operator": "<=",
        "default_start": 0.0,
        "default_end": 12.0,
        "default_step": 1.0,
        "precision": 0,
        "default_tokens": ["escanteioscasac", "escanteiosvisitantec"],
    },
    {
        "key": "m20_back_under25ft",
        "label": "Back Under 2.5 FT no minuto de entrada",
        "expression": "m20.BackUnder25FT",
        "operator": ">=",
        "default_start": 1.20,
        "default_end": 2.50,
        "default_step": 0.05,
        "precision": 2,
        "default_tokens": ["backunder25ft"],
    },
    {
        "key": "m20_lay_over25ft",
        "label": "Lay Over 2.5 FT no minuto de entrada",
        "expression": "m20.LayOver25FT",
        "operator": ">=",
        "default_start": 1.20,
        "default_end": 2.50,
        "default_step": 0.05,
        "precision": 2,
        "default_tokens": ["layover25ft"],
    },
    {
        "key": "m35_gols_total",
        "label": "Gols total no minuto 35",
        "expression": "m35.GolsTotal",
        "operator": "<=",
        "default_start": 0.0,
        "default_end": 2.0,
        "default_step": 1.0,
        "precision": 0,
        "default_tokens": ["m35.golstotal"],
    },
    {
        "key": "m35_back_under25ft",
        "label": "Back Under 2.5 FT no minuto 35",
        "expression": "m35.BackUnder25FT",
        "operator": ">=",
        "default_start": 1.20,
        "default_end": 2.50,
        "default_step": 0.05,
        "precision": 2,
        "default_tokens": ["m35.backunder25ft"],
    },
]


def infer_snapshot_field_keys(strategy_query: str, field_presets: Sequence[dict[str, Any]] | None = None) -> list[str]:
    presets = list(field_presets or SNAPSHOT_FIELD_PRESETS)
    lowered = (strategy_query or "").lower()
    selected: list[str] = []
    for preset in presets:
        tokens = [str(token).lower() for token in (preset.get("default_tokens") or [])]
        if tokens and any(token in lowered for token in tokens):
            selected.append(str(preset["key"]))
    return selected


def build_snapshot_filter_group(
    expression: str,
    operator: str,
    start: float,
    end: float,
    step: float,
    *,
    precision: int = 2,
) -> list[str]:
    if operator not in {"<=", ">="}:
        raise ValueError(f"unsupported operator: {operator}")
    values = build_float_range(float(start), float(end), float(step), precision=precision)
    return [f"({expression} {operator} {format_query_number(value)})" for value in values]


def build_snapshot_filter_groups(field_configs: Sequence[dict[str, Any]]) -> list[list[str]]:
    groups: list[list[str]] = []
    for config in field_configs:
        if not config.get("enabled"):
            continue
        groups.append(
            build_snapshot_filter_group(
                str(config["expression"]),
                str(config.get("operator") or "<="),
                float(config["start"]),
                float(config["end"]),
                float(config["step"]),
                precision=int(config.get("precision") or 2),
            )
        )
    return groups


def expand_queries_with_filter_groups(base_queries: Sequence[str], filter_groups: Sequence[Sequence[str]]) -> list[str]:
    if not filter_groups:
        return [query.strip() for query in base_queries if str(query).strip()]
    variants: list[str] = []
    for base_query in base_queries or [""]:
        base = str(base_query).strip()
        for combination in product(*filter_groups):
            variants.append(append_query_filters(base, combination))
    return variants


def expand_query_variants_with_filter_groups(
    base_queries: Sequence[str],
    filter_groups: Sequence[Sequence[str]],
) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    if not filter_groups:
        for base_query in base_queries or [""]:
            base = str(base_query).strip()
            if base:
                variants.append(
                    {
                        "base_query": base,
                        "snapshot_filters": [],
                        "augmented_base_query": base,
                    }
                )
        return variants
    for base_query in base_queries or [""]:
        base = str(base_query).strip()
        if not base:
            continue
        for combination in product(*filter_groups):
            snapshot_filters = [str(term).strip() for term in combination if str(term).strip()]
            variants.append(
                {
                    "base_query": base,
                    "snapshot_filters": snapshot_filters,
                    "augmented_base_query": append_query_filters(base, snapshot_filters),
                }
            )
    return variants


def candidate_product_with_snapshot_filters(
    base_variants: Sequence[dict[str, Any]],
    final_filters: Sequence[str],
    entry_minutes: Sequence[int],
    final_minutes: Sequence[int],
) -> list[dict[str, Any]]:
    combos: list[dict[str, Any]] = []
    for base_variant in base_variants or [{"base_query": "", "snapshot_filters": [], "augmented_base_query": ""}]:
        base_query = str(base_variant.get("base_query") or "").strip()
        augmented_base_query = str(base_variant.get("augmented_base_query") or base_query).strip()
        snapshot_filters = [str(term).strip() for term in (base_variant.get("snapshot_filters") or []) if str(term).strip()]
        for final_filter, entry_minute, final_minute in product(
            final_filters or [""],
            entry_minutes or [0],
            final_minutes or [500],
        ):
            combos.append(
                {
                    "base_query": base_query,
                    "augmented_base_query": augmented_base_query,
                    "snapshot_filters": snapshot_filters,
                    "final_filter": str(final_filter).strip(),
                    "entry_minute": int(entry_minute),
                    "final_minute": int(final_minute),
                }
            )
    return combos


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
            "validation_profit",
            "validation_roi",
            "validation_bets",
            "train_profit",
            "train_roi",
            "train_bets",
        ]
    else:
        sort_keys = [
            "train_profit",
            "train_roi",
            "train_bets",
            "train_drawdown",
        ]

    return sorted(
        records,
        key=lambda record: tuple(_metric(record, key) for key in sort_keys),
        reverse=True,
    )
