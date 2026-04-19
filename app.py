from __future__ import annotations

import asyncio
import hashlib
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
from src.query_parser import (
    extract_minute_refs,
    infer_entry_minute,
    infer_final_minute,
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
            .note-box {
                border-radius: 18px;
                background: rgba(15,118,110,0.08);
                border: 1px solid rgba(15,118,110,0.15);
                padding: 0.9rem 1rem;
                color: #134e4a;
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


def render_metrics(metrics: dict) -> None:
    cols = st.columns(4)
    items = [
        ("Jogos", f"{metrics.get('matches', 0)}"),
        ("Bets", f"{metrics.get('bets', 0)}"),
        ("Winrate", f"{metrics.get('win_rate', 0):.2f}%"),
        ("ROI", f"{metrics.get('roi', 0):.2f}%"),
    ]
    for col, (label, value) in zip(cols, items, strict=False):
        with col:
            st.markdown(metric_card(label, value), unsafe_allow_html=True)

    cols = st.columns(4)
    items = [
        ("Lucro", f"{metrics.get('total_profit', 0):.2f}"),
        ("Stake", f"{metrics.get('total_risked', 0):.2f}"),
        ("Maior perda", f"{metrics.get('worst_trade', 0):.2f}"),
        ("Odd media", f"{metrics.get('avg_entry_odd', 0):.2f}"),
    ]
    for col, (label, value) in zip(cols, items, strict=False):
        with col:
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
        "Mensal": ("MS", "Mes"),
        "Trimestral": ("QS", "Trimestre"),
        "Semestral": ("6MS", "Semestre"),
        "Anual": ("YS", "Ano"),
    }
    freq, period_label = period_config.get(block_period, period_config["Mensal"])

    st.subheader("Curva e distribuicao")
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
            title="Distribuicao da odd de entrada",
        )
        counts, edges = pd.cut(entry_odd, bins=bins, retbins=True, include_lowest=True, duplicates="drop")
        if len(counts):
            centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]
            freq_counts = counts.value_counts(sort=False).reindex(counts.cat.categories, fill_value=0)
            hist.add_trace(
                go.Scatter(
                    x=centers[: len(freq_counts)],
                    y=freq_counts.values,
                    mode="lines+markers",
                    name="Quantidade de jogos",
                    line=dict(color="#f59e0b", width=2),
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
            help="Escolha como agrupar o lucro: por mes, trimestre, semestre ou ano.",
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
                    yaxis="y2",
                )
            )
            fig_blocks.update_layout(
                height=380,
                template="plotly_white",
                margin=dict(l=0, r=0, t=30, b=0),
                barmode="relative",
                legend=dict(orientation="h"),
                xaxis_title=period_label,
                yaxis_title="Lucro no bloco",
                yaxis2=dict(overlaying="y", side="right", title="Acumulado"),
            )
            st.subheader(f"Profit por {period_label.lower()}")
            st.plotly_chart(fig_blocks, use_container_width=True)
            grouped = grouped.rename(columns={"match_datetime": period_label})
            st.dataframe(grouped, use_container_width=True, hide_index=True)


def render_game_timeline(client: ZeusClient, game_id: str, market_field: str) -> None:
    timeline = client.fetch_timeline(game_id, market_field=market_field)
    if not timeline:
        st.info("Nao foi possivel montar a linha do tempo deste jogo.")
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
        st.info("Nao foi possivel montar a linha do tempo deste jogo.")
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
    st.markdown(
        f"""
        <div class="note-box">
            <strong>Strategy query:</strong> {base_query}<br/>
            <strong>Final check:</strong> {final_filter or 'nenhum'}<br/>
            <strong>Query completa:</strong> {report['full_query']}<br/>
            <strong>Final check seguro:</strong> {report.get('sanitized_final_filter') or 'nenhum'}<br/>
            <strong>Minutos detectados:</strong> {', '.join(map(str, extract_minute_refs(report['full_query']))) or 'nenhum'}<br/>
            <strong>Entry minute usado:</strong> {report['backtest']['config'].entry_minute}<br/>
            <strong>Final minute usado:</strong> {report['backtest']['config'].final_minute}<br/>
            <strong>Universo base:</strong> {report['base_count_info'].get('count', 0)}<br/>
            <strong>Universo final:</strong> {report['count_info'].get('count', 0)}<br/>
            <strong>Conversão:</strong> {((report['count_info'].get('count', 0) / report['base_count_info'].get('count', 1)) * 100.0) if report['base_count_info'].get('count', 0) else 0:.2f}%<br/>
            <strong>Jogos carregados:</strong> {len(report['lucy_rows'])}<br/>
            <strong>Mercado:</strong> {market_label}
        </div>
        """,
        unsafe_allow_html=True,
    )

    if report.get("stripped_terms"):
        st.warning(
            "Removi termos com informacao futura da strategy query antes de pesquisar. "
            "Isso evita look-ahead bias e explica por que o winrate nao deve ser 100% so por causa do resultado final."
        )

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
            st.warning("Nao foi possivel localizar o jogo selecionado.")
            return
        selected = selected_rows.iloc[0]
        st.write(f"{selected['display_label']} | entrada {selected['entry_minute']} | odd {selected['entry_odd']:.2f}")
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
            st.caption("Selecione o jogo e clique em 'Carregar detalhe do jogo' para buscar a timeline sob demanda.")


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


def main() -> None:
    inject_styles()
    saved_session = load_saved_session()
    st.session_state.setdefault("zeus_auth_token", os.getenv("ZEUS_AUTH_TOKEN", "") or saved_session.get("auth_token", ""))
    st.session_state.setdefault("zeus_login_user", "")
    st.session_state.setdefault("zeus_login_status", "desconectado")
    st.session_state.setdefault("zeus_profile", {})
    st.session_state.setdefault("zeus_checked_session", False)
    st.markdown(
        """
        <div class="hero">
            <div class="kicker">Zeus + Lucy</div>
            <h1 class="title">Backtester direto nos endpoints internos</h1>
            <p class="subtitle">
                Rode uma query Zeus, pagina os jogos na Lucy, puxe snapshots por minuto e receba
                ROI, winrate, drawdown, curva de capital e leitura detalhada por jogo.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Configuracao")
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
            "Strategy query",
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
            "Final check",
            value="(m500.GolsTotal <= 2)",
            help="Regra de liquidacao. Use aqui a condicao final da estrategia.",
            height=90,
        )
        detected_market = detect_market_from_query(strategy_query)
        if detected_market:
            st.caption(f"Mercado detectado na strategy query: {detected_market}")
        market_label = st.selectbox(
            "Mercado manual",
            list(MARKET_OPTIONS.keys()),
            index=0,
            disabled=bool(detected_market),
            help="Usado apenas se a strategy query nao permitir detectar o mercado automaticamente.",
        )
        market_label = detected_market or market_label
        stake = st.number_input("Stake por entrada", min_value=1.0, value=100.0, step=10.0)
        commission = st.number_input("Comissao", min_value=0.0, max_value=0.2, value=0.08, step=0.01, format="%.2f")
        max_pages = st.number_input("Max pages Lucy", min_value=1, value=25, step=1)
        max_games = st.number_input("Max jogos", min_value=1, value=250, step=10)
        entry_override = st.text_input("Entry minute override", value="", help="Opcional. Ex: 20, 89 ou 500.")
        final_override = st.text_input(
            "Final minute override",
            value="",
            placeholder="500",
            help="Opcional. Deixe vazio para usar o padrao tecnico do app.",
        )
        run = st.button("Consultar Zeus / Lucy", type="primary", use_container_width=True)

    if run:
        if not token.strip():
            st.error("Entre com email/senha acima ou informe um token opcional.")
            return
        base_query = strategy_query.strip()
        final_filter = final_check.strip()
        try:
            sanitized_base, _ = sanitize_query_terms(base_query)
            if not sanitized_base:
                st.error("A strategy query ficou vazia depois da limpeza. Ajuste os termos e tente de novo.")
                return
            full_query = base_query
            sanitized_final, _ = sanitize_query_terms(final_filter)
            full_query = sanitized_base
            entry_minute = int(entry_override) if entry_override.strip() else infer_entry_minute(full_query)
            final_minute = int(final_override) if final_override.strip() else infer_final_minute(final_filter)
        except ValueError:
            st.error("Os minutos precisam ser numeros inteiros.")
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
                    float(commission),
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

        st.markdown(
            f"""
            <div class="note-box">
                <strong>Strategy query:</strong> {base_query}<br/>
                <strong>Final check:</strong> {final_filter or 'nenhum'}<br/>
                <strong>Query completa:</strong> {report['full_query']}<br/>
                <strong>Final check seguro:</strong> {report.get('sanitized_final_filter') or 'nenhum'}<br/>
                <strong>Minutos detectados:</strong> {', '.join(map(str, extract_minute_refs(report['full_query']))) or 'nenhum'}<br/>
                <strong>Entry minute usado:</strong> {report['backtest']['config'].entry_minute}<br/>
                <strong>Final minute usado:</strong> {report['backtest']['config'].final_minute}<br/>
                <strong>Universo base:</strong> {report['base_count_info'].get('count', 0)}<br/>
                <strong>Universo final:</strong> {report['count_info'].get('count', 0)}<br/>
                <strong>Conversão:</strong> {((report['count_info'].get('count', 0) / report['base_count_info'].get('count', 1)) * 100.0) if report['base_count_info'].get('count', 0) else 0:.2f}%<br/>
                <strong>Jogos carregados:</strong> {len(report['lucy_rows'])}<br/>
                <strong>Mercado:</strong> {market_label}
            </div>
            """,
            unsafe_allow_html=True,
        )

        if report.get("stripped_terms"):
            st.warning(
                "Removi termos com informacao futura da strategy query antes de pesquisar. "
                "Isso evita look-ahead bias e explica por que o winrate não deve ser 100% só por causa do resultado final."
            )

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
                st.warning("Nao foi possivel localizar o jogo selecionado.")
                return
            selected = selected_rows.iloc[0]
            st.write(f"{selected['display_label']} | entrada {selected['entry_minute']} | odd {selected['entry_odd']:.2f}")
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
                st.caption("Selecione o jogo e clique em 'Carregar detalhe do jogo' para buscar a timeline sob demanda.")

        st.caption("Consulta concluida com os endpoints internos do Zeus/Lucy.")

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
        st.caption("Consulta concluida com os endpoints internos do Zeus/Lucy.")


if __name__ == "__main__":
    main()
