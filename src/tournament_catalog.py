from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd


COUNTRY_ALIASES = {
    "turkiye": "turkey",
    "usa": "united states",
    "u.s.a.": "united states",
    "eng": "england",
}


def normalize_catalog_text(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\b\d{2}/\d{2}\b", "", text)
    text = re.sub(r"\b(?:19|20)\d{2}\b", "", text)
    text = re.sub(r"[\(\)\[\]{}]", " ", text)
    text = re.sub(r"\s*[-,/:]+\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,-/")
    return text


def normalize_country_name(value: object) -> str:
    text = normalize_catalog_text(value)
    return COUNTRY_ALIASES.get(text, text)


def build_tournament_catalog(bot_config: dict[str, object]) -> dict[str, list[dict[str, str]]]:
    catalog: dict[str, list[dict[str, str]]] = {}
    for row in bot_config.get("tournaments") or []:
        if not isinstance(row, dict):
            continue
        tournament_id = str(row.get("tournament_id") or "").strip()
        tournament_name = str(row.get("tournament_name") or "").strip()
        tournament_country = normalize_country_name(row.get("tournament_country"))
        tournament_country_code = normalize_catalog_text(row.get("tournament_country_code"))
        normalized_name = normalize_catalog_text(tournament_name)
        if not normalized_name or not tournament_id:
            continue
        catalog.setdefault(normalized_name, []).append(
            {
                "tournament_id": tournament_id,
                "tournament_name": tournament_name,
                "tournament_country": tournament_country,
                "tournament_country_code": tournament_country_code,
            }
        )
    return catalog


def resolve_tournament_from_row(row: pd.Series, catalog: dict[str, list[dict[str, str]]]) -> dict[str, str]:
    existing_id = str(row.get("tournament_id") or "").strip()
    existing_name = str(row.get("tournament_name") or "").strip()
    season_name = str(row.get("season_name") or "").strip()
    if existing_id:
        return {
            "tournament_id": existing_id,
            "tournament_name": existing_name,
            "season_name": season_name,
        }

    normalized_season = normalize_catalog_text(season_name)
    candidates = catalog.get(normalized_season, [])
    if not candidates:
        return {
            "tournament_id": existing_id,
            "tournament_name": existing_name,
            "season_name": season_name,
        }

    if len(candidates) == 1:
        match = candidates[0]
        return {
            "tournament_id": match["tournament_id"],
            "tournament_name": existing_name or match["tournament_name"],
            "season_name": season_name,
        }

    country_candidates = {
        normalize_country_name(row.get("country")),
        normalize_country_name(row.get("home_country")),
        normalize_country_name(row.get("away_country")),
    }
    country_candidates.discard("")
    narrowed = [item for item in candidates if item.get("tournament_country") in country_candidates]
    if len(narrowed) == 1:
        match = narrowed[0]
        return {
            "tournament_id": match["tournament_id"],
            "tournament_name": existing_name or match["tournament_name"],
            "season_name": season_name,
        }

    return {
        "tournament_id": existing_id,
        "tournament_name": existing_name,
        "season_name": season_name,
    }


def enrich_results_with_tournament_catalog(
    results_df: pd.DataFrame,
    bot_config: dict[str, Any] | None,
) -> pd.DataFrame:
    if results_df.empty or not bot_config:
        return results_df

    catalog = build_tournament_catalog(bot_config)
    if not catalog:
        return results_df

    enriched = results_df.copy()
    resolved = enriched.apply(
        lambda row: resolve_tournament_from_row(row, catalog),
        axis=1,
        result_type="expand",
    )
    enriched["tournament_id"] = resolved["tournament_id"]
    enriched["tournament_name"] = resolved["tournament_name"]
    enriched["season_name"] = resolved["season_name"]
    enriched["tournament_label"] = enriched.apply(
        lambda row: (
            f"{row['tournament_name']} ({row['tournament_id']})"
            if str(row.get("tournament_name") or "").strip() and str(row.get("tournament_id") or "").strip()
            else str(row.get("tournament_name") or row.get("tournament_id") or row.get("season_name") or "Sem torneio")
        ),
        axis=1,
    )
    return enriched
