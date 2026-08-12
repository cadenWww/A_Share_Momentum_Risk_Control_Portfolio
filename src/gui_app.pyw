"""无需终端的 A 股动量策略回测界面。

双击“launch_gui.cmd”即可打开本程序。界面只负责收集参数和展示
结果；真正的回测仍由已经验证过的 momentum_backtest.py 在后台完成。
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
CORE_SCRIPT = APP_DIR / "momentum_backtest.py"
DEFAULT_TICKERS = PROJECT_DIR / "tickers.csv"
README_PATH = PROJECT_DIR / "README.md"
GUI_RESULTS_DIR = PROJECT_DIR / "gui_results"
MODE_LABELS = {
    "推荐风控组合": "risk_controlled",
    "经典纯动量": "classic",
}


def percent(value: float) -> str:
    """把 0.1234 显示成 12.34%。"""
    return f"{value:.2%}"


def background_python() -> str:
    """使用 python.exe 执行后台任务，避免 pythonw.exe 丢失运行日志。"""
    current = Path(sys.executable)
    if current.name.lower() == "pythonw.exe":
        console_python = current.with_name("python.exe")
        if console_python.exists():
            return str(console_python)
    return sys.executable


class BacktestApp:
    """Tkinter 图形界面及后台回测进程的协调器。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("A 股风险控制研究台")
        self.root.minsize(980, 760)
        self.root.geometry("1120x900")

        self.events: queue.Queue[tuple[str, object, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.is_running = False
        self.current_result_dir: Path | None = None
        self.input_widgets: list[ttk.Widget] = []
        self._applying_preset = False

        self.tickers_var = tk.StringVar(value=str(DEFAULT_TICKERS))
        self.start_var = tk.StringVar(value="2019-01-01")
        self.end_var = tk.StringVar(value=date.today().isoformat())
        self.preset_var = tk.StringVar(value="推荐风控（建议）")
        self.strategy_mode_var = tk.StringVar(value="推荐风控组合")
        self.lookback_var = tk.StringVar(value="120")
        self.top_percent_var = tk.StringVar(value="30")
        self.momentum_weight_var = tk.StringVar(value="30")
        self.target_volatility_var = tk.StringVar(value="18")
        self.volatility_lookback_var = tk.StringVar(value="60")
        self.trend_ma_days_var = tk.StringVar(value="200")
        self.defensive_exposure_var = tk.StringVar(value="50")
        self.oos_start_var = tk.StringVar(value="2024-01-01")
        self.fee_percent_var = tk.StringVar(value="0.10")
        self.capital_var = tk.StringVar(value="100000")
        self.benchmark_var = tk.StringVar(value="000300")
        self.refresh_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="请设置参数，然后点击“开始回测”。")
        self.risk_recipe_var = tk.StringVar()
        self.result_vars = {
            "annual": tk.StringVar(value="--"),
            "drawdown": tk.StringVar(value="--"),
            "sharpe": tk.StringVar(value="--"),
            "risk_improvement": tk.StringVar(value="--"),
        }

        self._build_style()
        self._build_interface()
        for variable in (
            self.strategy_mode_var,
            self.lookback_var,
            self.top_percent_var,
            self.momentum_weight_var,
            self.target_volatility_var,
            self.trend_ma_days_var,
            self.defensive_exposure_var,
        ):
            variable.trace_add("write", self.parameter_edited)
        self.update_risk_recipe()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(120, self.drain_events)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(background="#edf2f6")
        style.configure("TFrame", background="#edf2f6")
        style.configure("Title.TLabel", font=("Microsoft YaHei", 20, "bold"), foreground="#17324d", background="#edf2f6")
        style.configure("SubTitle.TLabel", foreground="#5b6d7e", background="#edf2f6")
        style.configure("RiskStrip.TLabel", foreground="#ffffff", background="#0d7a75", font=("Microsoft YaHei", 10, "bold"), padding=(12, 8))
        style.configure("Hint.TLabel", foreground="#704d12", background="#fff4d8", padding=(10, 8))
        style.configure("MetricName.TLabel", foreground="#52606d", background="#ffffff")
        style.configure("MetricValue.TLabel", font=("Microsoft YaHei", 16, "bold"), foreground="#17324d", background="#ffffff")
        style.configure("Metric.TFrame", background="#ffffff")
        style.configure("Run.TButton", font=("Microsoft YaHei", 10, "bold"), foreground="#0d5f5b")

    def _build_interface(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="A 股动量策略研究助手", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="用同一份历史数据比较风控组合、经典动量、股票池等权和沪深300。",
            style="SubTitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(header, textvariable=self.risk_recipe_var, style="RiskStrip.TLabel").grid(
            row=2, column=0, sticky="ew", pady=(10, 0)
        )

        settings = ttk.Notebook(outer)
        settings.grid(row=1, column=0, sticky="ew")
        basic = ttk.Frame(settings, padding=14)
        risk = ttk.Frame(settings, padding=14)
        settings.add(basic, text="快速设置")
        settings.add(risk, text="风险参数与费用")

        for page in (basic, risk):
            page.columnconfigure(1, weight=1)
            page.columnconfigure(3, weight=1)

        self._add_file_row(basic)
        self._add_entry(basic, 1, 0, "开始日期", self.start_var, "格式：YYYY-MM-DD")
        self._add_entry(basic, 1, 2, "结束日期", self.end_var, "格式：YYYY-MM-DD")
        self._add_entry(basic, 2, 0, "动量周期", self.lookback_var, "推荐 120 个交易日")
        self._add_entry(basic, 2, 2, "选股比例", self.top_percent_var, "单位：%，推荐 30")
        self._add_entry(basic, 3, 0, "比较基准", self.benchmark_var, "默认 000300（沪深300）")
        self._add_entry(
            basic,
            3,
            2,
            "回顾性时间留出起点",
            self.oos_start_var,
            "例如 2024-01-01；仅作历史分段诊断，不是真正样本外",
        )

        ttk.Label(risk, text="策略预设").grid(row=0, column=0, sticky="w", pady=6)
        preset = ttk.Combobox(
            risk,
            textvariable=self.preset_var,
            values=("推荐风控（建议）", "原始动量复现", "自定义研究"),
            state="readonly",
            width=22,
        )
        preset.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=6)
        preset.bind("<<ComboboxSelected>>", self.apply_selected_preset)
        restore = ttk.Button(risk, text="恢复推荐值", command=self.apply_recommended_preset)
        restore.grid(row=0, column=2, sticky="w", padx=(18, 8), pady=6)
        self.input_widgets.extend([preset, restore])

        ttk.Label(risk, text="策略模式").grid(row=1, column=0, sticky="w", pady=6)
        mode = ttk.Combobox(
            risk,
            textvariable=self.strategy_mode_var,
            values=tuple(MODE_LABELS),
            state="readonly",
            width=22,
        )
        mode.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=6)
        self.input_widgets.append(mode)
        self._add_entry(risk, 1, 2, "动量卫星占比", self.momentum_weight_var, "单位：%，推荐 30；其余为等权核心")
        self._add_entry(risk, 2, 0, "目标年化波动率", self.target_volatility_var, "单位：%，推荐 18；0 表示关闭")
        self._add_entry(risk, 2, 2, "波动估计窗口", self.volatility_lookback_var, "单位：交易日，推荐 60")
        self._add_entry(risk, 3, 0, "趋势均线", self.trend_ma_days_var, "单位：交易日，推荐 200；0 表示关闭")
        self._add_entry(risk, 3, 2, "弱市股票仓位上限", self.defensive_exposure_var, "单位：%，推荐 50")
        self._add_entry(risk, 4, 0, "单边手续费", self.fee_percent_var, "单位：%，例如 0.10")
        self._add_entry(risk, 4, 2, "初始资金", self.capital_var, "单位：元，仅用于展示")

        refresh = ttk.Checkbutton(
            risk,
            text="强制重新下载数据（已有缓存时通常不要勾选）",
            variable=self.refresh_var,
        )
        refresh.grid(row=5, column=0, columnspan=2, sticky="w", pady=6)
        self.input_widgets.append(refresh)
        ttk.Label(
            risk,
            text="风险控制只会降低股票仓位，不会加杠杆；未配置部分自动保留为零收益现金。",
            style="Hint.TLabel",
        ).grid(row=5, column=2, columnspan=2, sticky="ew", padx=(18, 0), pady=6)

        actions = ttk.Frame(outer)
        actions.grid(row=2, column=0, sticky="ew", pady=12)
        actions.columnconfigure(5, weight=1)
        self.start_button = ttk.Button(
            actions, text="开始回测", style="Run.TButton", command=self.start_backtest
        )
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="停止当前回测", command=self.stop_backtest)
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        self.stop_button.state(["disabled"])
        self.open_report_button = ttk.Button(actions, text="打开分析报告", command=self.open_report)
        self.open_report_button.grid(row=0, column=2, padx=(0, 8))
        self.open_report_button.state(["disabled"])
        self.open_folder_button = ttk.Button(actions, text="打开结果文件夹", command=self.open_result_folder)
        self.open_folder_button.grid(row=0, column=3, padx=(0, 8))
        self.open_folder_button.state(["disabled"])
        help_button = ttk.Button(actions, text="查看使用说明", command=self.open_readme)
        help_button.grid(row=0, column=4, padx=(0, 8))

        status_panel = ttk.LabelFrame(outer, text="2. 运行状态与结果", padding=14)
        status_panel.grid(row=3, column=0, sticky="nsew")
        status_panel.columnconfigure(0, weight=1)
        status_panel.rowconfigure(3, weight=1)

        ttk.Label(status_panel, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status_panel, mode="indeterminate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(8, 12))

        metrics = ttk.Frame(status_panel)
        metrics.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        for column in range(4):
            metrics.columnconfigure(column, weight=1)
        self._metric_card(metrics, 0, "策略年化收益率", self.result_vars["annual"])
        self._metric_card(metrics, 1, "最大回撤", self.result_vars["drawdown"])
        self._metric_card(metrics, 2, "夏普比率（无风险利率=0）", self.result_vars["sharpe"])
        self._metric_card(metrics, 3, "较经典策略回撤改善", self.result_vars["risk_improvement"])

        log_frame = ttk.Frame(status_panel)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=13,
            wrap="word",
            font=("Consolas", 10),
            background="#18212f",
            foreground="#e9eef5",
            insertbackground="#ffffff",
            relief="flat",
            padx=10,
            pady=8,
        )
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.append_log("提示：首次下载数据可能需要几十秒到几分钟，请保持网络连接。\n")

    def _add_file_row(self, parent: ttk.LabelFrame) -> None:
        ttk.Label(parent, text="股票池 CSV").grid(row=0, column=0, sticky="w", pady=6)
        entry = ttk.Entry(parent, textvariable=self.tickers_var)
        entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=6)
        browse = ttk.Button(parent, text="选择文件", command=self.choose_tickers)
        browse.grid(row=0, column=3, sticky="e", pady=6)
        edit = ttk.Button(parent, text="查看/编辑默认股票池", command=self.open_tickers)
        edit.grid(row=0, column=4, sticky="e", padx=(8, 0), pady=6)
        self.input_widgets.extend([entry, browse, edit])

    def _add_entry(
        self,
        parent: ttk.LabelFrame,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        note: str,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=6)
        entry = ttk.Entry(parent, textvariable=variable, width=24)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(8, 0), pady=6)
        entry.bind("<FocusIn>", lambda _event, hint=note: self.status_var.set(hint))
        self.input_widgets.append(entry)

    def _metric_card(self, parent: ttk.Frame, column: int, label: str, value: tk.StringVar) -> None:
        card = ttk.Frame(parent, padding=10, relief="groove", style="Metric.TFrame")
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0))
        ttk.Label(card, text=label, style="MetricName.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=value, style="MetricValue.TLabel").pack(anchor="w", pady=(4, 0))

    def parameter_edited(self, *_args: object) -> None:
        if not self._applying_preset:
            self.preset_var.set("自定义研究")
        self.update_risk_recipe()

    def update_risk_recipe(self, *_args: object) -> None:
        mode = self.strategy_mode_var.get()
        if MODE_LABELS.get(mode) == "classic":
            self.risk_recipe_var.set("经典复现｜纯动量｜不主动留现金｜用于和升级方案对照")
            return
        try:
            core = 100.0 - float(self.momentum_weight_var.get())
            satellite = float(self.momentum_weight_var.get())
            target = float(self.target_volatility_var.get())
            trend = int(self.trend_ma_days_var.get())
            defensive = float(self.defensive_exposure_var.get())
            self.risk_recipe_var.set(
                f"风险预算｜等权核心 {core:.0f}% ＋ 动量卫星 {satellite:.0f}% ｜"
                f"目标波动 {target:.0f}% ｜{trend} 日趋势弱时仓位≤{defensive:.0f}%"
            )
        except ValueError:
            self.risk_recipe_var.set("风险预算｜请完成风险参数设置")

    def apply_selected_preset(self, _event: object | None = None) -> None:
        if self.preset_var.get() == "推荐风控（建议）":
            self.apply_recommended_preset()
        elif self.preset_var.get() == "原始动量复现":
            self._applying_preset = True
            try:
                self.strategy_mode_var.set("经典纯动量")
                self.lookback_var.set("20")
                self.top_percent_var.set("10")
                self.momentum_weight_var.set("100")
                self.target_volatility_var.set("0")
                self.volatility_lookback_var.set("60")
                self.trend_ma_days_var.set("0")
                self.defensive_exposure_var.set("100")
                self.preset_var.set("原始动量复现")
            finally:
                self._applying_preset = False
            self.update_risk_recipe()

    def apply_recommended_preset(self) -> None:
        self._applying_preset = True
        try:
            self.strategy_mode_var.set("推荐风控组合")
            self.lookback_var.set("120")
            self.top_percent_var.set("30")
            self.momentum_weight_var.set("30")
            self.target_volatility_var.set("18")
            self.volatility_lookback_var.set("60")
            self.trend_ma_days_var.set("200")
            self.defensive_exposure_var.set("50")
            self.preset_var.set("推荐风控（建议）")
        finally:
            self._applying_preset = False
        self.update_risk_recipe()

    def choose_tickers(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择股票池 CSV 文件",
            initialdir=str(APP_DIR),
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if selected:
            self.tickers_var.set(selected)

    def open_tickers(self) -> None:
        path = Path(self.tickers_var.get()).expanduser()
        if not path.exists():
            messagebox.showerror("找不到股票池", f"找不到 CSV 文件：\n{path}")
            return
        self.open_path(path)

    def open_readme(self) -> None:
        if README_PATH.exists():
            self.open_path(README_PATH)
        else:
            messagebox.showinfo("使用说明", "未找到 README.md。请确认项目文件是否完整。")

    def validate_parameters(self) -> dict[str, object] | None:
        try:
            ticker_path = Path(self.tickers_var.get().strip()).expanduser().resolve()
            if not ticker_path.exists() or not ticker_path.is_file():
                raise ValueError("请选择存在的股票池 CSV 文件。")
            start = date.fromisoformat(self.start_var.get().strip())
            end = date.fromisoformat(self.end_var.get().strip())
            if start >= end:
                raise ValueError("结束日期必须晚于开始日期。")
            lookback = int(self.lookback_var.get().strip())
            if not 1 <= lookback <= 1000:
                raise ValueError("动量周期请填写 1 到 1000 之间的整数。")
            top_percent = float(self.top_percent_var.get().strip())
            if not 0 < top_percent <= 100:
                raise ValueError("选股比例必须大于 0 且不超过 100（单位是 %）。")
            strategy_mode = MODE_LABELS.get(self.strategy_mode_var.get())
            if strategy_mode is None:
                raise ValueError("请选择策略模式。")
            momentum_weight = float(self.momentum_weight_var.get().strip())
            if not 0 <= momentum_weight <= 100:
                raise ValueError("动量卫星占比必须在 0 到 100 之间。")
            target_volatility = float(self.target_volatility_var.get().strip())
            if not 0 <= target_volatility <= 100:
                raise ValueError("目标年化波动率必须在 0 到 100 之间；0 表示关闭。")
            volatility_lookback = int(self.volatility_lookback_var.get().strip())
            if not 2 <= volatility_lookback <= 1000:
                raise ValueError("波动估计窗口请填写 2 到 1000 之间的整数。")
            trend_ma_days = int(self.trend_ma_days_var.get().strip())
            if not 0 <= trend_ma_days <= 1000:
                raise ValueError("趋势均线请填写 0 到 1000 之间的整数；0 表示关闭。")
            defensive_exposure = float(self.defensive_exposure_var.get().strip())
            if not 0 <= defensive_exposure <= 100:
                raise ValueError("弱市股票仓位上限必须在 0 到 100 之间。")
            oos_start_text = self.oos_start_var.get().strip()
            if oos_start_text:
                date.fromisoformat(oos_start_text)
            fee_percent = float(self.fee_percent_var.get().strip())
            if not 0 <= fee_percent <= 10:
                raise ValueError("单边手续费请填写 0 到 10 之间的百分数，例如 0.10。")
            capital = float(self.capital_var.get().strip().replace(",", ""))
            if capital <= 0:
                raise ValueError("初始资金必须大于 0。")
            benchmark = self.benchmark_var.get().strip()
            if not re.fullmatch(r"\d{6}", benchmark):
                raise ValueError("比较基准必须是 6 位数字，例如 000300。")
        except ValueError as error:
            messagebox.showerror("参数需要修改", str(error))
            return None

        return {
            "ticker_path": ticker_path,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "lookback": lookback,
            "top_percent": top_percent / 100.0,
            "strategy_mode": strategy_mode,
            "momentum_weight": momentum_weight / 100.0,
            "target_volatility": target_volatility / 100.0,
            "volatility_lookback": volatility_lookback,
            "trend_ma_days": trend_ma_days,
            "defensive_exposure": defensive_exposure / 100.0,
            "oos_start": oos_start_text,
            "fee_rate": fee_percent / 100.0,
            "capital": capital,
            "benchmark": benchmark,
            "refresh": self.refresh_var.get(),
        }

    def next_result_dir(self) -> Path:
        GUI_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        base = GUI_RESULTS_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        candidate = base
        serial = 2
        while candidate.exists():
            candidate = GUI_RESULTS_DIR / f"{base.name}_{serial:02d}"
            serial += 1
        return candidate

    def start_backtest(self) -> None:
        if self.is_running:
            return
        settings = self.validate_parameters()
        if settings is None:
            return
        if not CORE_SCRIPT.exists():
            messagebox.showerror("程序文件缺失", f"找不到回测脚本：\n{CORE_SCRIPT}")
            return

        result_dir = self.next_result_dir()
        command = [
            background_python(),
            "-u",
            str(CORE_SCRIPT),
            "--tickers",
            str(settings["ticker_path"]),
            "--start",
            str(settings["start"]),
            "--end",
            str(settings["end"]),
            "--lookback",
            str(settings["lookback"]),
            "--top-percent",
            str(settings["top_percent"]),
            "--strategy-mode",
            str(settings["strategy_mode"]),
            "--momentum-weight",
            str(settings["momentum_weight"]),
            "--target-volatility",
            str(settings["target_volatility"]),
            "--volatility-lookback",
            str(settings["volatility_lookback"]),
            "--trend-ma-days",
            str(settings["trend_ma_days"]),
            "--defensive-exposure",
            str(settings["defensive_exposure"]),
            "--oos-start",
            str(settings["oos_start"]),
            "--fee-rate",
            str(settings["fee_rate"]),
            "--initial-capital",
            str(settings["capital"]),
            "--benchmark",
            str(settings["benchmark"]),
            "--output-dir",
            str(result_dir),
        ]
        if bool(settings["refresh"]):
            command.append("--refresh-data")

        self.current_result_dir = result_dir
        self.is_running = True
        self.set_running_state(True)
        self.status_var.set("正在准备回测、对照组和参数敏感性分析，请耐心等待…")
        self.progress.start(12)
        self.clear_log()
        self.append_log(f"本次结果将保存到：\n{result_dir}\n\n")
        self.append_log("正在启动后台回测…\n")
        threading.Thread(
            target=self.run_process,
            args=(command, result_dir),
            daemon=True,
        ).start()

    def run_process(self, command: list[str], result_dir: Path) -> None:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        try:
            self.process = subprocess.Popen(
                command,
                cwd=APP_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if self.process.stdout is not None:
                for line in self.process.stdout:
                    self.events.put(("log", line, None))
            exit_code = self.process.wait()
            self.events.put(("finished", exit_code, result_dir))
        except Exception as error:  # 显示启动失败的原因，而不是让界面静默退出。
            self.events.put(("error", str(error), result_dir))

    def drain_events(self) -> None:
        try:
            while True:
                event_type, first, second = self.events.get_nowait()
                if event_type == "log":
                    self.append_log(str(first))
                elif event_type == "finished":
                    self.finish_backtest(int(first), Path(second))
                elif event_type == "error":
                    self.finish_error(str(first))
        except queue.Empty:
            pass
        self.root.after(120, self.drain_events)

    def stop_backtest(self) -> None:
        if not self.is_running or self.process is None:
            return
        if messagebox.askyesno("停止回测", "确定要停止当前回测吗？本次结果文件夹会保留已生成的部分文件。"):
            self.status_var.set("正在停止当前回测…")
            self.append_log("\n用户请求停止回测…\n")
            self.stop_button.state(["disabled"])
            try:
                self.process.terminate()
            except OSError as error:
                self.append_log(f"停止进程时出现问题：{error}\n")

    def finish_backtest(self, exit_code: int, result_dir: Path) -> None:
        self.process = None
        self.is_running = False
        self.progress.stop()
        self.set_running_state(False)
        report = result_dir / "analysis_report.html"
        if exit_code == 0 and report.exists():
            self.current_result_dir = result_dir
            self.show_metrics(result_dir)
            self.open_report_button.state(["!disabled"])
            self.open_folder_button.state(["!disabled"])
            self.status_var.set("回测完成：报告已生成并已尝试在浏览器中打开。")
            self.append_log(f"\n回测完成。分析报告：\n{report}\n")
            self.open_report()
            messagebox.showinfo(
                "回测完成",
                "回测已完成，HTML 分析报告已生成。\n"
                "浏览器应会自动打开报告；也可以点击“打开分析报告”。",
            )
        else:
            self.status_var.set("本次回测没有完成，请查看下方日志。")
            self.append_log(f"\n回测结束，退出代码：{exit_code}\n")
            self.open_folder_button.state(["!disabled"])
            messagebox.showerror(
                "回测未完成",
                "本次回测未能生成完整报告。请查看下方日志；"
                "也可以点击“打开结果文件夹”查看已保存的文件。",
            )

    def finish_error(self, error: str) -> None:
        self.process = None
        self.is_running = False
        self.progress.stop()
        self.set_running_state(False)
        self.status_var.set("无法启动回测，请查看提示。")
        self.append_log(f"\n无法启动后台回测：{error}\n")
        messagebox.showerror("无法启动回测", error)

    def set_running_state(self, running: bool) -> None:
        if running:
            self.start_button.state(["disabled"])
            self.stop_button.state(["!disabled"])
            for widget in self.input_widgets:
                widget.state(["disabled"])
        else:
            self.start_button.state(["!disabled"])
            self.stop_button.state(["disabled"])
            for widget in self.input_widgets:
                widget.state(["!disabled"])

    def show_metrics(self, result_dir: Path) -> None:
        try:
            metrics = pd.read_csv(result_dir / "performance_metrics.csv", encoding="utf-8-sig")
            series_names = metrics["series"].astype(str)
            strategy_rows = metrics.loc[series_names == "strategy"]
            if strategy_rows.empty:
                raise ValueError("绩效文件中缺少 strategy 行。")
            strategy = strategy_rows.iloc[0]
            classic_rows = metrics.loc[series_names == "classic_momentum_20_10"]

            annual = float(strategy["annualized_return"])
            drawdown = float(strategy["max_drawdown"])
            sharpe = float(strategy["sharpe_ratio_rf_0"])
            self.result_vars["annual"].set(percent(annual))
            self.result_vars["drawdown"].set(percent(drawdown))
            self.result_vars["sharpe"].set(f"{sharpe:.2f}")

            if classic_rows.empty:
                self.result_vars["risk_improvement"].set("N/A")
            else:
                classic_drawdown = float(classic_rows.iloc[0]["max_drawdown"])
                improvement = abs(classic_drawdown) - abs(drawdown)
                sign = "+" if improvement > 0 else ""
                self.result_vars["risk_improvement"].set(
                    f"{sign}{improvement * 100:.2f} 个百分点"
                )
        except Exception as error:
            self.append_log(f"读取绩效摘要时出现提示：{error}\n")

    def clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    def append_log(self, text: str) -> None:
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def open_report(self) -> None:
        if self.current_result_dir is None:
            return
        report = self.current_result_dir / "analysis_report.html"
        if report.exists():
            self.open_in_chrome(report)
        else:
            messagebox.showinfo("报告尚未生成", "请先成功完成一次回测。")

    def open_result_folder(self) -> None:
        if self.current_result_dir is None:
            return
        self.open_path(self.current_result_dir)

    @staticmethod
    def open_path(path: Path) -> None:
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows 专用。
        except AttributeError:
            webbrowser.open(path.resolve().as_uri())
        except OSError as error:
            messagebox.showerror("无法打开文件", str(error))

    @staticmethod
    def open_in_chrome(path: Path) -> None:
        """明确使用 Chrome 打开 HTML；不修改系统默认浏览器。"""
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        chrome = next((candidate for candidate in candidates if candidate.is_file()), None)
        try:
            if chrome is not None:
                subprocess.Popen(
                    [str(chrome), path.resolve().as_uri()],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                # 只有在本机未找到常见 Chrome 安装路径时才使用系统回退。
                webbrowser.open(path.resolve().as_uri())
        except OSError as error:
            messagebox.showerror("无法打开 Chrome", str(error))

    def close(self) -> None:
        if self.is_running:
            close_now = messagebox.askyesno(
                "回测仍在运行",
                "当前回测仍在运行。关闭窗口会停止它，确定关闭吗？",
            )
            if not close_now:
                return
            if self.process is not None:
                try:
                    self.process.terminate()
                except OSError:
                    pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    BacktestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
