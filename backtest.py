"""
backtest.py  ―  にゃるぱー秘書 バックテストエンジン v2.0
・複数ポジション可（シグナルが出るたびエントリー）
・期間・スコア閾値・RR閾値をパラメータで指定
・トレード詳細ログ（日付・エントリー価格・決済価格・PnL）を返す
・進捗コールバック対応（GUIプログレスバー用）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from ta.volatility import BollingerBands

from market_data import download_ohlcv
from screener import _calc_technical_score


# ──────────────────────────────────────────────
#  データクラス
# ──────────────────────────────────────────────

@dataclass
class Trade:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    upper_target: float
    lower_target: float
    score: int
    rr: float
    result: str          # "win" / "loss" / "timeout"
    pnl: float           # 損益率（小数）
    hold_days: int
    pattern: str = "ベース"   # "押し目" / "ブレイクアウト" / "押し目+BO" / "ベース"
    drop_pct: float = 0.0    # 押し目の深さ（%）

@dataclass
class BacktestResult:
    ticker: str
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)  # 累積損益率（0始まり）
    equity_dates: List[str]   = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.trades if t.result == "win")

    @property
    def loss_count(self) -> int:
        return sum(1 for t in self.trades if t.result == "loss")

    @property
    def timeout_count(self) -> int:
        return sum(1 for t in self.trades if t.result == "timeout")

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.win_count / self.total_trades * 100

    @property
    def avg_gain(self) -> float:
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        return sum(wins) / len(wins) * 100 if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        return sum(losses) / len(losses) * 100 if losses else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss   = abs(sum(t.pnl for t in self.trades if t.pnl <= 0))
        return gross_profit / gross_loss if gross_loss > 0 else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades) * 100

    @property
    def max_drawdown(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for v in self.equity_curve:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def to_summary_dict(self) -> Dict:
        return {
            "ticker":        self.ticker,
            "total_trades":  self.total_trades,
            "win_count":     self.win_count,
            "loss_count":    self.loss_count,
            "timeout_count": self.timeout_count,
            "win_rate":      round(self.win_rate, 1),
            "avg_gain_pct":  round(self.avg_gain, 2),
            "avg_loss_pct":  round(self.avg_loss, 2),
            "profit_factor": round(self.profit_factor, 3),
            "total_pnl_pct": round(self.total_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
        }


# ──────────────────────────────────────────────
#  RR計算（backtest専用・screenerと同ロジック）
# ──────────────────────────────────────────────

def _calc_rr(
    window: pd.DataFrame,
    atr_mult: float,
    tp_atr_mult: float = 0.0,   # 利確倍率（0=従来のBB/20日高値方式）
) -> Tuple[float, float, float]:
    """
    RR計算。
    tp_atr_mult > 0 の場合: min(ATR×tp_atr_mult, 20日高値) を利確目標に使用
    tp_atr_mult = 0 の場合: 従来通り min(20日高値, BB上限2σ)
    """
    close   = window["Close"]
    high    = window["High"]
    low     = window["Low"]
    current = float(close.iloc[-1])
    atr14   = float((high - low).tail(14).mean())
    high20  = float(high.tail(20).max())
    low20   = float(low.tail(20).min())

    # 利確ライン
    if tp_atr_mult > 0:
        upper = min(current + atr14 * tp_atr_mult, high20)
    else:
        bb    = BollingerBands(close)
        upper = min(high20, float(bb.bollinger_hband().iloc[-1]))

    # 損切りライン
    lower = max(current - atr14 * atr_mult, low20)

    risk   = current - lower
    reward = upper - current
    rr     = reward / risk if risk > 0 else 0.0
    return rr, upper, lower


# ──────────────────────────────────────────────
#  1銘柄バックテスト本体
# ──────────────────────────────────────────────

def run_single_backtest(
    ticker: str,
    years: int                    = 2,
    score_threshold: int          = 55,
    rr_threshold: float           = 1.2,
    max_hold_days: int            = 10,
    fee_rate: float               = 0.001,
    atr_multiplier: float         = 2.0,
    tp_atr_mult: float            = 0.0,   # 利確倍率（0=従来方式）
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> BacktestResult:
    """
    1銘柄のバックテストを実行して BacktestResult を返す。

    progress_cb(current_step, total_steps, message) が渡された場合、
    各日付処理時に呼び出す（GUIプログレスバー用）。
    """
    market = "jp" if ticker.endswith(".T") else "us"
    result = BacktestResult(ticker=ticker)

    # ─── 日足データ取得 ───
    df = download_ohlcv(ticker=ticker, period=f"{years}y", interval="1d")
    if len(df) < 120:
        return result   # データ不足

    # ─── 週足スコアをループ外で1回だけ取得 ───
    # バックテストでは週足を毎日取得すると非常に遅くなるため
    # 現在時点の週足スコアを固定値としてスコアに加算する
    weekly_bonus   = 0
    weekly_warning = False
    try:
        from screener import _calc_weekly_trend
        w_score, w_diag = _calc_weekly_trend(ticker, market)
        weekly_bonus   = w_score
        weekly_warning = w_diag.get("weekly_warning", False)
    except Exception:
        pass

    total_steps = len(df) - 82

    # ─── 進行中のポジション管理 ───
    # positions: List[Dict] = [{"entry_i", "entry_price", "upper", "lower", "score", "rr", "entry_date"}]
    positions: List[Dict] = []

    # 損益推移（エクイティカーブ）
    cumulative_pnl = 0.0
    result.equity_curve.append(0.0)
    result.equity_dates.append(str(df.index[80]))

    for i in range(80, len(df) - 1):
        today     = df.iloc[i]
        today_str = str(df.index[i])[:10]

        # ─── 既存ポジションの決済チェック ───
        still_open = []
        for pos in positions:
            upper  = pos["upper"]
            lower  = pos["lower"]
            ep     = pos["entry_price"]
            hold   = i - pos["entry_i"]

            day_high = float(today["High"])
            day_low  = float(today["Low"])

            closed     = False
            exit_price = 0.0
            exit_res   = "timeout"

            if day_high >= upper and day_low <= lower:
                # 同日両方タッチ → 損切り優先（保守的）
                exit_price = lower * (1 - fee_rate)
                exit_res   = "loss"
                closed     = True
            elif day_low <= lower:
                exit_price = lower * (1 - fee_rate)
                exit_res   = "loss"
                closed     = True
            elif day_high >= upper:
                exit_price = upper * (1 - fee_rate)
                exit_res   = "win"
                closed     = True
            elif hold >= max_hold_days:
                exit_price = float(today["Close"]) * (1 - fee_rate)
                exit_res   = "timeout"
                closed     = True

            if closed:
                pnl = (exit_price - ep) / ep
                cumulative_pnl += pnl
                result.trades.append(Trade(
                    ticker        = ticker,
                    entry_date    = pos["entry_date"],
                    exit_date     = today_str,
                    entry_price   = round(ep, 2),
                    exit_price    = round(exit_price, 2),
                    upper_target  = round(upper, 2),
                    lower_target  = round(lower, 2),
                    score         = pos["score"],
                    rr            = round(pos["rr"], 2),
                    result        = exit_res,
                    pnl           = round(pnl, 6),
                    hold_days     = hold,
                    pattern       = pos.get("pattern", "ベース"),
                    drop_pct      = pos.get("drop_pct", 0.0),
                ))
                result.equity_curve.append(round(cumulative_pnl, 6))
                result.equity_dates.append(today_str)
            else:
                still_open.append(pos)

        positions = still_open

        # ─── 新規エントリー判定 ───
        hist = df.iloc[: i + 1]
        try:
            score, diag, _ = _calc_technical_score(hist, market)
            score += weekly_bonus   # 週足ボーナスを固定加算
        except Exception:
            if progress_cb:
                progress_cb(i - 80, total_steps, today_str)
            continue

        rr, upper, lower = _calc_rr(hist, atr_multiplier, tp_atr_mult)

        if score >= score_threshold and rr >= rr_threshold:
            # 翌日始値でエントリー
            next_open   = float(df["Open"].iloc[i + 1])
            entry_price = next_open * (1 + fee_rate)
            positions.append({
                "entry_i":     i + 1,
                "entry_date":  str(df.index[i + 1])[:10],
                "entry_price": entry_price,
                "upper":       upper,
                "lower":       lower,
                "score":       score,
                "rr":          rr,
                "pattern":     diag.get("pattern", "ベース"),
                "drop_pct":    diag.get("drop_pct", 0.0),
            })

        if progress_cb:
            progress_cb(i - 80, total_steps, today_str)

    # ─── 期末にまだ保有中のポジションを時価で決済 ───
    if len(df) > 0:
        last_close = float(df["Close"].iloc[-1])
        last_str   = str(df.index[-1])[:10]
        for pos in positions:
            ep    = pos["entry_price"]
            ep_f  = last_close * (1 - fee_rate)
            pnl   = (ep_f - ep) / ep
            cumulative_pnl += pnl
            result.trades.append(Trade(
                ticker        = ticker,
                entry_date    = pos["entry_date"],
                exit_date     = last_str,
                entry_price   = round(ep, 2),
                exit_price    = round(ep_f, 2),
                upper_target  = round(pos["upper"], 2),
                lower_target  = round(pos["lower"], 2),
                score         = pos["score"],
                rr            = round(pos["rr"], 2),
                result        = "timeout",
                pnl           = round(pnl, 6),
                hold_days     = len(df) - 1 - pos["entry_i"],
                pattern       = pos.get("pattern", "ベース"),
                drop_pct      = pos.get("drop_pct", 0.0),
            ))
            result.equity_curve.append(round(cumulative_pnl, 6))
            result.equity_dates.append(last_str)

    return result


# ──────────────────────────────────────────────
#  全銘柄バックテスト（将来拡張用）
# ──────────────────────────────────────────────

def run_backtest(config: Dict, universe_path: Path) -> Dict:
    """
    既存インターフェース互換のラッパー。
    config["backtest_single_ticker"] が指定されていれば1銘柄、
    なければ universe_path の先頭20銘柄を処理する。
    """
    years       = int(config.get("backtest_years", 2))
    fee         = float(config.get("fee_rate", 0.001))
    max_hold    = int(config.get("backtest_max_hold_days", 10))
    score_th    = int(config.get("score_threshold", 55))
    rr_th       = float(config.get("rr_threshold", 1.2))
    atr_jp      = float(config.get("atr_multiplier_jp", 2.0))
    atr_us      = float(config.get("atr_multiplier_us", 2.5))

    single = config.get("backtest_single_ticker")
    if single:
        tickers = [single]
    else:
        universe = pd.read_csv(universe_path).head(20)
        tickers  = list(universe["ticker"].astype(str))

    all_trades = []
    for ticker in tickers:
        atr_mult = atr_jp if ticker.endswith(".T") else atr_us
        res = run_single_backtest(
            ticker          = ticker,
            years           = years,
            score_threshold = score_th,
            rr_threshold    = rr_th,
            max_hold_days   = max_hold,
            fee_rate        = fee,
            atr_multiplier  = atr_mult,
        )
        all_trades.extend(res.trades)

    if not all_trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "final_performance": 0.0}

    wins   = [t.pnl for t in all_trades if t.pnl > 0]
    losses = [t.pnl for t in all_trades if t.pnl <= 0]
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else 0.0

    return {
        "trades":            len(all_trades),
        "win_rate":          round(100 * len(wins) / len(all_trades), 2),
        "avg_gain":          round(sum(wins) / len(wins), 4)   if wins   else 0.0,
        "avg_loss":          round(sum(losses) / len(losses), 4) if losses else 0.0,
        "profit_factor":     round(pf, 3),
        "final_performance": round(sum(t.pnl for t in all_trades), 4),
    }
