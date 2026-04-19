from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime
import json
import os
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.backtest import (
    MARKET_OPTIONS,
    BacktestConfig,
    run_backtest_async,
)
from src.optimization import (
    add_date_filter,
    build_int_range,
    build_snapshot_filter_groups,
    candidate_product_with_snapshot_filters,
    expand_query_variants_with_filter_groups,
    infer_snapshot_field_keys,
    SNAPSHOT_FIELD_PRESETS,
    sort_optimization_records,
    split_query_variants,
)
from src.query_parser import (
    extract_minute_refs,
    infer_entry_minute,
)
from src.session_store import clear_saved_session, load_saved_session, save_token
from src.zeus_client import AsyncZeusClient, ZeusClient, ZeusClientError


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
    safe_terms: list[str] = []
    stripped_terms: list[str] = []
    for term in split_query_terms(query):
        m500_fields = re.findall(r"(?i)\bm500\.([A-Za-z0-9_]+)", term)
        if m500_fields and any(field not in SAFE_M500_FIELDS for field in m500_fields):
            stripped_terms.append(term)
            continue
        safe_terms.append(term)
    safe_query = " and ".join(safe_terms).strip()
    return safe_query, stripped_terms


def detect_market_from_query(query: str) -> str | None:
    lowered = (query or "").lower()
    matched: list[str] = []
    for label, market in MARKET_OPTIONS.items():
        for field in market.get("odds_fields") or []:
            if re.search(rf"(?i)\b{re.escape(field)}\b", query or ""):
                matched.append(label)
                break
    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]
    for label in MARKET_OPTIONS.keys():
        if label in matched:
            return label
    return matched[0]


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
    cols = st.columns(4)
    items = [
        ("Jogos", f"{metrics.get('matches', 0)}"),
        ("Entradas", f"{metrics.get('bets', 0)}"),
        ("Taxa de acerto", f"{metrics.get('win_rate', 0):.2f}%"),
        ("ROI", f"{metrics.get('roi', 0):.2f}%"),
    ]
    for col, (label, value) in zip(cols, items, strict=False):
        with col:
            st.markdown(metric_card(label, value), unsafe_allow_html=True)

    cols = st.columns(4)
    total_profit = float(metrics.get("total_profit", 0))
    profit_class = "metric-profit-positive" if total_profit >= 0 else "metric-profit-negative"
    items = [
        ("Lucro", f"{format_brl(total_profit)}", profit_class),
        ("Stake", f"{format_brl(float(metrics.get('total_risked', 0)))}"),
        ("Maior perda", f"{format_brl(float(metrics.get('worst_curve', metrics.get('worst_trade', 0))))}"),
        ("Odd média", f"{metrics.get('avg_entry_odd', 0):.2f}"),
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
            "won": "Won",
            "profit": "Profit",
            "result_text": "Resultado",
            "drawdown": "Drawdown",
        }
    )


def render_charts(results_df: pd.DataFrame, block_period: str = "Mensal") -> None:
    if results_df.empty:
        return

    period_config = {
        "Mensal": ("MS", "Mês"),
        "Trimestral": ("QS", "Trimestre"),
        "Semestral": ("6MS", "Semestre"),
        "Anual": ("YS", "Ano"),
    }
    freq, period_label = period_config.get(block_period, period_config["Mensal"])

    st.subheader("Profit Acumulado")
    left, right = st.columns((1.4, 1))

    with left:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=results_df["match_datetime"],
                y=results_df["cumulative_profit"],
                mode="lines",
                name="Equity",
                line=dict(color="#0f766e", width=3),
            )
        )
        fig.update_layout(
            height=420,
            margin=dict(l=0, r=0, t=20, b=0),
            template="plotly_white",
            legend=dict(orientation="h"),
            yaxis_title="Resultado acumulado",
        )
        st.plotly_chart(fig, use_container_width=True)

    with right:
        entry_odd = results_df["entry_odd"].dropna()
        bins = min(18, max(6, int(len(entry_odd) ** 0.5))) if len(entry_odd) else 18
        hist = px.histogram(
            results_df,
            x="entry_odd",
            nbins=bins,
            title="Faixa de Odd x Jogos",
        )
        counts, edges = pd.cut(entry_odd, bins=bins, retbins=True, include_lowest=True, duplicates="drop")
        if len(counts):
            centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]
            freq_counts = counts.value_counts(sort=False).reindex(counts.cat.categories, fill_value=0)
            won_entry_odd = results_df.loc[results_df["won"].fillna(False), "entry_odd"].dropna()
            won_cut = pd.cut(won_entry_odd, bins=edges, include_lowest=True, duplicates="drop")
            won_counts = won_cut.value_counts(sort=False).reindex(counts.cat.categories, fill_value=0)
            hist.add_trace(
                go.Scatter(
                    x=centers[: len(freq_counts)],
                    y=freq_counts.values,
                    mode="lines+markers",
                    name="Jogos total",
                    line=dict(color="#f59e0b", width=2),
                    yaxis="y2",
                )
            )
            hist.add_trace(
                go.Scatter(
                    x=centers[: len(won_counts)],
                    y=won_counts.values,
                    mode="lines+markers",
                    name="Jogos vencidos",
                    line=dict(color="#16a34a", width=2),
                    yaxis="y2",
                )
            )
            hist.update_layout(
                yaxis2=dict(overlaying="y", side="right", title="Jogos por faixa"),
            )
        hist.update_layout(height=420, margin=dict(l=0, r=0, t=40, b=0), template="plotly_white")
        st.plotly_chart(hist, use_container_width=True)

    with st.container():
        period_choice = st.selectbox(
            "Agrupar profit",
            options=["Mensal", "Trimestral", "Semestral", "Anual"],
            index=["Mensal", "Trimestral", "Semestral", "Anual"].index(block_period)
            if block_period in {"Mensal", "Trimestral", "Semestral", "Anual"}
            else 0,
            key="zeus_profit_period",
            help="Escolha como agrupar o lucro: por mês, trimestre, semestre ou ano.",
        )
        freq, period_label = period_config.get(period_choice, period_config["Mensal"])

        block_df = results_df.copy()
        if getattr(block_df["match_datetime"].dt, "tz", None) is not None:
            block_df["match_datetime"] = block_df["match_datetime"].dt.tz_convert(None)
        grouped = (
            block_df.groupby(pd.Grouper(key="match_datetime", freq=freq))
            .agg(profit=("profit", "sum"), bets=("profit", "size"))
            .reset_index()
            .sort_values("match_datetime")
        )
        if not grouped.empty:
            fig_blocks = go.Figure()
            fig_blocks.add_trace(
                go.Bar(
                    x=grouped["match_datetime"],
                    y=grouped["profit"],
                    name="Lucro do bloco",
                    marker_color=["#0f766e" if value >= 0 else "#b91c1c" for value in grouped["profit"]],
                )
            )
            fig_blocks.add_trace(
                go.Scatter(
                    x=grouped["match_datetime"],
                    y=grouped["profit"].cumsum(),
                    mode="lines+markers",
                    name="Acumulado por bloco",
                    line=dict(color="#1d4ed8", width=2),
                )
            )
            fig_blocks.update_layout(
                height=380,
                template="plotly_white",
                margin=dict(l=0, r=0, t=30, b=0),
                barmode="relative",
                legend=dict(orientation="h"),
                xaxis_title=period_label,
                yaxis_title="Lucro / acumulado",
            )
            st.subheader(f"Profit por {period_label.lower()}")
            st.plotly_chart(fig_blocks, use_container_width=True)
            grouped = grouped.rename(columns={"match_datetime": period_label}).copy()
            if "profit" in grouped.columns:
                grouped["profit"] = grouped["profit"].map(format_brl)
            st.dataframe(grouped, use_container_width=True, hide_index=True)


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
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df, use_container_width=True, hide_index=True)


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
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_report_view(report: dict, token: str, market_label: str, base_query: str, final_filter: str) -> None:

    render_metrics(report["backtest"]["metrics"])
    render_charts(report["backtest"]["result_df"], block_period=st.session_state.get("zeus_profit_period", "Mensal"))

    st.subheader("Resultados")
    display_df = build_results_display_df(report["backtest"]["result_df"])
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    csv = report["backtest"]["result_df"].to_csv(index=False).encode("utf-8")
    st.download_button(
        "Baixar CSV",
        data=csv,
        file_name="zeus_backtest.csv",
        mime="text/csv",
        key="zeus_download_csv",
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
        load_detail = st.button("Carregar detalhe do jogo", use_container_width=True, key="zeus_load_detail")
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
        max_pages: int | None = None,
        max_games: int | None = None,
        include_count: bool = False,
    ):
        key = (query, int(max_pages or 0), int(max_games or 0), bool(include_count))
        if key in self._search_cache:
            return self._search_cache[key]
        task = self._search_inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._client.search_all(
                    query,
                    max_pages=max_pages,
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
    max_pages: int,
    max_games: int,
    entry_minute: int,
    final_minute: int,
) -> dict:
    sanitized_base, stripped_base = sanitize_query_terms(base_query)
    sanitized_final, stripped_final = sanitize_query_terms(final_filter or "")
    full_query = sanitized_base
    if sanitized_final:
        full_query = f"({sanitized_base}) and ({sanitized_final})" if sanitized_base else sanitized_final
    stripped_terms = stripped_base
    stripped_terms.extend(stripped_final)
    async def _load() -> dict:
        async with AsyncZeusClient(auth_token=_token) as async_client:
            base_count_task = async_client.count(sanitized_base) if sanitized_base else asyncio.sleep(0, result={"count": 0})
            full_rows_task = async_client.search_all(
                full_query,
                max_pages=max_pages,
                max_games=max_games,
                include_count=True,
            )
            base_count_info, full_bundle = await asyncio.gather(base_count_task, full_rows_task)
            if isinstance(full_bundle, tuple) and len(full_bundle) == 2:
                full_count_info, lucy_rows = full_bundle
            else:
                lucy_rows = list(full_bundle or [])
                full_count_info = len(lucy_rows)
            config = BacktestConfig(
                market_label=market_label,
                stake=float(stake),
                commission=float(commission),
                entry_minute=entry_minute,
                final_minute=final_minute,
            )
            backtest = await run_backtest_async(async_client, lucy_rows, config)
            return {
                "base_count_info": base_count_info,
                "count_info": {"count": full_count_info},
                "lucy_rows": lucy_rows,
                "backtest": backtest,
                "full_query": full_query,
                "stripped_terms": stripped_terms,
                "sanitized_final_filter": sanitized_final,
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
    strategy_query: str,
    final_check: str,
    market_label: str,
    stake: float,
    commission_decimal: float,
    max_pages: int,
    max_games: int,
    entry_minute_default: int,
    final_minute_default: int,
) -> None:
    st.subheader("Otimiza??o")
    st.caption("Vamos varrer par?metros, comparar combina??es e ranquear as melhores estrat?gias.")

    field_meta_map = {str(field["key"]): field for field in SNAPSHOT_FIELD_PRESETS}
    default_snapshot_fields = infer_snapshot_field_keys(strategy_query, SNAPSHOT_FIELD_PRESETS)

    with st.form("zeus_optimization_form", clear_on_submit=False):
        st.markdown("#### Entradas principais")
        col_left, col_right = st.columns(2)

        with col_left:
            strategy_variants_text = st.text_area(
                "Consulta da estrat?gia",
                value=strategy_query,
                height=210,
                help="Uma consulta por bloco. Separe variantes com --- ou com linhas em branco.",
            )
            final_variants_text = st.text_area(
                "Verifica??o final",
                value=final_check,
                height=120,
                help="Use uma regra por bloco. Se houver s? uma, ela vale para todas as combina??es.",
            )
            market_label_opt = st.selectbox(
                "Mercado",
                list(MARKET_OPTIONS.keys()),
                index=list(MARKET_OPTIONS.keys()).index(market_label) if market_label in MARKET_OPTIONS else 0,
            )
            min_bets = st.number_input("M?nimo de bets", min_value=1, value=20, step=1)
            combo_limit = st.number_input("Limite de combina??es", min_value=1, value=200, step=10)
            top_n_validation = st.number_input("Top N para validar", min_value=1, value=20, step=1)

        with col_right:
            entry_start = st.number_input(
                "Minuto de entrada - in?cio", min_value=1, max_value=500, value=max(1, entry_minute_default - 10), step=1
            )
            entry_end = st.number_input(
                "Minuto de entrada - fim", min_value=1, max_value=500, value=min(500, entry_minute_default + 10), step=1
            )
            entry_step = st.number_input("Minuto de entrada - passo", min_value=1, max_value=100, value=1, step=1)
            final_start = st.number_input(
                "Minuto de sa?da - in?cio", min_value=1, max_value=500, value=max(1, final_minute_default - 10), step=1
            )
            final_end = st.number_input(
                "Minuto de sa?da - fim", min_value=1, max_value=500, value=min(500, final_minute_default + 10), step=1
            )
            final_step = st.number_input("Minuto de sa?da - passo", min_value=1, max_value=100, value=1, step=1)
            train_start_text = st.text_input("Treino - in?cio (YYYY-MM-DD)", value="", placeholder="2022-01-01")
            train_end_text = st.text_input("Treino - fim (YYYY-MM-DD)", value="", placeholder="2022-12-31")
            validation_enabled = st.checkbox("Validar melhores combina??es fora da amostra", value=True)
            validation_start_text = st.text_input("Valida??o - in?cio (YYYY-MM-DD)", value="", placeholder="2023-01-01")
            validation_end_text = st.text_input("Valida??o - fim (YYYY-MM-DD)", value="", placeholder="2023-12-31")
            search_max_pages = st.number_input("M?ximo de p?ginas na Lucy", min_value=1, value=max_pages, step=1)
            search_max_games = st.number_input("M?ximo de jogos por combina??o", min_value=1, value=max_games, step=10)

        snapshot_field_configs: list[dict[str, object]] = []
        with st.expander("Filtros extras do snapshot", expanded=bool(default_snapshot_fields)):
            st.caption("Marque s? os campos que voc? quer testar. Quanto mais filtros, maior o n?mero de combina??es.")
            selected_snapshot_fields = st.multiselect(
                "Campos extras",
                options=[str(field["key"]) for field in SNAPSHOT_FIELD_PRESETS],
                default=default_snapshot_fields,
                format_func=lambda key: field_meta_map[str(key)]["label"],
                key="zeus_optimization_snapshot_fields",
            )
            if selected_snapshot_fields:
                for field_key in selected_snapshot_fields:
                    meta = field_meta_map[str(field_key)]
                    with st.container(border=True):
                        st.markdown(f"**{meta['label']}**")
                        st.caption(f"`{meta['expression']}` | operador fixo `{meta['operator']}`")
                        row_start, row_end, row_step = st.columns(3)

                        def _widget_value(value: float, precision: int) -> float | int:
                            return int(value) if precision == 0 else float(value)

                        with row_start:
                            start_value = st.number_input(
                                "In?cio",
                                min_value=0 if int(meta["precision"]) == 0 else 0.0,
                                value=_widget_value(float(meta["default_start"]), int(meta["precision"])),
                                step=_widget_value(float(meta["default_step"]), int(meta["precision"])),
                                key=f"zeus_opt_{field_key}_start",
                            )
                        with row_end:
                            end_value = st.number_input(
                                "Fim",
                                min_value=0 if int(meta["precision"]) == 0 else 0.0,
                                value=_widget_value(float(meta["default_end"]), int(meta["precision"])),
                                step=_widget_value(float(meta["default_step"]), int(meta["precision"])),
                                key=f"zeus_opt_{field_key}_end",
                            )
                        with row_step:
                            step_value = st.number_input(
                                "Passo",
                                min_value=0.01 if int(meta["precision"]) else 1,
                                value=_widget_value(float(meta["default_step"]), int(meta["precision"])),
                                step=_widget_value(float(meta["default_step"]), int(meta["precision"])),
                                key=f"zeus_opt_{field_key}_step",
                            )
                        snapshot_field_configs.append(
                            {
                                "enabled": True,
                                "key": str(meta["key"]),
                                "label": str(meta["label"]),
                                "expression": str(meta["expression"]),
                                "operator": str(meta["operator"]),
                                "start": float(start_value),
                                "end": float(end_value),
                                "step": float(step_value),
                                "precision": int(meta["precision"]),
                            }
                        )
            else:
                st.info("Nenhum filtro extra selecionado. A otimiza??o vai usar apenas a consulta e os minutos.")

        submit = st.form_submit_button("Rodar otimiza??o", use_container_width=True, type="primary")

    if submit:
        if not token.strip():
            st.error("Entre com email/senha acima ou informe um token opcional antes de rodar a otimiza??o.")
            return
        try:
            entry_minutes = build_int_range(int(entry_start), int(entry_end), int(entry_step))
            final_minutes = build_int_range(int(final_start), int(final_end), int(final_step))
        except ValueError as exc:
            st.error(str(exc))
            return

        base_queries = split_query_variants(strategy_variants_text)
        final_filters = split_query_variants(final_variants_text)
        snapshot_filter_groups = build_snapshot_filter_groups(snapshot_field_configs)
        base_variants = expand_query_variants_with_filter_groups(base_queries, snapshot_filter_groups)
        combos = candidate_product_with_snapshot_filters(base_variants, final_filters, entry_minutes, final_minutes)
        total_combos = len(combos)
        if total_combos == 0:
            st.error("Nenhuma combina??o foi gerada. Verifique os intervalos e as consultas candidatas.")
            return
        if total_combos > int(combo_limit):
            st.warning(f"Foram geradas {total_combos} combina??es; vou executar apenas as primeiras {int(combo_limit)}.")
            combos = combos[: int(combo_limit)]

        def _join_query_parts(left: str, right: str) -> str:
            left = (left or "").strip()
            right = (right or "").strip()
            if left and right:
                return f"({left}) and ({right})"
            return left or right

        async def _run_optimization_search() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
            rows_cache: dict[str, list[dict[str, object]]] = {}
            train_records: list[dict[str, object]] = []
            validation_records: list[dict[str, object]] = []
            progress = st.progress(0)
            status = st.empty()

            async with AsyncZeusClient(auth_token=token) as raw_client:
                raw_client.config = replace(
                    raw_client.config,
                    page_concurrency=max(raw_client.config.page_concurrency, 16),
                    snapshot_concurrency=max(raw_client.config.snapshot_concurrency, 32),
                    max_connections=max(raw_client.config.max_connections, 64),
                    max_keepalive_connections=max(raw_client.config.max_keepalive_connections, 32),
                )
                client = CachedAsyncZeusClient(raw_client)

                async def _load_rows(full_query: str) -> list[dict[str, object]]:
                    if full_query not in rows_cache:
                        rows = await client.search_all(
                            full_query,
                            max_pages=int(search_max_pages),
                            max_games=int(search_max_games),
                            include_count=False,
                        )
                        rows_cache[full_query] = list(rows or [])
                    return rows_cache[full_query]

                for index, combo in enumerate(combos, start=1):
                    status.write(f"Executando {index}/{len(combos)} ? entrada {combo['entry_minute']} / sa?da {combo['final_minute']}")
                    sanitized_base, _ = sanitize_query_terms(str(combo["augmented_base_query"]))
                    sanitized_final, _ = sanitize_query_terms(str(combo["final_filter"]))
                    train_query = add_date_filter(sanitized_base, train_start_text, train_end_text)
                    train_filter = add_date_filter(sanitized_final, train_start_text, train_end_text)
                    full_train_query = _join_query_parts(train_query, train_filter)
                    try:
                        train_rows = await _load_rows(full_train_query)
                        train_report = await run_backtest_async(
                            client,
                            train_rows,
                            BacktestConfig(
                                market_label=market_label_opt,
                                stake=float(stake),
                                commission=float(commission_decimal),
                                entry_minute=int(combo["entry_minute"]),
                                final_minute=int(combo["final_minute"]),
                            ),
                        )
                        metrics = train_report["metrics"]
                        train_records.append(
                            {
                                "combo_key": f"{combo['augmented_base_query']}|{combo['final_filter']}|{combo['entry_minute']}|{combo['final_minute']}",
                                "snapshot_filters": " AND ".join(combo.get("snapshot_filters") or []),
                                "augmented_base_query": combo["augmented_base_query"],
                                "final_filter": combo["final_filter"],
                                "executed_train_query": full_train_query,
                                "entry_minute": int(combo["entry_minute"]),
                                "final_minute": int(combo["final_minute"]),
                                "train_matches": metrics.get("matches", 0),
                                "train_bets": metrics.get("bets", 0),
                                "train_wins": metrics.get("wins", 0),
                                "train_win_rate": metrics.get("win_rate", 0.0),
                                "train_roi": metrics.get("roi", 0.0),
                                "train_profit": metrics.get("total_profit", 0.0),
                                "train_drawdown": metrics.get("worst_curve", 0.0),
                                "train_avg_entry_odd": metrics.get("avg_entry_odd", 0.0),
                                "train_error": "",
                            }
                        )
                    except Exception as exc:
                        train_records.append(
                            {
                                "combo_key": f"{combo['augmented_base_query']}|{combo['final_filter']}|{combo['entry_minute']}|{combo['final_minute']}",
                                "snapshot_filters": " AND ".join(combo.get("snapshot_filters") or []),
                                "augmented_base_query": combo["augmented_base_query"],
                                "final_filter": combo["final_filter"],
                                "executed_train_query": full_train_query,
                                "entry_minute": int(combo["entry_minute"]),
                                "final_minute": int(combo["final_minute"]),
                                "train_matches": 0,
                                "train_bets": 0,
                                "train_wins": 0,
                                "train_win_rate": 0.0,
                                "train_roi": 0.0,
                                "train_profit": 0.0,
                                "train_drawdown": 0.0,
                                "train_avg_entry_odd": 0.0,
                                "train_error": str(exc),
                            }
                        )
                    progress.progress(index / len(combos))

                train_df = pd.DataFrame(train_records)
                valid_train_df = train_df.loc[train_df["train_bets"].fillna(0).astype(float) >= float(min_bets)].copy()
                ordered_train_records = sort_optimization_records(valid_train_df.to_dict("records"), validation_available=False)
                ranking_df = pd.DataFrame(ordered_train_records)

                if validation_enabled and not ranking_df.empty:
                    validation_target = min(int(top_n_validation), len(ranking_df))
                    for index, record in enumerate(ranking_df.head(validation_target).to_dict("records"), start=1):
                        status.write(f"Validando {index}/{validation_target} ? entrada {record['entry_minute']} / sa?da {record['final_minute']}")
                        sanitized_base, _ = sanitize_query_terms(str(record["augmented_base_query"]))
                        sanitized_final, _ = sanitize_query_terms(str(record["final_filter"]))
                        validation_query = add_date_filter(sanitized_base, validation_start_text, validation_end_text)
                        validation_filter = add_date_filter(sanitized_final, validation_start_text, validation_end_text)
                        full_validation_query = _join_query_parts(validation_query, validation_filter)
                        try:
                            validation_rows = await _load_rows(full_validation_query)
                            validation_report = await run_backtest_async(
                                client,
                                validation_rows,
                                BacktestConfig(
                                    market_label=market_label_opt,
                                    stake=float(stake),
                                    commission=float(commission_decimal),
                                    entry_minute=int(record["entry_minute"]),
                                    final_minute=int(record["final_minute"]),
                                ),
                            )
                            metrics = validation_report["metrics"]
                            validation_records.append(
                                {
                                    "combo_key": record["combo_key"],
                                    "executed_validation_query": full_validation_query,
                                    "validation_matches": metrics.get("matches", 0),
                                    "validation_bets": metrics.get("bets", 0),
                                    "validation_wins": metrics.get("wins", 0),
                                    "validation_win_rate": metrics.get("win_rate", 0.0),
                                    "validation_roi": metrics.get("roi", 0.0),
                                    "validation_profit": metrics.get("total_profit", 0.0),
                                    "validation_drawdown": metrics.get("worst_curve", 0.0),
                                    "validation_avg_entry_odd": metrics.get("avg_entry_odd", 0.0),
                                    "validation_error": "",
                                }
                            )
                        except Exception as exc:
                            validation_records.append(
                                {
                                    "combo_key": record["combo_key"],
                                    "executed_validation_query": full_validation_query,
                                    "validation_matches": 0,
                                    "validation_bets": 0,
                                    "validation_wins": 0,
                                    "validation_win_rate": 0.0,
                                    "validation_roi": 0.0,
                                    "validation_profit": 0.0,
                                    "validation_drawdown": 0.0,
                                    "validation_avg_entry_odd": 0.0,
                                    "validation_error": str(exc),
                                }
                            )

                validation_df = pd.DataFrame(validation_records)
                if not validation_df.empty:
                    ranking_df = ranking_df.merge(validation_df, on="combo_key", how="left")
                    ranking_df = pd.DataFrame(sort_optimization_records(ranking_df.to_dict("records"), validation_available=True))

                return train_df, ranking_df, validation_df

        train_df, ranking_df, validation_df = _run_async(_run_optimization_search())
        st.session_state["zeus_optimization_results"] = {
            "train_df": train_df,
            "ranking_df": ranking_df,
            "validation_df": validation_df,
            "market_label": market_label_opt,
            "combo_limit": int(combo_limit),
            "validation_enabled": validation_enabled,
            "train_start_text": train_start_text,
            "train_end_text": train_end_text,
            "validation_start_text": validation_start_text,
            "validation_end_text": validation_end_text,
        }
        st.success("Otimiza??o conclu?da.")

    results = st.session_state.get("zeus_optimization_results")
    if not results:
        st.info("Configure os par?metros acima e rode a otimiza??o para ver o ranking.")
        return

    ranking_df = results.get("ranking_df") if isinstance(results, dict) else None
    train_df = results.get("train_df") if isinstance(results, dict) else None
    validation_df = results.get("validation_df") if isinstance(results, dict) else None
    if not isinstance(ranking_df, pd.DataFrame) or ranking_df.empty:
        st.warning("A otimiza??o n?o encontrou combina??es v?lidas suficientes para ranquear.")
        return

    st.subheader("Ranking")
    display_columns = [
        "entry_minute",
        "final_minute",
        "snapshot_filters",
        "train_bets",
        "train_win_rate",
        "train_roi",
        "train_profit",
        "train_drawdown",
        "validation_bets",
        "validation_win_rate",
        "validation_roi",
        "validation_profit",
        "validation_drawdown",
        "executed_train_query",
        "executed_validation_query",
    ]
    available_columns = [column for column in display_columns if column in ranking_df.columns]
    display_df = ranking_df[available_columns].copy()
    st.dataframe(display_df.head(50), use_container_width=True, hide_index=True)

    best_row = ranking_df.iloc[0].to_dict()
    st.subheader("Melhor combina??o")
    st.write(
        f"Entrada {best_row.get('entry_minute')} | Sa?da {best_row.get('final_minute')} | "
        f"ROI treino {float(best_row.get('train_roi') or 0):.2f}%"
        + (
            f" | ROI valida??o {float(best_row.get('validation_roi') or 0):.2f}%"
            if 'validation_roi' in best_row
            else ""
        )
    )
    if best_row.get("snapshot_filters"):
        st.caption(f"Filtros extras: {best_row.get('snapshot_filters')}")
    with st.expander("Ver queries executadas"):
        st.write("Treino")
        st.code(str(best_row.get("executed_train_query") or ""), language="text")
        if best_row.get("executed_validation_query"):
            st.write("Valida??o")
            st.code(str(best_row.get("executed_validation_query") or ""), language="text")

    csv_data = ranking_df.to_csv(index=False).encode("utf-8")
    json_data = json.dumps(
        {
            "best": best_row,
            "ranking": ranking_df.to_dict(orient="records"),
            "summary": {
                "market_label": results.get("market_label"),
                "combo_limit": results.get("combo_limit"),
                "validation_enabled": results.get("validation_enabled"),
            },
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")
    st.download_button("Baixar ranking CSV", data=csv_data, file_name="zeus_optimization_ranking.csv", mime="text/csv")
    st.download_button("Baixar melhor estrat?gia JSON", data=json_data, file_name="zeus_best_strategy.json", mime="application/json")


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
    st.markdown(
        """
        <div class="hero">
            <div class="kicker">Zeus + Lucy</div>
            <h1 class="title">BACKTESTE <span>ZEUS / LUCY</span></h1>
            <p class="subtitle">
                Fa?a consultas no Zeus, pagine os jogos na Lucy, puxe snapshots por minuto e obtenha
                ROI, taxa de acerto, drawdown, curva de capital e leitura detalhada por jogo.
            </p>
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
            login_pressed = st.button("Entrar e salvar sessão", use_container_width=True)
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

        if st.session_state.get("zeus_login_status") == "conectado" and st.button("Sair e apagar sessão", use_container_width=True):
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
            "Verificação final",
            value="(m500.GolsTotal <= 2)",
            help="Regra de liquidação. Use aqui a condição final da estratégia.",
            height=90,
        )
        detected_market = detect_market_from_query(strategy_query)
        if detected_market:
            st.caption(f"Mercado detectado na consulta da estratégia: {detected_market}")
        market_options = list(MARKET_OPTIONS.keys())
        default_market_index = market_options.index(detected_market) if detected_market in market_options else 0
        market_label = st.selectbox(
            "Mercado",
            market_options,
            index=default_market_index,
            help="Escolha o mercado que será usado no backtest. A consulta da estratégia pode sugerir um valor, mas você pode alterar.",
        )
        stake = st.number_input("Stake por entrada", min_value=1.0, value=100.0, step=10.0)
        commission_pct = st.number_input("Comissão (%)", min_value=0.0, max_value=20.0, value=6.5, step=0.5, format="%.1f")
        commission_decimal = float(commission_pct) / 100.0
        max_pages = st.number_input("Máximo de páginas na Lucy", min_value=1, value=200, step=1)
        max_games = st.number_input("Máximo de jogos", min_value=1, value=1000, step=10)
        entry_override = st.text_input("Minuto de entrada", value="", help="Opcional. Ex.: 20, 89 ou 500.")
        final_override = st.text_input(
            "Minuto de saída",
            value="",
            placeholder="500",
            help="Opcional. Deixe vazio para usar o padrão técnico do app.",
        )
        entry_minute_default = infer_entry_minute(strategy_query)
        final_minute_default = 500
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
        run = st.button("Consultar Zeus / Lucy", type="primary", use_container_width=True)

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
            full_query = base_query
            sanitized_final, _ = sanitize_query_terms(final_filter)
            full_query = sanitized_base
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
                    int(max_pages),
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
        st.dataframe(display_df, use_container_width=True, hide_index=True)

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
            load_detail = st.button("Carregar detalhe do jogo", use_container_width=True)
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
            strategy_query=strategy_query,
            final_check=final_check,
            market_label=market_label,
            stake=float(stake),
            commission_decimal=commission_decimal,
            max_pages=int(max_pages),
            max_games=int(max_games),
            entry_minute_default=int(entry_minute_default),
            final_minute_default=int(final_minute_default),
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
