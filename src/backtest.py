from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.query_parser import absolute_to_period_minute, infer_entry_minute
from src.zeus_client import AsyncZeusClient, ZeusClient, ZeusClientError


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


def _final_outcome_under_05_ht(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) <= 0


def _final_outcome_over_05_ht(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) >= 1


def _final_outcome_under_25(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) <= 2


def _final_outcome_under_15(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) <= 1


def _final_outcome_over_15(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) >= 2


def _final_outcome_over_25(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) >= 3


def _final_outcome_under_35(snapshot: dict[str, Any]) -> bool:
    return _final_outcome_total_goals(snapshot) <= 3


def _final_outcome_scoreline(home_goals: int, away_goals: int) -> Callable[[dict[str, Any]], bool]:
    def _checker(snapshot: dict[str, Any]) -> bool:
        return int(snapshot.get("GolsCasa") or 0) == home_goals and int(snapshot.get("GolsVisitante") or 0) == away_goals

    return _checker


def _final_outcome_not_scoreline(home_goals: int, away_goals: int) -> Callable[[dict[str, Any]], bool]:
    scoreline = _final_outcome_scoreline(home_goals, away_goals)

    def _checker(snapshot: dict[str, Any]) -> bool:
        return not scoreline(snapshot)

    return _checker


def _final_outcome_any_other_home_win(snapshot: dict[str, Any]) -> bool:
    home_goals = int(snapshot.get("GolsCasa") or 0)
    away_goals = int(snapshot.get("GolsVisitante") or 0)
    return home_goals >= 4 and home_goals > away_goals


def _final_outcome_any_other_away_win(snapshot: dict[str, Any]) -> bool:
    home_goals = int(snapshot.get("GolsCasa") or 0)
    away_goals = int(snapshot.get("GolsVisitante") or 0)
    return away_goals >= 4 and away_goals > home_goals


def _first_available(snapshot: dict[str, Any], *fields: str) -> Any:
    for field in fields:
        value = snapshot.get(field)
        if value is not None:
            return value
    return None


def _market_settlement_minute(market: dict[str, Any]) -> int:
    return int(market.get("settle_minute") or 500)


MARKET_OPTIONS: dict[str, dict[str, Any]] = {
    "Back Under 0.5 HT": {
        "odds_fields": ["BackUnder05HT", "BackUnder0_5HT", "BackUnder0.5HT"],
        "side": "back",
        "settle": _final_outcome_under_05_ht,
        "settle_minute": 45,
        "description": "Back no Under 0.5 HT",
    },
    "Lay Under 0.5 HT": {
        "odds_fields": ["LayUnder05HT", "LayUnder0_5HT", "LayUnder0.5HT"],
        "side": "lay",
        "settle": _final_outcome_under_05_ht,
        "settle_minute": 45,
        "description": "Lay no Under 0.5 HT",
    },
    "Back Over 0.5 HT": {
        "odds_fields": ["BackOver05HT", "BackOver0_5HT", "BackOver0.5HT"],
        "side": "back",
        "settle": _final_outcome_over_05_ht,
        "settle_minute": 45,
        "description": "Back no Over 0.5 HT",
    },
    "Lay Over 0.5 HT": {
        "odds_fields": ["LayOver05HT", "LayOver0_5HT", "LayOver0.5HT"],
        "side": "lay",
        "settle": _final_outcome_over_05_ht,
        "settle_minute": 45,
        "description": "Lay no Over 0.5 HT",
    },
    "Back Under 2.5 FT": {
        "odds_fields": ["BackUnder25FT"],
        "side": "back",
        "settle": _final_outcome_under_25,
        "description": "Back no Under 2.5 FT",
    },
    "Lay Under 2.5 FT": {
        "odds_fields": ["LayUnder25FT"],
        "side": "lay",
        "settle": _final_outcome_under_25,
        "description": "Lay no Under 2.5 FT",
    },
    "Back Over 2.5 FT": {
        "odds_fields": ["BackOver25FT"],
        "side": "back",
        "settle": lambda snap: not _final_outcome_under_25(snap),
        "description": "Back no Over 2.5 FT",
    },
    "Lay Over 2.5 FT": {
        "odds_fields": ["LayOver25FT"],
        "side": "lay",
        "settle": lambda snap: not _final_outcome_under_25(snap),
        "description": "Lay no Over 2.5 FT",
    },
    "Back Under 1.5 FT": {
        "odds_fields": ["BackUnder15FT", "BackUnder1_5FT"],
        "side": "back",
        "settle": _final_outcome_under_15,
        "description": "Back no Under 1.5 FT",
    },
    "Lay Under 1.5 FT": {
        "odds_fields": ["LayUnder15FT", "LayUnder1_5FT"],
        "side": "lay",
        "settle": _final_outcome_under_15,
        "description": "Lay no Under 1.5 FT",
    },
    "Back Over 1.5 FT": {
        "odds_fields": ["BackOver15FT", "BackOver1_5FT"],
        "side": "back",
        "settle": _final_outcome_over_15,
        "description": "Back no Over 1.5 FT",
    },
    "Lay Over 1.5 FT": {
        "odds_fields": ["LayOver15FT", "LayOver1_5FT"],
        "side": "lay",
        "settle": _final_outcome_over_15,
        "description": "Lay no Over 1.5 FT",
    },
    "Back Over 3.5 FT": {
        "odds_fields": ["BackOver35FT", "BackOver3_5FT"],
        "side": "back",
        "settle": _final_outcome_over_25,
        "description": "Back no Over 3.5 FT",
    },
    "Lay Over 3.5 FT": {
        "odds_fields": ["LayOver35FT", "LayOver3_5FT"],
        "side": "lay",
        "settle": _final_outcome_over_25,
        "description": "Lay no Over 3.5 FT",
    },
    "Back BTTS Sim": {
        "odds_fields": ["BackBttsSim"],
        "side": "back",
        "settle": _final_outcome_btts,
        "description": "Back BTTS Sim",
    },
    "Back BTTS Nao": {
        "odds_fields": ["BackBttsNao"],
        "side": "back",
        "settle": lambda snap: not _final_outcome_btts(snap),
        "description": "Back BTTS Nao",
    },
    "Lay BTTS Sim": {
        "odds_fields": ["LayBttsSim"],
        "side": "lay",
        "settle": _final_outcome_btts,
        "description": "Lay BTTS Sim",
    },
    "Lay BTTS Nao": {
        "odds_fields": ["LayBttsNao"],
        "side": "lay",
        "settle": lambda snap: not _final_outcome_btts(snap),
        "description": "Lay BTTS Nao",
    },
    "Back Casa FT": {
        "odds_fields": ["BackCasaFT", "BackMoCasaFT", "BackHomeFT", "BackHomeResultFT"],
        "side": "back",
        "settle": _final_outcome_home_win,
        "description": "Back mandante FT",
    },
    "Back Visitante FT": {
        "odds_fields": ["BackVisitanteFT", "BackMoVisitanteFT", "BackAwayFT", "BackAwayResultFT"],
        "side": "back",
        "settle": _final_outcome_away_win,
        "description": "Back visitante FT",
    },
    "Back Empate FT": {
        "odds_fields": ["BackEmpateFT", "BackMoEmpateFT", "BackDrawFT"],
        "side": "back",
        "settle": _final_outcome_draw,
        "description": "Back empate FT",
    },
    "Lay Casa FT": {
        "odds_fields": ["LayCasaFT", "LayMoCasaFT", "LayHomeFT", "LayHomeResultFT"],
        "side": "lay",
        "settle": _final_outcome_home_win,
        "description": "Lay mandante FT",
    },
    "Lay Visitante FT": {
        "odds_fields": ["LayVisitanteFT", "LayMoVisitanteFT", "LayAwayFT", "LayAwayResultFT"],
        "side": "lay",
        "settle": _final_outcome_away_win,
        "description": "Lay visitante FT",
    },
    "Lay Empate FT": {
        "odds_fields": ["LayEmpateFT", "LayMoEmpateFT", "LayDrawFT"],
        "side": "lay",
        "settle": _final_outcome_draw,
        "description": "Lay empate FT",
    },
    "Lay Over 0.5 FT": {
        "odds_fields": ["LayOver05FT", "LayOver0_5FT", "LayOver0.5FT"],
        "side": "lay",
        "settle": _final_outcome_over_05,
        "description": "Lay over 0.5 FT",
    },
    "Lay Goleada Casa FT": {
        "odds_fields": [
            "LayGoleadaCasaFT",
            "LayAnyOtherHomeWinFT",
            "LayAnyOtherHomeResultFT",
            "LayOtherHomeWinFT",
        ],
        "side": "lay",
        "settle": _final_outcome_any_other_home_win,
        "description": "Lay goleada casa FT",
    },
    "Lay Goleada Fora FT": {
        "odds_fields": [
            "LayGoleadaForaFT",
            "LayAnyOtherAwayWinFT",
            "LayAnyOtherAwayResultFT",
            "LayOtherAwayWinFT",
        ],
        "side": "lay",
        "settle": _final_outcome_any_other_away_win,
        "description": "Lay goleada fora FT",
    },
    "Back Correct Score 0-0": {
        "odds_fields": ["BackCS00FT", "BackCorrectScore00FT", "BackScore00FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(0, 0),
        "description": "Back placar exato 0-0",
    },
    "Back Correct Score 1-0": {
        "odds_fields": ["BackCS10FT", "BackCorrectScore10FT", "BackScore10FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(1, 0),
        "description": "Back placar exato 1-0",
    },
    "Back Correct Score 0-1": {
        "odds_fields": ["BackCS01FT", "BackCorrectScore01FT", "BackScore01FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(0, 1),
        "description": "Back placar exato 0-1",
    },
    "Back Correct Score 1-1": {
        "odds_fields": ["BackCS11FT", "BackCorrectScore11FT", "BackScore11FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(1, 1),
        "description": "Back placar exato 1-1",
    },
    "Back Correct Score 2-0": {
        "odds_fields": ["BackCS20FT", "BackCorrectScore20FT", "BackScore20FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(2, 0),
        "description": "Back placar exato 2-0",
    },
    "Back Correct Score 0-2": {
        "odds_fields": ["BackCS02FT", "BackCorrectScore02FT", "BackScore02FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(0, 2),
        "description": "Back placar exato 0-2",
    },
    "Back Correct Score 2-1": {
        "odds_fields": ["BackCS21FT", "BackCorrectScore21FT", "BackScore21FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(2, 1),
        "description": "Back placar exato 2-1",
    },
    "Back Correct Score 1-2": {
        "odds_fields": ["BackCS12FT", "BackCorrectScore12FT", "BackScore12FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(1, 2),
        "description": "Back placar exato 1-2",
    },
    "Back Correct Score 2-2": {
        "odds_fields": ["BackCS22FT", "BackCorrectScore22FT", "BackScore22FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(2, 2),
        "description": "Back placar exato 2-2",
    },
    "Back Correct Score 3-0": {
        "odds_fields": ["BackCS30FT", "BackCorrectScore30FT", "BackScore30FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(3, 0),
        "description": "Back placar exato 3-0",
    },
    "Back Correct Score 0-3": {
        "odds_fields": ["BackCS03FT", "BackCorrectScore03FT", "BackScore03FT"],
        "side": "back",
        "settle": _final_outcome_scoreline(0, 3),
        "description": "Back placar exato 0-3",
    },
    "Lay Correct Score 0 x 3": {
        "odds_fields": ["LayCS03FT", "LayCorrectScore03FT", "LayScore03FT"],
        "side": "lay",
        "settle": _final_outcome_not_scoreline(0, 3),
        "description": "Lay placar exato 0 x 3",
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
    if odd <= 1.0:
        raise ZeusClientError("Odd invalida para lay.")
    liability = stake
    lay_stake = liability / (odd - 1.0)
    profit = -liability if won else lay_stake
    if profit > 0:
        profit *= 1.0 - commission
    risk = liability
    return profit, risk


def _cashout_back_profit(stake: float, entry_odd: float, exit_odd: float, commission: float) -> tuple[float, float]:
    if exit_odd <= 0:
        raise ZeusClientError("Odd de saida invalida para cashout back.")
    profit = stake * ((entry_odd / exit_odd) - 1.0)
    if profit > 0:
        profit *= 1.0 - commission
    return profit, stake


def _cashout_lay_profit(stake: float, entry_odd: float, exit_odd: float, commission: float) -> tuple[float, float]:
    if entry_odd <= 1.0 or exit_odd <= 0:
        raise ZeusClientError("Odd de saida invalida para cashout lay.")
    liability = stake
    lay_stake = liability / (entry_odd - 1.0)
    profit = lay_stake * (1.0 - (entry_odd / exit_odd))
    if profit > 0:
        profit *= 1.0 - commission
    return profit, liability


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
    settlement_minute = config.final_minute
    market_settle_minute = _market_settlement_minute(market)
    if settlement_minute == 500:
        settlement_minute = market_settle_minute
    final_snapshot = client.fetch_final_snapshot(game_id, final_minute=settlement_minute)

    odd_fields = list(market.get("odds_fields") or [])
    odd_field = odd_fields[0] if odd_fields else market.get("odds_field")
    odd_value = _first_available(entry_snapshot, *odd_fields)
    if odd_value is None and isinstance(odd_field, str):
        odd_value = entry_snapshot.get(odd_field)
    if odd_value is None and isinstance(odd_field, str):
        odd_value = row.get(odd_field)
    if odd_value is None:
        return {
            "sport_event_id": game_id,
            "display_label": f"{row.get('NomeCasa')} x {row.get('NomeVisitante')}",
            "status": "sem odd",
        }

    odd_value = float(odd_value)
    exit_odd = _first_available(final_snapshot, *odd_fields)
    if exit_odd is None and isinstance(odd_field, str):
        exit_odd = final_snapshot.get(odd_field)
    if exit_odd is None and isinstance(odd_field, str):
        exit_odd = row.get(odd_field)
    if exit_odd is None:
        return {
            "sport_event_id": game_id,
            "display_label": f"{row.get('NomeCasa')} x {row.get('NomeVisitante')}",
            "status": "sem odd de saida",
        }
    exit_odd = float(exit_odd)

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
        "exit_minute": settlement_minute,
        "entry_odd": odd_value,
        "exit_odd": exit_odd,
        "odds_field": odd_field,
        "odds_field_used": odd_field,
        "odds_fields": odd_fields,
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
                "odds_fields": market.get("odds_fields") or [],
                "status": f"erro: {exc}",
            }

    max_workers = min(8, max(2, len(rows))) if rows else 2
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_safe_build, row): row for row in rows}
        for future in as_completed(futures):
            enriched.append(future.result())

    return _finalize_backtest(rows, enriched, config)


def _finalize_backtest(
    rows: list[dict[str, Any]],
    enriched: list[dict[str, Any]],
    config: BacktestConfig,
) -> dict[str, Any]:
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
            "worst_trade": 0.0,
            "worst_curve": 0.0,
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
        "worst_trade": float(result_df["profit"].min()) if not result_df.empty else 0.0,
        "worst_curve": float(result_df["cumulative_profit"].min()) if not result_df.empty else 0.0,
        "avg_entry_odd": float(result_df["entry_odd"].mean()) if not result_df.empty else 0.0,
    }
    return {"metrics": metrics, "result_df": result_df, "config": config}


async def _build_row_async(
    client: AsyncZeusClient,
    row: dict[str, Any],
    config: BacktestConfig,
    market: dict[str, Any],
) -> dict[str, Any]:
    game_id = str(row.get("sport_event_id") or "").strip()
    if not game_id:
        raise ZeusClientError("Registro sem sport_event_id.")

    entry_minute = config.entry_minute or infer_entry_minute(str(row.get("query") or ""))
    entry_period, entry_period_minute = absolute_to_period_minute(entry_minute)
    entry_task = client.fetch_snapshot(game_id, minute=entry_period_minute, period=entry_period)
    settlement_minute = config.final_minute
    market_settle_minute = _market_settlement_minute(market)
    if settlement_minute == 500:
        settlement_minute = market_settle_minute
    final_task = client.fetch_final_snapshot(game_id, final_minute=settlement_minute)
    entry_snapshot, final_snapshot = await asyncio.gather(entry_task, final_task)

    odd_fields = list(market.get("odds_fields") or [])
    odd_field = odd_fields[0] if odd_fields else market.get("odds_field")
    odd_value = _first_available(entry_snapshot, *odd_fields)
    if odd_value is None and isinstance(odd_field, str):
        odd_value = entry_snapshot.get(odd_field)
    if odd_value is None and isinstance(odd_field, str):
        odd_value = row.get(odd_field)
    if odd_value is None:
        return {
            "sport_event_id": game_id,
            "display_label": f"{row.get('NomeCasa')} x {row.get('NomeVisitante')}",
            "status": "sem odd",
        }

    odd_value = float(odd_value)
    exit_odd = _first_available(final_snapshot, *odd_fields)
    if exit_odd is None and isinstance(odd_field, str):
        exit_odd = final_snapshot.get(odd_field)
    if exit_odd is None and isinstance(odd_field, str):
        exit_odd = row.get(odd_field)
    if exit_odd is None:
        return {
            "sport_event_id": game_id,
            "display_label": f"{row.get('NomeCasa')} x {row.get('NomeVisitante')}",
            "status": "sem odd de saida",
        }
    exit_odd = float(exit_odd)

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
        "exit_minute": settlement_minute,
        "entry_odd": odd_value,
        "exit_odd": exit_odd,
        "odds_field": odd_field,
        "odds_field_used": odd_field,
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


async def run_backtest_async(
    client: AsyncZeusClient,
    rows: list[dict[str, Any]],
    config: BacktestConfig,
) -> dict[str, Any]:
    if config.market_label not in MARKET_OPTIONS:
        raise ZeusClientError(f"Mercado desconhecido: {config.market_label}")

    market = MARKET_OPTIONS[config.market_label]

    async def _safe_build(row: dict[str, Any]) -> dict[str, Any]:
        try:
            return await _build_row_async(client, row, config, market)
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

    tasks = [_safe_build(row) for row in rows]
    enriched = await asyncio.gather(*tasks)
    return _finalize_backtest(rows, list(enriched), config)
