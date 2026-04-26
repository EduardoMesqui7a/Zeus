from __future__ import annotations

import ast
import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime
from io import BytesIO
import json
import os
import re
import subprocess
import time
import zipfile

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.backtest import (
    MARKET_OPTIONS,
    BacktestConfig,
    run_backtest_async,
)
from src import optimization as optimization_mod
from src.query_parser import (
    extract_minute_refs,
    infer_entry_minute,
    infer_final_minute,
    infer_snapshot_period,
    rewrite_query_minute_refs,
    rewrite_query_period_refs,
)
from src.session_store import clear_saved_session, load_saved_session, save_token
from src.tournament_catalog import enrich_results_with_tournament_catalog
from src.zeus_client import AsyncZeusClient, ZeusClient, ZeusClientError


build_int_range = optimization_mod.build_int_range


def dedupe_rows_by_sport_event_id(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for row in rows:
        game_id = str(row.get("sport_event_id") or "").strip()
        if not game_id or game_id in seen_ids:
            continue
        seen_ids.add(game_id)
        deduped.append(row)
    return deduped

def build_minute_candidate_grid(entry_minutes, final_minutes):
    if hasattr(optimization_mod, "build_minute_candidate_grid"):
        return optimization_mod.build_minute_candidate_grid(entry_minutes, final_minutes)
    grid: list[dict[str, int]] = []
    for entry_minute in entry_minutes or [0]:
        for final_minute in final_minutes or [500]:
            grid.append({"entry_minute": int(entry_minute), "final_minute": int(final_minute)})
    return grid


def expand_minute_candidates_around(*args, **kwargs):
    if hasattr(optimization_mod, "expand_minute_candidates_around"):
        return optimization_mod.expand_minute_candidates_around(*args, **kwargs)
    return []


def rank_strategy_candidates(records, *, base_bets=None, min_bets=0, min_volume_ratio=0.0):
    if hasattr(optimization_mod, "rank_strategy_candidates"):
        return optimization_mod.rank_strategy_candidates(
            records,
            base_bets=base_bets,
            min_bets=min_bets,
            min_volume_ratio=min_volume_ratio,
        )
    usable = []
    base_bets_value = max(int(base_bets or 0), 0)
    for record in records:
        bets = int(record.get("bets") or 0)
        if bets < int(min_bets):
            continue
        volume_ratio = (bets / base_bets_value * 100.0) if base_bets_value else 0.0
        if base_bets_value and volume_ratio < float(min_volume_ratio or 0.0):
            continue
        enriched = dict(record)
        enriched["volume_ratio"] = volume_ratio
        usable.append(enriched)
    return sorted(
        usable,
        key=lambda record: (
            float(record.get("profit") or 0.0),
            float(record.get("volume_ratio") or 0.0),
            float(record.get("roi") or 0.0),
            float(record.get("win_rate") or 0.0),
            float(record.get("drawdown") or 0.0),
        ),
        reverse=True,
    )


SAFE_M500_FIELDS = {
    "Minuto",
    "NivelDados",
    "DataJogo",
}

QUERY_TERM_SPLIT = r"(?i)\band\b"


def split_query_terms(query: str) -> list[str]:
    terms = [part.strip() for part in re.split(QUERY_TERM_SPLIT, query or "") if part.strip()]
    return terms


def sanitize_query_terms(query: str) -> tuple[str, list[str]]:
    return (query or "").strip(), []


def detect_market_from_query(query: str) -> str | None:
    query_text = query or ""
    scored_matches: list[tuple[float, str]] = []
    for label, market in MARKET_OPTIONS.items():
        score = 0.0
        for field in market.get("odds_fields") or []:
            if not re.search(rf"(?i)\b{re.escape(field)}\b", query_text):
                continue
            field_score = 1.0
            if re.search(rf"(?i)\b{re.escape(field)}\s+between\b", query_text):
                field_score += 3.0
            elif re.search(rf"(?i)\b{re.escape(field)}\s*(?:>=|<=|=)\b", query_text):
                field_score += 1.5
            field_score += min(len(field), 20) / 100.0
            score = max(score, field_score)
        if score:
            scored_matches.append((score, label))
    if not scored_matches:
        return None
    scored_matches.sort(key=lambda item: (-item[0], list(MARKET_OPTIONS.keys()).index(item[1])))
    return scored_matches[0][1]


def get_build_version() -> str:
    env_version = (os.getenv("GIT_COMMIT") or os.getenv("COMMIT_SHA") or "").strip()
    if env_version:
        return env_version[:7]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        version = result.stdout.strip()
        if version:
            return version
    except Exception:
        pass
    return "local"


st.set_page_config(
    page_title="Zeus Backtester",
    page_icon="Z",
    layout="wide",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
            html, body, [class*="css"]  {
                font-family: 'Inter', sans-serif;
            }
            .stApp {
                background:
                    radial-gradient(circle at top left, rgba(15,118,110,0.16), transparent 28%),
                    radial-gradient(circle at top right, rgba(37,99,235,0.10), transparent 24%),
                    linear-gradient(180deg, #f6fafc 0%, #eef3f8 100%);
            }
            .hero {
                border-radius: 28px;
                padding: 1.6rem 1.6rem 1.25rem 1.6rem;
                background: rgba(255,255,255,0.88);
                border: 1px solid rgba(15,23,42,0.08);
                box-shadow: 0 24px 70px rgba(15,23,42,0.08);
                margin-bottom: 1rem;
            }
            .kicker {
                display: inline-flex;
                padding: 0.45rem 0.8rem;
                border-radius: 999px;
                background: rgba(15,118,110,0.10);
                color: #0f766e;
                font-size: 0.78rem;
                font-weight: 800;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                margin-bottom: 0.85rem;
            }
            .title {
                font-size: clamp(2rem, 4vw, 3.3rem);
                line-height: 0.95;
                margin: 0;
                color: #0f172a;
                font-weight: 900;
                letter-spacing: -0.04em;
                text-transform: uppercase;
            }
            .title span {
                color: #0f766e;
            }
            .subtitle {
                color: #516079;
                max-width: 820px;
                line-height: 1.55;
                margin-top: 0.85rem;
                font-size: 1.02rem;
            }
            .metric-card {
                border-radius: 20px;
                background: rgba(255,255,255,0.86);
                border: 1px solid rgba(15,23,42,0.08);
                padding: 0.9rem 1rem;
                box-shadow: 0 18px 40px rgba(15,23,42,0.06);
            }
            .metric-label {
                color: #6b7280;
                font-size: 0.84rem;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.06em;
            }
            .metric-value {
                margin-top: 0.2rem;
                font-size: 1.6rem;
                font-weight: 800;
                color: #0f172a;
            }
            .metric-profit-positive {
                color: #0f766e;
            }
            .metric-profit-negative {
                color: #b91c1c;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def metric_card(label: str, value: str, delta: str | None = None) -> str:
    delta_html = f'<div style="margin-top:0.25rem;color:#64748b;font-size:0.9rem;">{delta}</div>' if delta else ""
    return f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {delta_html}
    </div>
    """


def format_brl(value: float) -> str:
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    formatted = f"{absolute:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {sign}{formatted}"


def render_metrics(metrics: dict) -> None:
    cols = st.columns(6)
    items = [
        ("Jogos", f"{metrics.get('matches', 0)}"),
        ("Entradas", f"{metrics.get('bets', 0)}"),
        ("Ganhou", f"{metrics.get('wins', 0)}"),
        ("Perdeu", f"{metrics.get('losses', max(int(metrics.get('bets', 0)) - int(metrics.get('wins', 0)), 0))}"),
        ("Taxa de acerto", f"{metrics.get('win_rate', 0):.2f}%"),
        ("ROI", f"{metrics.get('roi', 0):.2f}%"),
    ]
    for col, (label, value) in zip(cols, items, strict=False):
        with col:
            st.markdown(metric_card(label, value), unsafe_allow_html=True)

    cols = st.columns(6)
    total_profit = float(metrics.get("total_profit", 0))
    profit_class = "metric-profit-positive" if total_profit >= 0 else "metric-profit-negative"
    items = [
        ("Lucro", f"{format_brl(total_profit)}", profit_class),
        ("Stake", f"{format_brl(float(metrics.get('total_risked', 0)))}"),
        ("Drawdown máximo", f"{format_brl(float(metrics.get('worst_curve', metrics.get('worst_trade', 0))))}"),
        ("Odd média", f"{metrics.get('avg_entry_odd', 0):.2f}"),
        ("Sequência de vitória", f"{metrics.get('max_win_streak', 0)}"),
        ("Sequência de derrota", f"{metrics.get('max_loss_streak', 0)}"),
    ]
    for col, item in zip(cols, items, strict=False):
        label, value, *style_class = item
        style_class = style_class[0] if style_class else ""
        with col:
            if style_class:
                st.markdown(metric_card(label, value).replace('class="metric-value"', f'class="metric-value {style_class}"'), unsafe_allow_html=True)
            else:
                st.markdown(metric_card(label, value), unsafe_allow_html=True)


def build_results_display_df(result_df: pd.DataFrame) -> pd.DataFrame:
    display_columns = [
        "display_label",
        "match_datetime",
        "final_home_goals",
        "final_away_goals",
        "entry_minute",
        "entry_odd",
        "exit_minute",
        "exit_odd",
        "stake_risked",
        "final_verification_hit",
        "won",
        "profit",
        "result_text",
        "drawdown",
    ]
    if result_df.empty:
        return pd.DataFrame(columns=[
            "Jogos",
            "Data",
            "Minuto Entrada",
            "Odd Entrada",
            "Profit",
            "Stake",
            "Checagem Final",
            "Won",
            "Resultado",
            "Minuto Saída",
            "Odd Saída",
            "Gols Casa",
            "Gols Fora",
            "Drawdown",
        ])
    missing_columns = [column for column in display_columns if column not in result_df.columns]
    if missing_columns:
        result_df = result_df.copy()
        for column in missing_columns:
            result_df[column] = pd.NA
    display_df = result_df[display_columns].copy()
    display_df["match_datetime"] = (
        pd.to_datetime(display_df["match_datetime"], errors="coerce", utc=True)
        .dt.tz_convert("America/Sao_Paulo")
        .dt.strftime("%d/%m/%y %H:%M")
    )
    return display_df.rename(
        columns={
            "display_label": "Jogos",
            "match_datetime": "Data",
            "final_home_goals": "Gols Casa",
            "final_away_goals": "Gols Fora",
            "entry_minute": "Minuto Entrada",
            "entry_odd": "Odd Entrada",
            "exit_minute": "Minuto Saída",
            "exit_odd": "Odd Saída",
            "stake_risked": "Stake",
            "final_verification_hit": "Checagem Final",
            "won": "Won",
            "profit": "Profit",
            "result_text": "Resultado",
            "drawdown": "Drawdown",
        }
    )


def _build_odd_bucket_summary(results_df: pd.DataFrame) -> pd.DataFrame:
    odd_frame = results_df.loc[
        results_df["entry_odd"].notna(),
        ["entry_odd", "profit", "stake_risked", "won"],
    ].copy()
    if odd_frame.empty:
        return pd.DataFrame()

    bins = min(12, max(5, int(len(odd_frame) ** 0.5)))
    odd_frame["odd_bucket"] = pd.cut(odd_frame["entry_odd"], bins=bins, include_lowest=True, duplicates="drop")
    grouped = (
        odd_frame.groupby("odd_bucket", observed=False)
        .agg(
            bets=("entry_odd", "size"),
            wins=("won", lambda series: int(pd.Series(series).fillna(False).sum())),
            profit=("profit", "sum"),
            risk=("stake_risked", "sum"),
        )
        .reset_index()
    )
    grouped["winrate"] = grouped.apply(lambda row: (row["wins"] / row["bets"] * 100.0) if row["bets"] else 0.0, axis=1)
    grouped["roi"] = grouped.apply(lambda row: (row["profit"] / row["risk"] * 100.0) if row["risk"] else 0.0, axis=1)
    grouped["odd_bucket"] = grouped["odd_bucket"].apply(
        lambda bucket: f"[{bucket.left:.1f}, {bucket.right:.1f}]" if hasattr(bucket, "left") and hasattr(bucket, "right") else str(bucket)
    )
    return grouped


def _build_period_summary(block_df: pd.DataFrame, freq: str) -> pd.DataFrame:
    if block_df.empty:
        return pd.DataFrame()

    period_rows: list[dict[str, object]] = []
    for period_value, group in block_df.groupby(pd.Grouper(key="match_datetime", freq=freq), dropna=True):
        if group.empty:
            continue
        group = group.sort_values("match_datetime")
        cumulative = group["profit"].cumsum()
        peak = cumulative.cummax()
        drawdown = cumulative - peak
        bets = int(len(group))
        wins = int(group["won"].fillna(False).sum())
        total_profit = float(group["profit"].sum())
        total_risk = float(group["stake_risked"].sum())
        period_rows.append(
            {
                "period": period_value,
                "bets": bets,
                "wins": wins,
                "winrate": (wins / bets * 100.0) if bets else 0.0,
                "profit": total_profit,
                "roi": (total_profit / total_risk * 100.0) if total_risk else 0.0,
                "drawdown": float(drawdown.min()) if not drawdown.empty else 0.0,
            }
        )

    summary = pd.DataFrame(period_rows)
    if summary.empty:
        return summary
    return summary.sort_values("period").reset_index(drop=True)


def _extract_tournament_parts(row: pd.Series) -> dict[str, str]:
    parts = {
        "tournament_id": "",
        "tournament_name": "",
        "season_name": "",
    }
    direct_fields = {
        "tournament_id": ("tournament_id", "IdTorneio", "id_torneio", "TournamentId", "IdCompeticao", "CompetitionId"),
        "tournament_name": (
            "tournament_name",
            "NomeTorneio",
            "nome_torneio",
            "TournamentName",
            "Torneio",
            "CompetitionName",
            "NomeCompeticao",
            "Campeonato",
            "LeagueName",
        ),
        "season_name": ("season_name", "NomeTemporada", "nome_temporada", "SeasonName", "Season"),
    }
    for part_name, candidates in direct_fields.items():
        for candidate in candidates:
            value = row.get(candidate)
            if pd.notna(value):
                label = str(value).strip()
                if label and label.lower() != "gold":
                    parts[part_name] = label
                    break

    for snapshot_column in ("entry_snapshot", "final_snapshot"):
        snapshot_value = row.get(snapshot_column)
        if not snapshot_value:
            continue
        snapshot_data: dict[str, object] | None = None
        if isinstance(snapshot_value, dict):
            snapshot_data = snapshot_value
        else:
            try:
                parsed = ast.literal_eval(str(snapshot_value))
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                snapshot_data = parsed
        if not snapshot_data:
            continue
        for part_name, candidates in direct_fields.items():
            if parts[part_name]:
                continue
            for candidate in candidates:
                value = snapshot_data.get(candidate)
                if value is None:
                    continue
                label = str(value).strip()
                if label and label.lower() != "gold":
                    parts[part_name] = label
                    break

    tournament_name = parts["tournament_name"]
    tournament_id = parts["tournament_id"]
    season_name = parts["season_name"]
    if tournament_name and tournament_id:
        display_label = f"{tournament_name} ({tournament_id})"
    else:
        display_label = tournament_name or tournament_id or season_name or "Sem torneio"
    parts["group_key"] = tournament_id or tournament_name or season_name or "Sem torneio"
    parts["group_label"] = display_label
    return parts


def _build_league_summary(results_df: pd.DataFrame) -> pd.DataFrame:
    if results_df.empty:
        return pd.DataFrame()

    league_frame = results_df.copy()
    tournament_parts = league_frame.apply(_extract_tournament_parts, axis=1, result_type="expand")
    league_frame = pd.concat([league_frame, tournament_parts.add_prefix("summary_")], axis=1)
    league_group = (
        league_frame.groupby("summary_group_key", dropna=False)
        .agg(
            group_label=("summary_group_label", "first"),
            tournament_id=("summary_tournament_id", "first"),
            tournament_name=("summary_tournament_name", "first"),
            season_name=("summary_season_name", "first"),
            bets=("summary_group_key", "size"),
            wins=("won", lambda series: int(pd.Series(series).fillna(False).sum())),
            profit=("profit", "sum"),
            risk=("stake_risked", "sum"),
            avg_odd=("entry_odd", "mean"),
            max_drawdown=("drawdown", "min"),
        )
        .reset_index()
    )
    league_group["losses"] = league_group["bets"] - league_group["wins"]
    league_group["winrate"] = league_group.apply(lambda row: (row["wins"] / row["bets"] * 100.0) if row["bets"] else 0.0, axis=1)
    league_group["roi"] = league_group.apply(lambda row: (row["profit"] / row["risk"] * 100.0) if row["risk"] else 0.0, axis=1)
    league_group = league_group.sort_values(["profit", "bets"], ascending=[False, False]).reset_index(drop=True)
    return league_group


def _build_chart_artifacts(results_df: pd.DataFrame, block_period: str = "Mensal") -> dict[str, object]:
    period_config = {
        "Mensal": ("MS", "M?s"),
        "Trimestral": ("QS", "Trimestre"),
        "Semestral": ("6MS", "Semestre"),
        "Anual": ("YS", "Ano"),
    }
    freq, period_label = period_config.get(block_period, period_config["Mensal"])

    block_df = results_df.copy()
    if not block_df.empty and getattr(block_df["match_datetime"].dt, "tz", None) is not None:
        block_df["match_datetime"] = block_df["match_datetime"].dt.tz_convert(None)

    odd_summary = _build_odd_bucket_summary(results_df)
    grouped = _build_period_summary(block_df, freq)

    equity_fig = go.Figure()
    equity_fig.add_trace(
        go.Scatter(
            x=results_df["match_datetime"],
            y=results_df["cumulative_profit"],
            mode="lines",
            name="Equity",
            line=dict(color="#0f766e", width=3),
        )
    )
    equity_fig.add_trace(
        go.Scatter(
            x=results_df["match_datetime"],
            y=results_df["drawdown"],
            mode="lines",
            name="Drawdown",
            line=dict(color="#dc2626", width=2, dash="dot"),
            yaxis="y2",
        )
    )
    equity_fig.update_layout(
        height=420,
        margin=dict(l=0, r=0, t=20, b=0),
        template="plotly_white",
        legend=dict(orientation="h"),
        yaxis_title="Resultado acumulado",
        yaxis2=dict(overlaying="y", side="right", title="Drawdown"),
    )

    odd_fig = go.Figure()
    if not odd_summary.empty:
        odd_fig.add_trace(
            go.Bar(
                x=odd_summary["odd_bucket"],
                y=odd_summary["bets"],
                name="Bets",
                marker_color="#f59e0b",
                opacity=0.85,
            )
        )
        odd_fig.add_trace(
            go.Scatter(
                x=odd_summary["odd_bucket"],
                y=odd_summary["winrate"],
                mode="lines+markers",
                name="Winrate %",
                line=dict(color="#16a34a", width=3),
                yaxis="y2",
            )
        )
        odd_fig.add_trace(
            go.Scatter(
                x=odd_summary["odd_bucket"],
                y=odd_summary["roi"],
                mode="lines+markers",
                name="ROI %",
                line=dict(color="#2563eb", width=3),
                yaxis="y2",
            )
        )
    odd_fig.update_layout(
        title="ROI e Winrate por faixa de odd",
        height=420,
        margin=dict(l=0, r=0, t=50, b=0),
        template="plotly_white",
        barmode="group",
        legend=dict(orientation="h"),
        yaxis=dict(title="Bets"),
        yaxis2=dict(overlaying="y", side="right", title="Percentual"),
    )

    period_fig = go.Figure()
    if not grouped.empty:
        period_fig.add_trace(
            go.Bar(
                x=grouped["period"],
                y=grouped["profit"],
                name="Lucro do bloco",
                marker_color=["#0f766e" if value >= 0 else "#b91c1c" for value in grouped["profit"]],
            )
        )
        period_fig.add_trace(
            go.Scatter(
                x=grouped["period"],
                y=grouped["profit"].cumsum(),
                mode="lines+markers",
                name="Acumulado por bloco",
                line=dict(color="#1d4ed8", width=2),
            )
        )
    period_fig.update_layout(
        height=380,
        template="plotly_white",
        margin=dict(l=0, r=0, t=30, b=0),
        barmode="relative",
        legend=dict(orientation="h"),
        xaxis_title=period_label,
        yaxis_title="Lucro / acumulado",
    )

    return {
        "period_label": period_label,
        "odd_summary": odd_summary,
        "period_summary": grouped,
        "equity_fig": equity_fig,
        "odd_fig": odd_fig,
        "period_fig": period_fig,
    }


def render_charts(results_df: pd.DataFrame, block_period: str = "Mensal") -> None:
    if results_df.empty:
        return

    artifacts = _build_chart_artifacts(results_df, block_period)
    period_label = str(artifacts["period_label"])

    st.subheader("Profit Acumulado")
    left, right = st.columns((1.4, 1))

    with left:
        st.plotly_chart(artifacts["equity_fig"], width="stretch")

    with right:
        st.plotly_chart(artifacts["odd_fig"], width="stretch")

    with st.container():
        period_choice = st.selectbox(
            "Agrupar profit",
            options=["Mensal", "Trimestral", "Semestral", "Anual"],
            index=["Mensal", "Trimestral", "Semestral", "Anual"].index(block_period)
            if block_period in {"Mensal", "Trimestral", "Semestral", "Anual"}
            else 0,
            key="zeus_profit_period",
            help="Escolha como agrupar o lucro: por m?s, trimestre, semestre ou ano.",
        )
        artifacts = _build_chart_artifacts(results_df, period_choice)
        period_label = str(artifacts["period_label"])
        grouped = artifacts["period_summary"]
        if not grouped.empty:
            st.subheader(f"Profit por {period_label.lower()}")
            st.plotly_chart(artifacts["period_fig"], width="stretch")
            grouped = grouped.rename(
                columns={
                    "period": period_label,
                    "profit": "Lucro",
                    "bets": "Bets",
                    "wins": "Wins",
                    "winrate": "Winrate %",
                    "roi": "ROI %",
                    "drawdown": "Drawdown",
                }
            ).copy()
            grouped["Lucro"] = grouped["Lucro"].map(format_brl)
            grouped["Drawdown"] = grouped["Drawdown"].map(format_brl)
            grouped["Winrate %"] = grouped["Winrate %"].map(lambda value: f"{float(value):.2f}%")
            grouped["ROI %"] = grouped["ROI %"].map(lambda value: f"{float(value):.2f}%")
            st.dataframe(grouped[[period_label, "Bets", "Wins", "Winrate %", "ROI %", "Drawdown", "Lucro"]], width="stretch", hide_index=True)

    league_summary = _build_league_summary(results_df)
    if not league_summary.empty:
        st.subheader("Desempenho por torneio")
        league_chart = go.Figure()
        top_leagues = league_summary.head(12).copy()
        league_chart.add_trace(
            go.Bar(
                x=top_leagues["group_label"],
                y=top_leagues["roi"],
                name="ROI %",
                marker_color=["#0f766e" if value >= 0 else "#b91c1c" for value in top_leagues["roi"]],
                opacity=0.9,
            )
        )
        league_chart.add_trace(
            go.Scatter(
                x=top_leagues["group_label"],
                y=top_leagues["winrate"],
                mode="lines+markers",
                name="Winrate %",
                line=dict(color="#2563eb", width=3),
                yaxis="y2",
            )
        )
        league_chart.update_layout(
            height=420,
            template="plotly_white",
            margin=dict(l=0, r=0, t=30, b=0),
            barmode="group",
            legend=dict(orientation="h"),
            xaxis_title="Torneio",
            yaxis=dict(title="ROI %"),
            yaxis2=dict(overlaying="y", side="right", title="Winrate %"),
        )
        st.plotly_chart(league_chart, width="stretch")
        league_view = league_summary.copy()
        league_view["Lucro"] = league_view["profit"].map(format_brl)
        league_view["Drawdown"] = league_view["max_drawdown"].map(format_brl)
        league_view["Winrate %"] = league_view["winrate"].map(lambda value: f"{float(value):.2f}%")
        league_view["ROI %"] = league_view["roi"].map(lambda value: f"{float(value):.2f}%")
        league_view["Odd média"] = league_view["avg_odd"].map(lambda value: f"{float(value):.2f}")
        league_view = league_view.rename(
            columns={
                "group_label": "Torneio",
                "tournament_id": "ID Torneio",
                "tournament_name": "Campeonato",
                "season_name": "Temporada",
                "bets": "Bets",
                "wins": "Wins",
                "losses": "Losses",
            }
        )
        st.dataframe(
            league_view[["Torneio", "ID Torneio", "Campeonato", "Temporada", "Bets", "Wins", "Losses", "Winrate %", "ROI %", "Lucro", "Drawdown", "Odd média"]],
            width="stretch",
            hide_index=True,
        )


def build_backtest_export_bundle(
    report: dict,
    *,
    base_query: str,
    final_filter: str,
    market_label: str,
    block_period: str,
) -> tuple[bytes, str]:
    results_df = report["backtest"]["result_df"].copy()
    metrics = dict(report["backtest"]["metrics"])
    if results_df.empty or "match_datetime" not in results_df.columns:
        artifacts = {
            "period_label": block_period,
            "period_summary": pd.DataFrame(),
            "equity_fig": go.Figure(),
            "odd_fig": go.Figure(),
            "period_fig": go.Figure(),
        }
    else:
        artifacts = _build_chart_artifacts(results_df, block_period)

    display_df = build_results_display_df(results_df)
    period_summary = artifacts["period_summary"].copy()
    league_summary = _build_league_summary(results_df)
    if not period_summary.empty:
        period_summary = period_summary.rename(
            columns={
                "period": artifacts["period_label"],
                "profit": "Lucro",
                "bets": "Bets",
                "wins": "Wins",
                "winrate": "Winrate %",
                "roi": "ROI %",
                "drawdown": "Drawdown",
            }
        )
    league_table = league_summary.copy()
    if not league_table.empty:
        league_table["Lucro"] = league_table["profit"].map(format_brl)
        league_table["Drawdown"] = league_table["max_drawdown"].map(format_brl)
        league_table["Winrate %"] = league_table["winrate"].map(lambda value: f"{float(value):.2f}%")
        league_table["ROI %"] = league_table["roi"].map(lambda value: f"{float(value):.2f}%")
        league_table["Odd média"] = league_table["avg_odd"].map(lambda value: f"{float(value):.2f}")
        league_table = league_table.rename(
            columns={
                "group_label": "Torneio",
                "tournament_id": "ID Torneio",
                "tournament_name": "Campeonato",
                "season_name": "Temporada",
                "bets": "Bets",
                "wins": "Wins",
                "losses": "Losses",
            }
        )
        league_table = league_table[
            [
                "Torneio",
                "ID Torneio",
                "Campeonato",
                "Temporada",
                "Bets",
                "Wins",
                "Losses",
                "Winrate %",
                "ROI %",
                "Lucro",
                "Drawdown",
                "Odd média",
            ]
        ]

    summary_payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "market_label": market_label,
        "base_query": base_query,
        "final_filter": final_filter,
        "metrics": metrics,
        "period_grouping": block_period,
        "strategy_matches": int(metrics.get("strategy_matches", 0) or 0),
        "verification_hits": int(metrics.get("verification_hits", 0) or 0),
        "display_rows": int(len(display_df)),
    }

    gpt_notes = [
        "# Zeus Backtest Export",
        "",
        f"- Market: {market_label}",
        f"- Base query: {base_query}",
        f"- Final check: {final_filter}",
        f"- Rows: {int(len(results_df))}",
        f"- Wins: {int(metrics.get('wins', 0) or 0)}",
        f"- Losses: {int(metrics.get('losses', 0) or 0)}",
        f"- Win rate: {float(metrics.get('win_rate', 0) or 0):.2f}%",
        f"- ROI: {float(metrics.get('roi', 0) or 0):.2f}%",
        f"- Profit: {format_brl(float(metrics.get('total_profit', 0) or 0))}",
        f"- Drawdown max: {format_brl(float(metrics.get('max_drawdown', 0) or 0))}",
        "- Tournament analysis: use `resumo_por_campeonato.csv`; exclusions should be based on `ID Torneio`, not Gold/Silver or season labels.",
        "",
        "## Metrics",
        json.dumps(summary_payload, ensure_ascii=False, indent=2, default=str),
    ]

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("resumo_para_gpt.md", "\n".join(gpt_notes))
        archive.writestr("resumo_geral.json", json.dumps(summary_payload, ensure_ascii=False, indent=2, default=str))
        archive.writestr("resultados_raw.csv", results_df.to_csv(index=False))
        archive.writestr("tabela_resultados.csv", display_df.to_csv(index=False))
        archive.writestr("resumo_por_periodo.csv", period_summary.to_csv(index=False) if not period_summary.empty else "")
        archive.writestr("resumo_por_campeonato.csv", league_table.to_csv(index=False) if not league_table.empty else "")
        archive.writestr(
            "graficos/profit_acumulado.html",
            artifacts["equity_fig"].to_html(full_html=True, include_plotlyjs="inline"),
        )
        archive.writestr(
            "graficos/roi_winrate_faixa_odd.html",
            artifacts["odd_fig"].to_html(full_html=True, include_plotlyjs="inline"),
        )
        archive.writestr(
            "graficos/profit_por_periodo.html",
            artifacts["period_fig"].to_html(full_html=True, include_plotlyjs="inline"),
        )

    return buffer.getvalue(), f"zeus_backtest_bundle_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"

def render_game_timeline(client: ZeusClient, game_id: str, market_field: str) -> None:
    timeline = client.fetch_timeline(game_id, market_field=market_field)
    if not timeline:
        st.info("Não foi possível montar a linha do tempo deste jogo.")
        return

    df = pd.DataFrame(timeline)
    df = df.sort_values(["absolute_minute"])
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["absolute_minute"],
            y=df["odd_value"],
            mode="lines+markers",
            name=market_field,
            line=dict(color="#2563eb", width=3),
        )
    )
    if "gols_total" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["absolute_minute"],
                y=df["gols_total"],
                mode="lines",
                name="Gols Total",
                line=dict(color="#0f766e", width=2, dash="dot"),
                yaxis="y2",
            )
        )
        fig.update_layout(
            yaxis2=dict(overlaying="y", side="right", title="Gols"),
        )
    fig.update_layout(
        height=420,
        template="plotly_white",
        margin=dict(l=0, r=0, t=20, b=0),
        xaxis_title="Minuto absoluto",
        yaxis_title="Odd",
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig, width="stretch")
    st.dataframe(df, width="stretch", hide_index=True)


def render_timeline_frame(timeline: list[dict[str, object]], market_field: str) -> None:
    if not timeline:
        st.info("Não foi possível montar a linha do tempo deste jogo.")
        return

    df = pd.DataFrame(timeline)
    df = df.sort_values(["absolute_minute"])
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["absolute_minute"],
            y=df["odd_value"],
            mode="lines+markers",
            name=market_field,
            line=dict(color="#2563eb", width=3),
        )
    )
    if "gols_total" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["absolute_minute"],
                y=df["gols_total"],
                mode="lines",
                name="Gols Total",
                line=dict(color="#0f766e", width=2, dash="dot"),
                yaxis="y2",
            )
        )
        fig.update_layout(
            yaxis2=dict(overlaying="y", side="right", title="Gols"),
        )
    fig.update_layout(
        height=420,
        template="plotly_white",
        margin=dict(l=0, r=0, t=20, b=0),
        xaxis_title="Minuto absoluto",
        yaxis_title="Odd",
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig, width="stretch")
    st.dataframe(df, width="stretch", hide_index=True)


def render_report_view(report: dict, token: str, market_label: str, base_query: str, final_filter: str) -> None:

    render_metrics(report["backtest"]["metrics"])
    verification_hits = int(report["backtest"]["metrics"].get("verification_hits", 0) or 0)
    matches = int(report["backtest"]["metrics"].get("matches", 0) or 0)
    strategy_matches = int(report["backtest"]["metrics"].get("strategy_matches", matches) or matches)
    if strategy_matches:
        st.caption(f"Checagem final: {verification_hits}/{strategy_matches} jogos ({(verification_hits / strategy_matches) * 100.0:.2f}%)")
    timings = report.get("timings") if isinstance(report, dict) else {}
    if isinstance(timings, dict) and timings:
        timing_labels = {
            "total_load_seconds": "Total",
            "lucy_search_seconds": "Busca/paginacao Lucy",
            "backtest_snapshots_seconds": "Snapshots e calculo",
            "zeus_count_seconds": "Contagem Zeus",
            "bot_config_seconds": "Catalogo de torneios",
            "tournament_enrichment_seconds": "Enriquecimento torneios",
            "dedupe_seconds": "Deduplicacao",
        }
        timing_text = " | ".join(
            f"{label}: {float(timings.get(key, 0.0)):.2f}s"
            for key, label in timing_labels.items()
            if key in timings
        )
        st.caption(f"Diagnostico de tempo: {timing_text}")
    render_charts(report["backtest"]["result_df"], block_period=st.session_state.get("zeus_profit_period", "Mensal"))

    st.subheader("Resultados")
    display_df = build_results_display_df(report["backtest"]["result_df"])
    st.dataframe(display_df, width="stretch", hide_index=True)

    csv = report["backtest"]["result_df"].to_csv(index=False).encode("utf-8")
    st.download_button(
        "Baixar CSV",
        data=csv,
        file_name="zeus_backtest.csv",
        mime="text/csv",
        key="zeus_download_csv",
    )
    current_block_period = str(st.session_state.get("zeus_profit_period", "Mensal"))
    signature_columns = [
        column
        for column in [
            "sport_event_id",
            "match_datetime",
            "entry_minute",
            "exit_minute",
            "entry_odd",
            "exit_odd",
            "profit",
            "won",
            "result_text",
            "final_home_goals",
            "final_away_goals",
            "final_verification_hit",
        ]
        if column in report["backtest"]["result_df"].columns
    ]
    signature_frame = report["backtest"]["result_df"][signature_columns].copy() if signature_columns else pd.DataFrame()
    results_signature = hashlib.sha256(signature_frame.to_csv(index=False).encode("utf-8")).hexdigest()
    export_signature = hashlib.sha256(
        "|".join(
            [
                base_query,
                final_filter,
                market_label,
                current_block_period,
                results_signature,
            ]
        ).encode("utf-8")
    ).hexdigest()
    if st.session_state.get("zeus_export_signature") != export_signature:
        bundle_bytes, bundle_name = build_backtest_export_bundle(
            report,
            base_query=base_query,
            final_filter=final_filter,
            market_label=market_label,
            block_period=current_block_period,
        )
        st.session_state["zeus_export_signature"] = export_signature
        st.session_state["zeus_export_bundle"] = bundle_bytes
        st.session_state["zeus_export_bundle_name"] = bundle_name
    bundle_bytes = st.session_state.get("zeus_export_bundle")
    bundle_name = st.session_state.get("zeus_export_bundle_name") or "zeus_backtest_bundle.zip"
    st.download_button(
        "Baixar pacote completo",
        data=bundle_bytes,
        file_name=bundle_name,
        mime="application/zip",
        key="zeus_download_bundle",
        help="Baixa CSV, resumo em texto/JSON e gráficos em HTML em um único arquivo.",
    )

    st.subheader("Detalhe do jogo")
    if not report["backtest"]["result_df"].empty:
        st.session_state.setdefault("zeus_detail_game_id", "")
        st.session_state.setdefault("zeus_detail_market_field", "")
        st.session_state.setdefault("zeus_detail_timeline", [])
        selected_label = st.selectbox(
            "Jogo",
            report["backtest"]["result_df"]["display_label"].tolist(),
            key="zeus_selected_game",
        )
        selected_rows = report["backtest"]["result_df"].loc[report["backtest"]["result_df"]["display_label"].eq(selected_label)]
        if selected_rows.empty:
            st.warning("Não foi possível localizar o jogo selecionado.")
            return
        selected = selected_rows.iloc[0]
        st.write(f"{selected['display_label']} | entrada {selected['entry_minute']} | odd {selected['entry_odd']:.2f}")
        st.caption(f"Campo odd usado: {selected.get('odds_field_used') or selected.get('odds_field') or 'n/a'}")
        load_detail = st.button("Carregar detalhe do jogo", width="stretch", key="zeus_load_detail")
        cache_hit = (
            st.session_state.get("zeus_detail_game_id") == str(selected["sport_event_id"])
            and st.session_state.get("zeus_detail_market_field") == str(selected["odds_field"])
        )
        if load_detail:
            with st.spinner("Carregando timeline do jogo..."):
                st.session_state["zeus_detail_game_id"] = str(selected["sport_event_id"])
                st.session_state["zeus_detail_market_field"] = str(selected["odds_field"])
                st.session_state["zeus_detail_timeline"] = load_timeline_cached(
                    hashlib.sha256(token.encode("utf-8")).hexdigest(),
                    token,
                    str(selected["sport_event_id"]),
                    str(selected["odds_field"]),
                )
        if cache_hit and st.session_state.get("zeus_detail_timeline"):
            render_timeline_frame(
                st.session_state["zeus_detail_timeline"],
                str(selected["odds_field"]),
            )
        else:
            st.caption("Selecione o jogo e clique em 'Carregar detalhe do jogo' para buscar a linha do tempo sob demanda.")


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "running event loop" not in message:
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class CachedAsyncZeusClient:
    def __init__(self, client: AsyncZeusClient) -> None:
        self._client = client
        self._search_cache: dict[tuple[str, int, int, bool], object] = {}
        self._search_inflight: dict[tuple[str, int, int, bool], asyncio.Task] = {}
        self._snapshot_cache: dict[tuple[str, int, int], dict[str, object]] = {}
        self._snapshot_inflight: dict[tuple[str, int, int], asyncio.Task] = {}
        self._final_snapshot_cache: dict[tuple[str, int], dict[str, object]] = {}
        self._final_snapshot_inflight: dict[tuple[str, int], asyncio.Task] = {}

    async def search_all(
        self,
        query: str,
        *,
        max_games: int | None = None,
        include_count: bool = False,
    ):
        key = (query, int(max_games or 0), bool(include_count))
        if key in self._search_cache:
            return self._search_cache[key]
        task = self._search_inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._client.search_all(
                    query,
                    max_games=max_games,
                    include_count=include_count,
                )
            )
            self._search_inflight[key] = task
        try:
            result = await task
            self._search_cache[key] = result
            return result
        finally:
            if self._search_inflight.get(key) is task:
                self._search_inflight.pop(key, None)

    async def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict[str, object]:
        key = (game_id, int(minute), int(period))
        if key in self._snapshot_cache:
            return self._snapshot_cache[key]
        task = self._snapshot_inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._client.fetch_snapshot(game_id, minute=minute, period=period))
            self._snapshot_inflight[key] = task
        try:
            snapshot = await task
            self._snapshot_cache[key] = snapshot
            return snapshot
        finally:
            if self._snapshot_inflight.get(key) is task:
                self._snapshot_inflight.pop(key, None)

    async def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict[str, object]:
        key = (game_id, int(final_minute))
        if key in self._final_snapshot_cache:
            return self._final_snapshot_cache[key]
        task = self._final_snapshot_inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._client.fetch_final_snapshot(game_id, final_minute=final_minute))
            self._final_snapshot_inflight[key] = task
        try:
            snapshot = await task
            self._final_snapshot_cache[key] = snapshot
            return snapshot
        finally:
            if self._final_snapshot_inflight.get(key) is task:
                self._final_snapshot_inflight.pop(key, None)

    def __getattr__(self, name: str):
        return getattr(self._client, name)


@st.cache_data(ttl=300, show_spinner=False)
def load_backtest_report(
    cache_key: str,
    _token: str,
    base_query: str,
    final_filter: str,
    market_label: str,
    stake: float,
    commission: float,
    max_games: int,
    entry_minute: int,
    final_minute: int,
) -> dict:
    entry_query_source = infer_entry_minute(base_query)
    executed_base_query = rewrite_query_minute_refs(base_query, entry_query_source, entry_minute)
    sanitized_base, stripped_base = sanitize_query_terms(executed_base_query)
    final_filter_source = final_filter or ""
    final_target_period = infer_snapshot_period(final_filter_source, infer_final_minute(final_filter_source))
    executed_final_filter = rewrite_query_minute_refs(final_filter_source, infer_final_minute(final_filter_source), final_minute)
    executed_final_filter = rewrite_query_period_refs(executed_final_filter, final_target_period)
    async def _load() -> dict:
        timings: dict[str, float] = {}

        async def _timed(name: str, coro):
            start = time.perf_counter()
            try:
                return await coro
            finally:
                timings[name] = time.perf_counter() - start

        total_start = time.perf_counter()
        async with AsyncZeusClient(auth_token=_token) as async_client:
            base_count_task = async_client.count(sanitized_base) if sanitized_base else asyncio.sleep(0, result={"count": 0})
            full_rows_task = async_client.search_all(
                sanitized_base,
                max_games=max_games,
                include_count=True,
            )
            fetch_bot_config = getattr(async_client, "fetch_bot_config", None)
            if callable(fetch_bot_config):
                bot_config_task = fetch_bot_config()
            else:
                bot_config_task = asyncio.sleep(0, result={})
            base_count_info, full_bundle, bot_config = await asyncio.gather(
                _timed("zeus_count_seconds", base_count_task),
                _timed("lucy_search_seconds", full_rows_task),
                _timed("bot_config_seconds", bot_config_task),
            )
            if isinstance(full_bundle, tuple) and len(full_bundle) == 2:
                full_count_info, lucy_rows = full_bundle
            else:
                lucy_rows = list(full_bundle or [])
                full_count_info = len(lucy_rows)
            dedupe_start = time.perf_counter()
            lucy_rows = dedupe_rows_by_sport_event_id(lucy_rows)
            full_count_info = len(lucy_rows)
            timings["dedupe_seconds"] = time.perf_counter() - dedupe_start
            config = BacktestConfig(
                market_label=market_label,
                stake=float(stake),
                commission=float(commission),
                entry_minute=entry_minute,
                final_minute=final_minute,
                final_filter=executed_final_filter,
            )
            backtest_start = time.perf_counter()
            backtest = await run_backtest_async(async_client, lucy_rows, config)
            timings["backtest_snapshots_seconds"] = time.perf_counter() - backtest_start
            enrich_start = time.perf_counter()
            backtest["result_df"] = enrich_results_with_tournament_catalog(backtest["result_df"], bot_config)
            timings["tournament_enrichment_seconds"] = time.perf_counter() - enrich_start
            timings["total_load_seconds"] = time.perf_counter() - total_start
            return {
                "base_count_info": base_count_info,
                "count_info": {"count": full_count_info},
                "lucy_rows": lucy_rows,
                "backtest": backtest,
                "bot_config": bot_config,
                "timings": timings,
                "full_query": sanitized_base,
                "raw_base_query": base_query,
                "executed_base_query": executed_base_query,
                "raw_final_filter": final_filter,
                "executed_final_filter": executed_final_filter,
                "stripped_terms": stripped_base,
            }

    return _run_async(_load())


@st.cache_data(ttl=3600, show_spinner=False)
def load_timeline_cached(cache_key: str, _token: str, game_id: str, market_field: str) -> list[dict[str, object]]:
    async def _load() -> list[dict[str, object]]:
        async with AsyncZeusClient(auth_token=_token) as async_client:
            return await async_client.fetch_timeline(game_id, market_field=market_field)

    return _run_async(_load())


def parse_optional_date(text: str) -> str:
    return text.strip()






def render_optimization_tab(
    token: str,
    manual_report: dict | None,
    manual_inputs: dict | None,
) -> None:
    st.subheader("Otimização de estratégia")
    st.caption(
        "A base vem sempre do backtest manual. A busca roda em duas fases: varredura ampla e refinamento local. "
        "O ranking prioriza lucro, mas só aceita combinações que preservem volume."
    )

    manual_report = manual_report if isinstance(manual_report, dict) else {}
    manual_inputs = manual_inputs if isinstance(manual_inputs, dict) else {}
    lucy_rows = list(manual_report.get("lucy_rows") or [])
    backtest_bundle = manual_report.get("backtest") if isinstance(manual_report.get("backtest"), dict) else {}
    manual_metrics = backtest_bundle.get("metrics") if isinstance(backtest_bundle, dict) else {}
    manual_config = backtest_bundle.get("config")

    if not lucy_rows:
        st.info("Execute um backtest manual primeiro. A otimização usa exatamente os mesmos jogos carregados ali.")
        return

    base_query = str(manual_inputs.get("base_query") or "").strip()
    final_filter = str(manual_inputs.get("final_filter") or "").strip()
    market_label = str(manual_inputs.get("market_label") or getattr(manual_config, "market_label", "") or "")
    stake_value = float(getattr(manual_config, "stake", 100.0) or 100.0)
    commission_value = float(getattr(manual_config, "commission", 0.065) or 0.065)
    verification_filter_value = str(getattr(manual_config, "final_filter", "") or "")
    entry_default = int(manual_inputs.get("entry_minute") or getattr(manual_config, "entry_minute", 20) or 20)
    final_default = int(manual_inputs.get("final_minute") or getattr(manual_config, "final_minute", 500) or 500)
    base_bets = int((manual_metrics or {}).get("bets") or (manual_metrics or {}).get("matches") or len(lucy_rows) or 0)
    base_profit = float((manual_metrics or {}).get("total_profit", 0.0))
    base_roi = float((manual_metrics or {}).get("roi", 0.0))
    base_drawdown = float((manual_metrics or {}).get("max_drawdown", 0.0))
    base_win_rate = float((manual_metrics or {}).get("win_rate", 0.0))

    top_left, top_right = st.columns(2)
    with top_left:
        st.markdown("#### Base carregada")
        st.code(base_query or "(consulta não disponível)", language="text")
        if final_filter:
            st.code(final_filter, language="text")
    with top_right:
        st.markdown("#### Resumo do backtest manual")
        summary_cols = st.columns(2)
        with summary_cols[0]:
            st.metric("Jogos base", len(lucy_rows))
            st.metric("Mercado", market_label or "n/a")
        with summary_cols[1]:
            st.metric("Profit", f"R$ {base_profit:.2f}")
            st.metric("Bets", base_bets)

    with st.form("zeus_optimization_form", clear_on_submit=False):
        st.markdown("#### Busca ampla")
        col_left, col_right = st.columns(2)
        with col_left:
            entry_start = st.number_input(
                "Minuto de entrada - início",
                min_value=1,
                max_value=500,
                value=max(1, entry_default - 10),
                step=1,
            )
            entry_end = st.number_input(
                "Minuto de entrada - fim",
                min_value=1,
                max_value=500,
                value=min(500, entry_default + 10),
                step=1,
            )
            entry_step = st.number_input("Minuto de entrada - passo", min_value=1, max_value=100, value=5, step=1)
        with col_right:
            final_start = st.number_input(
                "Minuto de saída - início",
                min_value=1,
                max_value=90,
                value=max(1, min(90, final_default - 10)),
                step=1,
            )
            final_end = st.number_input(
                "Minuto de saída - fim",
                min_value=1,
                max_value=90,
                value=min(90, max(1, final_default + 10)),
                step=1,
            )
            final_step = st.number_input("Minuto de saída - passo", min_value=1, max_value=100, value=5, step=1)

        st.markdown("#### Filtro de volume")
        col_a, col_b = st.columns(2)
        with col_a:
            min_bets = st.number_input("Mínimo de bets", min_value=1, value=max(1, base_bets), step=1)
        with col_b:
            min_volume_ratio = st.number_input("Volume mínimo (%)", min_value=0.0, max_value=100.0, value=100.0, step=1.0)

        st.markdown("#### Refinamento local")
        ref_a, ref_b, ref_c = st.columns(3)
        with ref_a:
            refine_enabled = st.checkbox("Refinar melhores combinações", value=True)
        with ref_b:
            refine_top_n = st.number_input("Top N para refino", min_value=1, max_value=50, value=5, step=1)
        with ref_c:
            refine_radius = st.number_input("Raio do refino", min_value=0, max_value=20, value=2, step=1)

        ref_d, ref_e = st.columns(2)
        with ref_d:
            refine_step = st.number_input("Passo do refino", min_value=1, max_value=20, value=1, step=1)
        with ref_e:
            coarse_limit = st.number_input("Limite da fase ampla", min_value=1, max_value=10000, value=250, step=10)

        include_final_snapshot = st.checkbox("Incluir snapshot final (500)", value=bool(final_default == 500))

        submit = st.form_submit_button("Rodar otimização", width="stretch", type="primary")

    def _build_effective_final_filter(target_final_minute: int) -> str:
        if not verification_filter_value.strip():
            return ""
        source_minute = infer_final_minute(verification_filter_value)
        target_period = infer_snapshot_period(verification_filter_value, source_minute)
        return rewrite_query_period_refs(
            rewrite_query_minute_refs(verification_filter_value, source_minute, target_final_minute),
            target_period,
        )

    async def _evaluate_candidates(
        async_client: AsyncZeusClient,
        candidates: list[dict[str, int]],
        *,
        phase_label: str,
        phase_title: str,
        existing_keys: set[tuple[int, int]],
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        total = max(len(candidates), 1)
        progress = st.progress(0.0)
        status = st.empty()

        for index, combo in enumerate(candidates, start=1):
            key = (int(combo["entry_minute"]), int(combo["final_minute"]))
            if key in existing_keys:
                progress.progress(index / total)
                continue

            status.write(
                f"{phase_title} {index}/{len(candidates)} · entrada {combo['entry_minute']} · saída {combo['final_minute']}"
            )
            try:
                report = await run_backtest_async(
                    async_client,
                    list(lucy_rows),
                    BacktestConfig(
                        market_label=market_label,
                        stake=float(stake_value),
                        commission=float(commission_value),
                        entry_minute=int(combo["entry_minute"]),
                        final_minute=int(combo["final_minute"]),
                        final_filter=_build_effective_final_filter(int(combo["final_minute"])),
                    ),
                )
                metrics = report["metrics"]
                record = {
                    "phase": phase_label,
                    "entry_minute": int(combo["entry_minute"]),
                    "final_minute": int(combo["final_minute"]),
                    "bets": int(metrics.get("bets", 0) or 0),
                    "wins": int(metrics.get("wins", 0) or 0),
                    "win_rate": float(metrics.get("win_rate", 0.0) or 0.0),
                    "profit": float(metrics.get("total_profit", 0.0) or 0.0),
                    "roi": float(metrics.get("roi", 0.0) or 0.0),
                    "drawdown": float(metrics.get("max_drawdown", 0.0) or 0.0),
                    "avg_entry_odd": float(metrics.get("avg_entry_odd", 0.0) or 0.0),
                    "status": "ok",
                }
            except Exception as exc:
                record = {
                    "phase": phase_label,
                    "entry_minute": int(combo["entry_minute"]),
                    "final_minute": int(combo["final_minute"]),
                    "bets": 0,
                    "wins": 0,
                    "win_rate": 0.0,
                    "profit": 0.0,
                    "roi": 0.0,
                    "drawdown": 0.0,
                    "avg_entry_odd": 0.0,
                    "status": f"erro: {exc}",
                }

            records.append(record)
            existing_keys.add(key)
            progress.progress(index / total)

        progress.progress(1.0)
        return records

    if submit:
        if not token.strip():
            st.error("Entre com email/senha acima ou informe um token opcional antes de rodar a otimização.")
            return

        try:
            entry_minutes = build_int_range(int(entry_start), int(entry_end), int(entry_step))
            final_minutes = build_int_range(int(final_start), int(final_end), int(final_step))
            if include_final_snapshot and 500 not in final_minutes:
                final_minutes.append(500)
                final_minutes = sorted(set(final_minutes))
        except ValueError as exc:
            st.error(str(exc))
            return

        coarse_candidates = build_minute_candidate_grid(entry_minutes, final_minutes)
        if not coarse_candidates:
            st.error("Nenhuma combinação foi gerada. Verifique os intervalos dos minutos.")
            return
        if len(coarse_candidates) > int(coarse_limit):
            st.warning(
                f"Foram geradas {len(coarse_candidates)} combinações na fase ampla; vou executar apenas as primeiras {int(coarse_limit)}."
            )
            coarse_candidates = coarse_candidates[: int(coarse_limit)]

        async def _run_optimization_search() -> dict[str, object]:
            records_by_key: dict[tuple[int, int], dict[str, object]] = {}
            coarse_records: list[dict[str, object]] = []
            refined_records: list[dict[str, object]] = []
            existing_keys: set[tuple[int, int]] = set()

            async with AsyncZeusClient(auth_token=token) as async_client:
                async_client.config = replace(
                    async_client.config,
                    page_concurrency=1,
                    snapshot_concurrency=max(async_client.config.snapshot_concurrency, 32),
                    max_connections=max(async_client.config.max_connections, 64),
                    max_keepalive_connections=max(async_client.config.max_keepalive_connections, 32),
                )

                coarse_records = await _evaluate_candidates(
                    async_client,
                    coarse_candidates,
                    phase_label="fase_ampla",
                    phase_title="Fase ampla",
                    existing_keys=existing_keys,
                )
                for record in coarse_records:
                    key = (int(record["entry_minute"]), int(record["final_minute"]))
                    records_by_key[key] = record

                coarse_ranked = rank_strategy_candidates(
                    coarse_records,
                    base_bets=base_bets,
                    min_bets=int(min_bets),
                    min_volume_ratio=float(min_volume_ratio),
                )

                if refine_enabled and coarse_ranked:
                    seed_candidates = coarse_ranked[: int(refine_top_n)]
                    refinement_candidates = expand_minute_candidates_around(
                        seed_candidates,
                        entry_radius=int(refine_radius),
                        final_radius=int(refine_radius),
                        entry_step=int(refine_step),
                        final_step=int(refine_step),
                    )
                    refinement_candidates = [
                        candidate
                        for candidate in refinement_candidates
                        if (int(candidate["entry_minute"]), int(candidate["final_minute"])) not in records_by_key
                    ]
                    if refinement_candidates:
                        refined_records = await _evaluate_candidates(
                            async_client,
                            refinement_candidates,
                            phase_label="fase_refino",
                            phase_title="Fase de refino",
                            existing_keys=existing_keys,
                        )
                        for record in refined_records:
                            key = (int(record["entry_minute"]), int(record["final_minute"]))
                            records_by_key[key] = record

            combined_records = list(records_by_key.values())
            ranked_records = rank_strategy_candidates(
                combined_records,
                base_bets=base_bets,
                min_bets=int(min_bets),
                min_volume_ratio=float(min_volume_ratio),
            )
            ranking_df = pd.DataFrame(ranked_records)
            coarse_df = pd.DataFrame(coarse_records)
            refined_df = pd.DataFrame(refined_records)

            return {
                "ranking_df": ranking_df,
                "coarse_df": coarse_df,
                "refined_df": refined_df,
                "market_label": market_label,
                "base_query": base_query,
                "final_filter": final_filter,
                "manual_games": len(lucy_rows),
                "manual_profit": base_profit,
                "manual_bets": base_bets,
                "manual_roi": base_roi,
                "manual_drawdown": base_drawdown,
                "manual_win_rate": base_win_rate,
                "min_bets": int(min_bets),
                "min_volume_ratio": float(min_volume_ratio),
                "refine_enabled": bool(refine_enabled),
                "refine_top_n": int(refine_top_n),
                "refine_radius": int(refine_radius),
                "coarse_limit": int(coarse_limit),
                "coarse_candidates": len(coarse_candidates),
                "refinement_candidates": len(refined_records),
            }

        optimization_results = _run_async(_run_optimization_search())
        st.session_state["zeus_optimization_results"] = optimization_results
        st.success("Otimização concluída.")

    results = st.session_state.get("zeus_optimization_results")
    if not results:
        st.info("Configure a busca e rode a otimização para ver o ranking.")
        return

    ranking_df = results.get("ranking_df") if isinstance(results, dict) else None
    if not isinstance(ranking_df, pd.DataFrame) or ranking_df.empty:
        st.warning("A otimização não encontrou combinações válidas suficientes para ranquear.")
        return

    st.subheader("Resumo da busca")
    summary_cols = st.columns(4)
    with summary_cols[0]:
        st.metric("Base manual", int(results.get("manual_bets", 0) or 0))
        st.metric("Base jogos", int(results.get("manual_games", 0) or 0))
    with summary_cols[1]:
        st.metric("Profit manual", f"R$ {float(results.get('manual_profit', 0.0)):.2f}")
        st.metric("ROI manual", f"{float(results.get('manual_roi', 0.0)):.2f}%")
    with summary_cols[2]:
        st.metric("Volume mínimo", f"{float(results.get('min_volume_ratio', 0.0)):.0f}%")
        st.metric("Bets mínimos", int(results.get("min_bets", 0) or 0))
    with summary_cols[3]:
        st.metric("Fase ampla", int(results.get("coarse_candidates", 0) or 0))
        st.metric("Fase refino", int(results.get("refinement_candidates", 0) or 0))

    st.subheader("Ranking")
    available_columns = [
        column
        for column in [
            "phase",
            "entry_minute",
            "final_minute",
            "bets",
            "wins",
            "win_rate",
            "profit",
            "roi",
            "drawdown",
            "volume_ratio",
            "avg_entry_odd",
            "status",
        ]
        if column in ranking_df.columns
    ]
    st.dataframe(ranking_df[available_columns].head(50), width="stretch", hide_index=True)

    best_row = ranking_df.iloc[0].to_dict()
    best_profit = float(best_row.get("profit") or 0.0)
    best_roi = float(best_row.get("roi") or 0.0)
    best_bets = int(best_row.get("bets") or 0)
    best_volume_ratio = float(best_row.get("volume_ratio") or 0.0)
    st.subheader("Melhor combinação")
    st.write(
        f"Entrada {best_row.get('entry_minute')} | Saída {best_row.get('final_minute')} | "
        f"Profit R$ {best_profit:.2f} | ROI {best_roi:.2f}% | Bets {best_bets}"
    )
    st.caption(
        f"Volume preservado: {best_volume_ratio:.2f}% da base manual. "
        f"Delta de profit vs manual: R$ {(best_profit - float(results.get('manual_profit', 0.0))):.2f}"
    )

    with st.expander("Ver fases da busca"):
        col_phase1, col_phase2 = st.columns(2)
        with col_phase1:
            st.markdown("#### Fase ampla")
            st.write(f"{int(results.get('coarse_candidates', 0) or 0)} combinações avaliadas")
            coarse_df = results.get("coarse_df") if isinstance(results, dict) else None
            if isinstance(coarse_df, pd.DataFrame) and not coarse_df.empty:
                st.dataframe(coarse_df.head(20), width="stretch", hide_index=True)
        with col_phase2:
            st.markdown("#### Fase de refino")
            st.write(f"{int(results.get('refinement_candidates', 0) or 0)} combinações avaliadas")
            refined_df = results.get("refined_df") if isinstance(results, dict) else None
            if isinstance(refined_df, pd.DataFrame) and not refined_df.empty:
                st.dataframe(refined_df.head(20), width="stretch", hide_index=True)

    csv_data = ranking_df.to_csv(index=False).encode("utf-8")
    json_data = json.dumps(
        {
            "best": best_row,
            "ranking": ranking_df.to_dict(orient="records"),
            "summary": {
                "market_label": results.get("market_label"),
                "base_query": results.get("base_query"),
                "final_filter": results.get("final_filter"),
                "manual_games": results.get("manual_games"),
                "manual_bets": results.get("manual_bets"),
                "manual_profit": results.get("manual_profit"),
                "manual_roi": results.get("manual_roi"),
                "manual_drawdown": results.get("manual_drawdown"),
                "min_bets": results.get("min_bets"),
                "min_volume_ratio": results.get("min_volume_ratio"),
            },
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")
    st.download_button("Baixar ranking CSV", data=csv_data, file_name="zeus_optimization_ranking.csv", mime="text/csv")
    st.download_button("Baixar melhor estratégia JSON", data=json_data, file_name="zeus_best_strategy.json", mime="application/json")


def main() -> None:
    inject_styles()
    saved_session = load_saved_session()
    st.session_state.setdefault("zeus_auth_token", os.getenv("ZEUS_AUTH_TOKEN", "") or saved_session.get("auth_token", ""))
    st.session_state.setdefault("zeus_login_user", "")
    st.session_state.setdefault("zeus_login_status", "desconectado")
    st.session_state.setdefault("zeus_profile", {})
    st.session_state.setdefault("zeus_checked_session", False)
    commission_pct = 6.5
    commission_decimal = commission_pct / 100.0
    build_version = get_build_version()
    st.markdown(
        f"""
        <div class="hero">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap;">
                <div class="kicker">Zeus + Lucy</div>
                <div style="display:inline-flex;align-items:center;padding:0.45rem 0.8rem;border-radius:999px;background:rgba(15,23,42,0.06);color:#0f172a;font-size:0.78rem;font-weight:800;letter-spacing:0.08em;text-transform:uppercase;">
                    Commit {build_version}
                </div>
            </div>
            <h1 class="title">BACKTESTE <span>ZEUS / LUCY</span></h1>
            <p class="subtitle">
                Fa?a consultas no Zeus, pagine os jogos na Lucy, puxe snapshots por minuto e obtenha
                ROI, taxa de acerto, drawdown, curva de capital e leitura detalhada por jogo.
            </p>
            <div style="margin-top:0.6rem;color:#64748b;font-size:0.9rem;font-weight:600;">
                Deploy atual com a correção do snapshot final de `m500`.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configuração")
        st.caption("Você pode entrar com email/senha e eu extraio a sessão automaticamente.")
        token_in_session = st.session_state.get("zeus_auth_token", "").strip()
        if token_in_session and not st.session_state.get("zeus_checked_session"):
            with st.spinner("Validando sessão salva..."):
                try:
                    probe_client = ZeusClient(auth_token=token_in_session)
                    profile = probe_client.validate()
                except ZeusClientError:
                    st.session_state["zeus_auth_token"] = ""
                    st.session_state["zeus_login_status"] = "desconectado"
                    st.session_state["zeus_profile"] = {}
                    clear_saved_session()
                else:
                    st.session_state["zeus_login_status"] = "conectado"
                    st.session_state["zeus_profile"] = profile if isinstance(profile, dict) else {}
            st.session_state["zeus_checked_session"] = True

        status_text = "Conectado" if st.session_state.get("zeus_login_status") == "conectado" else "Desconectado"
        status_color = "#0f766e" if status_text == "Conectado" else "#b91c1c"
        st.markdown(
            f"""
            <div style="padding:0.8rem 0.95rem;border-radius:16px;border:1px solid rgba(15,23,42,0.08);background:#fff;margin-bottom:0.75rem;">
                <div style="font-size:0.78rem;font-weight:800;letter-spacing:0.08em;text-transform:uppercase;color:#64748b;">Status</div>
                <div style="font-size:1.05rem;font-weight:800;color:{status_color};margin-top:0.2rem;">{status_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        profile = st.session_state.get("zeus_profile") or {}
        if profile:
            display_name = profile.get("name") or profile.get("username") or profile.get("email") or "Sessão ativa"
            st.caption(f"Conta: {display_name}")

        manual_token = st.text_input(
            "Token opcional",
            value=st.session_state.get("zeus_auth_token", ""),
            type="password",
            help="Use apenas se você já tiver o bearer token em mãos.",
        )
        if manual_token.strip() and manual_token != st.session_state.get("zeus_auth_token", ""):
            st.session_state["zeus_auth_token"] = manual_token.strip()
            st.session_state["zeus_checked_session"] = False
            try:
                probe_client = ZeusClient(auth_token=manual_token.strip())
                profile = probe_client.validate()
            except ZeusClientError:
                st.session_state["zeus_login_status"] = "desconectado"
                st.session_state["zeus_profile"] = {}
            else:
                st.session_state["zeus_login_status"] = "conectado"
                st.session_state["zeus_profile"] = profile if isinstance(profile, dict) else {}
                save_token(manual_token.strip(), username=st.session_state.get("zeus_login_user") or None)
            st.session_state["zeus_checked_session"] = True

        with st.expander("Entrar com email e senha", expanded=not bool(st.session_state.get("zeus_auth_token", ""))):
            login_email = st.text_input(
                "Email",
                value=st.session_state.get("zeus_login_user", ""),
                key="zeus_login_email_input",
            )
            login_password = st.text_input(
                "Senha",
                type="password",
                key="zeus_login_password_input",
            )
            login_recap = st.text_input(
                "Recaptcha",
                value="risos",
                help="Normalmente o backend aceita este valor interno.",
            )
            remember_session = st.checkbox("Salvar sessão neste computador", value=True)
            login_pressed = st.button("Entrar e salvar sessão", width="stretch")
            if login_pressed:
                if not login_email.strip() or not login_password:
                    st.error("Informe email e senha.")
                else:
                    try:
                        temp_client = ZeusClient(auth_token="")
                        token_value = temp_client.login(
                            login_email.strip(),
                            login_password,
                            recaptcha=login_recap.strip() or "risos",
                        )
                        profile = temp_client.validate()
                    except ZeusClientError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["zeus_auth_token"] = token_value
                        st.session_state["zeus_login_user"] = login_email.strip()
                        st.session_state["zeus_login_status"] = "conectado"
                        st.session_state["zeus_profile"] = profile if isinstance(profile, dict) else {}
                        st.session_state["zeus_checked_session"] = True
                        if remember_session:
                            save_token(token_value, username=login_email.strip())
                        else:
                            clear_saved_session()
                        st.success("Sessão obtida com sucesso.")
                        st.rerun()

        if st.session_state.get("zeus_login_status") == "conectado" and st.button("Sair e apagar sessão", width="stretch"):
            st.session_state["zeus_auth_token"] = ""
            st.session_state["zeus_login_status"] = "desconectado"
            st.session_state["zeus_profile"] = {}
            st.session_state["zeus_checked_session"] = False
            clear_saved_session()
            st.rerun()

        token = st.session_state.get("zeus_auth_token", "").strip()
        strategy_query = st.text_area(
            "Consulta da estratégia",
            value=(
                '(m500.Minuto = 500) and (m500.NivelDados = "Gold") and (m500.DataJogo >= "2022-01-01") '
                'and (m20.Minuto = 20) and (m20.GolsTotal = 0) and (m20.CartaoVermelhoCasa = 0) '
                'and (m20.CartaoVermelhoVisitante = 0) and (m20.ChutesNoGolc1c2c3Total <= 2) '
                'and (m20.Pressao1Casa + m20.Pressao1Visitante <= 18) and (m20.Pressao2Casa + m20.Pressao2Visitante <= 22) '
                'and (m20.GraficoCasa + m20.GraficoVisitante <= 25) and (m20.BackUnder25FT between 1.55 and 2.10) '
                'and (m20.LayOver25FT >= 1.40) and (m35.Minuto = 35) and (m35.GolsTotal <= 1) '
                'and (m35.BackUnder25FT >= m20.BackUnder25FT)'
            ),
            height=220,
        )
        final_check = st.text_area(
            "Checagem final",
            value="(m500.GolsTotal <= 2)",
            help="Checagem pós-busca. Use aqui a validação extra da estratégia, sem alterar a amostra inicial.",
            height=90,
        )
        detected_market = detect_market_from_query(strategy_query)
        if detected_market:
            st.caption(f"Mercado detectado na consulta da estratégia: {detected_market}")
        market_options = list(MARKET_OPTIONS.keys())
        last_market_label = str(st.session_state.get("zeus_last_inputs", {}).get("market_label", "") or "").strip()
        if last_market_label not in market_options:
            last_market_label = detected_market if detected_market in market_options else market_options[0]
        if detected_market in market_options:
            st.session_state["zeus_market_label"] = detected_market
        elif st.session_state.get("zeus_market_label") not in market_options:
            st.session_state["zeus_market_label"] = last_market_label
        market_label = st.selectbox(
            "Mercado",
            market_options,
            key="zeus_market_label",
            help="Escolha o mercado que será usado no backtest. A consulta da estratégia pode sugerir um valor, mas você pode alterar.",
        )
        stake = st.number_input("Stake por entrada", min_value=1.0, value=100.0, step=10.0)
        commission_pct = st.number_input("Comissão (%)", min_value=0.0, max_value=20.0, value=6.5, step=0.5, format="%.1f")
        commission_decimal = float(commission_pct) / 100.0
        max_games = st.number_input("Máximo de jogos", min_value=1, value=1000, step=10)
        entry_override = st.text_input("Minuto de entrada", value="", help="Opcional. Ex.: 20, 89 ou 500.")
        final_override = st.text_input(
            "Minuto de saída",
            value="",
            placeholder="500",
            help="Opcional. Deixe vazio para usar o padrão técnico do app.",
        )
        entry_minute_default = infer_entry_minute(strategy_query)
        final_minute_default = infer_final_minute(final_check)
        entry_minute_override_error = None
        final_minute_override_error = None
        if entry_override.strip():
            try:
                entry_minute_default = int(entry_override)
            except ValueError:
                entry_minute_override_error = "Os minutos precisam ser números inteiros."
        if final_override.strip():
            try:
                final_minute_default = int(final_override)
            except ValueError:
                final_minute_override_error = "Os minutos precisam ser números inteiros."
        run = st.button("Consultar Zeus / Lucy", type="primary", width="stretch")

    if run:
        if not token.strip():
            st.error("Entre com email/senha acima ou informe um token opcional.")
            return
        if entry_minute_override_error or final_minute_override_error:
            st.error(entry_minute_override_error or final_minute_override_error)
            return
        base_query = strategy_query.strip()
        final_filter = final_check.strip()
        try:
            sanitized_base, _ = sanitize_query_terms(base_query)
            if not sanitized_base:
                st.error("A consulta da estratégia ficou vazia depois da limpeza. Ajuste os termos e tente novamente.")
                return
            entry_minute = entry_minute_default
            final_minute = final_minute_default
        except ValueError:
            st.error("Os minutos precisam ser números inteiros.")
            return

        with st.spinner("Consultando Zeus e Lucy..."):
            try:
                report = load_backtest_report(
                    hashlib.sha256(token.encode("utf-8")).hexdigest(),
                    token,
                    base_query,
                    final_filter,
                    market_label,
                    float(stake),
                    commission_decimal,
                    int(max_games),
                    entry_minute,
                    final_minute,
                )
            except ZeusClientError as exc:
                st.error(str(exc))
                return
            except Exception as exc:
                st.error(f"Erro inesperado: {exc}")
                return

        st.session_state["zeus_last_report"] = report
        st.session_state["zeus_last_inputs"] = {
            "token": token,
            "base_query": base_query,
            "final_filter": final_filter,
            "market_label": market_label,
        }
        st.session_state.setdefault("zeus_profit_period", "Mensal")
        st.rerun()

        render_metrics(report["backtest"]["metrics"])
        render_charts(report["backtest"]["result_df"], block_period="Mensal")

        st.subheader("Resultados")
        display_df = build_results_display_df(report["backtest"]["result_df"])
        st.dataframe(display_df, width="stretch", hide_index=True)

        csv = report["backtest"]["result_df"].to_csv(index=False).encode("utf-8")
        st.download_button(
            "Baixar CSV",
            data=csv,
            file_name="zeus_backtest.csv",
            mime="text/csv",
        )

        st.subheader("Detalhe do jogo")
        if not report["backtest"]["result_df"].empty:
            st.session_state.setdefault("zeus_detail_game_id", "")
            st.session_state.setdefault("zeus_detail_market_field", "")
            st.session_state.setdefault("zeus_detail_timeline", [])
            selected_label = st.selectbox(
                "Jogo",
                report["backtest"]["result_df"]["display_label"].tolist(),
            )
            selected_rows = report["backtest"]["result_df"].loc[report["backtest"]["result_df"]["display_label"].eq(selected_label)]
            if selected_rows.empty:
                st.warning("Não foi possível localizar o jogo selecionado.")
                return
            selected = selected_rows.iloc[0]
            st.write(f"{selected['display_label']} | entrada {selected['entry_minute']} | odd {selected['entry_odd']:.2f}")
            st.caption(f"Campo odd usado: {selected.get('odds_field_used') or selected.get('odds_field') or 'n/a'}")
            load_detail = st.button("Carregar detalhe do jogo", width="stretch")
            cache_hit = (
                st.session_state.get("zeus_detail_game_id") == str(selected["sport_event_id"])
                and st.session_state.get("zeus_detail_market_field") == str(selected["odds_field"])
            )
            if load_detail:
                with st.spinner("Carregando timeline do jogo..."):
                    st.session_state["zeus_detail_game_id"] = str(selected["sport_event_id"])
                    st.session_state["zeus_detail_market_field"] = str(selected["odds_field"])
                    st.session_state["zeus_detail_timeline"] = load_timeline_cached(
                        hashlib.sha256(token.encode("utf-8")).hexdigest(),
                        token,
                        str(selected["sport_event_id"]),
                        str(selected["odds_field"]),
                    )
            if cache_hit and st.session_state.get("zeus_detail_timeline"):
                render_timeline_frame(
                    st.session_state["zeus_detail_timeline"],
                    str(selected["odds_field"]),
                )
            else:
                st.caption("Selecione o jogo e clique em 'Carregar detalhe do jogo' para buscar a linha do tempo sob demanda.")

        st.caption("Consulta concluída com os endpoints internos do Zeus/Lucy.")

    tab_manual, tab_optimization = st.tabs(["Backtest manual", "Otimização"])

    with tab_manual:
        last_report = st.session_state.get("zeus_last_report")
        last_inputs = st.session_state.get("zeus_last_inputs") or {}
        if last_report and last_inputs:
            render_report_view(
                last_report,
                token=str(last_inputs.get("token", "")),
                market_label=str(last_inputs.get("market_label", "")),
                base_query=str(last_inputs.get("base_query", "")),
                final_filter=str(last_inputs.get("final_filter", "")),
            )
            st.caption("Consulta concluída com os endpoints internos do Zeus/Lucy.")
        else:
            st.info("Execute uma consulta no painel lateral para visualizar o backtest manual aqui.")

    with tab_optimization:
        render_optimization_tab(
            token=token,
            manual_report=st.session_state.get("zeus_last_report"),
            manual_inputs=st.session_state.get("zeus_last_inputs") or {},
        )

    return

    last_report = st.session_state.get("zeus_last_report")
    last_inputs = st.session_state.get("zeus_last_inputs") or {}
    if last_report and last_inputs and not run:
        render_report_view(
            last_report,
            token=str(last_inputs.get("token", "")),
            market_label=str(last_inputs.get("market_label", "")),
            base_query=str(last_inputs.get("base_query", "")),
            final_filter=str(last_inputs.get("final_filter", "")),
        )
        st.caption("Consulta concluída com os endpoints internos do Zeus/Lucy.")


if __name__ == "__main__":
    main()
