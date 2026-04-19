import unittest

from src.backtest import (
    MARKET_OPTIONS,
    BacktestConfig,
    _apply_back_profit,
    _apply_lay_profit,
    _build_row,
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


class MarketSettlementTests(unittest.TestCase):
    def test_all_markets_use_settlement_logic_for_custom_final_minute(self) -> None:
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
            selection_hit = bool(market["settle"](final_snapshot))
            self.assertEqual(result["market_hit"], selection_hit, msg=label)
            if market["side"] == "back":
                expected_profit, _ = _apply_back_profit(config.stake, 2.0, selection_hit, config.commission)
            else:
                expected_profit, _ = _apply_lay_profit(config.stake, 2.0, selection_hit, config.commission)
            self.assertAlmostEqual(result["profit"], expected_profit, places=7, msg=label)
            self.assertEqual(result["won"], result["profit"] >= 0, msg=label)

            summary = _finalize_backtest([base_row], [result], config)
            self.assertEqual(summary["metrics"]["wins"], int(result["won"]), msg=label)
            self.assertAlmostEqual(summary["metrics"]["win_rate"], 100.0 if result["won"] else 0.0, places=7, msg=label)


if __name__ == "__main__":
    unittest.main()
