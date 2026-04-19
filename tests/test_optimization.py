import unittest

from src.optimization import add_date_filter, build_int_range, candidate_product, split_query_variants


class OptimizationHelpersTests(unittest.TestCase):
    def test_split_query_variants_supports_blocks(self) -> None:
        variants = split_query_variants(
            '(m20.GolsTotal = 0)\n---\n(m20.GolsTotal <= 1)\n\n(m20.GolsTotal <= 2)'
        )
        self.assertEqual(variants, ['(m20.GolsTotal = 0)', '(m20.GolsTotal <= 1)', '(m20.GolsTotal <= 2)'])

    def test_build_int_range_is_inclusive(self) -> None:
        self.assertEqual(build_int_range(1, 5, 2), [1, 3, 5])

    def test_add_date_filter_wraps_query(self) -> None:
        query = add_date_filter('(m20.Minuto = 20)', '2022-01-01', '2022-01-31')
        self.assertIn('m500.DataJogo >= "2022-01-01"', query)
        self.assertIn('m500.DataJogo <= "2022-01-31"', query)

    def test_candidate_product_builds_cartesian_grid(self) -> None:
        combos = candidate_product(['a', 'b'], ['x'], [1, 2], [10])
        self.assertEqual(len(combos), 4)
        self.assertEqual(combos[0]['entry_minute'], 1)
        self.assertEqual(combos[-1]['final_minute'], 10)


if __name__ == '__main__':
    unittest.main()
