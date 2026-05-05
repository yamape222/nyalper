from pathlib import Path
from typing import Dict

import pandas as pd

from market_data import download_ohlcv
from screener import _calc_technical_score  # 本番スクリーニングと共通化


def _calc_rr(window: pd.DataFrame, atr_mult: float):
    from ta.volatility import BollingerBands
    close = window["Close"]
    high = window["High"]
    low = window["Low"]
    bb = BollingerBands(close)
    current = float(close.iloc[-1])
    upper = min(float(high.tail(20).max()), float(bb.bollinger_hband().iloc[-1]))
    atr14 = float((high - low).tail(14).mean())
    lower = max(current - atr14 * atr_mult, float(low.tail(20).min()))
    risk = current - lower
    reward = upper - current
    rr = reward / risk if risk > 0 else 0.0
    return rr, upper, lower


def run_backtest(config: Dict, universe_path: Path) -> Dict:
    asof_date = config.get("analysis_asof_date")
    years = int(config.get("backtest_years", 2))
    fee = float(config.get("fee_rate", 0.001))
    max_hold = int(config.get("backtest_max_hold_days", 10))
    score_th = int(config.get("score_threshold", 65))
    rr_th = float(config.get("rr_threshold", 1.5))
    atr_jp = float(config.get("atr_multiplier_jp", 2.0))
    atr_us = float(config.get("atr_multiplier_us", 2.5))

    universe = pd.read_csv(universe_path).head(20)
    trades = []

    for _, row in universe.iterrows():
        ticker = str(row["ticker"])
        market = "jp" if ticker.endswith(".T") else "us"
        atr_mult = atr_jp if market == "jp" else atr_us

        df = download_ohlcv(ticker=ticker, period=f"{years}y", interval="1d", asof_date=asof_date)
        if len(df) < 120:
            continue

        for i in range(80, len(df) - max_hold - 2):
            hist = df.iloc[: i + 1]

            # 本番スクリーニングと同じ_calc_technical_scoreを使用（共通化）
            try:
                score, _, _ = _calc_technical_score(hist, market)
            except Exception:
                continue

            rr, upper, lower = _calc_rr(hist, atr_mult=atr_mult)
            if score < score_th or rr < rr_th:
                continue

            entry = float(df["Open"].iloc[i + 1]) * (1 + fee)
            future = df.iloc[i + 1: i + 1 + max_hold]
            exit_price = float(future["Close"].iloc[-1]) * (1 - fee)
            decided = False

            for _, day in future.iterrows():
                hit_upper = float(day["High"]) >= upper
                hit_lower = float(day["Low"]) <= lower
                if hit_upper and hit_lower:
                    # 同日に両方到達した場合は損切り優先（保守的）
                    exit_price = lower * (1 - fee)
                    decided = True
                    break
                if hit_lower:
                    exit_price = lower * (1 - fee)
                    decided = True
                    break
                if hit_upper:
                    exit_price = upper * (1 - fee)
                    decided = True
                    break

            if not decided:
                exit_price = float(future["Close"].iloc[-1]) * (1 - fee)

            pnl = (exit_price - entry) / entry
            trades.append({"ticker": ticker, "pnl": pnl, "win": pnl > 0})

    if not trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "final_performance": 0.0}

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    final_perf = sum(t["pnl"] for t in trades)

    return {
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(trades), 2),
        "avg_gain": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "profit_factor": round(pf, 3),
        "final_performance": round(final_perf, 4),
    }
