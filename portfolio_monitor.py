from typing import Dict, List

from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator
from ta.volatility import BollingerBands

from market_data import download_ohlcv


def run_portfolio_monitor(config: Dict) -> List[Dict]:
    asof_date = config.get("analysis_asof_date")
    output = []
    for item in config.get("portfolio", []):
        t = item.get("ticker")
        if t:
            diag = _diagnose_ticker_with_asof(t, asof_date)
            diag["name"] = item.get("name", "")
            diag["buy_price"] = float(item.get("buy_price", 0))
            output.append(diag)

    for t in config.get("watchlist", []):
        output.append(_diagnose_ticker_with_asof(t, asof_date))
    return output


def _diagnose_ticker_with_asof(ticker: str, asof_date: str | None) -> Dict:
    df = download_ohlcv(ticker=ticker, period="6mo", interval="1d", asof_date=asof_date)
    close = df["Close"]
    current = float(close.iloc[-1])
    macd = MACD(close)
    rsi = RSIIndicator(close, 14).rsi()
    bb = BollingerBands(close)
    sma75 = SMAIndicator(close, 75).sma_indicator()
    high20 = float(df["High"].tail(20).max())
    low20 = float(df["Low"].tail(20).min())
    atr14 = float((df["High"] - df["Low"]).tail(14).mean())
    is_jp = ticker.endswith(".T")
    atr_mult = 2.0 if is_jp else 2.5
    take_profit = min(high20, float(bb.bollinger_hband().iloc[-1]))
    stop_loss = max(current - atr14 * atr_mult, low20)
    tp_dist_pct = ((take_profit - current) / current * 100) if current > 0 else 0.0
    sl_dist_pct = ((current - stop_loss) / current * 100) if current > 0 else 0.0

    alerts = []
    if macd.macd().iloc[-1] < macd.macd_signal().iloc[-1] or rsi.iloc[-1] > 70:
        alerts.append("🟡 注意")
    if current > bb.bollinger_hband().iloc[-1] or current < sma75.iloc[-1]:
        alerts.append("🔴 危険")

    return {
        "ticker": ticker,
        "current_price": current,
        "rsi14": float(rsi.iloc[-1]),
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "tp_dist_pct": tp_dist_pct,
        "sl_dist_pct": sl_dist_pct,
        "alerts": alerts or ["-"],
    }
