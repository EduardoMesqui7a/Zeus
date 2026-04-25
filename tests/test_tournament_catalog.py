import unittest

import pandas as pd

from src.tournament_catalog import enrich_results_with_tournament_catalog, normalize_catalog_text


class TournamentCatalogTests(unittest.TestCase):
    def test_normalize_catalog_text_strips_single_year(self) -> None:
        self.assertEqual(normalize_catalog_text("nm cup 2026"), "nm cup")

    def test_normalize_catalog_text_strips_season_range(self) -> None:
        self.assertEqual(normalize_catalog_text("laliga 25/26"), "laliga")

    def test_enrich_results_matches_tournament_id_from_season_name(self) -> None:
        results = pd.DataFrame(
            [
                {
                    "season_name": "nm cup 2026",
                    "country": "norway",
                    "home_country": "norway",
                    "away_country": "norway",
                    "tournament_id": "",
                    "tournament_name": "",
                }
            ]
        )
        bot_config = {
            "tournaments": [
                {
                    "tournament_id": "sr:tournament:29",
                    "tournament_name": "nm cup",
                    "tournament_country": "norway",
                    "tournament_country_code": "nor",
                }
            ]
        }

        enriched = enrich_results_with_tournament_catalog(results, bot_config)

        self.assertEqual(enriched.iloc[0]["tournament_id"], "sr:tournament:29")
        self.assertEqual(enriched.iloc[0]["tournament_name"], "nm cup")
        self.assertEqual(enriched.iloc[0]["tournament_label"], "nm cup (sr:tournament:29)")

    def test_enrich_results_uses_country_to_break_ambiguity(self) -> None:
        results = pd.DataFrame(
            [
                {
                    "season_name": "super lig 25/26",
                    "country": "turkiye",
                    "home_country": "turkiye",
                    "away_country": "turkiye",
                    "tournament_id": "",
                    "tournament_name": "",
                }
            ]
        )
        bot_config = {
            "tournaments": [
                {
                    "tournament_id": "sr:tournament:52",
                    "tournament_name": "super lig",
                    "tournament_country": "turkey",
                    "tournament_country_code": "tur",
                },
                {
                    "tournament_id": "sr:tournament:999999",
                    "tournament_name": "super lig",
                    "tournament_country": "switzerland",
                    "tournament_country_code": "sui",
                },
            ]
        }

        enriched = enrich_results_with_tournament_catalog(results, bot_config)

        self.assertEqual(enriched.iloc[0]["tournament_id"], "sr:tournament:52")
        self.assertEqual(enriched.iloc[0]["tournament_name"], "super lig")


if __name__ == "__main__":
    unittest.main()
