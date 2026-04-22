import unittest

from src.zeus_client import ZeusClient, _dedupe_rows_by_sport_event_id


class ZeusClientFinalSnapshotTests(unittest.TestCase):
    def test_dedupe_rows_by_sport_event_id_keeps_first_occurrence(self) -> None:
        rows = [
            {"sport_event_id": "sr:match:1", "value": 1},
            {"sport_event_id": "sr:match:2", "value": 2},
            {"sport_event_id": "sr:match:1", "value": 3},
            {"sport_event_id": "", "value": 4},
        ]

        deduped = _dedupe_rows_by_sport_event_id(rows)
        self.assertEqual([row["sport_event_id"] for row in deduped], ["sr:match:1", "sr:match:2"])
        self.assertEqual(deduped[0]["value"], 1)
        self.assertEqual(deduped[1]["value"], 2)

    def test_final_snapshot_prefers_true_final_minute_snapshot(self) -> None:
        client = ZeusClient(auth_token="token")
        calls: list[tuple[int, int]] = []

        def fake_fetch_snapshot(game_id: str, minute: int, period: int) -> dict:
            calls.append((minute, period))
            if minute == 500 and period == 0:
                return {"Minuto": 500, "Periodo": 0, "GolsCasa": 2, "GolsVisitante": 1}
            return {}

        def fake_fetch_match_detail(game_id: str) -> dict:
            raise AssertionError("fetch_match_detail should not be used when minute=500 snapshot exists")

        client.fetch_snapshot = fake_fetch_snapshot  # type: ignore[method-assign]
        client.fetch_match_detail = fake_fetch_match_detail  # type: ignore[method-assign]

        snapshot = client.fetch_final_snapshot("sr:match:demo", final_minute=500)
        self.assertEqual(snapshot["GolsCasa"], 2)
        self.assertEqual(snapshot["GolsVisitante"], 1)
        self.assertEqual(calls[0], (500, 0))

    def test_async_final_snapshot_prefers_true_final_minute_snapshot(self) -> None:
        client = ZeusClient(auth_token="token")
        calls: list[tuple[int, int]] = []

        async def fake_fetch_snapshot(game_id: str, minute: int, period: int) -> dict:
            calls.append((minute, period))
            if minute == 500 and period == 0:
                return {"Minuto": 500, "Periodo": 0, "GolsCasa": 2, "GolsVisitante": 1}
            return {}

        async def fake_fetch_match_detail(game_id: str) -> dict:
            raise AssertionError("fetch_match_detail should not be used when minute=500 snapshot exists")

        async_client = __import__("types").SimpleNamespace(
            fetch_snapshot=fake_fetch_snapshot,
            fetch_match_detail=fake_fetch_match_detail,
        )

        from src.zeus_client import AsyncZeusClient

        snapshot = __import__("asyncio").run(AsyncZeusClient.fetch_final_snapshot(async_client, "sr:match:demo", final_minute=500))
        self.assertEqual(snapshot["GolsCasa"], 2)
        self.assertEqual(snapshot["GolsVisitante"], 1)
        self.assertEqual(calls[0], (500, 0))


if __name__ == "__main__":
    unittest.main()
