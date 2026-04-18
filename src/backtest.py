from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.query_parser import absolute_to_period_minute, infer_entry_minute
from src.zeus_client import ZeusClient, ZeusClientError


def _final_outcome_total_goals(snapshot: dict[str, Any]) -> int:
    return int(snapshot.get("GolsCasa") or 0) + int(snapshot.get("GolsVisitante") or 0)


def _final_outcome_btts(snapshot: dict[str, Any]) -> bool:
    return int(snapshot.get("GolsCasa") or 0) > 0 and int(snapshot.get("GolsVisitante") or 0) > 0


def _final_outcome_home_win(snapshot: dict[str, Any]) -> bool:
    return int(snapshot.get("GolsCasa") or 0) > int(snapshot.get("GolsVisitante") or 0)


def _final_outcome_away_win(snapshot: dict[str, Any]) -> bool:
    return int(snapshot.get("GolsVisitante") or 0) > int(snapshot.get("GolsCasa") or 0)


def _final_outcome_draw(snapshot: dict[str, Any]) -> bool:
    return int(snapshot.get("GolsCasa") or 0) == int(snapshot.get("GolsVisitante") or 0)


def _final_outcome_over_05(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) >= 1


def _final_outcome_under_25(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) <= 2


MARKET_OPTIONS: dict[str, dict[str, Any]] = {
    "Back Under 2.5 FT": {
        "odds_field": "BackUnder25FT",
        "side": "back",
        "settle": _final_outcome_under_25,
        "description": "Back no Under 2.5 FT",
    },
    "Lay Under 2.5 FT": {
        "odds_field": "LayUnder25FT",
        "side": "lay",
        "settle": _final_outcome_under_25,
        "description": "Lay no Under 2.5 FT",
    },
    "Back Over 2.5 FT": {
        "odds_field": "BackOver25FT",
        "side": "back",
        "settle": lambda snap: not _final_outcome_under_25(snap),
        "description": "Back no Over 2.5 FT",
    },
    "Back BTTS Sim": {
        "odds_field": "BackBttsSim",
        "side": "back",
        "settle": _final_outcome_btts,
        "description": "Back BTTS Sim",
    },
    "Back BTTS Nao": {
        "odds_field": "BackBttsNao",
        "side": "back",
        "settle": lambda snap: not _final_outcome_btts(snap),
        "description": "Back BTTS Nao",
    },
    "Back Casa FT": {
        "odds_field": "BackMoCasaFT",
        "side": "back",
        "settle": _final_outcome_home_win,
        "description": "Back mandante FT",
    },
    "Back Visitante FT": {
        "odds_field": "BackMoVisitanteFT",
        "side": "back",
        "settle": _final_outcome_away_win,
        "description": "Back visitante FT",
    },
    "Back Empate FT": {
        "odds_field": "BackMoEmpateFT",
        "side": "back",
        "settle": _final_outcome_draw,
        "description": "Back empate FT",
    },
    "Lay Over 0.5 FT": {
        "odds_field": "LayOver05FT",
        "side": "lay",
        "settle": _final_outcome_over_05,
        "description": "Lay over 0.5 FT",
    },
}


@dataclass(frozen=True)
class BacktestConfig:
    market_label: str
    stake: float = 100.0
    commission: float = 0.08
    entry_minute: int | None = None
    final_minute: int = 500


def _apply_back_profit(stake: float, odd: float, won: bool, commission: float) -> tuple[float, float]:
    risk = stake
    profit = stake * (odd - 1.0) if won else -stake
    if profit > 0:
        profit *= 1.0 - commission
    return profit, risk


def _apply_lay_profit(stake: float, odd: float, won: bool, commission: float) -> tuple[float, float]:
    liability = stake * (odd - 1.0)
    profit = -liability if won else stake
    if profit > 0:
        profit *= 1.0 - commission
    risk = liability
    return profit, risk


def _normalize_datetime(value: Any) -> pd.Timestamp:
    dt = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(dt):
        return pd.Timestamp(datetime.utcnow(), tz="UTC")
    return dt


def _build_row(
    client: ZeusClient,
    row: dict[str, Any],
    config: BacktestConfig,
    market: dict[str, Any],
) -> dict[str, Any]:
    game_id = str(row.get("sport_event_id") or "").strip()
    if not game_id:
        raise ZeusClientError("Registro sem sport_event_id.")

    entry_minute = config.entry_minute or infer_entry_minute(str(row.get("query") or ""))
    entry_period, entry_period_minute = absolute_to_period_minute(entry_minute)
    entry_snapshot = client.fetch_snapshot(game_id, minute=entry_period_minute, period=entry_period)
    final_snapshot = client.fetch_final_snapshot(game_id, final_minute=config.final_minute)

    odd_field = market["odds_field"]
    odd_value = entry_snapshot.get(odd_field)
    if odd_value is None:
        odd_value = row.get(odd_field)
    if odd_value is None:
        return {
            "sport_event_id": game_id,
            "display_label": f"{row.get('NomeCasa')} x {row.get('NomeVisitante')}",
            "status": "sem odd",
        }

    odd_value = float(odd_value)
    won = bool(market["settle"](final_snapshot))
    if market["side"] == "back":
        profit, risk = _apply_back_profit(config.stake, odd_value, won, config.commission)
    else:
        profit, risk = _apply_lay_profit(config.stake, odd_value, won, config.commission)

    final_goals = _final_outcome_total_goals(final_snapshot)
    match_label = f"{pd.to_datetime(row.get('DataJogo'), errors='coerce').strftime('%Y-%m-%d') if pd.notna(pd.to_datetime(row.get('DataJogo'), errors='coerce')) else 'sem-data'} | {row.get('NomeCasa')} x {row.get('NomeVisitante')} | {game_id}"
    result_text = "WIN" if profit >= 0 else "LOSS"
    return {
        "sport_event_id": game_id,
        "display_label": match_label,
        "match_datetime": _normalize_datetime(row.get("DataJogo")),
        "league": row.get("NivelDados") or row.get("campeonato") or "",
        "home_team": row.get("NomeCasa") or row.get("mandante") or "",
        "away_team": row.get("NomeVisitante") or row.get("visitante") or "",
        "entry_minute": entry_minute,
        "entry_period": entry_period,
        "entry_odd": odd_value,
        "odds_field": odd_field,
        "final_goals": final_goals,
        "final_home_goals": int(final_snapshot.get("GolsCasa") or 0),
        "final_away_goals": int(final_snapshot.get("GolsVisitante") or 0),
        "won": won,
        "profit": float(profit),
        "stake_risked": float(risk),
        "result_text": result_text,
        "entry_snapshot": entry_snapshot,
        "final_snapshot": final_snapshot,
        "status": "ok",
    }


def run_backtest(client: ZeusClient, rows: list[dict[str, Any]], config: BacktestConfig) -> dict[str, Any]:
    if config.market_label not in MARKET_OPTIONS:
        raise ZeusClientError(f"Mercado desconhecido: {config.market_label}")

    market = MARKET_OPTIONS[config.market_label]
    enriched: list[dict[str, Any]] = []

    def _safe_build(row: dict[str, Any]) -> dict[str, Any]:
        try:
            return _build_row(client, row, config, market)
        except Exception as exc:
            return {
                "sport_event_id": row.get("sport_event_id"),
                "display_label": f"{row.get('NomeCasa')} x {row.get('NomeVisitante')}",
                "match_datetime": _normalize_datetime(row.get("DataJogo")),
                "league": row.get("NivelDados") or row.get("campeonato") or "",
                "home_team": row.get("NomeCasa") or row.get("mandante") or "",
                "away_team": row.get("NomeVisitante") or row.get("visitante") or "",
                "status": f"erro: {exc}",
            }

    max_workers = min(8, max(2, len(rows))) if rows else 2
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_safe_build, row): row for row in rows}
        for future in as_completed(futures):
            enriched.append(future.result())

    result_df = pd.DataFrame(enriched)
    if "status" in result_df.columns:
        result_df = result_df[result_df["status"].eq("ok")].copy()
    if result_df.empty:
        metrics = {
            "matches": len(rows),
            "bets": 0,
            "wins": 0,
            "win_rate": 0.0,
            "roi": 0.0,
            "total_profit": 0.0,
            "total_risked": 0.0,
            "max_drawdown": 0.0,
            "avg_entry_odd": 0.0,
        }
        return {"metrics": metrics, "result_df": result_df, "config": config}

    result_df = result_df.sort_values("match_datetime").reset_index(drop=True)
    result_df["match_datetime"] = pd.to_datetime(result_df["match_datetime"], errors="coerce", utc=True)
    result_df["cumulative_profit"] = result_df["profit"].cumsum()
    result_df["equity"] = config.stake + result_df["cumulative_profit"]
    peak = result_df["cumulative_profit"].cummax()
    result_df["drawdown"] = result_df["cumulative_profit"] - peak
    total_profit = float(result_df["profit"].sum())
    total_risked = float(result_df["stake_risked"].sum())
    bets = int(len(result_df))
    wins = int(result_df["won"].sum())
    metrics = {
        "matches": len(rows),
        "bets": bets,
        "wins": wins,
        "win_rate": (wins / bets * 100.0) if bets else 0.0,
        "roi": (total_profit / total_risked * 100.0) if total_risked else 0.0,
        "total_profit": total_profit,
        "total_risked": total_risked,
        "max_drawdown": float(result_df["drawdown"].min()) if not result_df.empty else 0.0,
        "avg_entry_odd": float(result_df["entry_odd"].mean()) if not result_df.empty else 0.0,
    }
    return {"metrics": metrics, "result_df": result_df, "config": config}
