# Zeus Backtester

Dashboard em Streamlit para consultar o Zeus e a Lucy via API interna do FullTrader Sports, executar backtests e visualizar métricas como ROI, winrate, drawdown e curva de capital.

## O que este projeto faz

- consulta a busca do Zeus via `POST /legacy/zeus`
- pagina os resultados da Lucy via `POST /legacy/lucy`
- carrega snapshots por minuto do jogo via `GET /legacy/lucy/{id}?period=X&minute=Y`
- calcula métricas de backtest por mercado
- mostra curva de capital, drawdown, distribuição de odds e tabela detalhada

## Configuracao

Voce pode usar o app de duas formas:

1. entrar com `email` e `senha` na barra lateral, para o app buscar a sessao automaticamente;
2. colar um bearer token manualmente, se ja tiver um em maos.

Quando voce faz login pelo formulario, o Zeus pode salvar a sessao localmente neste computador para abrir ja conectado na proxima vez.

```powershell
$env:ZEUS_AUTH_TOKEN="Bearer xxxxx"
```

O token e opcional se voce usar o formulario de login interno do app.

## Executar

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Observacao

Este projeto usa apenas os endpoints internos do Zeus/Lucy. Ele nao depende de SofaScore nem de API-Football.
