import unittest

from src.query_parser import absolute_to_period_minute, period_minute_to_absolute


class QueryParserTests(unittest.TestCase):
    def test_absolute_to_period_minute_keeps_second_half_absolute(self) -> None:
        self.assertEqual(absolute_to_period_minute(80), (2, 80))

    def test_absolute_to_period_minute_keeps_first_half_absolute(self) -> None:
        self.assertEqual(absolute_to_period_minute(20), (1, 20))

    def test_period_minute_to_absolute_is_identity(self) -> None:
        self.assertEqual(period_minute_to_absolute(2, 80), 80)


if __name__ == "__main__":
    unittest.main()
