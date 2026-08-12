"""将回测输出整理为可直接在浏览器打开的 HTML 研究报告。

报告同时兼容 2.x 经典动量结果与 3.x 风险控制结果。所有缺失的新字段都会
显示为 N/A 或带解释的空状态，而不会阻止旧样例生成报告。
"""

from __future__ import annotations

import argparse
import html
import math
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


TRADING_DAYS_PER_YEAR = 242


def safe_float(value: object, default: float | None = None) -> float | None:
    """把 CSV/元数据值安全转换为有限浮点数。"""
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    return converted if math.isfinite(converted) else default


def format_percent(value: float | None) -> str:
    value = safe_float(value)
    return "N/A" if value is None else f"{value:.2%}"


def format_signed_percent(value: float | None) -> str:
    value = safe_float(value)
    return "N/A" if value is None else f"{value:+.2%}"


def format_percentage_points(value: float | None) -> str:
    value = safe_float(value)
    return "N/A" if value is None else f"{value * 100:+.2f} 个百分点"


def format_number(value: float | None, digits: int = 2) -> str:
    value = safe_float(value)
    return "N/A" if value is None else f"{value:.{digits}f}"


def read_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if not path.exists():
        return metadata
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            metadata[key.strip()] = value.strip()
    return metadata


def read_optional_csv(path: Path, **kwargs: object) -> pd.DataFrame:
    """读取可选结果表；不存在或只有空表时返回空 DataFrame。"""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def metric(metrics: pd.DataFrame, series_name: str, column: str) -> float | None:
    if "series" not in metrics.columns or column not in metrics.columns:
        return None
    matching = metrics.loc[metrics["series"].astype(str) == str(series_name)]
    if matching.empty:
        return None
    return safe_float(matching.iloc[0][column])


def html_table(headers: list[str], rows: list[list[str]], table_class: str = "") -> str:
    """生成带横向滚动容器的安全 HTML 表格。"""
    header_html = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(item))}</td>" for item in row) + "</tr>"
        for row in rows
    )
    class_name = html.escape(table_class)
    return (
        '<div class="table-scroll">'
        f'<table class="{class_name}"><thead><tr>{header_html}</tr></thead>'
        f"<tbody>{body_html}</tbody></table></div>"
    )


def longest_drawdown_days(nav: pd.Series) -> int:
    """计算净值处于历史高点以下的最长连续交易日。"""
    values = pd.to_numeric(nav, errors="coerce").dropna()
    if values.empty:
        return 0
    underwater = values / values.cummax() - 1.0 < 0
    longest = 0
    current = 0
    for value in underwater:
        current = current + 1 if bool(value) else 0
        longest = max(longest, current)
    return longest


def series_label(series_name: str, benchmark: str, primary_label: str) -> str:
    labels = {
        "strategy": primary_label,
        "classic_momentum_20_10": "经典动量（20 日 / 前 10%）",
        "universe_equal_weight": "股票池等权买入持有",
        str(benchmark): f"沪深 300（{benchmark}）" if benchmark == "000300" else f"基准（{benchmark}）",
    }
    return labels.get(str(series_name), str(series_name))


def save_research_diagnostics(
    equity: pd.DataFrame,
    output_path: Path,
    benchmark: str,
    risk_control_log: pd.DataFrame | None = None,
) -> None:
    """生成四组合回撤、年度收益、月度收益和风险敞口四联图。"""
    figure, axes = plt.subplots(2, 2, figsize=(13.5, 9.2), facecolor="#eef2f4")
    figure.subplots_adjust(hspace=0.34, wspace=0.22)

    series_specs = [
        ("Primary strategy", "strategy_nav", "strategy_return", "#1b6f68", 2.0),
        ("Classic 20/10", "classic_momentum_nav", "classic_momentum_return", "#b64b42", 1.45),
        ("Equal-weight", "equal_weight_nav", "equal_weight_return", "#b27a22", 1.3),
        (f"Benchmark {benchmark}", "benchmark_nav", "benchmark_return", "#315a72", 1.3),
    ]
    for axis in axes.flat:
        axis.set_facecolor("#ffffff")
        axis.spines[["top", "right"]].set_visible(False)
        axis.spines[["left", "bottom"]].set_color("#bcc8ce")

    for label, nav_column, _, color, width in series_specs:
        if nav_column in equity.columns:
            nav = pd.to_numeric(equity[nav_column], errors="coerce")
            drawdown = nav / nav.cummax() - 1.0
            axes[0, 0].plot(drawdown.index, drawdown, label=label, color=color, linewidth=width)
    axes[0, 0].axhline(0, color="#89979f", linewidth=0.7)
    axes[0, 0].set_title("Drawdown comparison", loc="left", fontweight="bold")
    axes[0, 0].set_ylabel("Drawdown")
    axes[0, 0].grid(axis="y", alpha=0.18)
    axes[0, 0].legend(frameon=False, fontsize=8)

    annual_returns: dict[str, pd.Series] = {}
    color_map: dict[str, str] = {}
    for label, _, return_column, color, _ in series_specs:
        if return_column in equity.columns:
            values = pd.to_numeric(equity[return_column], errors="coerce").fillna(0.0)
            annual_returns[label] = (1.0 + values).groupby(equity.index.year).prod() - 1.0
            color_map[label] = color
    if annual_returns:
        annual_frame = pd.DataFrame(annual_returns)
        annual_frame.plot(
            kind="bar",
            ax=axes[0, 1],
            width=0.82,
            color=[color_map[column] for column in annual_frame.columns],
        )
        axes[0, 1].legend(frameon=False, fontsize=7)
    axes[0, 1].axhline(0, color="#89979f", linewidth=0.7)
    axes[0, 1].set_title("Calendar-year return", loc="left", fontweight="bold")
    axes[0, 1].set_xlabel("Year")
    axes[0, 1].set_ylabel("Return")
    axes[0, 1].grid(axis="y", alpha=0.18)

    strategy_returns = pd.to_numeric(equity["strategy_return"], errors="coerce").fillna(0.0)
    monthly = (1.0 + strategy_returns).groupby(equity.index.to_period("M")).prod() - 1.0
    recent_monthly = monthly.tail(36)
    colors = ["#1b6f68" if value >= 0 else "#b64b42" for value in recent_monthly]
    axes[1, 0].bar(
        [str(period) for period in recent_monthly.index],
        recent_monthly.values,
        color=colors,
        width=0.78,
    )
    axes[1, 0].axhline(0, color="#89979f", linewidth=0.7)
    axes[1, 0].set_title("Primary strategy monthly return — latest 36", loc="left", fontweight="bold")
    axes[1, 0].set_ylabel("Return")
    axes[1, 0].tick_params(axis="x", rotation=75, labelsize=7)
    axes[1, 0].grid(axis="y", alpha=0.18)

    if "equity_exposure" in equity.columns:
        exposure = pd.to_numeric(equity["equity_exposure"], errors="coerce").clip(0.0, 1.0)
        axes[1, 1].fill_between(
            exposure.index,
            0,
            exposure.values,
            color="#1b6f68",
            alpha=0.22,
            label="Actual equity exposure",
        )
        axes[1, 1].plot(exposure.index, exposure, color="#1b6f68", linewidth=1.1)
        if risk_control_log is not None and not risk_control_log.empty:
            if {"trade_date", "final_equity_exposure"}.issubset(risk_control_log.columns):
                dates = pd.to_datetime(risk_control_log["trade_date"], errors="coerce")
                targets = pd.to_numeric(
                    risk_control_log["final_equity_exposure"], errors="coerce"
                )
                axes[1, 1].step(
                    dates,
                    targets,
                    where="post",
                    color="#b27a22",
                    linewidth=1.0,
                    alpha=0.9,
                    label="Rebalance target",
                )
        axes[1, 1].set_ylim(-0.02, 1.04)
        axes[1, 1].set_ylabel("Portfolio weight")
        axes[1, 1].set_title("Equity risk budget through time", loc="left", fontweight="bold")
        axes[1, 1].legend(frameon=False, fontsize=8)
    else:
        turnover = pd.to_numeric(equity.get("turnover", pd.Series(index=equity.index)), errors="coerce")
        cost = pd.to_numeric(equity.get("trading_cost", pd.Series(index=equity.index)), errors="coerce")
        monthly_turnover = turnover.groupby(equity.index.to_period("M")).sum().tail(36)
        monthly_cost = cost.groupby(equity.index.to_period("M")).sum().reindex(monthly_turnover.index)
        axes[1, 1].bar(
            [str(period) for period in monthly_turnover.index],
            monthly_turnover.values,
            color="#315a72",
            label="Turnover",
        )
        axes[1, 1].plot(
            range(len(monthly_turnover)),
            monthly_cost.values,
            color="#b27a22",
            marker="o",
            markersize=2,
            label="Trading cost",
        )
        axes[1, 1].tick_params(axis="x", rotation=75, labelsize=7)
        axes[1, 1].set_title("Monthly turnover and cost — latest 36", loc="left", fontweight="bold")
        axes[1, 1].legend(frameon=False, fontsize=8)
    axes[1, 1].grid(axis="y", alpha=0.18)

    figure.savefig(output_path, dpi=165, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)


def generate_html_report(output_dir: str | Path) -> Path:
    """根据回测 CSV、PNG 和元数据生成 ``analysis_report.html``。"""
    output_dir = Path(output_dir)
    equity_path = output_dir / "equity_curve.csv"
    metrics_path = output_dir / "performance_metrics.csv"
    log_path = output_dir / "rebalance_log.csv"
    risk_log_path = output_dir / "risk_control_log.csv"
    metadata_path = output_dir / "run_metadata.txt"
    chart_path = output_dir / "equity_curve.png"
    diagnostics_path = output_dir / "research_diagnostics.png"
    sensitivity_path = output_dir / "parameter_sensitivity.csv"
    subperiod_path = output_dir / "subperiod_performance.csv"
    holdout_path = output_dir / "temporal_holdout_diagnostic.csv"
    legacy_holdout_path = output_dir / "out_of_sample_performance.csv"
    if not holdout_path.exists() and legacy_holdout_path.exists():
        holdout_path = legacy_holdout_path
    quality_path = output_dir / "data_quality.csv"

    if not equity_path.exists() or not metrics_path.exists():
        raise FileNotFoundError("缺少 equity_curve.csv 或 performance_metrics.csv，无法生成报告。")

    equity = pd.read_csv(equity_path, parse_dates=["date"]).set_index("date").sort_index()
    metrics = pd.read_csv(metrics_path)
    metadata = read_metadata(metadata_path)
    benchmark = metadata.get("benchmark", "000300")
    lookback = metadata.get("lookback", "20")
    top_percent_value = safe_float(metadata.get("top_percent"), 0.10) or 0.10
    fee_rate_value = safe_float(metadata.get("fee_rate"), 0.001) or 0.0
    strategy_mode = metadata.get("strategy_mode", "classic")
    strategy_label_code = metadata.get("strategy_label", "")
    is_risk_controlled = strategy_mode == "risk_controlled" or strategy_label_code.startswith(
        "risk_controlled"
    )
    primary_label = (
        "风控核心—卫星动量"
        if is_risk_controlled
        else ("主策略（纯动量）" if strategy_mode == "classic" else "主策略")
    )
    risk_control_log = read_optional_csv(risk_log_path)
    save_research_diagnostics(equity, diagnostics_path, benchmark, risk_control_log)

    strategy_total = metric(metrics, "strategy", "total_return")
    strategy_annual = metric(metrics, "strategy", "annualized_return")
    strategy_volatility = metric(metrics, "strategy", "annualized_volatility")
    strategy_drawdown = metric(metrics, "strategy", "max_drawdown")
    strategy_sharpe = metric(metrics, "strategy", "sharpe_ratio_rf_0")
    strategy_sortino = metric(metrics, "strategy", "sortino_ratio_rf_0")
    strategy_calmar = metric(metrics, "strategy", "calmar_ratio")
    strategy_exposure_metric = metric(metrics, "strategy", "average_equity_exposure")

    classic_name = "classic_momentum_20_10"
    classic_available = (
        "series" in metrics.columns
        and (metrics["series"].astype(str) == classic_name).any()
        and "classic_momentum_return" in equity.columns
    )
    classic_drawdown = metric(metrics, classic_name, "max_drawdown") if classic_available else None
    classic_volatility = (
        metric(metrics, classic_name, "annualized_volatility") if classic_available else None
    )
    classic_sharpe = metric(metrics, classic_name, "sharpe_ratio_rf_0") if classic_available else None

    benchmark_annual = metric(metrics, benchmark, "annualized_return")
    benchmark_total = metric(metrics, benchmark, "total_return")
    annual_gap = (
        strategy_annual - benchmark_annual
        if strategy_annual is not None and benchmark_annual is not None
        else None
    )
    drawdown_improvement = (
        abs(classic_drawdown) - abs(strategy_drawdown)
        if classic_drawdown is not None and strategy_drawdown is not None
        else None
    )
    drawdown_reduction_ratio = (
        1.0 - abs(strategy_drawdown) / abs(classic_drawdown)
        if classic_drawdown not in (None, 0.0) and strategy_drawdown is not None
        else None
    )
    volatility_reduction = (
        classic_volatility - strategy_volatility
        if classic_volatility is not None and strategy_volatility is not None
        else None
    )
    sharpe_change = (
        strategy_sharpe - classic_sharpe
        if strategy_sharpe is not None and classic_sharpe is not None
        else None
    )

    active_returns = None
    if {"strategy_return", "benchmark_return"}.issubset(equity.columns):
        active_returns = (
            pd.to_numeric(equity["strategy_return"], errors="coerce")
            - pd.to_numeric(equity["benchmark_return"], errors="coerce")
        ).dropna()
    tracking_error = (
        float(active_returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        if active_returns is not None and len(active_returns) > 1
        else None
    )
    information_ratio = (
        float(active_returns.mean() / active_returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        if active_returns is not None and active_returns.std(ddof=1) > 0
        else None
    )
    relative_total_return = None
    if {"strategy_nav", "benchmark_nav"}.issubset(equity.columns):
        relative_total_return = safe_float(
            equity["strategy_nav"].iloc[-1] / equity["benchmark_nav"].iloc[-1] - 1.0
        )
    strategy_returns = pd.to_numeric(equity["strategy_return"], errors="coerce").dropna()
    positive_day_ratio = float((strategy_returns > 0).mean()) if not strategy_returns.empty else None
    best_day = float(strategy_returns.max()) if not strategy_returns.empty else None
    worst_day = float(strategy_returns.min()) if not strategy_returns.empty else None
    rebalance_turnover = pd.to_numeric(
        equity.get("turnover", pd.Series(index=equity.index, dtype=float)), errors="coerce"
    ).dropna()
    rebalance_turnover = rebalance_turnover.loc[rebalance_turnover > 0]
    average_turnover = float(rebalance_turnover.mean()) if not rebalance_turnover.empty else None
    simple_cost_sum = safe_float(
        pd.to_numeric(
            equity.get("trading_cost", pd.Series(index=equity.index, dtype=float)), errors="coerce"
        ).sum()
    )
    max_drawdown_duration = (
        longest_drawdown_days(equity["strategy_nav"]) if "strategy_nav" in equity.columns else 0
    )

    monthly_strategy = (1.0 + strategy_returns).groupby(strategy_returns.index.to_period("M")).prod() - 1.0
    best_month = monthly_strategy.idxmax() if not monthly_strategy.empty else None
    worst_month = monthly_strategy.idxmin() if not monthly_strategy.empty else None
    positive_month_ratio = float((monthly_strategy > 0).mean()) if not monthly_strategy.empty else None

    daily_exposure = (
        pd.to_numeric(equity["equity_exposure"], errors="coerce").dropna().clip(0.0, 1.0)
        if "equity_exposure" in equity.columns
        else pd.Series(dtype=float)
    )
    daily_cash = (
        pd.to_numeric(equity["cash_weight"], errors="coerce").dropna().clip(0.0, 1.0)
        if "cash_weight" in equity.columns
        else pd.Series(dtype=float)
    )
    average_exposure = (
        float(daily_exposure.mean())
        if not daily_exposure.empty
        else strategy_exposure_metric
    )
    # 2.x 经典策略没有单独输出 exposure 列，但其目标权重按设计合计为 100%。
    # 只在明确的旧版非风控模式中使用这一兼容推断，避免把未知风控结果误写成满仓。
    if average_exposure is None and not is_risk_controlled:
        average_exposure = 1.0
    average_cash = (
        float(daily_cash.mean())
        if not daily_cash.empty
        else (1.0 - average_exposure if average_exposure is not None else None)
    )

    selected_text = "无调仓记录"
    latest_rebalance_rows: list[list[str]] = []
    latest_rebalance_headers = ["代码", "排名", f"{lookback} 日动量"]
    rebalance_count = 0
    rebalance_log = pd.DataFrame()
    if log_path.exists():
        rebalance_log = read_optional_csv(
            log_path,
            parse_dates=["signal_date", "trade_date"],
            dtype={"ticker": str},
        )
        if not rebalance_log.empty:
            rebalance_log["ticker"] = rebalance_log["ticker"].astype(str).str.zfill(6)
            rebalance_count = int(rebalance_log["trade_date"].nunique())
            latest_trade_date = rebalance_log["trade_date"].max()
            latest = rebalance_log.loc[
                rebalance_log["trade_date"] == latest_trade_date
            ].sort_values("rank")
            selected_text = "、".join(latest["ticker"].astype(str).tolist())
            momentum_column = (
                "momentum_return" if "momentum_return" in latest.columns else "return_20d"
            )
            weight_columns = [
                ("momentum_target_weight", "纯动量目标"),
                ("blended_target_weight", "核心—卫星目标"),
                ("scaled_target_weight", "风控后目标"),
                ("executed_weight", "实际执行"),
            ]
            available_weights = [item for item in weight_columns if item[0] in latest.columns]
            if not available_weights:
                fallback = "target_weight" if "target_weight" in latest.columns else None
                if fallback:
                    available_weights = [(fallback, "计划目标权重")]
            latest_rebalance_headers.extend(label for _, label in available_weights)
            for _, item in latest.iterrows():
                row = [
                    str(item["ticker"]),
                    str(int(item["rank"])),
                    format_percent(safe_float(item.get(momentum_column))),
                ]
                row.extend(format_percent(safe_float(item.get(column))) for column, _ in available_weights)
                latest_rebalance_rows.append(row)

    core_weight = safe_float(metadata.get("core_weight"))
    momentum_weight = safe_float(metadata.get("momentum_weight"))
    target_volatility = safe_float(metadata.get("target_volatility"))
    volatility_lookback = metadata.get("volatility_lookback", "N/A")
    trend_ma_days = metadata.get("trend_ma_days", "0")
    defensive_exposure = safe_float(metadata.get("defensive_exposure"))
    oos_start = metadata.get("oos_start", "")
    no_leverage = metadata.get("leverage_allowed", "false").lower() != "true"
    cash_return_text = metadata.get("cash_return_assumption", "0")

    config_rows = [
        ["主策略", primary_label],
        ["回测区间", f"{equity.index.min().date()} 至 {equity.index.max().date()}"],
        ["动量信号", f"{lookback} 个交易日 / 排名前 {format_percent(top_percent_value)}"],
        ["核心 / 卫星", (
            f"股票池等权核心 {format_percent(core_weight)} / 动量卫星 {format_percent(momentum_weight)}"
            if core_weight is not None or momentum_weight is not None
            else "旧版结果未设置核心—卫星权重"
        )],
        ["波动率控制", (
            f"{volatility_lookback} 日估计 / 目标 {format_percent(target_volatility)} / 仅降仓"
            if target_volatility is not None
            else "旧版结果未启用"
        )],
        ["趋势控制", (
            f"基准 {trend_ma_days} 日均线 / 弱市仓位上限 {format_percent(defensive_exposure)}"
            if safe_float(trend_ma_days, 0.0) and safe_float(trend_ma_days, 0.0) > 0
            else "关闭或旧版结果未启用"
        )],
        ["杠杆 / 现金", f"{'禁止杠杆' if no_leverage else '请核对杠杆设置'}；现金日收益假设 {cash_return_text}"],
        ["交易成本", f"简化单边成本 {format_percent(fee_rate_value)}"],
        ["比较基准", f"沪深 300（{benchmark}）" if benchmark == "000300" else benchmark],
        ["股票池", f"{metadata.get('universe_size', 'N/A')} 只固定 CSV 示例股票（存在幸存者偏差）"],
        ["数据源", metadata.get("data_source", "AkShare / 腾讯财经公开 A 股行情")],
        ["项目版本", metadata.get("project_version", "旧版 / 未记录")],
        ["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
    ]

    series_order = ["strategy", classic_name, "universe_equal_weight", str(benchmark)]
    metric_rows: list[list[str]] = []
    for series_name in series_order:
        if "series" not in metrics.columns or not (
            metrics["series"].astype(str) == series_name
        ).any():
            continue
        metric_rows.append(
            [
                series_label(series_name, benchmark, primary_label),
                format_percent(metric(metrics, series_name, "total_return")),
                format_percent(metric(metrics, series_name, "annualized_return")),
                format_percent(metric(metrics, series_name, "annualized_volatility")),
                format_percent(metric(metrics, series_name, "max_drawdown")),
                format_number(metric(metrics, series_name, "sharpe_ratio_rf_0")),
                format_number(metric(metrics, series_name, "sortino_ratio_rf_0")),
                format_number(metric(metrics, series_name, "calmar_ratio")),
                format_percent(metric(metrics, series_name, "average_equity_exposure")),
            ]
        )

    risk_rows = [
        ["相对基准累计收益", format_percent(relative_total_return)],
        ["相对基准年化收益率差", format_signed_percent(annual_gap)],
        ["跟踪误差（年化）", format_percent(tracking_error)],
        ["信息比率", format_number(information_ratio)],
        ["正收益交易日比例", format_percent(positive_day_ratio)],
        ["最佳 / 最差单日", f"{format_percent(best_day)} / {format_percent(worst_day)}"],
        ["最长回撤持续期", f"{max_drawdown_duration} 个交易日"],
        ["平均调仓换手率", format_percent(average_turnover)],
        ["交易成本率简单累计", format_percent(simple_cost_sum)],
        ["历史 95% VaR（日频）", format_percent(metric(metrics, "strategy", "value_at_risk_95"))],
        ["历史 95% CVaR（日频）", format_percent(metric(metrics, "strategy", "conditional_value_at_risk_95"))],
    ]

    risk_log_rows: list[list[str]] = []
    average_target_exposure = None
    minimum_target_exposure = None
    volatility_binding_count = 0
    trend_defensive_count = 0
    if not risk_control_log.empty:
        for column in ["signal_date", "trade_date"]:
            if column in risk_control_log.columns:
                risk_control_log[column] = pd.to_datetime(risk_control_log[column], errors="coerce")
        target_series = pd.to_numeric(
            risk_control_log.get("final_equity_exposure"), errors="coerce"
        ).dropna()
        if not target_series.empty:
            average_target_exposure = float(target_series.mean())
            minimum_target_exposure = float(target_series.min())
        vol_caps = pd.to_numeric(risk_control_log.get("volatility_cap"), errors="coerce")
        trend_caps = pd.to_numeric(risk_control_log.get("trend_cap"), errors="coerce")
        volatility_binding_count = int((vol_caps < 1.0 - 1e-9).sum())
        trend_defensive_count = int((trend_caps < 1.0 - 1e-9).sum())
        recent_risk = risk_control_log.sort_values("trade_date").tail(12).iloc[::-1]
        for _, item in recent_risk.iterrows():
            above = item.get("above_trend")
            if pd.isna(above):
                trend_state = "关闭 / 历史不足"
            elif str(above).strip().lower() in {"true", "1", "1.0"}:
                trend_state = "均线上方"
            else:
                trend_state = "均线下方"
            signal_value = item.get("signal_date")
            trade_value = item.get("trade_date")
            signal_text = (
                pd.Timestamp(signal_value).date().isoformat() if not pd.isna(signal_value) else "N/A"
            )
            trade_text = (
                pd.Timestamp(trade_value).date().isoformat() if not pd.isna(trade_value) else "N/A"
            )
            risk_log_rows.append(
                [
                    signal_text,
                    trade_text,
                    format_percent(safe_float(item.get("realized_volatility"))),
                    format_percent(safe_float(item.get("volatility_cap"))),
                    trend_state,
                    format_percent(safe_float(item.get("trend_cap"))),
                    format_percent(safe_float(item.get("final_equity_exposure"))),
                    format_percent(safe_float(item.get("cash_target"))),
                ]
            )

    exposure_for_strip = average_exposure if average_exposure is not None else average_target_exposure
    exposure_for_strip = min(1.0, max(0.0, exposure_for_strip or 0.0))
    cash_for_strip = 1.0 - exposure_for_strip
    exposure_strip_html = f"""
      <div class="risk-ledger" aria-label="平均股票风险敞口与现金权重">
        <div class="risk-ledger__head">
          <span>平均实际风险预算</span>
          <code>{format_percent(exposure_for_strip)} 股票 / {format_percent(cash_for_strip)} 现金</code>
        </div>
        <div class="risk-rail" role="img" aria-label="股票敞口 {format_percent(exposure_for_strip)}，现金 {format_percent(cash_for_strip)}">
          <span class="risk-rail__equity" style="width:{exposure_for_strip * 100:.4f}%"></span>
          <span class="risk-rail__cash" style="width:{cash_for_strip * 100:.4f}%"></span>
        </div>
        <div class="risk-legend"><span><i class="dot equity"></i>股票风险资产</span><span><i class="dot cash"></i>零收益现金假设</span></div>
      </div>
    """

    subperiod_rows: list[list[str]] = []
    subperiod = read_optional_csv(subperiod_path)
    if not subperiod.empty:
        period_labels = {"full_sample": "全样本", "first_half": "前半段", "second_half": "后半段"}
        for _, item in subperiod.iterrows():
            subperiod_rows.append(
                [
                    period_labels.get(str(item.get("period")), str(item.get("period"))),
                    series_label(str(item.get("series")), benchmark, primary_label),
                    f"{item.get('start_date')} 至 {item.get('end_date')}",
                    format_percent(safe_float(item.get("annualized_return"))),
                    format_percent(safe_float(item.get("annualized_volatility"))),
                    format_percent(safe_float(item.get("max_drawdown"))),
                    format_number(safe_float(item.get("sharpe_ratio_rf_0"))),
                ]
            )

    holdout_rows: list[list[str]] = []
    holdout = read_optional_csv(holdout_path)
    if not holdout.empty:
        phase_order = {"development": 0, "holdout": 1}
        holdout = holdout.assign(
            _phase_order=holdout["phase"].astype(str).map(phase_order).fillna(2),
            _series_order=holdout["series"].astype(str).map(
                {name: position for position, name in enumerate(series_order)}
            ).fillna(9),
        ).sort_values(["_phase_order", "_series_order"])
        phase_labels = {"development": "开发期（回顾）", "holdout": "时间留出期（回顾）"}
        for _, item in holdout.iterrows():
            holdout_rows.append(
                [
                    phase_labels.get(str(item.get("phase")), str(item.get("phase"))),
                    series_label(str(item.get("series")), benchmark, primary_label),
                    f"{item.get('start_date')} 至 {item.get('end_date')}",
                    format_percent(safe_float(item.get("annualized_return"))),
                    format_percent(safe_float(item.get("annualized_volatility"))),
                    format_percent(safe_float(item.get("max_drawdown"))),
                    format_number(safe_float(item.get("sharpe_ratio_rf_0"))),
                    format_number(safe_float(item.get("sortino_ratio_rf_0"))),
                ]
            )

    sensitivity_rows: list[list[str]] = []
    sensitivity = read_optional_csv(sensitivity_path)
    if not sensitivity.empty:
        requested = sensitivity.get(
            "is_requested_setting", pd.Series(False, index=sensitivity.index)
        ).astype(str).str.lower().eq("true")
        sensitivity = sensitivity.assign(_requested=requested).sort_values(
            ["_requested", "lookback", "top_percent"],
            ascending=[False, True, True],
        )
        for _, item in sensitivity.iterrows():
            sensitivity_rows.append(
                [
                    "本次动量参数" if bool(item["_requested"]) else "纯动量对照参数",
                    str(int(item["lookback"])),
                    format_percent(safe_float(item.get("top_percent"))),
                    format_number(safe_float(item.get("average_selected_stocks")), 1),
                    format_percent(safe_float(item.get("annualized_return"))),
                    format_percent(safe_float(item.get("max_drawdown"))),
                    format_number(safe_float(item.get("sharpe_ratio_rf_0"))),
                ]
            )

    quality_rows: list[list[str]] = []
    quality = read_optional_csv(quality_path, dtype={"ticker": str})
    if not quality.empty:
        quality["ticker"] = quality["ticker"].astype(str).str.zfill(6)
        quality = quality.sort_values("missing_quote_ratio", ascending=False)
        for _, item in quality.iterrows():
            quality_rows.append(
                [
                    str(item.get("ticker")),
                    str(item.get("first_quote_date")),
                    str(item.get("last_quote_date")),
                    str(int(item.get("quote_days", 0))),
                    format_percent(safe_float(item.get("missing_quote_ratio"))),
                    str(int(item.get("longest_missing_run", 0))),
                ]
            )

    insights: list[str] = []
    if drawdown_improvement is not None:
        if drawdown_improvement >= 0:
            insights.append(
                f"相对经典 20 日 / 前 10% 动量，主策略最大回撤绝对值减少 "
                f"{format_percentage_points(drawdown_improvement)}（相对缩减 {format_percent(drawdown_reduction_ratio)}）。"
            )
        else:
            insights.append(
                f"本次主策略未改善经典动量的最大回撤，绝对值反而增加 "
                f"{format_percentage_points(abs(drawdown_improvement))}。"
            )
    elif not classic_available:
        insights.append("该结果来自旧版输出，未单独保存经典 20 日 / 前 10% 对照，无法计算回撤改善幅度。")
    if annual_gap is not None:
        direction = "高于" if annual_gap >= 0 else "低于"
        insights.append(
            f"主策略年化收益率在本样本内{direction}基准 {format_percent(abs(annual_gap))}；"
            "这是历史差异，不是未来收益预测。"
        )
    if best_month is not None and worst_month is not None:
        insights.append(
            f"主策略最佳月份为 {best_month}（{format_percent(monthly_strategy.loc[best_month])}），"
            f"最差月份为 {worst_month}（{format_percent(monthly_strategy.loc[worst_month])}）；"
            f"正收益月份占比 {format_percent(positive_month_ratio)}。"
        )
    if average_exposure is not None:
        insights.append(
            f"主策略日均实际股票敞口为 {format_percent(average_exposure)}，"
            f"日均现金为 {format_percent(average_cash)}；降仓意味着收益与损失都会被同步压低。"
        )
    insights.append(
        f"回测共记录 {rebalance_count} 次月度调仓；最近一次动量入选代码为：{selected_text}。"
    )

    chart_html = (
        f'<img class="chart" src="{html.escape(chart_path.name)}" alt="四组合净值曲线">'
        if chart_path.exists()
        else '<div class="empty">未找到净值图片；请确认 equity_curve.png 已生成。</div>'
    )
    diagnostics_html = (
        f'<img class="chart" src="{html.escape(diagnostics_path.name)}" alt="回撤、年度收益、月度收益与风险敞口诊断图">'
        if diagnostics_path.exists()
        else '<div class="empty">研究诊断图生成失败。</div>'
    )
    latest_table = (
        html_table(latest_rebalance_headers, latest_rebalance_rows, "position-table")
        if latest_rebalance_rows
        else '<div class="empty">没有可展示的最近调仓记录。</div>'
    )
    risk_log_table = (
        html_table(
            ["信号日", "执行日", "估计波动率", "波动率上限", "趋势状态", "趋势上限", "目标股票仓位", "目标现金"],
            risk_log_rows,
            "risk-log-table",
        )
        if risk_log_rows
        else '<div class="empty">旧版结果未包含 risk_control_log.csv；仍可查看经典回测，但无法审计逐次风险预算。</div>'
    )
    subperiod_table = (
        html_table(
            ["时期", "组合", "区间", "年化收益率", "年化波动率", "最大回撤", "夏普比率"],
            subperiod_rows,
        )
        if subperiod_rows
        else '<div class="empty">暂无分阶段绩效数据。</div>'
    )
    holdout_table = (
        html_table(
            ["区段", "组合", "区间", "年化收益率", "年化波动率", "最大回撤", "夏普", "Sortino"],
            holdout_rows,
            "holdout-table",
        )
        if holdout_rows
        else '<div class="empty">该结果未提供时间留出表。新版运行可通过预先指定 oos_start 生成此诊断。</div>'
    )
    sensitivity_table = (
        html_table(
            ["类型", "观察期", "选股比例", "平均持股数", "年化收益率", "最大回撤", "夏普比率"],
            sensitivity_rows,
        )
        if sensitivity_rows
        else '<div class="empty">暂无参数敏感性数据。</div>'
    )
    quality_table = (
        html_table(
            ["代码", "首个报价日", "最后报价日", "报价天数", "缺失比例", "最长连续缺失"],
            quality_rows,
        )
        if quality_rows
        else '<div class="empty">暂无数据质量记录。</div>'
    )

    thesis = (
        f"本次历史回测中，主策略最大回撤为 {format_percent(strategy_drawdown)}，"
        f"经典 20/10 动量为 {format_percent(classic_drawdown)}；"
        f"回撤绝对值改善 {format_percentage_points(drawdown_improvement)}。"
        if classic_available
        else f"本次历史回测中，主策略最大回撤为 {format_percent(strategy_drawdown)}。旧版结果未提供独立经典对照。"
    )
    risk_status = "风险控制模式" if is_risk_controlled else "经典 / 自定义动量模式"
    report_title = (
        "A 股风险控制型动量研究报告"
        if is_risk_controlled
        else "A 股经典动量研究报告"
    )
    report_title_html = (
        "A 股风险控制型<br>动量研究报告"
        if is_risk_controlled
        else "A 股经典<br>动量研究报告"
    )

    report_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(report_title)}</title>
  <style>
    :root {{
      --navy: #142638;
      --ink: #1d2b34;
      --muted: #65747d;
      --line: #cbd5da;
      --line-soft: #e3eaed;
      --paper: #f0f4f5;
      --panel: #ffffff;
      --wash: #e8eff1;
      --blue: #315a72;
      --jade: #1b6f68;
      --jade-soft: #dcece9;
      --cinnabar: #b64b42;
      --cinnabar-soft: #f3e3e0;
      --ochre: #a96f1d;
      --ochre-soft: #f4ead8;
      --mono: "Cascadia Mono", "IBM Plex Mono", Consolas, monospace;
      --display: "Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", SimSun, serif;
      --body: "Inter", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(90deg, transparent 0, transparent calc(50% - .5px), rgba(49,90,114,.045) 50%, transparent calc(50% + .5px)),
        var(--paper);
      font-family: var(--body);
      line-height: 1.65;
      -webkit-font-smoothing: antialiased;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 24px 64px; }}
    .masthead {{
      position: relative;
      overflow: hidden;
      min-height: 320px;
      padding: 34px 38px 30px;
      color: #f7fbfc;
      background: var(--navy);
      border-top: 5px solid var(--jade);
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(260px, .55fr);
      gap: 36px;
      align-items: end;
    }}
    .masthead::after {{
      content: "RISK / RETURN";
      position: absolute;
      right: -18px;
      top: 7px;
      color: rgba(255,255,255,.045);
      font: 700 72px/1 var(--mono);
      letter-spacing: -.07em;
      pointer-events: none;
    }}
    .eyebrow {{
      margin: 0 0 14px;
      color: #a8c6c2;
      font: 700 11px/1.3 var(--mono);
      letter-spacing: .18em;
      text-transform: uppercase;
    }}
    h1, h2, h3 {{ font-family: var(--display); }}
    h1 {{ margin: 0; max-width: 760px; font-size: clamp(35px, 4.2vw, 50px); line-height: 1.12; font-weight: 700; }}
    .masthead__thesis {{ margin: 20px 0 0; max-width: 760px; color: #d5e1e5; font-size: 16px; }}
    .run-stamp {{ border-left: 1px solid rgba(255,255,255,.2); padding-left: 22px; position: relative; z-index: 1; }}
    .run-stamp strong {{ display: block; font: 700 18px/1.4 var(--mono); color: #ffffff; }}
    .run-stamp span {{ display: block; margin-top: 6px; color: #aabac2; font-size: 13px; }}
    .run-stamp .mode {{ color: #9bd0c8; }}
    .report-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 22px; align-items: start; margin-top: 22px; }}
    .main-column, .side-column {{ min-width: 0; }}
    .side-column {{ position: static; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); margin-bottom: 18px; }}
    .panel__head {{ padding: 18px 22px 14px; border-bottom: 1px solid var(--line-soft); display: flex; justify-content: space-between; gap: 16px; align-items: baseline; }}
    .panel__head h2 {{ margin: 0; font-size: 21px; line-height: 1.25; }}
    .panel__head small {{ color: var(--muted); font: 11px/1.3 var(--mono); letter-spacing: .08em; text-transform: uppercase; }}
    .panel__body {{ padding: 22px; }}
    .metric-ledger {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--line); margin-bottom: 18px; background: #fff; }}
    .metric-card {{ min-height: 122px; padding: 17px 18px; border-right: 1px solid var(--line); position: relative; }}
    .metric-card:last-child {{ border-right: 0; }}
    .metric-card::before {{ content: ""; position: absolute; left: 0; top: 0; width: 100%; height: 3px; background: var(--blue); }}
    .metric-card.improvement::before {{ background: var(--jade); }}
    .metric-card.loss::before {{ background: var(--cinnabar); }}
    .metric-card.exposure::before {{ background: var(--ochre); }}
    .metric-card .label {{ color: var(--muted); font-size: 12px; line-height: 1.35; }}
    .metric-card .value {{ margin-top: 13px; color: var(--navy); font: 700 clamp(22px, 2.6vw, 31px)/1 var(--mono); letter-spacing: -.04em; }}
    .metric-card .detail {{ margin-top: 9px; color: var(--muted); font-size: 11px; }}
    .risk-ledger {{ padding: 20px; border: 1px solid var(--line); background: #f8fbfb; }}
    .risk-ledger__head {{ display: flex; justify-content: space-between; gap: 16px; align-items: baseline; margin-bottom: 12px; }}
    .risk-ledger__head span {{ font-weight: 700; }}
    .risk-ledger__head code {{ font: 700 12px var(--mono); color: var(--navy); }}
    .risk-rail {{ height: 22px; display: flex; overflow: hidden; background: var(--wash); border: 1px solid #b8c7cc; }}
    .risk-rail__equity {{ display: block; background: var(--jade); }}
    .risk-rail__cash {{ display: block; background: repeating-linear-gradient(135deg, #dfe6e8 0, #dfe6e8 6px, #f4f7f8 6px, #f4f7f8 12px); }}
    .risk-legend {{ display: flex; gap: 18px; flex-wrap: wrap; margin-top: 10px; color: var(--muted); font-size: 11px; }}
    .dot {{ display: inline-block; width: 8px; height: 8px; margin-right: 6px; }}
    .dot.equity {{ background: var(--jade); }}
    .dot.cash {{ border: 1px solid #9babb1; background: #edf2f3; }}
    .table-scroll {{ width: 100%; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; white-space: nowrap; }}
    th, td {{ padding: 11px 10px; border-bottom: 1px solid var(--line-soft); text-align: right; font-variant-numeric: tabular-nums; }}
    th {{ color: #52616a; background: #f5f8f9; font-size: 11px; font-weight: 700; letter-spacing: .02em; }}
    th:first-child, td:first-child {{ position: sticky; left: 0; z-index: 1; text-align: left; background: inherit; }}
    tbody tr:nth-child(even) {{ background: #fafcfc; }}
    tbody tr:hover {{ background: #edf4f3; }}
    .metric-table tbody tr:first-child {{ background: var(--jade-soft); font-weight: 700; }}
    .holdout-table tbody tr:nth-last-child(-n+4) {{ background: #f4f8f7; }}
    .config-table, .side-table {{ white-space: normal; table-layout: fixed; }}
    .config-table th:first-child, .config-table td:first-child,
    .side-table th:first-child, .side-table td:first-child {{ position: static; width: 45%; }}
    .config-table th, .config-table td, .side-table th, .side-table td {{
      padding-left: 6px;
      padding-right: 6px;
      overflow-wrap: anywhere;
      vertical-align: top;
    }}
    .chart {{ display: block; max-width: 100%; height: auto; border: 1px solid var(--line-soft); }}
    .prose {{ color: #40515b; }}
    .prose p:first-child {{ margin-top: 0; }}
    .prose ul {{ margin: 0; padding-left: 20px; }}
    .prose li + li {{ margin-top: 7px; }}
    .callout {{ padding: 16px 18px; border-left: 4px solid var(--ochre); background: var(--ochre-soft); color: #5a421d; }}
    .callout strong {{ color: #3f2d13; }}
    .callout.research {{ border-left-color: var(--blue); background: #e6edf1; color: #334e5f; }}
    .empty {{ padding: 18px; color: var(--muted); border: 1px dashed var(--line); background: #f8fafb; }}
    .side-card {{ padding: 18px; border: 1px solid var(--line); background: #fff; margin-bottom: 14px; }}
    .side-card h3 {{ margin: 0 0 12px; font-size: 17px; }}
    .side-card ul {{ margin: 0; padding-left: 17px; color: #40515b; font-size: 13px; }}
    .side-card li + li {{ margin-top: 8px; }}
    .side-card code, footer code {{ font-family: var(--mono); font-size: .92em; }}
    .audit-list {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .audit-item {{ padding: 15px; border: 1px solid var(--line-soft); background: #f9fbfb; }}
    .audit-item strong {{ display: block; color: var(--navy); margin-bottom: 5px; }}
    .audit-item span {{ color: var(--muted); font-size: 12px; }}
    footer {{ margin-top: 24px; padding: 18px 0; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }}
    a:focus-visible {{ outline: 3px solid var(--ochre); outline-offset: 3px; }}
    @media (max-width: 920px) {{
      .masthead {{ grid-template-columns: 1fr; min-height: auto; }}
      .run-stamp {{ border-left: 0; border-top: 1px solid rgba(255,255,255,.2); padding: 16px 0 0; }}
      .report-grid {{ grid-template-columns: 1fr; }}
      .side-column {{ position: static; display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
      .metric-ledger {{ grid-template-columns: 1fr 1fr; }}
      .metric-card:nth-child(2) {{ border-right: 0; }}
      .metric-card:nth-child(-n+2) {{ border-bottom: 1px solid var(--line); }}
    }}
    @media (max-width: 600px) {{
      main {{ padding: 12px 10px 40px; }}
      .masthead {{ padding: 28px 22px; }}
      .masthead::after {{ display: none; }}
      .metric-ledger, .side-column, .audit-list {{ grid-template-columns: 1fr; }}
      .metric-card {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .metric-card:last-child {{ border-bottom: 0; }}
      .panel__head, .panel__body {{ padding-left: 16px; padding-right: 16px; }}
      .risk-ledger__head {{ display: block; }}
      .risk-ledger__head code {{ display: block; margin-top: 5px; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} }}
    @page {{ size: A4; margin: 11mm; }}
    @media print {{
      body {{ background: white; }}
      main {{ max-width: none; padding: 0; font-size: 10px; }}
      .masthead {{ min-height: 0; break-after: avoid; padding: 20px 24px; margin-bottom: 8px; }}
      .masthead h1 {{ font-size: 34px; }}
      .report-grid {{ display: block; }}
      .side-column {{ position: static; }}
      .panel {{ break-inside: auto; box-shadow: none; margin-bottom: 8px; }}
      .panel__head {{ break-after: avoid; padding: 10px 14px; }}
      .panel__body {{ padding: 10px 14px 12px; }}
      .side-card, .metric-ledger {{ break-inside: avoid; box-shadow: none; }}
      .metric-card {{ padding: 11px 13px; }}
      .table-scroll {{ overflow: visible; }}
      table {{ white-space: normal; font-size: 8px; }}
      th, td {{ padding: 5px 6px; }}
      thead {{ display: table-header-group; }}
      tr {{ break-inside: avoid; }}
      .chart {{ max-height: 230mm; object-fit: contain; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="masthead">
      <div>
        <p class="eyebrow">A-SHARE QUANT RESEARCH · RISK BUDGET LEDGER</p>
        <h1>{report_title_html}</h1>
        <p class="masthead__thesis">{html.escape(thesis)}</p>
      </div>
      <div class="run-stamp">
        <strong>{equity.index.min().date()} → {equity.index.max().date()}</strong>
        <span class="mode">{html.escape(risk_status)}</span>
        <span>{html.escape(str(lookback))} 日动量 · 前 {format_percent(top_percent_value)} · 月度调仓</span>
        <span>研究回测，不构成投资建议</span>
      </div>
    </header>

    <div class="report-grid">
      <div class="main-column">
        <section class="metric-ledger" aria-label="核心指标">
          <article class="metric-card loss"><div class="label">主策略最大回撤</div><div class="value">{format_percent(strategy_drawdown)}</div><div class="detail">越接近 0，历史峰值损失越小</div></article>
          <article class="metric-card"><div class="label">经典 20/10 最大回撤</div><div class="value">{format_percent(classic_drawdown)}</div><div class="detail">固定经典策略对照</div></article>
          <article class="metric-card improvement"><div class="label">回撤绝对值改善</div><div class="value">{format_percentage_points(drawdown_improvement)}</div><div class="detail">相对改善 {format_percent(drawdown_reduction_ratio)}</div></article>
          <article class="metric-card exposure"><div class="label">日均实际股票敞口</div><div class="value">{format_percent(average_exposure)}</div><div class="detail">主策略夏普 {format_number(strategy_sharpe)}</div></article>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>四组合绩效对照</h2><small>COMMON COMPARISON WINDOW</small></div>
          <div class="panel__body">
            {html_table(["组合", "总收益率", "年化收益率", "年化波动率", "最大回撤", "夏普", "Sortino", "Calmar", "平均股票敞口"], metric_rows, "metric-table")}
            <div class="callout research" style="margin-top:16px"><strong>阅读顺序：</strong>先比较最大回撤和波动率，再看夏普、Sortino 与 Calmar，最后结合平均股票敞口判断收益是否主要来自承担更多风险。四组使用主程序提供的共同比较起点。</div>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>净值与风险诊断</h2><small>PATH, NOT JUST ENDPOINT</small></div>
          <div class="panel__body">
            {chart_html}
            <div style="height:16px"></div>
            {diagnostics_html}
            <p class="prose">诊断图依次比较回撤路径、自然年度收益、主策略最近 36 个月收益，以及实际股票风险敞口。图形用于识别风险集中与时期依赖，不代表未来预测。</p>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>风险预算与降仓记录</h2><small>NO LEVERAGE · CASH IS EXPLICIT</small></div>
          <div class="panel__body">
            {exposure_strip_html}
            <div class="audit-list" style="margin:16px 0">
              <div class="audit-item"><strong>{format_percent(average_target_exposure)}</strong><span>调仓日平均目标股票仓位</span></div>
              <div class="audit-item"><strong>{format_percent(minimum_target_exposure)}</strong><span>最低目标股票仓位</span></div>
              <div class="audit-item"><strong>{volatility_binding_count} 次</strong><span>波动率上限实际触发</span></div>
              <div class="audit-item"><strong>{trend_defensive_count} 次</strong><span>基准趋势弱市上限触发</span></div>
            </div>
            {risk_log_table}
            <p class="prose">表中上限均在月末信号日收盘后、仅使用当日及以前数据计算。目标仓位不超过 100%，系统不向上放大低波动组合；未投资部分按日收益 0 的现金处理。停牌冻结持仓可能令实际敞口短期偏离目标，因此同时保留目标与执行权重供审计。</p>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>回顾性时间留出诊断</h2><small>TEMPORAL HOLDOUT · NOT TRUE OOS</small></div>
          <div class="panel__body">
            {holdout_table}
            <div class="callout" style="margin-top:16px"><strong>研究诚信说明：</strong><code>temporal_holdout_diagnostic.csv</code> 仅用于回顾性时间分段。项目此前已经查看过 2019—2026 全样本及参数敏感性，因此以 {html.escape(oos_start or '预设日期')} 切分不能宣传为真正样本外检验。真正的样本外证据需要在参数冻结后等待未来数据，或采用严格的滚动 walk-forward 流程。</div>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>分阶段与参数稳健性</h2><small>ROBUSTNESS CHECKS</small></div>
          <div class="panel__body">
            <h3>全样本 / 前半段 / 后半段</h3>
            {subperiod_table}
            <h3 style="margin-top:26px">纯动量参数敏感性</h3>
            {sensitivity_table}
            <p class="prose">敏感性表检验的是纯动量观察期与选股比例，并不把每个风控开关再次网格搜索。这样可以减少用同一历史样本反复挑选风险参数造成的过拟合。</p>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>最近一次动量信号</h2><small>PLANNED → SCALED → EXECUTED</small></div>
          <div class="panel__body">
            {latest_table}
            <p class="prose">该表列出动量卫星入选股票；等权核心还可能持有其他股票。新版日志并列保存纯动量、核心—卫星混合、风控缩放与真实执行权重。</p>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>数据质量</h2><small>QUOTE AVAILABILITY</small></div>
          <div class="panel__body">
            {quality_table}
            <p class="prose">缺失报价日使用上一笔有效收盘价估值，但该日禁止交易。价格缓存、失败代码、股票池与运行参数记录在 <code>run_metadata.txt</code>。</p>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>方法、时序与边界</h2><small>RESEARCH PROTOCOL</small></div>
          <div class="panel__body prose">
            <ul>
              <li><strong>信号：</strong>每月最后一个交易日收盘后，使用截至该日的价格计算过去 {html.escape(str(lookback))} 个交易日横截面动量；不会用下一交易日能否成交改变排名。</li>
              <li><strong>执行滞后：</strong>目标在下一交易日收盘执行。执行日当天先由旧持仓取得收益，再调仓并扣费；新持仓从再下一交易日开始贡献收盘到收盘收益。</li>
              <li><strong>核心—卫星：</strong>等权核心与动量卫星先在目标权重层合并，再统一计算换手和成本，避免事后拼接收益率低估交易摩擦。</li>
              <li><strong>风险控制：</strong>波动率与基准均线只读取信号日及以前数据；两者均为股票仓位上限。最终权重合计不得超过 100%，不允许卖空或隐含杠杆。</li>
              <li><strong>对照组：</strong>经典动量固定为 20 日 / 前 10%；股票池等权为共同起点买入持有；沪深 300 为未复权价格指数。</li>
              <li><strong>费用与现金：</strong>扣除简化单边成本 {format_percent(fee_rate_value)}；现金日收益假设为 {html.escape(cash_return_text)}，未计货币基金或融资收益。</li>
            </ul>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>关键局限</h2><small>WHAT THIS BACKTEST CANNOT CLAIM</small></div>
          <div class="panel__body">
            <div class="callout"><strong>不可直接外推到实盘：</strong>固定示例股票池带来幸存者偏差；股票采用前复权价格而基准采用价格指数，口径不完全一致；系统尚未完整模拟涨跌停、T+1、100 股整数倍、最低佣金、卖出印花税、滑点、冲击成本、ST、退市及历史成分股。风险控制降低历史回撤并不保证未来仍然有效。</div>
          </div>
        </section>
      </div>

      <aside class="side-column">
        <section class="side-card">
          <p class="eyebrow" style="color:var(--jade)">RUN CONFIGURATION</p>
          <h3>本次设置</h3>
          {html_table(["项目", "值"], config_rows, "config-table")}
        </section>
        <section class="side-card">
          <p class="eyebrow" style="color:var(--jade)">AUTOMATED READOUT</p>
          <h3>自动观察</h3>
          <ul>{"".join(f"<li>{html.escape(item)}</li>" for item in insights)}</ul>
        </section>
        <section class="side-card">
          <p class="eyebrow" style="color:var(--cinnabar)">RISK SNAPSHOT</p>
          <h3>尾部与交易摘要</h3>
          {html_table(["指标", "结果"], risk_rows, "side-table")}
        </section>
      </aside>
    </div>

    <footer>
      数据源：{html.escape(metadata.get("data_source", "AkShare / 腾讯财经公开 A 股行情"))} ·
      版本：{html.escape(metadata.get("project_version", "旧版 / 未记录"))} ·
      生成：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}。本报告仅用于量化研究展示，不构成投资建议。
    </footer>
  </main>
</body>
</html>
"""

    report_path = output_dir / "analysis_report.html"
    report_path.write_text(report_html, encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="根据回测结果文件夹重新生成 HTML 研究报告"
    )
    parser.add_argument(
        "--result-dir",
        required=True,
        help="包含 equity_curve.csv 与 performance_metrics.csv 的结果文件夹",
    )
    args = parser.parse_args()
    report_path = generate_html_report(Path(args.result_dir))
    print(f"报告已生成：{report_path.resolve()}")


if __name__ == "__main__":
    main()
