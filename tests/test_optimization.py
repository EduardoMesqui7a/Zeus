import unittest

from src.optimization import (
    add_date_filter,
    build_int_range,
    build_minute_candidate_grid,
    candidate_product,
    expand_minute_candidates_around,
    rank_strategy_candidates,
    sort_optimization_records,
    split_query_variants,
)


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

    def test_build_minute_candidate_grid_builds_pairs(self) -> None:
        combos = build_minute_candidate_grid([30, 35], [44, 45])
        self.assertEqual(
            combos,
            [
                {'entry_minute': 30, 'final_minute': 44},
                {'entry_minute': 30, 'final_minute': 45},
                {'entry_minute': 35, 'final_minute': 44},
                {'entry_minute': 35, 'final_minute': 45},
            ],
        )

    def test_expand_minute_candidates_around_refines_seed(self) -> None:
        refined = expand_minute_candidates_around(
            [{'entry_minute': 35, 'final_minute': 45}],
            entry_radius=1,
            final_radius=1,
            entry_step=1,
            final_step=1,
        )
        self.assertIn({'entry_minute': 34, 'final_minute': 44}, refined)
        self.assertIn({'entry_minute': 35, 'final_minute': 45}, refined)
        self.assertIn({'entry_minute': 36, 'final_minute': 46}, refined)

    def test_rank_strategy_candidates_filters_volume_and_orders_profit(self) -> None:
        records = [
            {'profit': 40, 'roi': 2, 'win_rate': 55, 'drawdown': -10, 'bets': 10},
            {'profit': 60, 'roi': 1, 'win_rate': 50, 'drawdown': -15, 'bets': 5},
            {'profit': 70, 'roi': 3, 'win_rate': 60, 'drawdown': -5, 'bets': 10},
        ]
        ordered = rank_strategy_candidates(records, base_bets=10, min_bets=10, min_volume_ratio=100)
        self.assertEqual(len(ordered), 2)
        self.assertEqual([record['profit'] for record in ordered], [70, 40])
        self.assertEqual([record['volume_ratio'] for record in ordered], [100.0, 100.0])

    def test_sort_optimization_records_prefers_profit(self) -> None:
        records = [
            {'train_profit': 50, 'train_roi': 2, 'train_bets': 10},
            {'train_profit': 60, 'train_roi': 1, 'train_bets': 3},
            {'train_profit': 10, 'train_roi': 20, 'train_bets': 100},
        ]
        ordered = sort_optimization_records(records, validation_available=False)
        self.assertEqual([record['train_profit'] for record in ordered], [60, 50, 10])


if __name__ == '__main__':
    unittest.main()
