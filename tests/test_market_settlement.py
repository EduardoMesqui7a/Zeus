import unittest

from src.backtest import (
    MARKET_OPTIONS,
    BacktestConfig,
    _apply_back_profit,
    _apply_lay_profit,
    _extract_tournament_label,
    _build_row,
    _build_row_async,
    _finalize_backtest,
)


class FakeClient:
    def __init__(self, entry_snapshot: dict, final_snapshot: dict) -> None:
        self.entry_snapshot = entry_snapshot
        self.final_snapshot = final_snapshot
        self.snapshot_calls: list[tuple[str, int, int]] = []
        self.final_snapshot_calls: list[tuple[str, int]] = []

    def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict:
        self.snapshot_calls.append((game_id, minute, period))
        if len(self.snapshot_calls) == 1:
            return self.entry_snapshot
        return self.final_snapshot

    def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict:
        self.final_snapshot_calls.append((game_id, final_minute))
        return self.final_snapshot


class AsyncFakeClient:
    def __init__(self, entry_snapshot: dict, final_snapshot: dict) -> None:
        self.entry_snapshot = entry_snapshot
        self.final_snapshot = final_snapshot
        self.snapshot_calls: list[tuple[str, int, int]] = []
        self.final_snapshot_calls: list[tuple[str, int]] = []

    async def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict:
        self.snapshot_calls.append((game_id, minute, period))
        if len(self.snapshot_calls) == 1:
            return self.entry_snapshot
        return self.final_snapshot

    async def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict:
        self.final_snapshot_calls.append((game_id, final_minute))
        return self.final_snapshot


class FallbackFakeClient:
    def __init__(self, entry_snapshot: dict, final_snapshots: dict[int, dict]) -> None:
        self.entry_snapshot = entry_snapshot
        self.final_snapshots = final_snapshots
        self.snapshot_calls: list[tuple[str, int, int]] = []
        self.final_snapshot_calls: list[tuple[str, int]] = []

    def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict:
        self.snapshot_calls.append((game_id, minute, period))
        if len(self.snapshot_calls) == 1:
            return self.entry_snapshot
        return self.final_snapshots.get(minute, {})

    def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict:
        self.final_snapshot_calls.append((game_id, final_minute))
        return self.final_snapshots.get(final_minute, {})


class AsyncFallbackFakeClient:
    def __init__(self, entry_snapshot: dict, final_snapshots: dict[int, dict]) -> None:
        self.entry_snapshot = entry_snapshot
        self.final_snapshots = final_snapshots
        self.snapshot_calls: list[tuple[str, int, int]] = []
        self.final_snapshot_calls: list[tuple[str, int]] = []

    async def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict:
        self.snapshot_calls.append((game_id, minute, period))
        if len(self.snapshot_calls) == 1:
            return self.entry_snapshot
        return self.final_snapshots.get(minute, {})

    async def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict:
        self.final_snapshot_calls.append((game_id, final_minute))
        return self.final_snapshots.get(final_minute, {})


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

    def test_final_filter_does_not_exclude_row_before_market_settlement(self) -> None:
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
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["final_verification_hit"])
        summary = _finalize_backtest([base_row], [result], config)
        self.assertEqual(summary["metrics"]["strategy_matches"], 1)
        self.assertEqual(summary["metrics"]["matches"], 1)
        self.assertEqual(summary["metrics"]["bets"], 1)
        self.assertEqual(len(summary["result_df"]), 1)
        self.assertFalse(summary["result_df"].iloc[0]["final_verification_hit"])

    def test_final_filter_uses_requested_final_minute_for_all_markets(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy-market",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m20.Minuto = 20)",
        }
        entry_snapshot = {"BackUnder05HT": 2.5, "LayGoleadaCasaFT": 40.0}
        final_snapshots = {
            45: {"BackUnder05HT": 2.5, "LayGoleadaCasaFT": 40.0, "GolsCasa": 1, "GolsVisitante": 0},
            90: {"BackUnder05HT": 2.5, "LayGoleadaCasaFT": 40.0, "GolsCasa": 5, "GolsVisitante": 3},
        }
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=20,
            final_minute=500,
            final_filter="((m500.GolsCasa < 4) OR (m500.GolsCasa <= m500.GolsVisitante))",
        )

        under_client = FallbackFakeClient(entry_snapshot, final_snapshots)
        under_result = _build_row(under_client, base_row, config, MARKET_OPTIONS["Back Under 0.5 HT"])
        self.assertEqual(under_client.final_snapshot_calls, [("sr:match:dummy-market", 500)])
        self.assertFalse(under_result["final_verification_hit"])

        lay_client = FallbackFakeClient(entry_snapshot, final_snapshots)
        lay_result = _build_row(lay_client, base_row, config, MARKET_OPTIONS["Lay Goleada Casa FT"])
        self.assertEqual(lay_client.final_snapshot_calls, [("sr:match:dummy-market", 500)])
        self.assertFalse(lay_result["final_verification_hit"])
        self.assertGreaterEqual(lay_result["final_home_goals"], 5)

    def test_final_filter_period_two_uses_explicit_second_half_minute(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m30.Minuto = 30)",
        }
        market = MARKET_OPTIONS["Back Under 0.5 HT"]
        entry_snapshot = {"BackUnder05HT": 2.0}
        final_snapshot = {
            "BackUnder05HT": 1.5,
            "Periodo": 1,
            "GolsTotal": 0,
            "GolsCasa": 0,
            "GolsVisitante": 0,
        }
        client = FallbackFakeClient(entry_snapshot, {44: {}, 45: final_snapshot})
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=30,
            final_minute=44,
            final_filter="(m44.Periodo = 1) and (m44.GolsTotal = 0)",
        )

        result = _build_row(client, base_row, config, market)
        self.assertEqual(client.snapshot_calls[-2:], [("sr:match:dummy", 44, 1), ("sr:match:dummy", 45, 1)])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["won"])

    def test_extract_tournament_label_uses_season_name_instead_of_tier(self) -> None:
        snapshot = {
            "NomeTemporada": "liga mx, clausura 2026",
            "tournament_round_number": 8,
            "NivelDados": "Gold",
        }
        tournament_id, tournament_name, tournament_label = _extract_tournament_label(snapshot)
        self.assertEqual(tournament_id, "")
        self.assertEqual(tournament_name, "liga mx, clausura 2026")
        self.assertEqual(tournament_label, "liga mx, clausura 2026")

    def test_back_under_half_ht_uses_cashout_when_market_is_still_open(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:cashout",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m30.Minuto = 30)",
        }
        market = MARKET_OPTIONS["Back Under 0.5 HT"]
        entry_snapshot = {"BackUnder05HT": 2.9}
        final_snapshot = {
            "Minuto": 40,
            "BackUnder05HT": 1.02,
            "Periodo": 1,
            "GolsTotal": 0,
            "GolsCasa": 0,
            "GolsVisitante": 0,
        }
        client = FakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=30,
            final_minute=40,
            final_filter="(m40.Minuto = 40) and (m40.Periodo = 1) and (m40.GolsTotal = 0)",
        )

        result = _build_row(client, base_row, config, market)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["market_hit"])
        self.assertGreater(result["profit"], 0)
        self.assertTrue(result["won"])

    def test_back_under_half_ht_settles_when_market_is_already_closed(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:settlement",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m30.Minuto = 30)",
        }
        market = MARKET_OPTIONS["Back Under 0.5 HT"]
        entry_snapshot = {"BackUnder05HT": 2.9}
        final_snapshot = {
            "Minuto": 40,
            "BackUnder05HT": 1.02,
            "Periodo": 1,
            "GolsTotal": 1,
            "GolsCasa": 1,
            "GolsVisitante": 0,
        }
        client = FakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=30,
            final_minute=40,
            final_filter="(m40.Minuto = 40) and (m40.Periodo = 1)",
        )

        result = _build_row(client, base_row, config, market)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result.get("won", False))
        self.assertLessEqual(result.get("profit", 0), 0)

    def test_back_under_half_ht_uses_final_snapshot_at_settlement_minute(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:final-snapshot",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m45.Minuto = 45)",
        }
        market = MARKET_OPTIONS["Back Under 0.5 HT"]
        entry_snapshot = {"BackUnder05HT": 2.9}
        final_snapshot = {
            "Periodo": 1,
            "GolsTotal": 0,
            "GolsCasa": 0,
            "GolsVisitante": 0,
        }
        client = FakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=45,
            final_minute=45,
            final_filter="(m45.Periodo = 1) and (m45.GolsTotal = 0)",
        )

        result = _build_row(client, base_row, config, market)
        self.assertEqual(client.final_snapshot_calls, [])
        self.assertIn(("sr:match:final-snapshot", 45, 1), client.snapshot_calls)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["won"])
        self.assertIsNone(result["exit_odd"])

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

    def test_async_final_filter_period_two_uses_explicit_second_half_minute(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:dummy",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m30.Minuto = 30)",
        }
        market = MARKET_OPTIONS["Back Under 0.5 HT"]
        entry_snapshot = {"BackUnder05HT": 2.0}
        final_snapshot = {
            "BackUnder05HT": 1.5,
            "Periodo": 2,
            "GolsTotal": 0,
            "GolsCasa": 0,
            "GolsVisitante": 0,
        }
        client = AsyncFallbackFakeClient(entry_snapshot, {44: {}, 45: final_snapshot})
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=30,
            final_minute=44,
            final_filter="(m44.Periodo = 2) and (m44.GolsTotal = 0)",
        )

        result = __import__("asyncio").run(_build_row_async(client, base_row, config, market))
        self.assertIn(("sr:match:dummy", 44, 2), client.snapshot_calls)
        self.assertIn(("sr:match:dummy", 45, 2), client.snapshot_calls)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["won"])

    def test_async_final_filter_does_not_exclude_row_before_market_settlement(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:async-filter",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m79.Minuto = 79)",
        }
        market = MARKET_OPTIONS["Back Under 2.5 FT"]
        entry_snapshot = {"BackUnder25FT": 2.0}
        final_snapshot = {"BackUnder25FT": 2.0, "GolsCasa": 3, "GolsVisitante": 1}
        client = AsyncFakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(
            market_label="Back Under 2.5 FT",
            stake=100.0,
            commission=0.0,
            entry_minute=79,
            final_minute=79,
            final_filter="GolsCasa <= 1",
        )

        result = __import__("asyncio").run(_build_row_async(client, base_row, config, market))
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["final_verification_hit"])
        summary = _finalize_backtest([base_row], [result], config)
        self.assertEqual(summary["metrics"]["strategy_matches"], 1)
        self.assertEqual(summary["metrics"]["matches"], 1)
        self.assertEqual(summary["metrics"]["bets"], 1)
        self.assertEqual(len(summary["result_df"]), 1)
        self.assertFalse(summary["result_df"].iloc[0]["final_verification_hit"])

    def test_async_final_filter_uses_requested_final_minute_for_all_markets(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:async-dummy-market",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m20.Minuto = 20)",
        }
        entry_snapshot = {"BackUnder05HT": 2.5, "LayGoleadaCasaFT": 40.0}
        final_snapshots = {
            45: {"BackUnder05HT": 2.5, "LayGoleadaCasaFT": 40.0, "GolsCasa": 1, "GolsVisitante": 0},
            90: {"BackUnder05HT": 2.5, "LayGoleadaCasaFT": 40.0, "GolsCasa": 5, "GolsVisitante": 3},
        }
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=20,
            final_minute=500,
            final_filter="((m500.GolsCasa < 4) OR (m500.GolsCasa <= m500.GolsVisitante))",
        )

        under_client = AsyncFallbackFakeClient(entry_snapshot, final_snapshots)
        under_result = __import__("asyncio").run(_build_row_async(under_client, base_row, config, MARKET_OPTIONS["Back Under 0.5 HT"]))
        self.assertEqual(under_client.final_snapshot_calls, [("sr:match:async-dummy-market", 500)])
        self.assertFalse(under_result["final_verification_hit"])

        lay_client = AsyncFallbackFakeClient(entry_snapshot, final_snapshots)
        lay_result = __import__("asyncio").run(_build_row_async(lay_client, base_row, config, MARKET_OPTIONS["Lay Goleada Casa FT"]))
        self.assertEqual(lay_client.final_snapshot_calls, [("sr:match:async-dummy-market", 500)])
        self.assertFalse(lay_result["final_verification_hit"])

    def test_async_back_under_half_ht_uses_final_snapshot_at_settlement_minute(self) -> None:
        base_row = {
            "sport_event_id": "sr:match:async-final-snapshot",
            "NomeCasa": "home",
            "NomeVisitante": "away",
            "DataJogo": "2022-02-13T10:15:00Z",
            "NivelDados": "Gold",
            "query": "(m45.Minuto = 45)",
        }
        market = MARKET_OPTIONS["Back Under 0.5 HT"]
        entry_snapshot = {"BackUnder05HT": 2.9}
        final_snapshot = {
            "Periodo": 1,
            "GolsTotal": 0,
            "GolsCasa": 0,
            "GolsVisitante": 0,
        }
        client = AsyncFakeClient(entry_snapshot, final_snapshot)
        config = BacktestConfig(
            market_label="Back Under 0.5 HT",
            stake=100.0,
            commission=0.0,
            entry_minute=45,
            final_minute=45,
            final_filter="(m45.Periodo = 1) and (m45.GolsTotal = 0)",
        )

        result = __import__("asyncio").run(_build_row_async(client, base_row, config, market))
        self.assertEqual(client.final_snapshot_calls, [])
        self.assertIn(("sr:match:async-final-snapshot", 45, 1), client.snapshot_calls)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["won"])
        self.assertIsNone(result["exit_odd"])


if __name__ == "__main__":
    unittest.main()
