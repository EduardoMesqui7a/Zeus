from __future__ import annotations

from datetime import datetime
import json
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.backtest import (
    MARKET_OPTIONS,
    BacktestConfig,
    run_backtest,
)
from src.query_parser import (
    extract_minute_refs,
    infer_entry_minute,
)
from src.session_store import clear_saved_session, load_saved_session, save_token
from src.zeus_client import ZeusClient, ZeusClientError


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
        ("Drawdown", f"{metrics.get('max_drawdown', 0):.2f}"),
        ("Odd media", f"{metrics.get('avg_entry_odd', 0):.2f}"),
    ]
    for col, (label, value) in zip(cols, items, strict=False):
        with col:
            st.markdown(metric_card(label, value), unsafe_allow_html=True)


def render_charts(results_df: pd.DataFrame) -> None:
    if results_df.empty:
        return

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
        fig.add_trace(
            go.Scatter(
                x=results_df["match_datetime"],
                y=results_df["drawdown"],
                mode="lines",
                name="Drawdown",
                line=dict(color="#b91c1c", width=2),
                fill="tozeroy",
                opacity=0.3,
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
        hist = px.histogram(
            results_df,
            x="entry_odd",
            nbins=18,
            title="Distribuicao da odd de entrada",
        )
        hist.update_layout(height=420, margin=dict(l=0, r=0, t=40, b=0), template="plotly_white")
        st.plotly_chart(hist, use_container_width=True)


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
        query = st.text_area(
            "Query Zeus",
            value='(m500.Minuto = 500) and (m20.Minuto = 20) and (m20.GolsTotal = 0)',
            height=220,
        )
        market_label = st.selectbox("Mercado", list(MARKET_OPTIONS.keys()), index=0)
        stake = st.number_input("Stake por entrada", min_value=1.0, value=100.0, step=10.0)
        commission = st.number_input("Comissao", min_value=0.0, max_value=0.2, value=0.08, step=0.01, format="%.2f")
        max_pages = st.number_input("Max pages Lucy", min_value=1, value=25, step=1)
        max_games = st.number_input("Max jogos", min_value=1, value=250, step=10)
        entry_override = st.text_input("Entry minute override", value="", help="Opcional. Ex: 20, 89 ou 500.")
        final_override = st.text_input("Final minute override", value="500", help="Normalmente 500.")
        run = st.button("Consultar Zeus / Lucy", type="primary", use_container_width=True)

    client = ZeusClient(auth_token=token.strip())

    if run:
        if not token.strip():
            st.error("Entre com email/senha acima ou informe um token opcional.")
            return
        try:
            entry_minute = int(entry_override) if entry_override.strip() else infer_entry_minute(query)
            final_minute = int(final_override) if final_override.strip() else 500
        except ValueError:
            st.error("Os minutos precisam ser numeros inteiros.")
            return

        with st.spinner("Consultando Zeus e Lucy..."):
            try:
                count_info = client.count(query)
                lucy_rows = client.search_all(query, max_pages=int(max_pages), max_games=int(max_games))
                config = BacktestConfig(
                    market_label=market_label,
                    stake=float(stake),
                    commission=float(commission),
                    entry_minute=entry_minute,
                    final_minute=final_minute,
                )
                backtest = run_backtest(client, lucy_rows, config)
            except ZeusClientError as exc:
                st.error(str(exc))
                return
            except Exception as exc:
                st.error(f"Erro inesperado: {exc}")
                return

        st.markdown(
            f"""
            <div class="note-box">
                <strong>Query recebida:</strong> {query}<br/>
                <strong>Minutos detectados:</strong> {', '.join(map(str, extract_minute_refs(query))) or 'nenhum'}<br/>
                <strong>Entry minute usado:</strong> {backtest['config'].entry_minute}<br/>
                <strong>Total no Zeus:</strong> {count_info.get('count', 0)}<br/>
                <strong>Jogos carregados:</strong> {len(lucy_rows)}<br/>
                <strong>Mercado:</strong> {market_label}
            </div>
            """,
            unsafe_allow_html=True,
        )

        render_metrics(backtest["metrics"])
        render_charts(backtest["result_df"])

        st.subheader("Resultados")
        display_columns = [
            "display_label",
            "match_datetime",
            "entry_minute",
            "entry_odd",
            "profit",
            "stake_risked",
            "won",
            "result_text",
            "final_home_goals",
            "final_away_goals",
            "drawdown",
        ]
        display_df = backtest["result_df"][display_columns].copy()
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        csv = backtest["result_df"].to_csv(index=False).encode("utf-8")
        st.download_button(
            "Baixar CSV",
            data=csv,
            file_name="zeus_backtest.csv",
            mime="text/csv",
        )

        st.subheader("Detalhe do jogo")
        if not backtest["result_df"].empty:
            selected_label = st.selectbox(
                "Jogo",
                backtest["result_df"]["display_label"].tolist(),
            )
            selected = backtest["result_df"].set_index("display_label").loc[selected_label]
            if isinstance(selected, pd.DataFrame):
                selected = selected.iloc[0]
            st.write(f"{selected['display_label']} | entrada {selected['entry_minute']} | odd {selected['entry_odd']:.2f}")
            with st.spinner("Carregando timeline do jogo..."):
                render_game_timeline(client, str(selected["sport_event_id"]), selected["odds_field"])

        st.caption("Consulta concluida com os endpoints internos do Zeus/Lucy.")


if __name__ == "__main__":
    main()
