# Data, Assumptions, and Limitations

## Scope

This is a cross-sectional momentum and risk-budgeting study within a fixed sample of 20 currently observable A-share stocks. It is not a point-in-time reconstruction of the Chinese equity market, an investable fund backtest, or a future-performance forecast.

## Data pipeline

- Stock prices are obtained through AkShare from Tencent Finance's public endpoint.
- Stocks use forward-adjusted (`qfq`) closing prices.
- The CSI 300 comparator uses an unadjusted price-index series.
- The benchmark calendar defines the research trading dates.
- Observed quote availability is stored separately from forward-filled valuation prices.
- When a quote is missing, the last observed close can be carried forward for valuation, but the stock cannot be traded that day.
- Downloaded raw matrices and local caches are excluded; derived audit outputs are retained.

The frozen run was made on 2026-08-07 with AkShare 1.18.81 and contains observations through 2026-07-30. A later online download may differ if the upstream endpoint revises history or changes behavior.

## Signal, execution, and risk assumptions

- Momentum is trailing price return over a configurable number of trading days.
- Signals form at month-end close and execute at the following trading-day close.
- The following trading day's return belongs to the old holdings; new holdings earn returns only afterward.
- The default stock sleeve mixes a 70% monthly equal-weight core and 30% momentum satellite at the target-weight level.
- Realized volatility uses only the unscaled core/satellite portfolio returns dated on or before the signal date, up to 60 observations and with at least 20 required.
- Volatility targeting can reduce exposure but never increase it above 100%; insufficient history stays in cash.
- The trend cap uses only the signal-date CSI 300 close and trailing average.
- Unused weight earns a 0% cash return; shorting and leverage are prohibited.
- A simplified 0.10% cost is applied to absolute traded notional on buys and sells.
- Reported Sharpe ratios assume a 0% risk-free rate.

## Material limitations

1. **Survivorship and selection bias.** The fixed current-stock sample can omit weak, failed, long-suspended, or delisted securities. Risk control cannot repair this bias.
2. **Small-universe inference.** Twenty stocks are not representative of the full A-share market. A 30% satellite normally contains only six names.
3. **Return-definition mismatch.** Forward-adjusted stock prices and an unadjusted price index are not economically identical total-return measures.
4. **Initialization asymmetry.** The risk-controlled portfolio begins with a warm-up cash period; the classic comparator can enter the common report window with an already running position.
5. **No separate warm-up download.** A 120-day signal and 200-day trend rule need history. Early decisions may be skipped or held in cash until enough in-sample observations exist.
6. **Volatility-target uncertainty.** Historical volatility is an estimate, not a guarantee. Gaps and regime changes can make realized volatility exceed the target.
7. **Trend whipsaw.** A moving-average cap can repeatedly de-risk and re-risk near the threshold, miss rebounds, and increase turnover.
8. **Suspension constraint.** Frozen holdings cannot be sold. Actual exposure can temporarily exceed a new lower target until trading becomes possible.
9. **Simplified execution.** The model does not fully reconstruct price limits, T+1 settlement, board lots, minimum commission, stamp duty, slippage, liquidity, market impact, or queue priority.
10. **Corporate and listing events.** ST treatment, rights issues, mergers, delistings, IPO seasoning, and forced liquidation are not fully reconstructed.
11. **Factor attribution.** Market, industry, size, quality, and low-volatility exposures are not neutralized, so results cannot be attributed solely to momentum.
12. **Endpoint and revision risk.** Public interfaces can fail, change fields, or revise historical observations.
13. **Researcher degrees of freedom.** The same historical sample was inspected during development. Parameter sensitivity and the 2024 time split are retrospective diagnostics, not independent evidence.
14. **No future claim.** A better historical drawdown or Sharpe ratio does not imply future outperformance and is not investment advice.

## Appropriate interpretation

The classic 20-day / top-10% rule is retained because its large historical drawdown motivated the redesign. The risk-controlled portfolio should be evaluated against that fixed baseline, the same-universe equal-weight comparator, and the CSI 300. In this sample, it reduced drawdown and volatility but earned less than both classic momentum and equal weight. The defensible contribution is the auditable process: chronological signals, no leverage, explicit cash, fixed comparisons, deterministic tests, and transparent negative evidence.

