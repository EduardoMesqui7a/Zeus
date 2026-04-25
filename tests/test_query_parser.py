import unittest

from src.query_parser import (
    absolute_to_period_minute,
    evaluate_snapshot_query,
    infer_final_minute,
    infer_snapshot_period,
    period_minute_to_absolute,
    rewrite_query_minute_refs,
    rewrite_query_period_refs,
)


class QueryParserTests(unittest.TestCase):
    def test_absolute_to_period_minute_keeps_second_half_absolute(self) -> None:
        self.assertEqual(absolute_to_period_minute(80), (2, 80))

    def test_absolute_to_period_minute_keeps_first_half_absolute(self) -> None:
        self.assertEqual(absolute_to_period_minute(20), (1, 20))

    def test_period_minute_to_absolute_is_identity(self) -> None:
        self.assertEqual(period_minute_to_absolute(2, 80), 80)

    def test_infer_final_minute_uses_final_filter_minute(self) -> None:
        self.assertEqual(infer_final_minute("(m45.Periodo = 2) and (m45.GolsTotal = 0)"), 45)

    def test_infer_snapshot_period_treats_m45_as_halftime_close(self) -> None:
        self.assertEqual(infer_snapshot_period("(m45.GolsTotal >= 1)", 45), 2)
        self.assertEqual(infer_snapshot_period("(m45.Periodo = 1) and (m45.GolsTotal >= 1)", 45), 1)

    def test_rewrite_query_minute_refs_only_replaces_source_minute(self) -> None:
        query = '(m25.GolsTotal = 0) and (m500.DataJogo >= "2023-01-01")'
        rewritten = rewrite_query_minute_refs(query, 25, 30)
        self.assertIn("m30.GolsTotal", rewritten)
        self.assertIn("m500.DataJogo", rewritten)

    def test_rewrite_query_period_refs_updates_period(self) -> None:
        query = "(m45.Periodo = 2) and (m45.GolsTotal = 0)"
        rewritten = rewrite_query_period_refs(query, 1)
        self.assertIn("Periodo = 1", rewritten)
        self.assertNotIn("Periodo = 2", rewritten)

    def test_evaluate_snapshot_query_supports_arithmetic_and_between(self) -> None:
        snapshot = {
            "GolsTotal": 0,
            "Pressao2Casa": 2,
            "Pressao2Visitante": 3,
            "BackUnder05HT": 2.4,
        }
        query = '(GolsTotal = 0) and ((Pressao2Casa + Pressao2Visitante) >= 5) and (BackUnder05HT between 2 and 3)'
        self.assertTrue(evaluate_snapshot_query(query, snapshot))

    def test_evaluate_snapshot_query_strips_minute_prefixes(self) -> None:
        snapshot = {"GolsCasa": 0, "GolsVisitante": 0}
        query = '(m80.GolsCasa = 0) and (m80.GolsVisitante = 0)'
        self.assertTrue(evaluate_snapshot_query(query, snapshot))

    def test_evaluate_snapshot_query_derives_golstotal_when_missing(self) -> None:
        snapshot = {"GolsCasa": 1, "GolsVisitante": 2}
        self.assertFalse(evaluate_snapshot_query("(m500.GolsTotal <= 2)", snapshot))
        self.assertTrue(evaluate_snapshot_query("(m500.GolsTotal <= 3)", snapshot))


if __name__ == "__main__":
    unittest.main()
