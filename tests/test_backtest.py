"""核心回测规则的离线单元测试，不访问任何行情接口。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_DIR / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from momentum_backtest import (
    apply_risk_controls,
    blend_with_equal_weight_core,
    build_rebalance_targets,
    simulate_strategy,
)


class BacktestRuleTests(unittest.TestCase):
    def test_signal_is_traded_on_next_trading_day(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=45)
        prices = pd.DataFrame(
            {
                "000001": np.linspace(10.0, 20.0, len(index)),
                "000002": np.full(len(index), 10.0),
            },
            index=index,
        )
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        targets, log = build_rebalance_targets(
            prices,
            lookback=5,
            top_percent=0.5,
            tradeable=tradeable,
        )

        first_signal = pd.Timestamp(log.iloc[0]["signal_date"])
        first_trade = pd.Timestamp(log.iloc[0]["trade_date"])
        self.assertGreater(first_trade, first_signal)
        self.assertTrue(targets.loc[first_signal].isna().all())
        self.assertEqual(float(targets.loc[first_trade, "000001"]), 1.0)

        result, _ = simulate_strategy(prices, targets, fee_rate=0.0, tradeable=tradeable)
        self.assertEqual(float(result.loc[first_trade, "gross_return"]), 0.0)

    def test_initial_purchase_charges_one_side_cost(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=3)
        prices = pd.DataFrame({"000001": [10.0, 10.0, 10.0]}, index=index)
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        targets = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        targets.loc[index[1], "000001"] = 1.0

        result, _ = simulate_strategy(
            prices,
            targets,
            fee_rate=0.001,
            tradeable=tradeable,
        )
        self.assertAlmostEqual(float(result.loc[index[1], "turnover"]), 1.0)
        self.assertAlmostEqual(float(result.loc[index[1], "strategy_return"]), -0.001)

    def test_suspended_holding_cannot_be_sold(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=4)
        prices = pd.DataFrame(
            {"000001": [10.0, 10.0, 10.0, 10.0], "000002": [10.0, 10.0, 10.0, 10.0]},
            index=index,
        )
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        tradeable.loc[index[2], "000001"] = False
        targets = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        targets.loc[index[0]] = [1.0, 0.0]
        targets.loc[index[2]] = [0.0, 1.0]

        result, weights = simulate_strategy(
            prices,
            targets,
            fee_rate=0.0,
            tradeable=tradeable,
        )
        self.assertAlmostEqual(float(weights.loc[index[2], "000001"]), 1.0)
        self.assertAlmostEqual(float(weights.loc[index[2], "000002"]), 0.0)
        self.assertAlmostEqual(float(result.loc[index[2], "turnover"]), 0.0)

    def test_cash_target_is_not_scaled_back_to_full_investment(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=3)
        prices = pd.DataFrame({"000001": [10.0, 10.0, 12.0]}, index=index)
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        targets = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        targets.loc[index[1], "000001"] = 0.40

        result, weights = simulate_strategy(
            prices,
            targets,
            fee_rate=0.0,
            tradeable=tradeable,
        )

        self.assertAlmostEqual(float(weights.loc[index[1], "000001"]), 0.40)
        self.assertAlmostEqual(float(result.loc[index[1], "cash_weight"]), 0.60)
        self.assertAlmostEqual(float(result.loc[index[2], "gross_return"]), 0.08)

    def test_invalid_leverage_and_short_targets_are_rejected(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=3)
        prices = pd.DataFrame(
            {"000001": [10.0, 10.0, 10.0], "000002": [10.0, 10.0, 10.0]},
            index=index,
        )
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)

        leveraged = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        leveraged.loc[index[1]] = [0.70, 0.50]
        with self.assertRaisesRegex(RuntimeError, "超过 100%"):
            simulate_strategy(prices, leveraged, fee_rate=0.0, tradeable=tradeable)

        short = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        short.loc[index[1]] = [0.80, -0.10]
        with self.assertRaisesRegex(RuntimeError, "不能为负数"):
            simulate_strategy(prices, short, fee_rate=0.0, tradeable=tradeable)

    def test_suspended_position_uses_partial_risk_budget(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=4)
        prices = pd.DataFrame(
            {"000001": [10.0] * 4, "000002": [10.0] * 4},
            index=index,
        )
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        tradeable.loc[index[2], "000001"] = False
        targets = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        targets.loc[index[0]] = [0.30, 0.00]
        targets.loc[index[2]] = [0.00, 0.50]

        result, weights = simulate_strategy(
            prices, targets, fee_rate=0.0, tradeable=tradeable
        )

        self.assertAlmostEqual(float(weights.loc[index[2], "000001"]), 0.30)
        self.assertAlmostEqual(float(weights.loc[index[2], "000002"]), 0.20)
        self.assertAlmostEqual(float(result.loc[index[2], "equity_exposure"]), 0.50)
        self.assertAlmostEqual(float(result.loc[index[2], "cash_weight"]), 0.50)

    def test_next_day_suspension_does_not_change_signal_ranking(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=45)
        prices = pd.DataFrame(
            {
                "000001": np.linspace(10.0, 20.0, len(index)),
                "000002": np.full(len(index), 10.0),
            },
            index=index,
        )
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        _, initial_log = build_rebalance_targets(
            prices,
            lookback=5,
            top_percent=0.5,
            tradeable=tradeable,
        )
        first_trade = pd.Timestamp(initial_log.iloc[0]["trade_date"])
        tradeable.loc[first_trade, "000001"] = False

        targets, log = build_rebalance_targets(
            prices,
            lookback=5,
            top_percent=0.5,
            tradeable=tradeable,
        )

        first_trade_rows = log.loc[pd.to_datetime(log["trade_date"]) == first_trade]
        self.assertEqual(first_trade_rows.iloc[0]["ticker"], "000001")
        self.assertAlmostEqual(float(targets.loc[first_trade, "000001"]), 1.0)

    def test_future_suffix_does_not_change_past_targets(self) -> None:
        index = pd.bdate_range("2023-01-02", periods=180)
        prices = pd.DataFrame(
            {
                "000001": np.linspace(10.0, 30.0, len(index)),
                "000002": np.linspace(12.0, 9.0, len(index)),
                "000003": 10.0 + np.sin(np.arange(len(index)) / 8.0),
            },
            index=index,
        )
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        cutoff = index[115]
        changed = prices.copy()
        changed.loc[changed.index > cutoff, "000001"] *= 0.05
        changed.loc[changed.index > cutoff, "000002"] *= 8.0

        targets_a, log_a = build_rebalance_targets(
            prices, lookback=20, top_percent=1 / 3, tradeable=tradeable
        )
        targets_b, log_b = build_rebalance_targets(
            changed, lookback=20, top_percent=1 / 3, tradeable=tradeable
        )

        pd.testing.assert_frame_equal(
            targets_a.loc[targets_a.index <= cutoff],
            targets_b.loc[targets_b.index <= cutoff],
        )
        past_a = log_a.loc[pd.to_datetime(log_a["trade_date"]) <= cutoff].reset_index(drop=True)
        past_b = log_b.loc[pd.to_datetime(log_b["trade_date"]) <= cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(past_a, past_b)

    def test_core_satellite_target_is_diversified(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=3)
        prices = pd.DataFrame(
            {"000001": [10.0, 10.0, 10.0], "000002": [10.0, 10.0, 10.0]},
            index=index,
        )
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        momentum_targets = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        momentum_targets.loc[index[1]] = [1.0, 0.0]

        blended = blend_with_equal_weight_core(
            momentum_targets,
            prices,
            tradeable,
            momentum_weight=0.30,
        )

        self.assertAlmostEqual(float(blended.loc[index[1], "000001"]), 0.65)
        self.assertAlmostEqual(float(blended.loc[index[1], "000002"]), 0.35)
        self.assertAlmostEqual(float(blended.loc[index[1]].sum()), 1.0)

    def test_volatility_control_uses_only_information_through_signal_date(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=50)
        targets = pd.DataFrame(np.nan, index=index, columns=["000001"])
        targets.loc[index[30], "000001"] = 1.0
        history = pd.Series(
            [0.04 if position % 2 == 0 else -0.04 for position in range(len(index))],
            index=index,
        )
        changed_future = history.copy()
        changed_future.loc[index[30]:] = 0.50
        benchmark = pd.Series(np.linspace(100.0, 120.0, len(index)), index=index)
        log = pd.DataFrame(
            {"signal_date": [index[29]], "trade_date": [index[30]]}
        )

        controlled_a, risk_a = apply_risk_controls(
            targets,
            history,
            benchmark,
            log,
            target_volatility=0.18,
            volatility_lookback=20,
            trend_ma_days=0,
            defensive_exposure=0.5,
        )
        controlled_b, risk_b = apply_risk_controls(
            targets,
            changed_future,
            benchmark,
            log,
            target_volatility=0.18,
            volatility_lookback=20,
            trend_ma_days=0,
            defensive_exposure=0.5,
        )

        exposure_a = float(controlled_a.loc[index[30]].sum())
        exposure_b = float(controlled_b.loc[index[30]].sum())
        self.assertLess(exposure_a, 1.0)
        self.assertAlmostEqual(exposure_a, exposure_b)
        self.assertAlmostEqual(
            float(risk_a.iloc[0]["realized_volatility"]),
            float(risk_b.iloc[0]["realized_volatility"]),
        )

    def test_insufficient_volatility_history_stays_in_cash(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=15)
        targets = pd.DataFrame(np.nan, index=index, columns=["000001"])
        targets.loc[index[10], "000001"] = 1.0
        returns = pd.Series(0.01, index=index)
        benchmark = pd.Series(100.0, index=index)
        log = pd.DataFrame(
            {"signal_date": [index[9]], "trade_date": [index[10]]}
        )

        controlled, risk = apply_risk_controls(
            targets,
            returns,
            benchmark,
            log,
            target_volatility=0.18,
            volatility_lookback=60,
            trend_ma_days=0,
            defensive_exposure=0.50,
        )

        self.assertAlmostEqual(float(controlled.loc[index[10]].sum()), 0.0)
        self.assertEqual(str(risk.iloc[0]["volatility_status"]), "insufficient_history")
        self.assertAlmostEqual(float(risk.iloc[0]["cash_target"]), 1.0)

    def test_return_cost_and_weight_identities_hold(self) -> None:
        index = pd.bdate_range("2024-01-02", periods=4)
        prices = pd.DataFrame({"000001": [10.0, 10.0, 11.0, 11.0]}, index=index)
        tradeable = pd.DataFrame(True, index=index, columns=prices.columns)
        targets = pd.DataFrame(np.nan, index=index, columns=prices.columns)
        targets.loc[index[1], "000001"] = 0.50

        result, weights = simulate_strategy(
            prices, targets, fee_rate=0.001, tradeable=tradeable
        )

        expected_net = (1.0 + result["gross_return"]) * (
            1.0 - result["trading_cost"]
        ) - 1.0
        pd.testing.assert_series_equal(
            result["strategy_return"], expected_net.rename("strategy_return")
        )
        self.assertTrue(
            np.allclose(result["trading_cost"], result["turnover"] * 0.001)
        )
        self.assertTrue((weights.sum(axis=1) <= 1.0 + 1e-12).all())
        self.assertTrue(
            np.allclose(result["equity_exposure"] + result["cash_weight"], 1.0)
        )

    def test_trend_cap_uses_signal_close_not_trade_day_close(self) -> None:
        index = pd.bdate_range("2023-01-02", periods=220)
        targets = pd.DataFrame(np.nan, index=index, columns=["000001"])
        targets.loc[index[205], "000001"] = 1.0
        returns = pd.Series(0.0, index=index)
        benchmark = pd.Series(np.linspace(200.0, 100.0, len(index)), index=index)
        benchmark_with_trade_day_jump = benchmark.copy()
        benchmark_with_trade_day_jump.loc[index[205]] = 1000.0
        log = pd.DataFrame(
            {"signal_date": [index[204]], "trade_date": [index[205]]}
        )

        controlled_a, _ = apply_risk_controls(
            targets,
            returns,
            benchmark,
            log,
            target_volatility=0.0,
            volatility_lookback=60,
            trend_ma_days=200,
            defensive_exposure=0.50,
        )
        controlled_b, _ = apply_risk_controls(
            targets,
            returns,
            benchmark_with_trade_day_jump,
            log,
            target_volatility=0.0,
            volatility_lookback=60,
            trend_ma_days=200,
            defensive_exposure=0.50,
        )

        self.assertAlmostEqual(float(controlled_a.loc[index[205]].sum()), 0.50)
        self.assertAlmostEqual(
            float(controlled_a.loc[index[205]].sum()),
            float(controlled_b.loc[index[205]].sum()),
        )


if __name__ == "__main__":
    unittest.main()
