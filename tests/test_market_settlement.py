import unittest

from src.backtest import (
    MARKET_OPTIONS,
    BacktestConfig,
    _apply_back_profit,
    _apply_lay_profit,
    _build_row,
    _build_row_async,
    _finalize_backtest,
)


class FakeClient:
    def __init__(self, entry_snapshot: dict, final_snapshot: dict) -> None:
        self.entry_snapshot = entry_snapshot
        self.final_snapshot = final_snapshot

    def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict:
        return self.entry_snapshot

    def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict:
        return self.final_snapshot


class AsyncFakeClient:
    def __init__(self, entry_snapshot: dict, final_snapshot: dict) -> None:
        self.entry_snapshot = entry_snapshot
        self.final_snapshot = final_snapshot

    async def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict:
        return self.entry_snapshot

    async def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict:
        return self.final_snapshot


class MarketSettlementTests(unittest.TestCase):
    def test_all_markets_keep_bet_win_aligned_with_profit(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m35.Minuto = 35)",
        }
        all_odds_fields = sorted({field for market in MARKET_OPTIONS.values() for field in market.get("odds_fields") or []})
        entry_snapshot = {field: 2.0 for field in all_odds_fields}
        final_snapshot = {field: 2.0 for field in all_odds_fields}
        final_snapshot["GolsCasa"] = 3
        final_snapshot["GolsVisitante"] = 1

        client = FakeClient(entry_snapshot, final_snapshot)

        for label, market in MARKET_OPTIONS.items():
            config = BacktestConfig(market_label=label, stake=100.0, commission=0.0, entry_minute=35, final_minute=80)
            result = _build_row(client, base_row, config, market)
            self.assertEqual(result["won"], result["profit"] >= 0, msg=label)

            summary = _finalize_backtest([base_row], [result], config)
            self.assertEqual(summary["metrics"]["wins"], int(result["won"]), msg=label)
            self.assertAlmostEqual(summary["metrics"]["win_rate"], 100.0 if result["won"] else 0.0, places=7, msg=label)

    def test_lay_correct_score_uses_cashout_before_settlement(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m79.Minuto = 79)",
        }
        market = MARKET_OPTIONS["Lay Correct Score 0-3"]
        entry_snapshot = {"Lay0x3FT": 80.0}
        final_snapshot = {"Lay0x3FT": 4.0, "GolsCasa": 0, "GolsVisitante": 3}
        client = FakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(market_label="Lay Correct Score 0-3", stake=100.0, commission=0.0, entry_minute=79, final_minute=79)

        result = _build_row(client, base_row, config, market)
        self.assertIsNone(result["market_hit"])
        self.assertAlmostEqual(result["profit"], 100.0 / 79.0 * (1.0 - 80.0 / 4.0), places=7)
        self.assertFalse(result["won"])

    def test_back_under_25_uses_settlement_when_threshold_is_crossed(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m79.Minuto = 79)",
        }
        market = MARKET_OPTIONS["Back Under 2.5 FT"]
        entry_snapshot = {"BackUnder25FT": 2.0}
        final_snapshot = {"BackUnder25FT": 2.0, "GolsCasa": 3, "GolsVisitante": 1}
        client = FakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(market_label="Back Under 2.5 FT", stake=100.0, commission=0.0, entry_minute=79, final_minute=79)

        result = _build_row(client, base_row, config, market)
        self.assertEqual(result["market_hit"], False)
        self.assertAlmostEqual(result["profit"], -100.0, places=7)
        self.assertFalse(result["won"])

    def test_final_filter_excludes_row_before_market_settlement(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m79.Minuto = 79)",
        }
        market = MARKET_OPTIONS["Back Under 2.5 FT"]
        entry_snapshot = {"BackUnder25FT": 2.0}
        final_snapshot = {"BackUnder25FT": 2.0, "GolsCasa": 3, "GolsVisitante": 1}
        client = FakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(
            market_label="Back Under 2.5 FT",
            stake=100.0,
            commission=0.0,
            entry_minute=79,
            final_minute=79,
            final_filter="GolsCasa <= 1",
        )

        result = _build_row(client, base_row, config, market)
        self.assertEqual(result["status"], "fora da verificacao final")
        summary = _finalize_backtest([base_row], [result], config)
        self.assertEqual(summary["metrics"]["bets"], 0)
        self.assertEqual(summary["metrics"]["wins"], 0)

    def test_async_backtest_matches_sync_backtest_for_all_markets(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m79.Minuto = 79)",
        }
        all_odds_fields = sorted({field for market in MARKET_OPTIONS.values() for field in market.get("odds_fields") or []})
        entry_snapshot = {field: 2.0 for field in all_odds_fields}
        final_snapshot = {field: 2.0 for field in all_odds_fields}
        final_snapshot["GolsCasa"] = 1
        final_snapshot["GolsVisitante"] = 0

        for label, market in MARKET_OPTIONS.items():
            config = BacktestConfig(market_label=label, stake=100.0, commission=0.0, entry_minute=79, final_minute=79)
            sync_result = _build_row(FakeClient(entry_snapshot, final_snapshot), base_row, config, market)
            async_result = __import__("asyncio").run(
                _build_row_async(AsyncFakeClient(entry_snapshot, final_snapshot), base_row, config, market)
            )
            for key in ("won", "market_hit", "profit", "stake_risked", "exit_minute", "entry_odd", "exit_odd"):
                self.assertEqual(sync_result.get(key), async_result.get(key), msg=f"{label} / {key}")


if __name__ == "__main__":
    unittest.main()
