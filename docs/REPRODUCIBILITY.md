# Reproducibility Record

## Frozen configuration

- Project version: `3.0.0-risk-controlled`
- Requested dates: 2019-01-01 to 2026-07-30
- Effective common comparison window: 2019-08-01 to 2026-07-30
- Universe: 20 fixed example A-share stocks in `tickers.csv`
- Selected strategy: no-leverage risk-controlled core/satellite momentum
- Momentum signal: 120 trading days; top 30%
- Target mix: 70% equal-weight core; 30% momentum satellite
- Volatility overlay: 18% annualized target; up to 60 prior trading-day observations, minimum 20
- Trend overlay: CSI 300 versus its trailing 200-trading-day average
- Defensive cap: at most 50% equity exposure when the signal-date close is below the average
- Cash return: 0%; leverage and shorting: prohibited
- Execution: month-end signal close, following trading-day close
- Simplified cost: 0.10% of absolute traded notional
- Fixed comparator: classic 20-day / top-10% momentum
- Diagnostic split: 2024-01-01, retrospective rather than true out-of-sample
- Frozen environment: Windows 11, Python 3.14, direct dependencies in `requirements-lock.txt`

## Verification sequence

From the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Then run the reviewed configuration:

```powershell
.\.venv\Scripts\python.exe src\momentum_backtest.py --tickers tickers.csv --start 2019-01-01 --end 2026-07-30 --strategy-mode risk_controlled --lookback 120 --top-percent 0.30 --momentum-weight 0.30 --target-volatility 0.18 --volatility-lookback 60 --trend-ma-days 200 --defensive-exposure 0.50 --oos-start 2024-01-01 --fee-rate 0.001 --output-dir reproduced_risk_results
```

Review `reproduced_risk_results/integrity_checks.csv`, including:

- all signal dates precede their execution dates;
- each NAV series equals the cumulative product of one plus daily net return;
- `trading_cost = turnover × fee_rate`;
- equity exposure is non-negative and does not use leverage;
- cash plus equity exposure equals 100%;
- volatility and trend inputs end at the signal close; and
- the fixed classic comparator remains 20 days / top 10%.

To regenerate only the HTML report from an existing result directory:

```powershell
.\.venv\Scripts\python.exe src\analysis_report.py --result-dir reproduced_risk_results
```

## Reproducibility boundary

The offline tests are deterministic. A new online market-data run may not be byte-identical because public endpoints and historical observations can be revised. The repository therefore records parameters, dependency versions, effective dates, aggregate results, diagnostics, and run metadata rather than treating a hash match as a future guarantee.

The 2024 split is not a genuine out-of-sample test because the broader 2019–2026 history and sensitivity results had already been inspected while the upgraded defaults were selected. Genuine out-of-sample evidence requires future data after freezing the code and parameters, or a strictly specified walk-forward protocol.

