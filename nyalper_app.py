"""
nyalper_app.py
にゃるぱー秘書 デスクトップアプリ v2.0
- ボタン1個で分析スタート
- 銘柄名・ティッカー検索で自動補完
- ポートフォリオ管理UI
"""

import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import yaml
import csv

BASE_DIR      = Path(__file__).resolve().parent
CONFIG_PATH   = BASE_DIR / "config.yaml"
OUTPUT_DIR    = BASE_DIR / "outputs"
UNIVERSE_PATH = BASE_DIR / "universe_jp.csv"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def load_universe() -> list:
    stocks = []
    if UNIVERSE_PATH.exists():
        with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stocks.append({"ticker": row["ticker"], "name": row["name"], "sector": row.get("sector", "")})
    us_stocks = [
        {"ticker": "NVDA",  "name": "NVIDIA",    "sector": "半導体"},
        {"ticker": "AAPL",  "name": "Apple",      "sector": "テクノロジー"},
        {"ticker": "MSFT",  "name": "Microsoft",  "sector": "テクノロジー"},
        {"ticker": "GOOGL", "name": "Alphabet",   "sector": "テクノロジー"},
        {"ticker": "AMZN",  "name": "Amazon",     "sector": "小売"},
        {"ticker": "META",  "name": "Meta",       "sector": "テクノロジー"},
        {"ticker": "TSLA",  "name": "Tesla",      "sector": "自動車"},
        {"ticker": "AMD",   "name": "AMD",        "sector": "半導体"},
    ]
    stocks.extend(us_stocks)
    return stocks


class StockSearchDialog(tk.Toplevel):
    def __init__(self, parent, stocks, callback):
        super().__init__(parent)
        self.stocks = stocks
        self.callback = callback
        self.title("銘柄を検索")
        self.geometry("600x700")
        self.configure(bg="#0d1117")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._build()
        self.search_var.trace("w", self._on_search)
        self._update_list(self.stocks)
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _build(self):
        sf = tk.Frame(self, bg="#0d1117", pady=12, padx=12)
        sf.pack(fill="x")
        tk.Label(sf, text="銘柄名またはティッカーで検索",
                bg="#0d1117", fg="#8b949e",
                font=("Yu Gothic UI", 12)).pack(anchor="w", pady=(0,4))
        self.search_var = tk.StringVar()
        e = tk.Entry(sf, textvariable=self.search_var,
                    bg="#21262d", fg="#e6edf3",
                    font=("Yu Gothic UI", 15),
                    relief="flat", insertbackground="#e6edf3")
        e.pack(fill="x", ipady=8)
        e.focus()
        tk.Frame(self, bg="#21262d", height=1).pack(fill="x")
        lf = tk.Frame(self, bg="#0d1117")
        lf.pack(fill="both", expand=True, padx=12, pady=8)
        sb = tk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(lf, bg="#0d1117", fg="#e6edf3",
                                  font=("Yu Gothic UI", 13), relief="flat",
                                  selectbackground="#1f6feb", selectforeground="white",
                                  activestyle="none", yscrollcommand=sb.set, cursor="hand2",
                                  height=20)
        self.listbox.pack(fill="both", expand=True)
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self._on_select)
        self.listbox.bind("<Return>", self._on_select)
        bf = tk.Frame(self, bg="#0d1117", pady=10, padx=12)
        bf.pack(fill="x")
        tk.Button(bf, text="選択", bg="#238636", fg="white",
                 font=("Yu Gothic UI", 13, "bold"), relief="flat", cursor="hand2",
                 command=self._on_select, padx=16, pady=8).pack(side="right", padx=(8,0))
        tk.Button(bf, text="キャンセル", bg="#21262d", fg="#8b949e",
                 font=("Yu Gothic UI", 13), relief="flat", cursor="hand2",
                 command=self.destroy, padx=16, pady=8).pack(side="right")
        self.matched = []

    def _on_search(self, *args):
        q = self.search_var.get().strip().lower()
        if not q:
            self._update_list(self.stocks)
            return
        matched = [s for s in self.stocks
                  if q in s["ticker"].lower() or q in s["name"].lower() or q in s["sector"].lower()]
        self._update_list(matched)

    def _update_list(self, stocks):
        self.listbox.delete(0, tk.END)
        self.matched = stocks
        for s in stocks:
            self.listbox.insert(tk.END, f"  {s['ticker']}   {s['name']}   [{s['sector']}]")

    def _on_select(self, event=None):
        sel = self.listbox.curselection()
        idx = sel[0] if sel else 0
        if idx < len(self.matched):
            self.callback(self.matched[idx])
            self.destroy()


class PortfolioRow:
    def __init__(self, parent_app, frame, row_num, stocks,
                 ticker="", name="", price="", shares="", on_delete=None):
        self.app = parent_app
        self.frame = frame
        self.row_num = row_num
        self.stocks = stocks
        self.on_delete = on_delete
        self.ticker_var = tk.StringVar(value=ticker)
        self.name_var   = tk.StringVar(value=name)
        self.price_var  = tk.StringVar(value=price)
        self.shares_var = tk.StringVar(value=shares)
        self._build(row_num)

    def _build(self, row):
        tk.Button(self.frame, text="🔍", bg="#1f6feb", fg="white",
                 font=("Yu Gothic UI", 12), relief="flat", cursor="hand2",
                 command=self._open_search, padx=6, pady=2
                 ).grid(row=row, column=0, padx=(0,4), pady=3)
        tk.Label(self.frame, textvariable=self.ticker_var,
                bg="#21262d", fg="#58a6ff",
                font=("Yu Gothic UI", 12, "bold"),
                width=10, anchor="w", padx=6
                ).grid(row=row, column=1, padx=2, pady=3, sticky="ew")
        tk.Label(self.frame, textvariable=self.name_var,
                bg="#21262d", fg="#e6edf3",
                font=("Yu Gothic UI", 12),
                width=14, anchor="w", padx=6
                ).grid(row=row, column=2, padx=2, pady=3, sticky="ew")
        tk.Entry(self.frame, textvariable=self.price_var,
                bg="#21262d", fg="#e6edf3",
                font=("Yu Gothic UI", 13), relief="flat", width=10,
                insertbackground="#e6edf3"
                ).grid(row=row, column=3, padx=2, pady=3)
        tk.Entry(self.frame, textvariable=self.shares_var,
                bg="#21262d", fg="#e6edf3",
                font=("Yu Gothic UI", 13), relief="flat", width=8,
                insertbackground="#e6edf3"
                ).grid(row=row, column=4, padx=2, pady=3)
        tk.Button(self.frame, text="✕", bg="#161b22", fg="#da3633",
                 font=("Yu Gothic UI", 12), relief="flat", cursor="hand2",
                 command=self._delete, padx=4
                 ).grid(row=row, column=5, padx=2, pady=3)

    def _open_search(self):
        StockSearchDialog(self.app.root, self.stocks, self._on_selected)

    def _on_selected(self, stock):
        self.ticker_var.set(stock["ticker"])
        self.name_var.set(stock["name"])

    def _delete(self):
        for w in self.frame.grid_slaves():
            if int(w.grid_info()["row"]) == self.row_num:
                w.destroy()
        if self.on_delete:
            self.on_delete(self)

    def get_data(self):
        ticker = self.ticker_var.get().strip()
        if not ticker:
            return None
        try:
            return {"ticker": ticker, "name": self.name_var.get().strip(),
                    "buy_price": float(self.price_var.get() or 0),
                    "shares": int(self.shares_var.get() or 0)}
        except ValueError:
            return None


class NyalperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🐱 にゃるぱー秘書")
        self.root.geometry("900x1000")
        self.root.resizable(True, True)
        self.root.configure(bg="#0d1117")
        self.config = load_config()
        self.stocks = load_universe()
        self.is_running = False
        self.process = None
        self.portfolio_rows = []
        self._next_row = 1
        self._bt_running = False
        self._bt_result = None
        self._build_ui()

    def _build_ui(self):
        header = tk.Frame(self.root, bg="#0d1117", pady=16)
        header.pack(fill="x", padx=24)
        tk.Label(header, text="🐱 にゃるぱー秘書",
                bg="#0d1117", fg="#58a6ff",
                font=("Yu Gothic UI", 24, "bold")).pack(side="left")
        tk.Label(header, text="v5.0", bg="#0d1117", fg="#8b949e",
                font=("Yu Gothic UI", 13)).pack(side="left", padx=8, pady=8)
        tk.Button(header, text="📊 ダッシュボード",
                 bg="#1f6feb", fg="white", font=("Yu Gothic UI", 12),
                 relief="flat", cursor="hand2",
                 command=self._open_dashboard, padx=12, pady=6).pack(side="right")
        tk.Frame(self.root, bg="#21262d", height=1).pack(fill="x")

        canvas = tk.Canvas(self.root, bg="#0d1117", highlightthickness=0)
        vsb = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        self.sf = tk.Frame(canvas, bg="#0d1117")
        self.sf.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=self.sf, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._build_portfolio(self.sf)
        self._build_watchlist(self.sf)
        self._build_start(self.sf)
        self._build_log(self.sf)
        self._build_result(self.sf)
        self._build_backtest(self.sf)

    def _section(self, parent, title):
        outer = tk.Frame(parent, bg="#161b22",
                        highlightbackground="#30363d", highlightthickness=1)
        outer.pack(fill="x", pady=(0,12), padx=24)
        tk.Label(outer, text=title, bg="#161b22", fg="#f0f6fc",
                font=("Yu Gothic UI", 13, "bold"), padx=12, pady=8).pack(anchor="w")
        tk.Frame(outer, bg="#21262d", height=1).pack(fill="x")
        inner = tk.Frame(outer, bg="#161b22", padx=12, pady=10)
        inner.pack(fill="x")
        return inner

    def _build_portfolio(self, parent):
        frame = self._section(parent, "💼 ポートフォリオ")
        tk.Label(frame, text="🔍 ボタンで銘柄を検索 → 買値と株数を入力",
                bg="#161b22", fg="#8b949e",
                font=("Yu Gothic UI", 11)).pack(anchor="w", pady=(0,8))
        self.pf = tk.Frame(frame, bg="#161b22")
        self.pf.pack(fill="x")
        for i, (h, w) in enumerate(zip(
                ["検索","ティッカー","銘柄名","買値（円）","株数",""],
                [4, 10, 14, 10, 8, 4])):
            tk.Label(self.pf, text=h, bg="#161b22", fg="#8b949e",
                    font=("Yu Gothic UI", 11), width=w
                    ).grid(row=0, column=i, padx=2, pady=(0,4))
        for item in self.config.get("portfolio", []):
            self._add_row(item.get("ticker",""), item.get("name",""),
                         str(item.get("buy_price","")), str(item.get("shares","")))
        tk.Button(frame, text="＋ 銘柄を追加",
                 bg="#21262d", fg="#58a6ff", font=("Yu Gothic UI", 12),
                 relief="flat", cursor="hand2",
                 command=self._add_row, padx=8, pady=4
                 ).pack(anchor="w", pady=(8,0))

    def _add_row(self, ticker="", name="", price="", shares=""):
        rn = self._next_row
        self._next_row += 1
        def on_del(pr):
            self.portfolio_rows.remove(pr)
        pr = PortfolioRow(self, self.pf, rn, self.stocks,
                         ticker, name, price, shares, on_del)
        self.portfolio_rows.append(pr)

    def _build_watchlist(self, parent):
        frame = self._section(parent, "👁️ 監視銘柄")
        tk.Label(frame, text="ティッカーをカンマ区切りで入力（例：6723.T, NVDA）",
                bg="#161b22", fg="#8b949e",
                font=("Yu Gothic UI", 11)).pack(anchor="w", pady=(0,4))
        wl = self.config.get("watchlist", [])
        self.wl_var = tk.StringVar(value=", ".join(str(t) for t in wl))
        tk.Entry(frame, textvariable=self.wl_var,
                bg="#21262d", fg="#e6edf3",
                font=("Yu Gothic UI", 13), relief="flat",
                insertbackground="#e6edf3"
                ).pack(fill="x", pady=2, ipady=4)

    def _build_start(self, parent):
        frame = tk.Frame(parent, bg="#0d1117")
        frame.pack(fill="x", pady=(4,12), padx=24)
        style = ttk.Style()
        style.configure("G.Horizontal.TProgressbar",
                        background="#238636", troughcolor="#21262d",
                        borderwidth=0, thickness=6)
        self.progress = ttk.Progressbar(frame, style="G.Horizontal.TProgressbar",
                                        mode="indeterminate")
        self.progress.pack(fill="x", pady=(0,6))
        self.status_var = tk.StringVar(value="待機中")
        tk.Label(frame, textvariable=self.status_var,
                bg="#0d1117", fg="#8b949e",
                font=("Yu Gothic UI", 12)).pack(pady=(0,6))
        bf = tk.Frame(frame, bg="#0d1117")
        bf.pack(fill="x")
        self.start_btn = tk.Button(bf, text="🚀  分析スタート！",
                                   bg="#238636", fg="white",
                                   font=("Yu Gothic UI", 16, "bold"),
                                   relief="flat", cursor="hand2",
                                   command=self._start, padx=20, pady=12)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0,8))
        self.stop_btn = tk.Button(bf, text="⏹ 停止",
                                  bg="#21262d", fg="#8b949e",
                                  font=("Yu Gothic UI", 14),
                                  relief="flat", cursor="hand2",
                                  command=self._stop, padx=12, pady=12,
                                  state="disabled")
        self.stop_btn.pack(side="left")

    def _build_log(self, parent):
        frame = self._section(parent, "📋 実行ログ")
        self.log = scrolledtext.ScrolledText(frame,
                                             bg="#0d1117", fg="#e6edf3",
                                             font=("Consolas", 12),
                                             relief="flat", height=10,
                                             insertbackground="#e6edf3",
                                             wrap="word")
        self.log.pack(fill="both", expand=True)
        tk.Button(frame, text="クリア", bg="#21262d", fg="#8b949e",
                 font=("Yu Gothic UI", 11), relief="flat", cursor="hand2",
                 command=lambda: self.log.delete(1.0, tk.END),
                 padx=6, pady=2).pack(anchor="e", pady=(4,0))

    def _build_result(self, parent):
        """分析結果表示セクション"""
        self.result_frame = tk.Frame(parent, bg="#0d1117")
        self.result_frame.pack(fill="x", padx=24, pady=(0,12))

    def _show_result(self):
        """最新のレポートを読み込んでアプリ内に表示"""
        # 古い結果をクリア
        for w in self.result_frame.winfo_children():
            w.destroy()

        # 最新のJSONレポートを探す
        import json, glob
        report_files = sorted(
            glob.glob(str(OUTPUT_DIR / "daily_report_*.json")),
            reverse=True)

        if not report_files:
            # JSONがなければtxtを表示
            txt_files = sorted(
                glob.glob(str(OUTPUT_DIR / "daily_report_*.txt")),
                reverse=True)
            if txt_files:
                self._show_text_result(txt_files[0])
            return

        with open(report_files[0], encoding="utf-8") as f:
            data = json.load(f)

        self._render_result(data)

    def _show_text_result(self, path):
        """テキストレポートをそのまま表示"""
        outer = tk.Frame(self.result_frame, bg="#161b22",
                        highlightbackground="#30363d", highlightthickness=1)
        outer.pack(fill="x")
        tk.Label(outer, text="📊 分析結果",
                bg="#161b22", fg="#f0f6fc",
                font=("Yu Gothic UI", 13, "bold"),
                padx=12, pady=8).pack(anchor="w")
        tk.Frame(outer, bg="#21262d", height=1).pack(fill="x")
        inner = tk.Frame(outer, bg="#161b22", padx=12, pady=10)
        inner.pack(fill="x")
        with open(path, encoding="utf-8", errors="replace") as f:
            content_txt = f.read()
        txt = tk.Text(inner, bg="#0d1117", fg="#e6edf3",
                     font=("Consolas", 12), relief="flat",
                     height=20, wrap="word")
        txt.insert("1.0", content_txt)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)

    def _render_result(self, data):
        """JSONデータから結果カードを描画"""
        outer = tk.Frame(self.result_frame, bg="#161b22",
                        highlightbackground="#30363d", highlightthickness=1)
        outer.pack(fill="x")
        tk.Label(outer, text="📊 分析結果",
                bg="#161b22", fg="#f0f6fc",
                font=("Yu Gothic UI", 13, "bold"),
                padx=12, pady=8).pack(anchor="w")
        tk.Frame(outer, bg="#21262d", height=1).pack(fill="x")
        inner = tk.Frame(outer, bg="#161b22", padx=12, pady=10)
        inner.pack(fill="x")

        # ===== マクロ情報 =====
        macro  = data.get("macro", {})
        vix    = macro.get("vix", 0)
        nikkei = macro.get("nikkei", {})
        sp500  = macro.get("sp500", {})
        nikkei_price = nikkei.get("last_close", 0)
        nikkei_pct   = nikkei.get("change_pct", 0)
        sp500_price  = sp500.get("last_close", 0)
        sp500_pct    = sp500.get("change_pct", 0)

        macro_frame = tk.Frame(inner, bg="#21262d", padx=10, pady=10)
        macro_frame.pack(fill="x", pady=(0,10))

        # 主要指数
        idx_frame = tk.Frame(macro_frame, bg="#21262d")
        idx_frame.pack(fill="x")

        for label, price, pct, prefix in [
            ("日経平均", nikkei_price, nikkei_pct, "¥"),
            ("S&P500", sp500_price, sp500_pct, "$"),
        ]:
            col = tk.Frame(idx_frame, bg="#1c2128", padx=8, pady=6)
            col.pack(side="left", padx=(0,8), pady=4)
            tk.Label(col, text=label, bg="#1c2128", fg="#8b949e",
                    font=("Yu Gothic UI", 10)).pack(anchor="w")
            tk.Label(col,
                    text=f"{prefix}{price:,.0f}",
                    bg="#1c2128",
                    fg="#58a6ff" if pct >= 0 else "#f85149",
                    font=("Yu Gothic UI", 14, "bold")).pack(anchor="w")
            tk.Label(col,
                    text=f"{'▲' if pct>=0 else '▼'}{abs(pct):.2f}%",
                    bg="#1c2128",
                    fg="#3fb950" if pct >= 0 else "#f85149",
                    font=("Yu Gothic UI", 11)).pack(anchor="w")

        # VIX
        vix_col = tk.Frame(idx_frame, bg="#1c2128", padx=8, pady=6)
        vix_col.pack(side="left", padx=(0,8), pady=4)
        tk.Label(vix_col, text="VIX", bg="#1c2128", fg="#8b949e",
                font=("Yu Gothic UI", 10)).pack(anchor="w")
        vix_color = "#f85149" if vix >= 25 else "#e3b341" if vix >= 20 else "#3fb950"
        tk.Label(vix_col, text=f"{vix:.2f}",
                bg="#1c2128", fg=vix_color,
                font=("Yu Gothic UI", 14, "bold")).pack(anchor="w")
        tk.Label(vix_col,
                text="⚠️ 警戒" if vix >= 25 else "安定",
                bg="#1c2128", fg=vix_color,
                font=("Yu Gothic UI", 11)).pack(anchor="w")

        # CME先物
        cme = macro.get("cme_futures", {})
        if cme and cme.get("last"):
            cme_col = tk.Frame(idx_frame, bg="#1c2128", padx=8, pady=6)
            cme_col.pack(side="left", padx=(0,8), pady=4)
            tk.Label(cme_col, text="CME先物", bg="#1c2128", fg="#8b949e",
                    font=("Yu Gothic UI", 10)).pack(anchor="w")
            tk.Label(cme_col, text=f"¥{cme['last']:,.0f}",
                    bg="#1c2128", fg="#58a6ff",
                    font=("Yu Gothic UI", 14, "bold")).pack(anchor="w")
            gap_text = "⬆️ ギャップアップ予想" if cme.get("gap_up") else "⬇️ ギャップダウン予想"
            gap_color = "#3fb950" if cme.get("gap_up") else "#f85149"
            tk.Label(cme_col, text=gap_text,
                    bg="#1c2128", fg=gap_color,
                    font=("Yu Gothic UI", 10)).pack(anchor="w")

        # コモディティ
        comm = macro.get("commodities", {})
        if comm:
            comm_frame = tk.Frame(macro_frame, bg="#21262d")
            comm_frame.pack(fill="x", pady=(8,0))
            comm_labels = {
                "crude_oil": ("WTI原油", "$", ""),
                "gold":      ("金",     "$", ""),
                "bond_10y":  ("米10年債", "", "%"),
                "usdjpy":    ("USD/JPY", "", "円"),
            }
            for key, (label, pre, suf) in comm_labels.items():
                if key not in comm:
                    continue
                d = comm[key]
                pct = d.get("change_pct", 0)
                col = tk.Frame(comm_frame, bg="#0d1117", padx=6, pady=4)
                col.pack(side="left", padx=(0,6))
                tk.Label(col, text=label, bg="#0d1117", fg="#8b949e",
                        font=("Yu Gothic UI", 9)).pack(anchor="w")
                tk.Label(col, text=f"{pre}{d['last']}{suf}",
                        bg="#0d1117", fg="#e6edf3",
                        font=("Yu Gothic UI", 11, "bold")).pack(anchor="w")
                tk.Label(col,
                        text=f"{'▲' if pct>=0 else '▼'}{abs(pct):.2f}%",
                        bg="#0d1117",
                        fg="#3fb950" if pct>=0 else "#f85149",
                        font=("Yu Gothic UI", 10)).pack(anchor="w")

        # 注目セクター
        sector_focus = macro.get("sector_focus", [])
        top_etf      = macro.get("top_sector_etf", "")
        top_perf     = macro.get("top_sector_perf", 0)
        if sector_focus:
            sf = tk.Frame(macro_frame, bg="#21262d")
            sf.pack(fill="x", pady=(8,0))
            tk.Label(sf,
                    text=f"🔥 注目セクター: {' / '.join(sector_focus)}（{top_etf} {top_perf:+.2f}%）",
                    bg="#21262d", fg="#e3b341",
                    font=("Yu Gothic UI", 11, "bold")).pack(anchor="w")

        # 地合い判定
        warning = data.get("macro_warning", False)
        entry_ok = not warning
        tk.Label(macro_frame,
                text=f"{'✅ 地合い：新規エントリー可' if entry_ok else '⚠️ マクロ警告：新規エントリー見送り'}",
                bg="#21262d",
                fg="#3fb950" if entry_ok else "#f85149",
                font=("Yu Gothic UI", 12, "bold")).pack(anchor="w", pady=(8,0))

        # ===== テーマ株枠 =====
        theme_ai = data.get("theme_ai", {})
        jp_sector = data.get("jp_sector_strength", {})
        if theme_ai:
            tk.Label(inner, text="🎯 今日のテーマ株",
                    bg="#161b22", fg="#e3b341",
                    font=("Yu Gothic UI", 13, "bold")).pack(anchor="w", pady=(10, 4))

            theme_card = tk.Frame(inner, bg="#1c1f26",
                                 highlightbackground="#e3b341", highlightthickness=1)
            theme_card.pack(fill="x", pady=(0, 8))
            tc_inner = tk.Frame(theme_card, bg="#1c1f26", padx=12, pady=10)
            tc_inner.pack(fill="x")

            theme     = theme_ai.get("theme", "")
            sub_theme = theme_ai.get("sub_theme", "")
            reason    = theme_ai.get("reason", "")
            caution   = theme_ai.get("caution", "")
            sectors   = theme_ai.get("target_sectors_jp", [])

            tk.Label(tc_inner, text=f"🔥 {theme}",
                    bg="#1c1f26", fg="#3fb950",
                    font=("Yu Gothic UI", 13, "bold")).pack(anchor="w")
            if sub_theme:
                tk.Label(tc_inner, text=f"💡 {sub_theme}",
                        bg="#1c1f26", fg="#e3b341",
                        font=("Yu Gothic UI", 11)).pack(anchor="w")
            if reason:
                tk.Label(tc_inner, text=reason,
                        bg="#1c1f26", fg="#c9d1d9",
                        font=("Yu Gothic UI", 11),
                        wraplength=700, justify="left").pack(anchor="w", pady=(4, 0))
            if caution:
                tk.Label(tc_inner, text=f"⚠️ {caution}",
                        bg="#1c1f26", fg="#e3b341",
                        font=("Yu Gothic UI", 11)).pack(anchor="w")
            if sectors:
                tk.Label(tc_inner, text=f"🎯 注目業種: {'・'.join(sectors)}",
                        bg="#1c1f26", fg="#58a6ff",
                        font=("Yu Gothic UI", 11)).pack(anchor="w")

            # 東証セクター強弱
            if jp_sector:
                tk.Label(tc_inner, text="【東証セクター強弱】",
                        bg="#1c1f26", fg="#8b949e",
                        font=("Yu Gothic UI", 10)).pack(anchor="w", pady=(6, 2))
                top3    = jp_sector.get("top3", [])
                bottom3 = jp_sector.get("bottom3", [])
                row_f   = tk.Frame(tc_inner, bg="#1c1f26")
                row_f.pack(anchor="w")
                for s in top3:
                    tk.Label(row_f, text=f"▲{s['sector']} +{s['return']:.2f}%  ",
                            bg="#1c1f26", fg="#3fb950",
                            font=("Yu Gothic UI", 10)).pack(side="left")
                row_b = tk.Frame(tc_inner, bg="#1c1f26")
                row_b.pack(anchor="w")
                for s in bottom3:
                    tk.Label(row_b, text=f"▼{s['sector']} {s['return']:.2f}%  ",
                            bg="#1c1f26", fg="#f85149",
                            font=("Yu Gothic UI", 10)).pack(side="left")

        # ===== BUY候補（プライム）=====
        rr = data.get("risk_reward", {})
        picks = []
        for market in ("jp", "us"):
            picks.extend(rr.get(market, []))

        tk.Label(inner, text="📈 BUY候補（プライム）",
                bg="#161b22", fg="#3fb950",
                font=("Yu Gothic UI", 13, "bold")).pack(anchor="w", pady=(4,6))

        if picks:
            for i, item in enumerate(picks[:5], 1):
                self._render_pick_card(inner, item, i)
        else:
            tk.Label(inner, text="条件一致なし（スコア/RR未達）",
                    bg="#161b22", fg="#8b949e",
                    font=("Yu Gothic UI", 12)).pack(anchor="w", pady=4)

        # ===== BUY候補（グロース）=====
        growth_picks = rr.get("growth", [])
        if growth_picks:
            tk.Label(inner, text="🌱 BUY候補（グロース）",
                    bg="#161b22", fg="#58a6ff",
                    font=("Yu Gothic UI", 13, "bold")).pack(anchor="w", pady=(10, 2))
            tk.Label(inner, text="⚠️ 流動性・損切りに注意してください",
                    bg="#161b22", fg="#e3b341",
                    font=("Yu Gothic UI", 11)).pack(anchor="w", pady=(0, 6))
            for i, item in enumerate(growth_picks[:5], 1):
                self._render_pick_card(inner, item, i, is_growth=True)

        # ===== トレンドフォロー枠 =====
        trend_follow = data.get("trend_follow", [])
        if trend_follow:
            tk.Label(inner, text="🚀 トレンドフォロー候補（右肩上がり）",
                    bg="#161b22", fg="#e3b341",
                    font=("Yu Gothic UI", 13, "bold")).pack(anchor="w", pady=(10, 6))
            for i, item in enumerate(trend_follow[:10], 1):
                self._render_trend_card(inner, item, i)

        # ===== テクニカル上位（参考）=====
        screening = data.get("screening", {})
        refs = []
        pick_tickers = {p["ticker"] for p in picks} | {p["ticker"] for p in growth_picks}
        for market in ("jp", "us"):
            for item in screening.get(market, []):
                if item["ticker"] not in pick_tickers:
                    refs.append(item)

        if refs:
            tk.Label(inner, text="📋 テクニカル上位（参考）",
                    bg="#161b22", fg="#e3b341",
                    font=("Yu Gothic UI", 13, "bold")).pack(anchor="w", pady=(10,6))
            for i, item in enumerate(refs[:5], 1):
                self._render_pick_card(inner, item, i, is_ref=True)

    def _render_pick_card(self, parent, item, i, is_ref=False, is_growth=False):
        """銘柄カードを描画（充実版）"""
        nums = "①②③④⑤⑥⑦⑧⑨⑩"
        num  = nums[i-1] if i <= len(nums) else str(i)

        vd      = item.get("ai_verdict", {})
        verdict = vd.get("verdict", "")
        conf    = vd.get("confidence", "")
        reasons = vd.get("reasons", [])
        risk    = vd.get("risk", "")
        fund_reason = vd.get("fundamental_reason", "")
        is_jp   = item.get("market", "jp") == "jp"
        cur_val = item.get("current_price", 0)
        cur     = f"¥{cur_val:,.1f}" if is_jp else f"${cur_val:,.2f}"
        upper   = item.get("upper_target", 0)
        lower   = item.get("lower_target", 0)
        entry   = item.get("entry_price", cur_val)
        rr_val  = item.get("rr", 0)
        score   = item.get("score", item.get("tech_score", 0))
        parts   = item.get("score_parts", {})
        fund    = item.get("fundamentals", {})
        adr     = item.get("adr", {})
        sent_score = item.get("sentiment_score", 0)
        earnings_label = item.get("earnings_label", "")

        # カード背景色
        if is_growth:
            card_bg, border = "#0d1f35", "#1f6feb"   # 青系（グロース専用）
        elif is_ref:
            card_bg, border = "#1c2128", "#30363d"
        elif "BUY" in verdict:
            card_bg, border = "#0f2d1c", "#238636"
        elif "SELL" in verdict:
            card_bg, border = "#2d1010", "#da3633"
        else:
            card_bg, border = "#1c1f26", "#30363d"

        card = tk.Frame(parent, bg=card_bg,
                       highlightbackground=border, highlightthickness=2)
        card.pack(fill="x", pady=4)

        # ===== ヘッダー =====
        header = tk.Frame(card, bg=card_bg, padx=12, pady=8)
        header.pack(fill="x")
        flag = "🇯🇵" if is_jp else "🇺🇸"
        growth_tag = "  🌱グロース" if is_growth else ""
        ref_tag    = "  ※RR未達"   if is_ref    else ""
        tk.Label(header,
                text=f"{num} {flag}  {item.get('name', '')}（{item.get('ticker', '')}）{growth_tag}{ref_tag}",
                bg=card_bg, fg="#f0f6fc",
                font=("Yu Gothic UI", 14, "bold")).pack(side="left")

        right = tk.Frame(header, bg=card_bg)
        right.pack(side="right")
        tk.Label(right, text=cur,
                bg=card_bg, fg="#f0f6fc",
                font=("Yu Gothic UI", 14, "bold")).pack(anchor="e")
        if verdict:
            v_color = "#238636" if "BUY" in verdict else "#da3633" if "SELL" in verdict else "#9e6a03"
            v_icon  = "🟢" if "BUY" in verdict else "🔴" if "SELL" in verdict else "🟡"
            tk.Label(right,
                    text=f"{v_icon} {verdict}  確信度:{conf}",
                    bg=v_color, fg="white",
                    font=("Yu Gothic UI", 10, "bold"),
                    padx=6, pady=2).pack(anchor="e", pady=(4,0))

        # 決算フラグ
        if earnings_label:
            tk.Label(card, text=f"⚠️ {earnings_label}",
                    bg="#2d1010", fg="#e3b341",
                    font=("Yu Gothic UI", 11),
                    padx=12).pack(anchor="w")

        # ===== ADR =====
        if adr and adr.get("last"):
            adr_pct = adr.get("change_pct", 0)
            adr_icon = "⬆️" if adr.get("gap_up") else "⬇️" if adr.get("gap_down") else "➡️"
            adr_bg = "#0f2d1c" if adr.get("gap_up") else "#2d1010" if adr.get("gap_down") else "#1c2128"
            adr_frame = tk.Frame(card, bg=adr_bg, padx=12, pady=4)
            adr_frame.pack(fill="x")
            tk.Label(adr_frame,
                    text=f"🌐 ADR({adr['ticker']}): ${adr['last']}  {adr_pct:+.2f}%  {adr_icon}",
                    bg=adr_bg,
                    fg="#3fb950" if adr.get("gap_up") else "#f85149" if adr.get("gap_down") else "#8b949e",
                    font=("Yu Gothic UI", 11, "bold")).pack(anchor="w")

        # ===== エントリー・利確・損切 =====
        if upper > 0 and lower > 0:
            trade_frame = tk.Frame(card, bg=card_bg, padx=12, pady=6)
            trade_frame.pack(fill="x")

            entry_diff = (entry - cur_val) / cur_val * 100 if cur_val > 0 else 0
            entry_note = f"（ADR予想 {entry_diff:+.1f}%）" if abs(entry_diff) > 0.1 else "（現在値）"
            tp_pct = (upper - entry) / entry * 100 if entry > 0 else 0
            sl_pct = (entry - lower) / entry * 100 if entry > 0 else 0

            for label, val, pct, color, fmt in [
                ("💰 エントリー", entry, entry_diff, "#58a6ff",
                 f"¥{entry:,.0f}" if is_jp else f"${entry:.2f}"),
                ("🎯 利確", upper, tp_pct, "#3fb950",
                 f"¥{upper:,.0f}" if is_jp else f"${upper:.2f}"),
                ("🛡️ 損切", lower, sl_pct, "#f85149",
                 f"¥{lower:,.0f}" if is_jp else f"${lower:.2f}"),
            ]:
                row = tk.Frame(trade_frame, bg=card_bg)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=label, bg=card_bg, fg="#8b949e",
                        font=("Yu Gothic UI", 11), width=12, anchor="w").pack(side="left")
                tk.Label(row, text=fmt, bg=card_bg, fg=color,
                        font=("Yu Gothic UI", 12, "bold")).pack(side="left", padx=6)
                sign = "+" if pct >= 0 else ""
                tk.Label(row, text=f"{sign}{pct:.1f}%",
                        bg=card_bg, fg=color,
                        font=("Yu Gothic UI", 11)).pack(side="left")

            tk.Label(trade_frame,
                    text=f"⚖️ RR: {rr_val:.2f}   Score: {score}/120",
                    bg=card_bg, fg="#8b949e",
                    font=("Yu Gothic UI", 11)).pack(anchor="w", pady=(4,0))

        # ===== AI分析 =====
        if reasons and not is_ref:
            ai_frame = tk.Frame(card, bg="#161b22", padx=12, pady=8)
            ai_frame.pack(fill="x")
            tk.Label(ai_frame, text="🤖 AI分析",
                    bg="#161b22", fg="#58a6ff",
                    font=("Yu Gothic UI", 11, "bold")).pack(anchor="w", pady=(0,4))
            for r in reasons:
                tk.Label(ai_frame, text=f"  ・{r}",
                        bg="#161b22", fg="#c9d1d9",
                        font=("Yu Gothic UI", 11),
                        wraplength=820, justify="left").pack(anchor="w")
            if fund_reason:
                tk.Label(ai_frame, text=f"  📊 {fund_reason}",
                        bg="#161b22", fg="#79c0ff",
                        font=("Yu Gothic UI", 11),
                        wraplength=820, justify="left").pack(anchor="w", pady=(2,0))
            if risk:
                tk.Label(ai_frame, text=f"  ⚠️ {risk}",
                        bg="#161b22", fg="#e3b341",
                        font=("Yu Gothic UI", 11),
                        wraplength=820, justify="left").pack(anchor="w", pady=(2,0))

        # ===== ファンダメンタル =====
        if fund:
            fund_frame = tk.Frame(card, bg="#0d1117", padx=12, pady=8)
            fund_frame.pack(fill="x")
            tk.Label(fund_frame, text="📋 ファンダメンタル",
                    bg="#0d1117", fg="#8b949e",
                    font=("Yu Gothic UI", 11, "bold")).pack(anchor="w", pady=(0,4))

            items1 = []
            if fund.get("per"):   items1.append(f"PER:{fund['per']}倍")
            if fund.get("pbr"):   items1.append(f"PBR:{fund['pbr']}倍")
            if fund.get("roe"):   items1.append(f"ROE:{fund['roe']}%")
            if fund.get("eps"):   items1.append(f"EPS:{fund['eps']}")
            if items1:
                tk.Label(fund_frame, text="  " + "  /  ".join(items1),
                        bg="#0d1117", fg="#c9d1d9",
                        font=("Yu Gothic UI", 11)).pack(anchor="w")

            items2 = []
            if fund.get("revenue_growth"):   items2.append(f"売上YoY:{fund['revenue_growth']:+.1f}%")
            if fund.get("earnings_growth"):  items2.append(f"利益YoY:{fund['earnings_growth']:+.1f}%")
            if fund.get("operating_margin"): items2.append(f"営業利益率:{fund['operating_margin']:.1f}%")
            if fund.get("dividend_yield"):   items2.append(f"配当:{fund['dividend_yield']:.1f}%")
            if fund.get("target_upside"):    items2.append(f"目標株価:{fund['target_upside']:+.1f}%")
            if items2:
                tk.Label(fund_frame, text="  " + "  /  ".join(items2),
                        bg="#0d1117", fg="#c9d1d9",
                        font=("Yu Gothic UI", 11)).pack(anchor="w")

        # ===== スコア内訳 =====
        score_frame = tk.Frame(card, bg=card_bg, padx=12, pady=6)
        score_frame.pack(fill="x")
        t        = parts.get("trend", 0)
        m        = parts.get("momentum", 0)
        h        = parts.get("heat", 0)
        f_score  = parts.get("fundamental", 0)
        v_score  = parts.get("volume", 0)
        pb_score = parts.get("pullback", 0)
        bo_score = parts.get("breakout", 0)
        wk_score = parts.get("weekly", 0)

        pattern_map = {
            "押し目+BO":     ("🔥 押し目+BO",   "#e3b341"),
            "押し目":        ("📉 押し目",       "#58a6ff"),
            "ブレイクアウト":("🚀 BO",           "#3fb950"),
            "ベース":        ("📊 ベース",        "#8b949e"),
        }
        diag          = item.get("tech_diag", {})
        pattern       = diag.get("pattern", "ベース")
        vol_ratio     = diag.get("vol_ratio", 0)
        weekly_trend  = diag.get("weekly_trend", "")
        weekly_sigs   = diag.get("weekly_signals", [])
        pat_label, pat_color = pattern_map.get(pattern, ("📊 ベース", "#8b949e"))

        tk.Label(score_frame, text=pat_label,
                bg=card_bg, fg=pat_color,
                font=("Yu Gothic UI", 11, "bold")).pack(anchor="w")

        tk.Label(score_frame,
                text=f"  総合: {score}/100点　T:{t}/50  M:{m}/20  H:{h}/20  F:{f_score}/85  N:{sent_score:+d}",
                bg=card_bg, fg="#8b949e",
                font=("Yu Gothic UI", 11)).pack(anchor="w")

        bonus_parts = []
        if v_score > 0:
            bonus_parts.append(f"📊出来高:+{v_score}({vol_ratio:.1f}倍)")
        if pb_score > 0:
            bonus_parts.append(f"押し目ボーナス:+{pb_score}/50")
        if bo_score > 0:
            bonus_parts.append(f"BOボーナス:+{bo_score}/40")
        if bonus_parts:
            tk.Label(score_frame,
                    text="  " + "  ".join(bonus_parts),
                    bg=card_bg, fg=pat_color,
                    font=("Yu Gothic UI", 11)).pack(anchor="w")

        # 週足トレンド表示
        if weekly_trend:
            wk_color = "#3fb950" if wk_score > 0 else "#f85149" if wk_score < 0 else "#8b949e"
            wk_text  = f"📅 週足: {weekly_trend} ({wk_score:+d}点)"
            if weekly_sigs:
                wk_text += f"  [{' / '.join(weekly_sigs)}]"
            tk.Label(score_frame, text=f"  {wk_text}",
                    bg=card_bg, fg=wk_color,
                    font=("Yu Gothic UI", 11)).pack(anchor="w")

    def _save(self):
        portfolio = [d for pr in self.portfolio_rows if (d := pr.get_data())]
        watchlist = [t.strip() for t in self.wl_var.get().split(",") if t.strip()]
        self.config["portfolio"] = portfolio
        self.config["watchlist"] = watchlist
        save_config(self.config)
        self._log(f"✅ 保存: ポートフォリオ{len(portfolio)}銘柄 / 監視{len(watchlist)}銘柄")

    def _log(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.root.update_idletasks()

    def _start(self):
        if self.is_running:
            return
        self._save()
        self.is_running = True
        self.start_btn.config(state="disabled", bg="#1a7f37")
        self.stop_btn.config(state="normal", bg="#da3633", fg="white")
        self.status_var.set("分析中...")
        self.progress.start(10)
        self._log("=" * 50)
        self._log("🚀 にゃるぱー秘書 分析開始！")
        self._log("=" * 50)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            # Windows環境でUTF-8出力を強制
            env = __import__('os').environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(BASE_DIR / "main.py")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(BASE_DIR), env=env)
            
            # stdoutとstderrを別スレッドで読む
            def read_stream(stream, prefix=""):
                for raw_line in stream:
                    try:
                        line = raw_line.decode("utf-8", errors="replace").rstrip()
                    except Exception:
                        line = str(raw_line).rstrip()
                    if line:
                        self.root.after(0, self._log, prefix + line)
            
            t_out = threading.Thread(
                target=read_stream,
                args=(self.process.stdout, ""),
                daemon=True)
            t_err = threading.Thread(
                target=read_stream,
                args=(self.process.stderr, "⚠️ "),
                daemon=True)
            t_out.start()
            t_err.start()
            t_out.join()
            t_err.join()
            self.process.wait()
            if self.process.returncode == 0:
                self.root.after(0, self._done)
            else:
                self.root.after(0, self._err, f"終了コード: {self.process.returncode}")
        except Exception as e:
            self.root.after(0, self._err, str(e))

    def _done(self):
        self._log("=" * 50)
        self._log("✅ 分析完了！")
        self._log("=" * 50)
        self.status_var.set("✅ 完了！")
        self._reset()
        self.progress.stop()
        self._show_result()
        self._open_dashboard()

    def _err(self, msg):
        self._log(f"❌ エラー: {msg}")
        self.status_var.set("❌ エラー")
        self._reset()
        self.progress.stop()

    def _stop(self):
        if self.process:
            self.process.terminate()
            self._log("⏹ 停止しました")
        self.status_var.set("停止")
        self._reset()
        self.progress.stop()

    def _reset(self):
        self.is_running = False
        self.start_btn.config(state="normal", bg="#238636")
        self.stop_btn.config(state="disabled", bg="#21262d", fg="#8b949e")

    def _open_dashboard(self):
        d = OUTPUT_DIR / "dashboard.html"
        if d.exists():
            webbrowser.open(f"file:///{d}")
            self._log("📊 ダッシュボードを開きました")
        else:
            messagebox.showwarning("未生成",
                "まだ分析が実行されていません。\n「分析スタート！」を押してください。")

    # ══════════════════════════════════════════
    #  バックテスト セクション
    # ══════════════════════════════════════════

    def _build_backtest(self, parent):
        """バックテストUIセクションを構築する"""
        outer = tk.Frame(parent, bg="#161b22",
                        highlightbackground="#30363d", highlightthickness=1)
        outer.pack(fill="x", pady=(0, 12), padx=24)

        # ─── ヘッダー ───
        tk.Label(outer, text="🔬 バックテスト",
                bg="#161b22", fg="#f0f6fc",
                font=("Yu Gothic UI", 13, "bold"),
                padx=12, pady=8).pack(anchor="w")
        tk.Frame(outer, bg="#21262d", height=1).pack(fill="x")

        inner = tk.Frame(outer, bg="#161b22", padx=12, pady=12)
        inner.pack(fill="x")

        # ─── 銘柄選択 ───
        row0 = tk.Frame(inner, bg="#161b22")
        row0.pack(fill="x", pady=(0, 8))

        tk.Label(row0, text="銘柄：", bg="#161b22", fg="#8b949e",
                font=("Yu Gothic UI", 12)).pack(side="left")

        self._bt_ticker_var = tk.StringVar(value="8306.T")
        self._bt_name_var   = tk.StringVar(value="三菱UFJ")

        tk.Label(row0, textvariable=self._bt_ticker_var,
                bg="#21262d", fg="#58a6ff",
                font=("Yu Gothic UI", 12, "bold"),
                width=10, anchor="w", padx=6).pack(side="left", padx=(0, 4))
        tk.Label(row0, textvariable=self._bt_name_var,
                bg="#21262d", fg="#e6edf3",
                font=("Yu Gothic UI", 12),
                width=16, anchor="w", padx=6).pack(side="left", padx=(0, 8))
        tk.Button(row0, text="🔍 銘柄を選ぶ",
                 bg="#1f6feb", fg="white",
                 font=("Yu Gothic UI", 11), relief="flat", cursor="hand2",
                 command=self._bt_open_search,
                 padx=10, pady=4).pack(side="left")

        # ─── パラメータ スライダー（1行目）───
        params_frame = tk.Frame(inner, bg="#161b22")
        params_frame.pack(fill="x", pady=(0, 6))

        # 期間
        self._bt_years_var = tk.IntVar(value=2)
        self._make_slider(params_frame, "期間（年）", self._bt_years_var,
                         from_=1, to=5, resolution=1, col=0)

        # スコア閾値（180点満点対応）
        self._bt_score_var = tk.IntVar(value=70)
        self._make_slider(params_frame, "スコア閾値(195点満点)", self._bt_score_var,
                         from_=30, to=195, resolution=5, col=1)

        # RR閾値
        self._bt_rr_var = tk.DoubleVar(value=1.2)
        self._make_slider(params_frame, "RR閾値", self._bt_rr_var,
                         from_=0.8, to=3.0, resolution=0.1, col=2)

        # 最大保有日数
        self._bt_hold_var = tk.IntVar(value=10)
        self._make_slider(params_frame, "最大保有日数", self._bt_hold_var,
                         from_=3, to=30, resolution=1, col=3)

        # ─── パラメータ スライダー（2行目：損切り・利確）───
        params_frame2 = tk.Frame(inner, bg="#161b22")
        params_frame2.pack(fill="x", pady=(0, 10))

        # ATR乗数（損切り幅）
        self._bt_atr_var = tk.DoubleVar(value=2.0)
        self._make_slider(params_frame2, "ATR乗数（損切り幅）", self._bt_atr_var,
                         from_=1.0, to=4.0, resolution=0.5, col=0)

        # 利確倍率（ATR×N）
        self._bt_tp_var = tk.DoubleVar(value=0.0)
        self._make_slider(params_frame2, "利確倍率ATR×N（0=自動）", self._bt_tp_var,
                         from_=0.0, to=4.0, resolution=0.5, col=1)

        # 利確モード説明ラベル
        tip_frame = tk.Frame(params_frame2, bg="#161b22", padx=8)
        tip_frame.grid(row=0, column=2, columnspan=2, sticky="w")
        tk.Label(tip_frame,
                text="💡 利確倍率 0 = 従来方式（BB上限 or 20日高値）\n"
                     "   1.5〜2.0 = ATR×N と20日高値の小さい方",
                bg="#161b22", fg="#8b949e",
                font=("Yu Gothic UI", 10),
                justify="left").pack(anchor="w")

        # ─── 実行ボタン ───
        btn_frame = tk.Frame(inner, bg="#161b22")
        btn_frame.pack(fill="x", pady=(4, 8))

        self._bt_run_btn = tk.Button(btn_frame,
                text="▶  バックテスト実行",
                bg="#1f6feb", fg="white",
                font=("Yu Gothic UI", 13, "bold"),
                relief="flat", cursor="hand2",
                command=self._bt_start,
                padx=16, pady=8)
        self._bt_run_btn.pack(side="left")

        self._bt_status_var = tk.StringVar(value="")
        tk.Label(btn_frame, textvariable=self._bt_status_var,
                bg="#161b22", fg="#8b949e",
                font=("Yu Gothic UI", 11)).pack(side="left", padx=12)

        # プログレスバー
        style = ttk.Style()
        style.configure("BT.Horizontal.TProgressbar",
                        background="#1f6feb", troughcolor="#21262d",
                        borderwidth=0, thickness=5)
        self._bt_progress = ttk.Progressbar(inner, style="BT.Horizontal.TProgressbar",
                                             mode="indeterminate")
        self._bt_progress.pack(fill="x", pady=(0, 8))

        # ─── 結果表示エリア ───
        self._bt_result_frame = tk.Frame(inner, bg="#161b22")
        self._bt_result_frame.pack(fill="x")

    def _make_slider(self, parent, label, var, from_, to, resolution, col):
        """スライダーとラベルを横並びで生成"""
        f = tk.Frame(parent, bg="#161b22", padx=8)
        f.grid(row=0, column=col, sticky="ew", padx=(0, 16))
        parent.columnconfigure(col, weight=1)

        tk.Label(f, text=label, bg="#161b22", fg="#8b949e",
                font=("Yu Gothic UI", 10)).pack(anchor="w")

        val_label = tk.Label(f, textvariable=var,
                            bg="#21262d", fg="#e6edf3",
                            font=("Yu Gothic UI", 12, "bold"),
                            width=6, anchor="center")
        val_label.pack(anchor="w", pady=(2, 2))

        tk.Scale(f, variable=var,
                from_=from_, to=to, resolution=resolution,
                orient="horizontal", length=160,
                bg="#161b22", fg="#e6edf3",
                troughcolor="#21262d", highlightthickness=0,
                showvalue=False,
                command=lambda v, lv=val_label, vr=var: lv.config(text=f"{vr.get()}")
                ).pack(anchor="w")

    def _render_trend_card(self, parent, item, i):
        """トレンドフォロー銘柄カードを描画"""
        nums  = "①②③④⑤⑥⑦⑧⑨⑩"
        num   = nums[i-1] if i <= len(nums) else str(i)
        price = item.get("current_price", 0)
        cur   = f"¥{price:,.1f}"
        po    = "✅ PO" if item.get("perfect_order") else "　　"
        r1m   = item.get("ret_1m", 0)
        r3m   = item.get("ret_3m", 0)
        r6m   = item.get("ret_6m", 0)
        score = item.get("tf_score", 0)

        card = tk.Frame(parent, bg="#1a1f2e",
                       highlightbackground="#e3b341", highlightthickness=1)
        card.pack(fill="x", pady=3)

        inner = tk.Frame(card, bg="#1a1f2e", padx=12, pady=8)
        inner.pack(fill="x")

        # ヘッダー行
        hdr = tk.Frame(inner, bg="#1a1f2e")
        hdr.pack(fill="x")
        tk.Label(hdr,
                text=f"{num} 🇯🇵  {item.get('name', '')}（{item.get('ticker', '')}）  {po}",
                bg="#1a1f2e", fg="#f0f6fc",
                font=("Yu Gothic UI", 13, "bold")).pack(side="left")
        tk.Label(hdr,
                text=f"🏆 {score}/100点",
                bg="#1a1f2e", fg="#e3b341",
                font=("Yu Gothic UI", 12, "bold")).pack(side="right")

        # 価格・業種
        tk.Label(inner,
                text=f"📍 {cur}  業種: {item.get('sector', '')}",
                bg="#1a1f2e", fg="#8b949e",
                font=("Yu Gothic UI", 11)).pack(anchor="w")

        # リターン行
        ret_frame = tk.Frame(inner, bg="#1a1f2e")
        ret_frame.pack(anchor="w")
        for label, val in [("1ヶ月", r1m), ("3ヶ月", r3m), ("6ヶ月", r6m)]:
            color = "#3fb950" if val >= 0 else "#f85149"
            sign  = "+" if val >= 0 else ""
            tk.Label(ret_frame,
                    text=f"📈 {label}: {sign}{val:.1f}%   ",
                    bg="#1a1f2e", fg=color,
                    font=("Yu Gothic UI", 11)).pack(side="left")
        StockSearchDialog(self.root, self.stocks, self._bt_on_selected)

    def _bt_open_search(self):
        StockSearchDialog(self.root, self.stocks, self._bt_on_selected)

    def _bt_on_selected(self, stock):
        self._bt_ticker_var.set(stock["ticker"])
        self._bt_name_var.set(stock["name"])

    def _bt_start(self):
        if self._bt_running:
            return
        ticker = self._bt_ticker_var.get().strip()
        if not ticker:
            messagebox.showwarning("エラー", "銘柄を選択してください")
            return

        self._bt_running = True
        self._bt_run_btn.config(state="disabled", bg="#21262d")
        self._bt_progress.start(10)
        self._bt_status_var.set("データ取得・計算中...")

        # 結果エリアをクリア
        for w in self._bt_result_frame.winfo_children():
            w.destroy()

        params = {
            "ticker":        ticker,
            "years":         self._bt_years_var.get(),
            "score_th":      self._bt_score_var.get(),
            "rr_th":         self._bt_rr_var.get(),
            "max_hold":      self._bt_hold_var.get(),
            "atr_mult":      self._bt_atr_var.get(),
            "tp_atr_mult":   self._bt_tp_var.get(),
            "fee_rate":      float(self.config.get("fee_rate", 0.001)),
        }
        threading.Thread(target=self._bt_run, args=(params,), daemon=True).start()

    def _bt_run(self, params):
        try:
            from backtest import run_single_backtest

            def cb(step, total, date_str):
                if total > 0:
                    pct = int(step / total * 100)
                    self.root.after(0, self._bt_status_var.set,
                                   f"計算中... {pct}%  ({date_str})")

            result = run_single_backtest(
                ticker          = params["ticker"],
                years           = params["years"],
                score_threshold = params["score_th"],
                rr_threshold    = params["rr_th"],
                max_hold_days   = params["max_hold"],
                fee_rate        = params["fee_rate"],
                atr_multiplier  = params["atr_mult"],
                tp_atr_mult     = params["tp_atr_mult"],
                progress_cb     = cb,
            )
            self._bt_result = result
            self.root.after(0, self._bt_done, result)
        except Exception as e:
            self.root.after(0, self._bt_error, str(e))

    def _bt_done(self, result):
        self._bt_running = False
        self._bt_run_btn.config(state="normal", bg="#1f6feb")
        self._bt_progress.stop()
        self._bt_status_var.set(
            f"✅ 完了  {result.total_trades}件のトレード")
        self._bt_render(result)

    def _bt_error(self, msg):
        self._bt_running = False
        self._bt_run_btn.config(state="normal", bg="#1f6feb")
        self._bt_progress.stop()
        self._bt_status_var.set(f"❌ エラー: {msg}")

    def _bt_render(self, result):
        """バックテスト結果をGUIに描画する"""
        frame = self._bt_result_frame

        # ─── サマリーカード ───
        summary_outer = tk.Frame(frame, bg="#21262d",
                                highlightbackground="#30363d", highlightthickness=1)
        summary_outer.pack(fill="x", pady=(0, 10))

        tk.Label(summary_outer,
                text=f"📊 {self._bt_ticker_var.get()}  {self._bt_name_var.get()}  "
                     f"（{self._bt_years_var.get()}年間バックテスト結果）",
                bg="#21262d", fg="#f0f6fc",
                font=("Yu Gothic UI", 12, "bold"),
                padx=10, pady=6).pack(anchor="w")
        tk.Frame(summary_outer, bg="#30363d", height=1).pack(fill="x")

        metrics_frame = tk.Frame(summary_outer, bg="#21262d")
        metrics_frame.pack(fill="x", padx=10, pady=8)

        win_color = "#3fb950" if result.win_rate >= 50 else "#f85149"
        pnl_color = "#3fb950" if result.total_pnl >= 0 else "#f85149"
        pf_color  = "#3fb950" if result.profit_factor >= 1.0 else "#f85149"

        metrics = [
            ("総トレード数", f"{result.total_trades} 件",    "#e6edf3"),
            ("勝率",         f"{result.win_rate:.1f} %",      win_color),
            ("勝/負/タイムアウト",
             f"{result.win_count} / {result.loss_count} / {result.timeout_count}", "#8b949e"),
            ("平均利益",     f"{result.avg_gain:.2f} %",      "#3fb950"),
            ("平均損失",     f"{result.avg_loss:.2f} %",      "#f85149"),
            ("プロフィットファクター", f"{result.profit_factor:.2f}", pf_color),
            ("累積損益",     f"{result.total_pnl:+.2f} %",   pnl_color),
            ("最大ドローダウン", f"{result.max_drawdown*100:.2f} %", "#e3b341"),
        ]

        col_count = 4
        for idx, (label, value, color) in enumerate(metrics):
            c = idx % col_count
            r = idx // col_count
            cell = tk.Frame(metrics_frame, bg="#0d1117", padx=10, pady=8)
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="ew")
            metrics_frame.columnconfigure(c, weight=1)
            tk.Label(cell, text=label, bg="#0d1117", fg="#8b949e",
                    font=("Yu Gothic UI", 10)).pack(anchor="w")
            tk.Label(cell, text=value, bg="#0d1117", fg=color,
                    font=("Yu Gothic UI", 14, "bold")).pack(anchor="w")

        # ─── エクイティカーブ（matplotlib埋め込み）───
        if result.equity_curve and len(result.equity_curve) > 1:
            try:
                import matplotlib
                matplotlib.use("TkAgg")
                import matplotlib.pyplot as plt
                import matplotlib.dates as mdates
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
                import datetime

                fig, ax = plt.subplots(figsize=(8.5, 2.8))
                fig.patch.set_facecolor("#0d1117")
                ax.set_facecolor("#161b22")

                dates = []
                for d in result.equity_dates:
                    try:
                        dates.append(datetime.date.fromisoformat(d[:10]))
                    except Exception:
                        dates.append(datetime.date.today())

                pnl_pct = [v * 100 for v in result.equity_curve]

                # 0ライン
                ax.axhline(0, color="#30363d", linewidth=1, linestyle="--")

                # 塗りつぶし
                ax.fill_between(dates, pnl_pct, 0,
                               where=[p >= 0 for p in pnl_pct],
                               alpha=0.25, color="#3fb950")
                ax.fill_between(dates, pnl_pct, 0,
                               where=[p < 0 for p in pnl_pct],
                               alpha=0.25, color="#f85149")

                # ライン
                ax.plot(dates, pnl_pct, color="#58a6ff", linewidth=1.5)

                ax.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
                ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
                ax.tick_params(colors="#8b949e", labelsize=8)
                for spine in ax.spines.values():
                    spine.set_edgecolor("#30363d")
                ax.set_ylabel("累積損益 (%)", color="#8b949e", fontsize=9)
                ax.set_title("エクイティカーブ", color="#c9d1d9", fontsize=10, pad=6)
                plt.tight_layout(pad=1.0)

                chart_frame = tk.Frame(frame, bg="#161b22")
                chart_frame.pack(fill="x", pady=(0, 8))

                canvas_widget = FigureCanvasTkAgg(fig, master=chart_frame)
                canvas_widget.draw()
                canvas_widget.get_tk_widget().pack(fill="x")
                plt.close(fig)

            except ImportError:
                tk.Label(frame,
                        text="💡 matplotlib をインストールするとグラフが表示されます\n"
                             "   pip install matplotlib",
                        bg="#161b22", fg="#e3b341",
                        font=("Yu Gothic UI", 11)).pack(anchor="w", pady=4)

        # ─── トレード一覧 ───
        if result.trades:
            list_outer = tk.Frame(frame, bg="#161b22",
                                 highlightbackground="#30363d", highlightthickness=1)
            list_outer.pack(fill="x", pady=(0, 8))

            tk.Label(list_outer, text="📋 トレード一覧（最新50件）",
                    bg="#161b22", fg="#f0f6fc",
                    font=("Yu Gothic UI", 12, "bold"),
                    padx=10, pady=6).pack(anchor="w")
            tk.Frame(list_outer, bg="#30363d", height=1).pack(fill="x")

            list_inner = tk.Frame(list_outer, bg="#161b22", padx=10, pady=8)
            list_inner.pack(fill="x")

            # ヘッダー
            headers = ["エントリー日", "決済日", "保有", "スコア", "RR",
                       "エントリー", "決済", "損益%", "パターン", "結果"]
            widths   = [11, 11, 5, 6, 5, 10, 10, 8, 12, 8]
            hdr_row  = tk.Frame(list_inner, bg="#21262d")
            hdr_row.pack(fill="x", pady=(0, 2))
            for h, w in zip(headers, widths):
                tk.Label(hdr_row, text=h, bg="#21262d", fg="#8b949e",
                        font=("Yu Gothic UI", 10), width=w, anchor="w").pack(side="left")

            # 行（最新50件・新しい順）
            recent = sorted(result.trades, key=lambda t: t.exit_date, reverse=True)[:50]
            pat_icon = {
                "押し目+BO":      "🔥",
                "押し目":         "📉",
                "ブレイクアウト": "🚀",
                "ベース":         "📊",
            }
            pat_color_map = {
                "押し目+BO":      "#e3b341",
                "押し目":         "#58a6ff",
                "ブレイクアウト": "#3fb950",
                "ベース":         "#8b949e",
            }
            for trade in recent:
                pnl_pct  = trade.pnl * 100
                res_map  = {"win": ("✅ 利確", "#3fb950"),
                            "loss": ("❌ 損切", "#f85149"),
                            "timeout": ("⏱ TL", "#e3b341")}
                res_text, res_color = res_map.get(trade.result, ("?", "#8b949e"))
                pnl_color = "#3fb950" if pnl_pct > 0 else "#f85149"
                icon  = pat_icon.get(trade.pattern, "📊")
                pcol  = pat_color_map.get(trade.pattern, "#8b949e")
                pat_text = f"{icon} {trade.pattern}"

                row = tk.Frame(list_inner,
                               bg="#0d1117" if recent.index(trade) % 2 == 0 else "#161b22")
                row.pack(fill="x")

                data_cells = [
                    (trade.entry_date,             "#c9d1d9"),
                    (trade.exit_date,               "#c9d1d9"),
                    (f"{trade.hold_days}日",        "#8b949e"),
                    (str(trade.score),              "#58a6ff"),
                    (f"{trade.rr:.1f}",             "#8b949e"),
                    (f"¥{trade.entry_price:,.0f}",  "#c9d1d9"),
                    (f"¥{trade.exit_price:,.0f}",   "#c9d1d9"),
                    (f"{pnl_pct:+.2f}%",            pnl_color),
                    (pat_text,                      pcol),
                    (res_text,                      res_color),
                ]
                for (text, color), w in zip(data_cells, widths):
                    tk.Label(row, text=text, bg=row.cget("bg"), fg=color,
                            font=("Yu Gothic UI", 10), width=w, anchor="w").pack(side="left")


if __name__ == "__main__":
    # Windows高DPI対応（文字・UIを鮮明に）
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor DPI
    except Exception:
        try:
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()

    # tkinter自体のDPIスケーリング
    try:
        root.tk.call("tk", "scaling", 1.5)
    except Exception:
        pass

    app = NyalperApp(root)
    root.mainloop()
