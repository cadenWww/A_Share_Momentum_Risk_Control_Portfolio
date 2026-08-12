"""A 股横截面动量与风险控制组合回测。

系统同时保留经典动量策略作为研究基准，并提供风险控制模式：

1. 月末收盘后计算横截面动量，下一交易日收盘执行，避免信号日收益泄漏。
2. 将分散化等权核心仓位与动量卫星仓位混合，降低少数股票集中度。
3. 仅使用调仓日前已经发生的收益估计波动率，高波动时主动保留现金。
4. 可用基准长期均线限制弱势市场中的最高股票仓位，且绝不使用杠杆。
5. 每次调仓按实际换手率扣除简化单边交易成本。

数据通过 AkShare 从腾讯财经公开 A 股行情接口获取。本脚本仅用于学习和研究，
不构成投资建议或未来收益承诺。
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import time
from datetime import date
from pathlib import Path

import matplotlib

# 让脚本在没有图形界面的终端中也可以保存 PNG。
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import akshare as ak

from analysis_report import generate_html_report


# 沪深市场每年通常约有 242 个交易日；用于年化展示的教学近似。
TRADING_DAYS_PER_YEAR = 242
PROJECT_VERSION = "3.0.0-risk-controlled"
CLASSIC_LOOKBACK = 20
CLASSIC_TOP_PERCENT = 0.10
# 缓存版本进入文件名；数据接口或复权方法改变后不会误用旧缓存。
DATA_CACHE_VERSION = "tencent_qfq_v2"


def parse_arguments() -> argparse.Namespace:
    """读取用户在终端输入的参数。"""
    parser = argparse.ArgumentParser(
        description="A 股回测：经典动量与无杠杆风险控制型动量组合"
    )
    parser.add_argument(
        "--tickers",
        default="tickers.csv",
        help="股票池 CSV 文件路径（默认：tickers.csv）",
    )
    parser.add_argument(
        "--start",
        default="2019-01-01",
        help="回测开始日期，格式 YYYY-MM-DD（默认：2019-01-01）",
    )
    parser.add_argument(
        "--end",
        default=date.today().isoformat(),
        help="回测结束日期，格式 YYYY-MM-DD，包含该日期（默认：今天）",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=120,
        help="动量观察期（交易日数，风险控制模式推荐：120）",
    )
    parser.add_argument(
        "--top-percent",
        type=float,
        default=0.30,
        help="每次持有排名前多少比例，例如 0.30 表示前 30%%（默认：0.30）",
    )
    parser.add_argument(
        "--strategy-mode",
        choices=("risk_controlled", "classic"),
        default="risk_controlled",
        help="risk_controlled 为推荐风险控制模式；classic 为纯动量模式",
    )
    parser.add_argument(
        "--momentum-weight",
        type=float,
        default=0.30,
        help="风险控制模式中动量卫星仓位占比，剩余为股票池等权核心（默认：0.30）",
    )
    parser.add_argument(
        "--target-volatility",
        type=float,
        default=0.18,
        help="目标年化波动率；仅降仓、不加杠杆，填 0 可关闭（默认：0.18）",
    )
    parser.add_argument(
        "--volatility-lookback",
        type=int,
        default=60,
        help="估计已实现波动率的历史交易日数（默认：60）",
    )
    parser.add_argument(
        "--trend-ma-days",
        type=int,
        default=200,
        help="基准趋势均线交易日数；填 0 可关闭（默认：200）",
    )
    parser.add_argument(
        "--defensive-exposure",
        type=float,
        default=0.50,
        help="基准低于趋势均线时的最高股票仓位（默认：0.50）",
    )
    parser.add_argument(
        "--oos-start",
        default="2024-01-01",
        help="回顾性时间留出起点 YYYY-MM-DD；留空则不生成时间分段诊断",
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=0.001,
        help="单边交易成本率，例如 0.001 表示 0.1%%（默认：0.001）",
    )
    parser.add_argument(
        "--benchmark",
        default="000300",
        help="比较基准的 A 股指数代码（默认：000300，即沪深 300）",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=100_000,
        help="用于展示的初始资金（默认：100000）",
    )
    parser.add_argument(
        "--output-dir",
        default="results",
        help="结果保存文件夹（默认：results）",
    )
    parser.add_argument(
        "--download-pause",
        type=float,
        default=0.8,
        help="两次请求腾讯财经公开数据之间等待的秒数（默认：0.8）",
    )
    parser.add_argument(
        "--download-retries",
        type=int,
        default=2,
        help="每只股票最多尝试下载几次（默认：2）",
    )
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="忽略本地价格缓存，重新下载数据",
    )
    args = parser.parse_args()

    if args.lookback < 1:
        parser.error("--lookback 必须是正整数。")
    if not 0 < args.top_percent <= 1:
        parser.error("--top-percent 必须大于 0 且不超过 1。")
    if not 0 <= args.momentum_weight <= 1:
        parser.error("--momentum-weight 必须在 0 到 1 之间。")
    if args.target_volatility < 0:
        parser.error("--target-volatility 不能为负数。")
    if args.volatility_lookback < 2:
        parser.error("--volatility-lookback 至少为 2。")
    if args.trend_ma_days < 0:
        parser.error("--trend-ma-days 不能为负数。")
    if not 0 <= args.defensive_exposure <= 1:
        parser.error("--defensive-exposure 必须在 0 到 1 之间。")
    if args.fee_rate < 0:
        parser.error("--fee-rate 不能为负数。")
    if args.initial_capital <= 0:
        parser.error("--initial-capital 必须大于 0。")
    if args.download_pause < 0:
        parser.error("--download-pause 不能为负数。")
    if args.download_retries < 1:
        parser.error("--download-retries 必须是正整数。")

    try:
        start = pd.Timestamp(args.start)
        end = pd.Timestamp(args.end)
    except ValueError as error:
        parser.error(f"日期格式错误：{error}")
    if start >= end:
        parser.error("--end 必须晚于 --start。")
    if args.oos_start:
        try:
            pd.Timestamp(args.oos_start)
        except ValueError as error:
            parser.error(f"--oos-start 日期格式错误：{error}")

    args.benchmark = args.benchmark.strip().zfill(6)
    if not re.fullmatch(r"\d{6}", args.benchmark):
        parser.error("--benchmark 必须是 6 位指数代码，例如 000300。")
    return args


def read_tickers(csv_path: Path) -> list[str]:
    """从 CSV 的 ticker 列读取 6 位 A 股代码，并去重。"""
    if not csv_path.exists():
        raise FileNotFoundError(
            f"找不到股票池文件：{csv_path}\n"
            "请确认你在项目文件夹中运行命令，或使用 --tickers 指定文件。"
        )

    try:
        table = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str)
    except UnicodeDecodeError:
        table = pd.read_csv(csv_path, encoding="gbk", dtype=str)

    if table.empty:
        raise ValueError("股票池文件是空的。请至少填写一只股票代码。")

    normalized_columns = {str(column).strip().lower(): column for column in table.columns}
    column = normalized_columns.get("ticker", table.columns[0])

    tickers: list[str] = []
    seen: set[str] = set()
    invalid_tickers: list[str] = []
    for value in table[column].dropna():
        ticker = str(value).strip()
        if ticker.isdigit():
            ticker = ticker.zfill(6)
        if not re.fullmatch(r"\d{6}", ticker):
            invalid_tickers.append(ticker)
            continue
        if ticker and ticker not in seen:
            tickers.append(ticker)
            seen.add(ticker)

    if invalid_tickers:
        raise ValueError(
            "以下代码不是 6 位 A 股代码："
            f"{', '.join(invalid_tickers)}。例如：000001、600519。"
        )
    if not tickers:
        raise ValueError("没有读到股票代码。请检查 tickers.csv 的 ticker 列。")
    return tickers


def price_cache_path(
    cache_directory: Path,
    tickers: list[str],
    start: str,
    end: str,
) -> Path:
    """不同的股票池和日期范围使用不同缓存文件，避免误用旧数据。"""
    signature_text = "|".join([DATA_CACHE_VERSION, start, end, *tickers])
    signature = hashlib.sha256(signature_text.encode("utf-8")).hexdigest()[:12]
    return cache_directory / f"prices_{start}_{end}_{signature}.csv"


def normalise_close_series(table: pd.DataFrame, code: str) -> pd.Series:
    """把 AkShare 返回的行情表转换为日期索引的收盘价序列。"""
    if table is None or table.empty:
        raise ValueError("数据为空。")

    if {"日期", "收盘"}.issubset(table.columns):
        date_column, close_column = "日期", "收盘"
    elif {"date", "close"}.issubset(table.columns):
        date_column, close_column = "date", "close"
    else:
        raise ValueError("返回字段异常，未找到日期和收盘价列。")

    prices = table.loc[:, [date_column, close_column]].copy()
    prices[date_column] = pd.to_datetime(prices[date_column])
    prices[close_column] = pd.to_numeric(prices[close_column], errors="coerce")
    prices = prices.dropna().drop_duplicates(subset=date_column).sort_values(date_column)
    series = prices.set_index(date_column)[close_column]
    series.index = series.index.tz_localize(None)
    series.name = code
    if series.empty:
        raise ValueError("没有有效的收盘价。")
    return series


def tencent_symbol(code: str, is_index: bool = False) -> str:
    """把 6 位沪深京代码转换成腾讯财经所需的市场前缀格式。"""
    # 指数代码和股票代码的前缀规则不同：沪深 300 的腾讯代码是 sh000300，
    # 而深证指数（399xxx）使用 sz 前缀。
    if is_index:
        return f"sz{code}" if code.startswith("399") else f"sh{code}"
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    raise ValueError(f"无法判断代码 {code} 的交易所。")


def fetch_a_share_close(
    code: str,
    start_date: str,
    end_date: str,
    kind: str,
    retries: int,
    pause_seconds: float,
) -> pd.Series | None:
    """下载一只 A 股或一个指数的日收盘价；失败时保守重试。"""
    for attempt in range(1, retries + 1):
        try:
            table = ak.stock_zh_a_hist_tx(
                symbol=tencent_symbol(code, is_index=(kind == "index")),
                start_date=start_date,
                end_date=end_date,
                adjust="qfq" if kind == "stock" else "",
            )
            return normalise_close_series(table, code)
        except Exception as error:
            if attempt == retries:
                print(f"代码 {code} 下载失败：{error}")
                return None
            wait_seconds = max(3.0, pause_seconds) * attempt
            print(
                f"代码 {code} 第 {attempt}/{retries} 次下载失败，"
                f"等待 {wait_seconds:.0f} 秒后重试：{error}"
            )
            time.sleep(wait_seconds)
    return None


def download_a_share_close_prices(
    tickers: list[str],
    benchmark: str,
    start: str,
    end: str,
    pause_seconds: float,
    retries: int,
    cache_directory: Path,
    refresh_data: bool,
) -> pd.DataFrame:
    """
    下载 A 股前复权日收盘价和沪深指数日收盘价。

    使用 AkShare 的 stock_zh_a_hist_tx（腾讯财经公开数据）。股票指定 qfq 前复权，
    指数不复权。成功后会保存 CSV 缓存，避免反复联网请求。
    """
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_keys = [f"index:{benchmark}", *tickers]
    price_cache = price_cache_path(cache_directory, cache_keys, start, end)
    legacy_signature_text = "|".join([start, end, *cache_keys])
    legacy_signature = hashlib.sha256(legacy_signature_text.encode("utf-8")).hexdigest()[:12]
    legacy_cache = cache_directory / f"prices_{start}_{end}_{legacy_signature}.csv"
    available_cache = price_cache if price_cache.exists() else legacy_cache
    if available_cache.exists() and not refresh_data:
        cached_prices = pd.read_csv(available_cache, index_col="date", parse_dates=["date"])
        cached_prices.index = pd.to_datetime(cached_prices.index).tz_localize(None)
        print(f"使用本地价格缓存：{available_cache.name}")
        return cached_prices.sort_index()

    start_date = pd.Timestamp(start).strftime("%Y%m%d")
    end_date = pd.Timestamp(end).strftime("%Y%m%d")
    print("数据源：AkShare → 腾讯财经公开 A 股行情")
    print(f"正在下载沪深 300 指数 {benchmark}……")
    benchmark_close = fetch_a_share_close(
        code=benchmark,
        start_date=start_date,
        end_date=end_date,
        kind="index",
        retries=retries,
        pause_seconds=pause_seconds,
    )
    if benchmark_close is None:
        raise RuntimeError(
            "未能下载沪深 300 基准数据。请检查网络，或稍后重新运行。"
        )

    downloaded_parts: list[pd.DataFrame] = [benchmark_close.to_frame()]
    unavailable: list[str] = []
    for position, ticker in enumerate(tickers, start=1):
        print(f"下载股票 {position}/{len(tickers)}：{ticker}")
        close = fetch_a_share_close(
            code=ticker,
            start_date=start_date,
            end_date=end_date,
            kind="stock",
            retries=retries,
            pause_seconds=pause_seconds,
        )
        if close is None:
            unavailable.append(ticker)
        else:
            downloaded_parts.append(close.to_frame())
        if position < len(tickers) and pause_seconds > 0:
            time.sleep(pause_seconds)

    if len(downloaded_parts) == 1:
        raise RuntimeError(
            "没有下载到任何 A 股价格数据。请检查网络和股票代码，或稍后重试。"
        )

    prices = pd.concat(downloaded_parts, axis=1)
    prices = prices.loc[:, ~prices.columns.duplicated()].sort_index()
    prices.index.name = "date"
    prices.to_csv(price_cache, encoding="utf-8-sig")

    if unavailable:
        print(f"提示：以下代码本次没有下载成功：{', '.join(unavailable)}")
    return prices


def last_trading_day_each_month(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """从真实交易日中找出每个月最后一个交易日。"""
    monthly_last = (
        pd.Series(index, index=index)
        .groupby(index.to_period("M"), sort=True)
        .max()
    )
    return list(monthly_last)


def longest_missing_run(values: pd.Series) -> int:
    """返回布尔序列中连续 False 的最长长度。"""
    longest = 0
    current = 0
    for available in values.astype(bool):
        if available:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def prepare_research_prices(
    all_prices: pd.DataFrame,
    tickers: list[str],
    benchmark: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.DataFrame]:
    """
    将股票价格统一到基准交易日，并同时保留“当天是否真的有报价”的信息。

    估值价格对停牌/偶发缺失使用上一笔有效收盘价；但 tradeable 会把这些日期
    标记为不可交易，避免回测在停牌日虚构买卖。前导缺失不会被填充。
    """
    benchmark_close = pd.to_numeric(
        all_prices[benchmark], errors="coerce"
    ).dropna().sort_index()
    if benchmark_close.empty:
        raise RuntimeError(f"基准 {benchmark} 没有有效价格，无法建立交易日历。")

    calendar = benchmark_close.index
    raw_prices = all_prices.reindex(index=calendar, columns=tickers).apply(
        pd.to_numeric, errors="coerce"
    )
    raw_prices = raw_prices.where(raw_prices > 0).dropna(axis=1, how="all")
    if raw_prices.shape[1] == 0:
        raise RuntimeError("股票池中没有能够对齐到基准交易日的有效价格。")

    tradeable = raw_prices.notna()
    valuation_prices = raw_prices.ffill()

    quality_rows: list[dict[str, object]] = []
    for ticker in raw_prices.columns:
        observed = tradeable[ticker]
        valid_dates = raw_prices.index[observed]
        quality_rows.append(
            {
                "ticker": ticker,
                "first_quote_date": valid_dates.min().date() if len(valid_dates) else "N/A",
                "last_quote_date": valid_dates.max().date() if len(valid_dates) else "N/A",
                "quote_days": int(observed.sum()),
                "calendar_days": int(len(observed)),
                "missing_quote_days": int((~observed).sum()),
                "missing_quote_ratio": float((~observed).mean()),
                "longest_missing_run": longest_missing_run(observed),
            }
        )

    data_quality = pd.DataFrame(quality_rows)
    return valuation_prices, tradeable, benchmark_close, data_quality


def build_rebalance_targets(
    prices: pd.DataFrame,
    lookback: int,
    top_percent: float,
    tradeable: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    构建每个调仓日的目标权重。

    targets 中普通日期为 NaN；仅调仓日有完整的一行 0/非 0 权重。
    这样能区分“没有调仓”与“调仓后某股票权重为 0”。
    """
    targets = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    if tradeable is None:
        tradeable = prices.notna()
    tradeable = tradeable.reindex(index=prices.index, columns=prices.columns).fillna(False)
    records: list[dict[str, object]] = []

    for signal_date in last_trading_day_each_month(prices.index):
        signal_position = prices.index.get_loc(signal_date)

        # 数据不足 20 个交易日，或月末后没有下一交易日时，不能建立一笔交易。
        if signal_position < lookback or signal_position + 1 >= len(prices.index):
            continue

        trade_date = prices.index[signal_position + 1]
        current_price = prices.iloc[signal_position]
        past_price = prices.iloc[signal_position - lookback]
        # 排名只能使用信号日及更早的信息；下一交易日能否成交由执行器处理。
        valid_price = (
            (current_price > 0)
            & (past_price > 0)
            & tradeable.iloc[signal_position]
            & tradeable.iloc[signal_position - lookback]
        )
        momentum = (current_price / past_price - 1).where(valid_price)
        momentum = momentum.replace([np.inf, -np.inf], np.nan).dropna()

        if momentum.empty:
            continue

        number_to_select = max(1, math.ceil(len(momentum) * top_percent))
        selected = momentum.nlargest(number_to_select, keep="first")
        target = pd.Series(0.0, index=prices.columns)
        target.loc[selected.index] = 1.0 / len(selected)
        targets.loc[trade_date] = target

        for rank, (ticker, momentum_return) in enumerate(selected.items(), start=1):
            records.append(
                {
                    "signal_date": signal_date,
                    "trade_date": trade_date,
                    "rank": rank,
                    "ticker": ticker,
                    "momentum_return": momentum_return,
                    # 保留旧列名，兼容 2.x 报告和历史结果读取器。
                    "return_20d": momentum_return,
                    "target_weight": target[ticker],
                    "eligible_stocks": len(momentum),
                    "selected_stocks": len(selected),
                }
            )

    rebalance_log = pd.DataFrame(records)
    if rebalance_log.empty:
        raise RuntimeError(
            "没有生成调仓记录。请延长日期范围、降低观察期，或检查股票数据。"
        )
    return targets, rebalance_log


def blend_with_equal_weight_core(
    momentum_targets: pd.DataFrame,
    prices: pd.DataFrame,
    tradeable: pd.DataFrame,
    momentum_weight: float,
) -> pd.DataFrame:
    """把月度动量目标与分散化等权核心混合。

    ``momentum_weight=1`` 等价于纯动量；``0`` 等价于每月对可交易股票等权。
    只在原动量目标已经出现的交易日生成目标，因此不会额外改变交易时点。
    """
    if not 0 <= momentum_weight <= 1:
        raise ValueError("momentum_weight 必须在 0 到 1 之间。")

    blended = pd.DataFrame(
        np.nan,
        index=momentum_targets.index,
        columns=momentum_targets.columns,
    )
    aligned_tradeable = tradeable.reindex(
        index=momentum_targets.index,
        columns=momentum_targets.columns,
    ).fillna(False)

    for trade_date, momentum_target in momentum_targets.dropna(how="all").iterrows():
        trade_position = momentum_targets.index.get_loc(trade_date)
        if trade_position == 0:
            continue
        signal_date = momentum_targets.index[trade_position - 1]
        eligible = (
            aligned_tradeable.loc[signal_date].astype(bool)
            & prices.loc[signal_date].notna()
            & (prices.loc[signal_date] > 0)
        )
        if not eligible.any():
            continue
        core_target = pd.Series(0.0, index=momentum_targets.columns)
        core_target.loc[eligible] = 1.0 / int(eligible.sum())
        momentum_target = momentum_target.fillna(0.0).clip(lower=0.0)
        if momentum_target.sum() > 0:
            momentum_target = momentum_target / float(momentum_target.sum())
        target = (1.0 - momentum_weight) * core_target + momentum_weight * momentum_target
        blended.loc[trade_date] = target

    if blended.dropna(how="all").empty:
        raise RuntimeError("核心—卫星组合没有生成任何可执行目标。")
    return blended


def apply_risk_controls(
    targets: pd.DataFrame,
    unscaled_returns: pd.Series,
    benchmark_close: pd.Series,
    rebalance_log: pd.DataFrame,
    target_volatility: float,
    volatility_lookback: int,
    trend_ma_days: int,
    defensive_exposure: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """用历史波动率和已知趋势信号限制每次调仓后的股票仓位。

    波动率历史严格截止到信号日；趋势也只读取信号日及以前的基准收盘价。
    两个模块都是仓位上限，不会把股票仓位放大到 100% 以上，也不会加杠杆。
    """
    if target_volatility < 0:
        raise ValueError("target_volatility 不能为负数。")
    if volatility_lookback < 2:
        raise ValueError("volatility_lookback 至少为 2。")
    if trend_ma_days < 0:
        raise ValueError("trend_ma_days 不能为负数。")
    if not 0 <= defensive_exposure <= 1:
        raise ValueError("defensive_exposure 必须在 0 到 1 之间。")

    controlled = targets.copy()
    records: list[dict[str, object]] = []
    signal_dates = (
        rebalance_log[["trade_date", "signal_date"]]
        .drop_duplicates("trade_date")
        .assign(
            trade_date=lambda frame: pd.to_datetime(frame["trade_date"]),
            signal_date=lambda frame: pd.to_datetime(frame["signal_date"]),
        )
        .set_index("trade_date")["signal_date"]
    )
    minimum_volatility_observations = max(20, volatility_lookback // 3)

    for trade_date, base_target in targets.dropna(how="all").iterrows():
        signal_date = signal_dates.get(trade_date)
        if pd.isna(signal_date):
            trade_position = targets.index.get_loc(trade_date)
            if trade_position == 0:
                continue
            signal_date = targets.index[trade_position - 1]
        signal_date = pd.Timestamp(signal_date)

        history = unscaled_returns.loc[unscaled_returns.index <= signal_date].dropna().tail(
            volatility_lookback
        )
        realized_volatility = float("nan")
        volatility_cap = 1.0
        volatility_status = "disabled"
        if target_volatility > 0:
            # 没有足够历史时无法声称完成了波动率估计；保守地保持现金，
            # 而不是把未知风险误当作低风险并直接满仓。
            volatility_cap = 0.0
            volatility_status = "insufficient_history"
            if len(history) >= minimum_volatility_observations:
                daily_volatility = float(history.std(ddof=1))
                if math.isfinite(daily_volatility) and daily_volatility > 0:
                    realized_volatility = daily_volatility * math.sqrt(TRADING_DAYS_PER_YEAR)
                    volatility_cap = min(1.0, target_volatility / realized_volatility)
                    volatility_status = "estimated"
                elif math.isfinite(daily_volatility):
                    # 足量历史内的零波动不会触发加杠杆，股票仓位最多仍为 100%。
                    realized_volatility = 0.0
                    volatility_cap = 1.0
                    volatility_status = "zero_volatility_no_leverage"

        trend_close = float("nan")
        trend_average = float("nan")
        above_trend: bool | None = None
        trend_cap = 1.0
        benchmark_history = benchmark_close.loc[benchmark_close.index <= signal_date].dropna()
        if trend_ma_days > 0 and len(benchmark_history) >= trend_ma_days:
            trend_close = float(benchmark_history.iloc[-1])
            trend_average = float(benchmark_history.tail(trend_ma_days).mean())
            above_trend = bool(trend_close >= trend_average)
            trend_cap = 1.0 if above_trend else defensive_exposure

        final_exposure = min(1.0, volatility_cap, trend_cap)
        base_target = base_target.fillna(0.0).clip(lower=0.0)
        base_exposure = min(1.0, float(base_target.sum()))
        if base_target.sum() > 0:
            base_target = base_target / float(base_target.sum()) * base_exposure
        controlled_target = base_target * final_exposure
        controlled.loc[trade_date] = controlled_target
        records.append(
            {
                "signal_date": signal_date,
                "trade_date": trade_date,
                "volatility_observations": len(history),
                "realized_volatility": realized_volatility,
                "volatility_status": volatility_status,
                "volatility_cap": volatility_cap,
                "trend_close": trend_close,
                "trend_average": trend_average,
                "above_trend": above_trend,
                "trend_cap": trend_cap,
                "final_equity_exposure": float(controlled_target.sum()),
                "cash_target": max(0.0, 1.0 - float(controlled_target.sum())),
            }
        )

    return controlled, pd.DataFrame(records)


def simulate_strategy(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    fee_rate: float,
    tradeable: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    逐日模拟策略，并允许权重在月内随价格涨跌自然漂移。

    每天先按上一收盘后的实际权重计算当天收益，随后若当天是调仓日，
    则在当天收盘按目标权重调仓并扣交易成本。这样信号不会提前使用。
    """
    asset_returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    if tradeable is None:
        tradeable = prices.notna()
    tradeable = tradeable.reindex(index=prices.index, columns=prices.columns).fillna(False)
    current_weights = pd.Series(0.0, index=prices.columns)
    end_of_day_weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    gross_returns = pd.Series(0.0, index=prices.index, name="gross_return")
    net_returns = pd.Series(0.0, index=prices.index, name="strategy_return")
    turnovers = pd.Series(0.0, index=prices.index, name="turnover")
    trading_costs = pd.Series(0.0, index=prices.index, name="trading_cost")
    equity_exposures = pd.Series(0.0, index=prices.index, name="equity_exposure")
    cash_weights = pd.Series(1.0, index=prices.index, name="cash_weight")

    for trading_date in prices.index:
        day_returns = asset_returns.loc[trading_date]
        unvalued_holdings = (current_weights.abs() > 1e-12) & day_returns.isna()
        if unvalued_holdings.any():
            problem_codes = ", ".join(unvalued_holdings[unvalued_holdings].index)
            raise RuntimeError(
                f"{trading_date.date()} 的持仓缺少可用估值价格：{problem_codes}。"
            )
        # 尚未上市的股票可能没有收益，但它们在建仓前权重为 0。
        day_returns = day_returns.fillna(0.0)
        gross_return = float((current_weights * day_returns).sum())
        portfolio_value_factor = 1.0 + gross_return

        if portfolio_value_factor <= 0:
            raise RuntimeError(
                f"{trading_date.date()} 的组合价值归零，无法继续计算。"
            )

        # 价格变动后、尚未调仓时的权重。它会在月内自然漂移，而非每天重置等权。
        weights_after_market_move = (
            current_weights * (1.0 + day_returns) / portfolio_value_factor
        )

        target = targets.loc[trading_date]
        is_rebalance_day = not target.isna().all()
        if is_rebalance_day:
            target = target.fillna(0.0)
            if (target < -1e-12).any():
                raise RuntimeError("目标权重不能为负数；当前系统不模拟卖空。")
            target = target.clip(lower=0.0)
            target_exposure = float(target.sum())
            if target_exposure > 1.0 + 1e-9:
                raise RuntimeError("目标股票权重合计超过 100%，系统禁止隐含杠杆。")
            target_exposure = min(1.0, target_exposure)
            can_trade_today = tradeable.loc[trading_date].astype(bool)
            # 没有真实报价的原持仓被冻结；它会优先占用本次股票风险预算。
            executed_target = pd.Series(0.0, index=prices.columns)
            frozen = ~can_trade_today & (weights_after_market_move.abs() > 1e-12)
            executed_target.loc[frozen] = weights_after_market_move.loc[frozen]
            frozen_weight = float(executed_target.sum())
            # 冻结持仓优先占用“本次目标股票风险预算”，而不只是占用物理上的
            # 100% 容量。否则 50% 的风险目标可能因一只停牌旧持仓被错误抬到 90%。
            available_weight = max(0.0, target_exposure - frozen_weight)
            desired_tradable = target.where(can_trade_today, 0.0).clip(lower=0.0)
            desired_total = float(desired_tradable.sum())
            if desired_total > 0 and available_weight > 0:
                # 只在物理容量不足时向下缩放，绝不把低于 100% 的风险目标放大为满仓。
                scale = min(1.0, available_weight / desired_total)
                executed_target += desired_tradable * scale
            target = executed_target
            # sum(abs(新权重 - 旧权重)) 同时计算买入和卖出金额。
            turnover = float((target - weights_after_market_move).abs().sum())
            trading_cost = turnover * fee_rate
            current_weights = target.copy()
        else:
            turnover = 0.0
            trading_cost = 0.0
            current_weights = weights_after_market_move

        gross_returns.loc[trading_date] = gross_return
        turnovers.loc[trading_date] = turnover
        trading_costs.loc[trading_date] = trading_cost
        # 交易成本按调仓后组合价值的一定比例扣除。
        net_returns.loc[trading_date] = (
            (1.0 + gross_return) * (1.0 - trading_cost) - 1.0
        )
        end_of_day_weights.loc[trading_date] = current_weights
        equity_exposures.loc[trading_date] = float(current_weights.sum())
        cash_weights.loc[trading_date] = max(0.0, 1.0 - float(current_weights.sum()))

    first_trade_date = targets.dropna(how="all").index.min()
    result = pd.concat(
        [
            gross_returns,
            net_returns,
            turnovers,
            trading_costs,
            equity_exposures,
            cash_weights,
        ],
        axis=1,
    )
    result = result.loc[first_trade_date:].copy()
    return result, end_of_day_weights.loc[first_trade_date:].copy()


def calculate_metrics(returns: pd.Series) -> dict[str, float]:
    """计算收益、波动、尾部损失和回撤指标；无风险利率假设为 0。"""
    returns = returns.dropna()
    if len(returns) < 2:
        raise RuntimeError("有效回测交易日不足，无法计算绩效指标。")

    nav = (1.0 + returns).cumprod()
    total_return = float(nav.iloc[-1] - 1.0)
    annualized_return = float(nav.iloc[-1] ** (TRADING_DAYS_PER_YEAR / len(returns)) - 1.0)
    annualized_volatility = float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
    sharpe_ratio = (
        float(returns.mean() / returns.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
        if returns.std(ddof=1) > 0
        else float("nan")
    )
    drawdown = nav / nav.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    downside = returns.clip(upper=0.0)
    downside_deviation = float(
        math.sqrt(float((downside**2).mean())) * math.sqrt(TRADING_DAYS_PER_YEAR)
    )
    sortino_ratio = (
        float(returns.mean() * TRADING_DAYS_PER_YEAR / downside_deviation)
        if downside_deviation > 0
        else float("nan")
    )
    calmar_ratio = (
        float(annualized_return / abs(max_drawdown))
        if max_drawdown < 0
        else float("nan")
    )
    value_at_risk_95 = float(returns.quantile(0.05))
    tail = returns.loc[returns <= value_at_risk_95]
    conditional_value_at_risk_95 = float(tail.mean()) if not tail.empty else float("nan")

    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "max_drawdown": max_drawdown,
        "sharpe_ratio_rf_0": sharpe_ratio,
        "downside_deviation": downside_deviation,
        "sortino_ratio_rf_0": sortino_ratio,
        "calmar_ratio": calmar_ratio,
        "worst_day": float(returns.min()),
        "value_at_risk_95": value_at_risk_95,
        "conditional_value_at_risk_95": conditional_value_at_risk_95,
        "trading_days": float(len(returns)),
    }


def build_equal_weight_buy_hold_target(
    prices: pd.DataFrame,
    tradeable: pd.DataFrame,
    trade_date: pd.Timestamp,
) -> pd.DataFrame:
    """在策略首次建仓日建立股票池等权买入并持有的对照组。"""
    targets = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    eligible = tradeable.loc[trade_date] & prices.loc[trade_date].notna()
    if not eligible.any():
        raise RuntimeError("首次建仓日没有可用于等权对照组的股票。")
    target = pd.Series(0.0, index=prices.columns)
    target.loc[eligible] = 1.0 / int(eligible.sum())
    targets.loc[trade_date] = target
    return targets


def result_series_columns(benchmark: str) -> dict[str, str]:
    """返回结果表中可用的研究组合名称与收益列映射。"""
    return {
        "strategy": "strategy_return",
        "classic_momentum_20_10": "classic_momentum_return",
        benchmark: "benchmark_return",
        "universe_equal_weight": "equal_weight_return",
    }


def calculate_subperiod_metrics(result: pd.DataFrame, benchmark: str) -> pd.DataFrame:
    """计算全样本、前半段和后半段表现，帮助观察结果是否只集中在某一时期。"""
    midpoint = len(result) // 2
    periods = {
        "full_sample": result,
        "first_half": result.iloc[:midpoint],
        "second_half": result.iloc[midpoint:],
    }
    series_columns = {
        name: column
        for name, column in result_series_columns(benchmark).items()
        if column in result.columns
    }
    records: list[dict[str, object]] = []
    for period_name, period_data in periods.items():
        if len(period_data) < 2:
            continue
        for series_name, column in series_columns.items():
            values = calculate_metrics(period_data[column])
            records.append(
                {
                    "period": period_name,
                    "series": series_name,
                    "start_date": period_data.index.min().date(),
                    "end_date": period_data.index.max().date(),
                    **values,
                }
            )
    return pd.DataFrame(records)


def calculate_oos_metrics(
    result: pd.DataFrame,
    benchmark: str,
    oos_start: str | None,
) -> pd.DataFrame:
    """按指定日期做回顾性时间留出诊断，不把它宣传为真正样本外。"""
    if not oos_start:
        return pd.DataFrame()
    boundary = pd.Timestamp(oos_start)
    periods = {
        "development": result.loc[result.index < boundary],
        "holdout": result.loc[result.index >= boundary],
    }
    series_columns = {
        name: column
        for name, column in result_series_columns(benchmark).items()
        if column in result.columns
    }
    records: list[dict[str, object]] = []
    for phase, phase_data in periods.items():
        if len(phase_data) < 2:
            continue
        for series_name, column in series_columns.items():
            values = calculate_metrics(phase_data[column])
            records.append(
                {
                    "phase": phase,
                    "series": series_name,
                    "start_date": phase_data.index.min().date(),
                    "end_date": phase_data.index.max().date(),
                    **values,
                }
            )
    return pd.DataFrame(records)


def build_integrity_checks(
    result: pd.DataFrame,
    rebalance_log: pd.DataFrame,
    risk_control_log: pd.DataFrame,
    fee_rate: float,
) -> pd.DataFrame:
    """对结果包执行可复核的不变量检查，并返回机器可读的检查表。"""
    records: list[dict[str, object]] = []

    def record(check: str, passed: bool, detail: str) -> None:
        records.append({"check": check, "passed": bool(passed), "detail": detail})

    signal_dates = pd.to_datetime(rebalance_log["signal_date"])
    trade_dates = pd.to_datetime(rebalance_log["trade_date"])
    record(
        "signal_precedes_trade",
        bool((signal_dates < trade_dates).all()),
        f"checked_rows={len(rebalance_log)}",
    )

    for label, return_column, nav_column in [
        ("selected_strategy", "strategy_return", "strategy_nav"),
        ("classic_momentum", "classic_momentum_return", "classic_momentum_nav"),
        ("benchmark", "benchmark_return", "benchmark_nav"),
        ("equal_weight", "equal_weight_return", "equal_weight_nav"),
    ]:
        if return_column not in result or nav_column not in result:
            continue
        rebuilt = (1.0 + result[return_column]).cumprod()
        error = float((rebuilt - result[nav_column]).abs().max())
        record(f"{label}_nav_identity", error <= 1e-10, f"max_abs_error={error:.3e}")

    cost_error = float(
        (result["trading_cost"] - result["turnover"] * fee_rate).abs().max()
    )
    record("cost_identity", cost_error <= 1e-12, f"max_abs_error={cost_error:.3e}")

    exposure = result["equity_exposure"]
    exposure_ok = bool(((exposure >= -1e-12) & (exposure <= 1.0 + 1e-12)).all())
    record(
        "no_short_or_leverage",
        exposure_ok,
        f"min={float(exposure.min()):.6f}; max={float(exposure.max()):.6f}",
    )
    cash_error = float((exposure + result["cash_weight"] - 1.0).abs().max())
    record("cash_equity_identity", cash_error <= 1e-10, f"max_abs_error={cash_error:.3e}")

    if not risk_control_log.empty:
        risk_signal = pd.to_datetime(risk_control_log["signal_date"])
        risk_trade = pd.to_datetime(risk_control_log["trade_date"])
        record(
            "risk_signal_precedes_trade",
            bool((risk_signal < risk_trade).all()),
            f"checked_rebalances={len(risk_control_log)}",
        )
        target_exposure = pd.to_numeric(
            risk_control_log["final_equity_exposure"], errors="coerce"
        )
        target_ok = bool(
            ((target_exposure >= -1e-12) & (target_exposure <= 1.0 + 1e-12)).all()
        )
        record(
            "risk_target_without_leverage",
            target_ok,
            f"max_target={float(target_exposure.max()):.6f}",
        )

    return pd.DataFrame(records)


def run_parameter_sensitivity(
    prices: pd.DataFrame,
    tradeable: pd.DataFrame,
    fee_rate: float,
    requested_lookback: int,
    requested_top_percent: float,
) -> pd.DataFrame:
    """对几组常见参数重复回测，并统一评价起点，避免只展示单一最优参数。"""
    lookbacks = sorted({10, 20, 60, 120, requested_lookback})
    top_percents = sorted({0.10, 0.20, 0.30, requested_top_percent})
    completed: list[tuple[int, float, pd.DataFrame, pd.DataFrame]] = []

    for lookback in lookbacks:
        for top_percent in top_percents:
            try:
                targets, rebalance_log = build_rebalance_targets(
                    prices=prices,
                    lookback=lookback,
                    top_percent=top_percent,
                    tradeable=tradeable,
                )
                trial, _ = simulate_strategy(
                    prices=prices,
                    targets=targets,
                    fee_rate=fee_rate,
                    tradeable=tradeable,
                )
            except RuntimeError:
                continue
            completed.append((lookback, top_percent, trial, rebalance_log))

    if not completed:
        return pd.DataFrame()
    common_start = max(item[2].index.min() for item in completed)
    records: list[dict[str, object]] = []
    for lookback, top_percent, trial, rebalance_log in completed:
        comparable_returns = trial.loc[common_start:, "strategy_return"]
        if len(comparable_returns) < 2:
            continue
        values = calculate_metrics(comparable_returns)
        selected_per_rebalance = rebalance_log.groupby("trade_date")["ticker"].nunique()
        records.append(
            {
                "lookback": lookback,
                "top_percent": top_percent,
                "fee_rate": fee_rate,
                "comparison_start": common_start.date(),
                "average_selected_stocks": float(selected_per_rebalance.mean()),
                "is_requested_setting": bool(
                    lookback == requested_lookback
                    and math.isclose(top_percent, requested_top_percent)
                ),
                **values,
            }
        )
    return pd.DataFrame(records).sort_values(["lookback", "top_percent"])


def save_chart(result: pd.DataFrame, output_path: Path, benchmark_label: str) -> None:
    """把选定策略、经典动量与两个对照组的净值曲线保存为 PNG。"""
    figure, axis = plt.subplots(figsize=(12, 6))
    axis.plot(
        result.index,
        result["strategy_nav"],
        label="Selected strategy",
        linewidth=2.1,
        color="#0d7a75",
    )
    if "classic_momentum_nav" in result:
        axis.plot(
            result.index,
            result["classic_momentum_nav"],
            label="Classic momentum (20d / top 10%)",
            linewidth=1.2,
            alpha=0.8,
            color="#b06b30",
        )
    axis.plot(
        result.index,
        result["benchmark_nav"],
        label=f"Benchmark ({benchmark_label})",
        linewidth=1.4,
        alpha=0.85,
    )
    if "equal_weight_nav" in result:
        axis.plot(
            result.index,
            result["equal_weight_nav"],
            label="Universe equal-weight buy & hold",
            linewidth=1.3,
            alpha=0.8,
        )
    axis.set_title("A-share momentum strategy and risk-control comparison")
    axis.set_xlabel("Date")
    axis.set_ylabel("Net asset value (start = 1)")
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_path, dpi=160)
    plt.close(figure)


def format_percent(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def main() -> None:
    args = parse_arguments()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tickers = read_tickers(Path(args.tickers))
    if len(tickers) < 10:
        print("提示：股票池少于 10 只，排名前 10% 将至少选 1 只，结果参考价值有限。")

    cache_directory = Path(args.tickers).resolve().parent / "data_cache"
    all_prices = download_a_share_close_prices(
        tickers=tickers,
        benchmark=args.benchmark,
        start=args.start,
        end=args.end,
        pause_seconds=args.download_pause,
        retries=args.download_retries,
        cache_directory=cache_directory,
        refresh_data=args.refresh_data,
    )

    if args.benchmark not in all_prices.columns or all_prices[args.benchmark].dropna().empty:
        raise RuntimeError(f"未下载到基准 {args.benchmark} 的有效数据。")

    unavailable = [
        ticker
        for ticker in tickers
        if ticker not in all_prices.columns or all_prices[ticker].dropna().empty
    ]
    if unavailable:
        print(f"提示：以下代码没有有效数据，已忽略：{', '.join(unavailable)}")

    prices, tradeable, benchmark_close, data_quality = prepare_research_prices(
        all_prices=all_prices,
        tickers=tickers,
        benchmark=args.benchmark,
    )

    momentum_targets, rebalance_log = build_rebalance_targets(
        prices=prices,
        lookback=args.lookback,
        top_percent=args.top_percent,
        tradeable=tradeable,
    )
    if args.strategy_mode == "risk_controlled":
        blended_targets = blend_with_equal_weight_core(
            momentum_targets=momentum_targets,
            prices=prices,
            tradeable=tradeable,
            momentum_weight=args.momentum_weight,
        )
        unscaled_result, _ = simulate_strategy(
            prices=prices,
            targets=blended_targets,
            fee_rate=args.fee_rate,
            tradeable=tradeable,
        )
        selected_targets, risk_control_log = apply_risk_controls(
            targets=blended_targets,
            unscaled_returns=unscaled_result["strategy_return"],
            benchmark_close=benchmark_close,
            rebalance_log=rebalance_log,
            target_volatility=args.target_volatility,
            volatility_lookback=args.volatility_lookback,
            trend_ma_days=args.trend_ma_days,
            defensive_exposure=args.defensive_exposure,
        )
        strategy_label = "risk_controlled_core_satellite"
    else:
        blended_targets = momentum_targets.copy()
        selected_targets = momentum_targets.copy()
        risk_control_log = pd.DataFrame(
            [
                {
                    "signal_date": rebalance_log.loc[
                        rebalance_log["trade_date"] == trade_date, "signal_date"
                    ].iloc[0],
                    "trade_date": trade_date,
                    "volatility_observations": 0,
                    "realized_volatility": float("nan"),
                    "volatility_status": "disabled",
                    "volatility_cap": 1.0,
                    "trend_close": float("nan"),
                    "trend_average": float("nan"),
                    "above_trend": None,
                    "trend_cap": 1.0,
                    "final_equity_exposure": float(target.sum()),
                    "cash_target": max(0.0, 1.0 - float(target.sum())),
                }
                for trade_date, target in selected_targets.dropna(how="all").iterrows()
            ]
        )
        strategy_label = "classic_custom_momentum"

    result, daily_weights = simulate_strategy(
        prices=prices,
        targets=selected_targets,
        fee_rate=args.fee_rate,
        tradeable=tradeable,
    )

    classic_targets, _ = build_rebalance_targets(
        prices=prices,
        lookback=CLASSIC_LOOKBACK,
        top_percent=CLASSIC_TOP_PERCENT,
        tradeable=tradeable,
    )
    classic_result, classic_daily_weights = simulate_strategy(
        prices=prices,
        targets=classic_targets,
        fee_rate=args.fee_rate,
        tradeable=tradeable,
    )

    # 所有组合从共同可交易起点比较，避免观察期长短造成区间偏差。
    comparison_start = max(result.index.min(), classic_result.index.min())
    result = result.loc[comparison_start:].copy()
    daily_weights = daily_weights.loc[comparison_start:].copy()
    classic_result = classic_result.loc[comparison_start:].copy()
    classic_daily_weights = classic_daily_weights.loc[comparison_start:].copy()
    # 共同评价窗口从 comparison_start 的收盘执行时点开始。首日不计入此前持仓
    # 从上一收盘到当日收盘的市场收益，但保留当日收盘实际发生的调仓成本。
    result.iloc[0, result.columns.get_loc("gross_return")] = 0.0
    result.iloc[0, result.columns.get_loc("strategy_return")] = -float(
        result.iloc[0]["trading_cost"]
    )
    classic_result.iloc[0, classic_result.columns.get_loc("gross_return")] = 0.0
    classic_result.iloc[0, classic_result.columns.get_loc("strategy_return")] = -float(
        classic_result.iloc[0]["trading_cost"]
    )
    rebalance_log["signal_date"] = pd.to_datetime(rebalance_log["signal_date"])
    rebalance_log["trade_date"] = pd.to_datetime(rebalance_log["trade_date"])
    rebalance_log = rebalance_log.loc[rebalance_log["trade_date"] >= comparison_start].copy()
    if not risk_control_log.empty:
        risk_control_log["signal_date"] = pd.to_datetime(risk_control_log["signal_date"])
        risk_control_log["trade_date"] = pd.to_datetime(risk_control_log["trade_date"])
        risk_control_log = risk_control_log.loc[
            risk_control_log["trade_date"] >= comparison_start
        ].copy()
        risk_control_log["executed_equity_exposure"] = [
            float(daily_weights.at[pd.Timestamp(trade_date), "equity_exposure"])
            if "equity_exposure" in daily_weights.columns
            else float(result.at[pd.Timestamp(trade_date), "equity_exposure"])
            for trade_date in risk_control_log["trade_date"]
        ]
        risk_control_log["executed_cash_weight"] = [
            max(0.0, 1.0 - exposure)
            for exposure in risk_control_log["executed_equity_exposure"]
        ]

    # 记录动量、核心—卫星、风控目标与真实执行权重，便于审计停牌和现金仓位。
    rebalance_log["lookback_days"] = args.lookback
    rebalance_log["strategy_variant"] = strategy_label
    rebalance_log["momentum_target_weight"] = [
        float(momentum_targets.at[pd.Timestamp(row.trade_date), row.ticker])
        for row in rebalance_log.itertuples()
    ]
    rebalance_log["blended_target_weight"] = [
        float(blended_targets.at[pd.Timestamp(row.trade_date), row.ticker])
        for row in rebalance_log.itertuples()
    ]
    rebalance_log["scaled_target_weight"] = [
        float(selected_targets.at[pd.Timestamp(row.trade_date), row.ticker])
        for row in rebalance_log.itertuples()
    ]
    rebalance_log["executed_weight"] = [
        float(daily_weights.at[pd.Timestamp(row.trade_date), row.ticker])
        for row in rebalance_log.itertuples()
    ]

    equal_weight_targets = build_equal_weight_buy_hold_target(
        prices=prices,
        tradeable=tradeable,
        trade_date=result.index.min(),
    )
    equal_weight_result, _ = simulate_strategy(
        prices=prices,
        targets=equal_weight_targets,
        fee_rate=args.fee_rate,
        tradeable=tradeable,
    )

    benchmark_returns = (
        benchmark_close
        .pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .reindex(result.index)
        .fillna(0.0)
    )
    # 基准也从策略第一次建仓日开始记为 1，避免多算此前的收益。
    benchmark_returns.iloc[0] = 0.0

    result["benchmark_return"] = benchmark_returns
    result["classic_momentum_return"] = (
        classic_result["strategy_return"].reindex(result.index).fillna(0.0)
    )
    result["classic_equity_exposure"] = (
        classic_result["equity_exposure"].reindex(result.index).fillna(0.0)
    )
    result["equal_weight_return"] = (
        equal_weight_result["strategy_return"].reindex(result.index).fillna(0.0)
    )
    result["strategy_nav"] = (1.0 + result["strategy_return"]).cumprod()
    result["classic_momentum_nav"] = (1.0 + result["classic_momentum_return"]).cumprod()
    result["benchmark_nav"] = (1.0 + result["benchmark_return"]).cumprod()
    result["equal_weight_nav"] = (1.0 + result["equal_weight_return"]).cumprod()
    result["strategy_value"] = result["strategy_nav"] * args.initial_capital
    result["classic_momentum_value"] = result["classic_momentum_nav"] * args.initial_capital
    result["benchmark_value"] = result["benchmark_nav"] * args.initial_capital
    result["equal_weight_value"] = result["equal_weight_nav"] * args.initial_capital

    strategy_metrics = calculate_metrics(result["strategy_return"])
    classic_metrics = calculate_metrics(result["classic_momentum_return"])
    benchmark_metrics = calculate_metrics(result["benchmark_return"])
    equal_weight_metrics = calculate_metrics(result["equal_weight_return"])
    strategy_metrics["average_equity_exposure"] = float(result["equity_exposure"].mean())
    classic_metrics["average_equity_exposure"] = float(result["classic_equity_exposure"].mean())
    benchmark_metrics["average_equity_exposure"] = 1.0
    equal_weight_metrics["average_equity_exposure"] = 1.0
    metrics = pd.DataFrame(
        [strategy_metrics, classic_metrics, benchmark_metrics, equal_weight_metrics],
        index=[
            "strategy",
            "classic_momentum_20_10",
            args.benchmark,
            "universe_equal_weight",
        ],
    )
    metrics.index.name = "series"

    subperiod_metrics = calculate_subperiod_metrics(result, args.benchmark)
    oos_metrics = calculate_oos_metrics(result, args.benchmark, args.oos_start or None)
    integrity_checks = build_integrity_checks(
        result=result,
        rebalance_log=rebalance_log,
        risk_control_log=risk_control_log,
        fee_rate=args.fee_rate,
    )
    if not bool(integrity_checks["passed"].all()):
        failed = ", ".join(
            integrity_checks.loc[~integrity_checks["passed"], "check"].astype(str)
        )
        raise RuntimeError(f"结果完整性检查失败：{failed}")
    print("正在进行参数敏感性分析……")
    sensitivity = run_parameter_sensitivity(
        prices=prices,
        tradeable=tradeable,
        fee_rate=args.fee_rate,
        requested_lookback=args.lookback,
        requested_top_percent=args.top_percent,
    )

    result.index.name = "date"
    daily_weights.index.name = "date"
    classic_daily_weights.index.name = "date"
    prices.index.name = "date"
    tradeable.index.name = "date"
    result.to_csv(output_dir / "equity_curve.csv", encoding="utf-8-sig")
    daily_weights.to_csv(output_dir / "daily_positions.csv", encoding="utf-8-sig")
    classic_daily_weights.to_csv(
        output_dir / "classic_daily_positions.csv", encoding="utf-8-sig"
    )
    prices.to_csv(output_dir / "adjusted_close_prices.csv", encoding="utf-8-sig")
    tradeable.astype(int).to_csv(output_dir / "quote_availability.csv", encoding="utf-8-sig")
    data_quality.to_csv(output_dir / "data_quality.csv", index=False, encoding="utf-8-sig")
    rebalance_log.to_csv(output_dir / "rebalance_log.csv", index=False, encoding="utf-8-sig")
    risk_control_log.to_csv(
        output_dir / "risk_control_log.csv", index=False, encoding="utf-8-sig"
    )
    metrics.to_csv(output_dir / "performance_metrics.csv", encoding="utf-8-sig")
    subperiod_metrics.to_csv(
        output_dir / "subperiod_performance.csv", index=False, encoding="utf-8-sig"
    )
    oos_metrics.to_csv(
        output_dir / "temporal_holdout_diagnostic.csv", index=False, encoding="utf-8-sig"
    )
    integrity_checks.to_csv(
        output_dir / "integrity_checks.csv", index=False, encoding="utf-8-sig"
    )
    sensitivity.to_csv(
        output_dir / "parameter_sensitivity.csv", index=False, encoding="utf-8-sig"
    )
    save_chart(result, output_dir / "equity_curve.png", args.benchmark)

    missing_quote_ratio = float(1.0 - tradeable.to_numpy().mean())
    effective_core_weight = (
        1.0 - args.momentum_weight if args.strategy_mode == "risk_controlled" else 0.0
    )
    effective_momentum_weight = (
        args.momentum_weight if args.strategy_mode == "risk_controlled" else 1.0
    )
    effective_target_volatility = (
        args.target_volatility if args.strategy_mode == "risk_controlled" else 0.0
    )
    effective_trend_days = args.trend_ma_days if args.strategy_mode == "risk_controlled" else 0
    effective_defensive_exposure = (
        args.defensive_exposure if args.strategy_mode == "risk_controlled" else 1.0
    )
    summary_title = (
        "A 股风险控制型动量组合回测（研究作品集 3.0）"
        if args.strategy_mode == "risk_controlled"
        else "A 股经典动量策略回测（研究作品集 3.0）"
    )
    construction_line = (
        f"核心仓位：{effective_core_weight:.0%}；动量卫星：{effective_momentum_weight:.0%}"
        if args.strategy_mode == "risk_controlled"
        else "组合结构：纯动量（风险覆盖层未启用）"
    )
    risk_line = (
        f"目标波动率：{effective_target_volatility:.0%}；趋势均线：{effective_trend_days} 日；弱市上限：{effective_defensive_exposure:.0%}"
        if args.strategy_mode == "risk_controlled"
        else "风险覆盖层：波动率降仓和趋势仓位上限均未启用"
    )
    summary = "\n".join(
        [
            summary_title,
            f"回测区间：{result.index.min().date()} 至 {result.index.max().date()}",
            f"股票池有效股票数：{prices.shape[1]}",
            f"主策略模式：{strategy_label}",
            f"动量观察期：{args.lookback} 个交易日；选股比例：{args.top_percent:.0%}",
            construction_line,
            risk_line,
            f"单边交易成本：{args.fee_rate:.2%}",
            f"对齐后缺失报价比例：{missing_quote_ratio:.2%}",
            "",
            "策略绩效（无风险利率假设为 0）：",
            f"总收益率：{format_percent(strategy_metrics['total_return'])}",
            f"年化收益率：{format_percent(strategy_metrics['annualized_return'])}",
            f"年化波动率：{format_percent(strategy_metrics['annualized_volatility'])}",
            f"最大回撤：{format_percent(strategy_metrics['max_drawdown'])}",
            f"夏普比率：{strategy_metrics['sharpe_ratio_rf_0']:.2f}",
            f"平均股票仓位：{format_percent(strategy_metrics['average_equity_exposure'])}",
            "",
            "经典动量对照（20 日 / 前 10%）：",
            f"年化收益率：{format_percent(classic_metrics['annualized_return'])}",
            f"最大回撤：{format_percent(classic_metrics['max_drawdown'])}",
            f"夏普比率：{classic_metrics['sharpe_ratio_rf_0']:.2f}",
            "",
            "股票池等权买入持有对照组：",
            f"年化收益率：{format_percent(equal_weight_metrics['annualized_return'])}",
            f"最大回撤：{format_percent(equal_weight_metrics['max_drawdown'])}",
            "",
            "说明：本项目仍使用固定股票池，存在幸存者偏差；结果仅用于研究/学习，",
            "不代表未来收益，也不构成投资建议。",
        ]
    )
    (output_dir / "summary.txt").write_text(summary, encoding="utf-8")
    metadata = "\n".join(
        [
            f"run_date={date.today().isoformat()}",
            f"project_version={PROJECT_VERSION}",
            "data_source=AkShare / 腾讯财经公开 A 股行情",
            "stock_adjustment=qfq",
            "benchmark_type=price_index_unadjusted",
            "valuation_rule=last_observed_close_for_missing_quote",
            "trade_rule=no_trade_without_observed_quote",
            "universe_type=fixed_csv_current_examples",
            f"akshare_version={getattr(ak, '__version__', 'unknown')}",
            f"start={args.start}",
            f"end={args.end}",
            f"actual_start={result.index.min().date()}",
            f"actual_end={result.index.max().date()}",
            f"strategy_mode={args.strategy_mode}",
            f"strategy_label={strategy_label}",
            f"lookback={args.lookback}",
            f"top_percent={args.top_percent}",
            f"classic_lookback={CLASSIC_LOOKBACK}",
            f"classic_top_percent={CLASSIC_TOP_PERCENT}",
            f"core_weight={effective_core_weight}",
            f"momentum_weight={effective_momentum_weight}",
            f"target_volatility={effective_target_volatility}",
            f"volatility_lookback={args.volatility_lookback}",
            f"trend_ma_days={effective_trend_days}",
            f"defensive_exposure={effective_defensive_exposure}",
            f"requested_momentum_weight={args.momentum_weight}",
            f"requested_target_volatility={args.target_volatility}",
            f"requested_trend_ma_days={args.trend_ma_days}",
            f"requested_defensive_exposure={args.defensive_exposure}",
            f"oos_start={args.oos_start}",
            "temporal_holdout_is_true_oos=false",
            "true_oos_definition=future data after parameter and code freeze or strict walk-forward",
            "cash_return_assumption=0",
            "leverage_allowed=false",
            f"fee_rate={args.fee_rate}",
            f"initial_capital={args.initial_capital}",
            f"benchmark={args.benchmark}",
            f"requested_universe_size={len(tickers)}",
            f"universe_size={prices.shape[1]}",
            f"missing_quote_ratio={missing_quote_ratio}",
            f"tickers={','.join(prices.columns)}",
            f"unavailable_tickers={','.join(unavailable)}",
        ]
    )
    (output_dir / "run_metadata.txt").write_text(metadata, encoding="utf-8")
    report_path = generate_html_report(output_dir)

    print("\n=== 回测完成 ===")
    print(summary)
    print(f"\n结果已保存到：{output_dir.resolve()}")
    print(f"分析报告已生成：{report_path.resolve()}")


if __name__ == "__main__":
    main()
