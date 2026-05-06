import datetime as dt
import json
import re
import time
import logging
from typing import Dict, List, Tuple

import pandas as pd
import requests
from ta.momentum import RSIIndicator
from ta.trend import IchimokuIndicator, MACD, SMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange

from market_data import download_ohlcv

logger = logging.getLogger(__name__)

# ===== NYSE休場日カレンダー =====
_NYSE_FIXED = [(1, 1), (7, 4), (12, 25)]

def _nth_weekday(year, month, n, weekday):
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + (n - 1) * 7)

def _last_weekday(year, month, weekday):
    last = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)

def _calc_easter(year):
    a = year % 19; b = year // 100; c = year % 100
    d = b // 4; e = b % 4; f = (b + 8) // 25
    g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)

def _get_nyse_holidays(year):
    holidays = []
    for month, day in _NYSE_FIXED:
        d = dt.date(year, month, day)
        if d.weekday() == 5:   holidays.append(d - dt.timedelta(days=1))
        elif d.weekday() == 6: holidays.append(d + dt.timedelta(days=1))
        else:                  holidays.append(d)
    holidays.append(_nth_weekday(year, 1, 3, 0))
    holidays.append(_nth_weekday(year, 2, 3, 0))
    easter = _calc_easter(year)
    holidays.append(easter - dt.timedelta(days=2))
    holidays.append(_last_weekday(year, 5, 0))
    d = dt.date(year, 6, 19)
    if d.weekday() == 5:   holidays.append(d - dt.timedelta(days=1))
    elif d.weekday() == 6: holidays.append(d + dt.timedelta(days=1))
    else:                  holidays.append(d)
    holidays.append(_nth_weekday(year, 9, 1, 0))
    holidays.append(_nth_weekday(year, 11, 4, 3))
    return sorted(set(holidays))

def is_nyse_holiday(d: dt.date) -> bool:
    return d.weekday() >= 5 or d in _get_nyse_holidays(d.year)


# ===== テクニカルスナップショット =====
def _trend_snapshot(df: pd.DataFrame) -> Dict:
    close = df["Close"]
    macd  = MACD(close)
    rsi   = RSIIndicator(close, window=14)
    bb    = BollingerBands(close)
    ichi  = IchimokuIndicator(df["High"], df["Low"])
    sma75 = SMAIndicator(close, window=75).sma_indicator()
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else float(close.iloc[-1])
    last_close = float(close.iloc[-1])
    change     = last_close - prev_close
    change_pct = (change / prev_close * 100) if prev_close != 0 else 0.0
    return {
        "last_close":  last_close,
        "prev_close":  prev_close,
        "change":      round(change, 2),
        "change_pct":  round(change_pct, 2),
        "macd":        float(macd.macd().iloc[-1]),
        "macd_signal": float(macd.macd_signal().iloc[-1]),
        "rsi14":       float(rsi.rsi().iloc[-1]),
        "bb_high":     float(bb.bollinger_hband().iloc[-1]),
        "bb_low":      float(bb.bollinger_lband().iloc[-1]),
        "tenkan":      float(ichi.ichimoku_conversion_line().iloc[-1]),
        "kijun":       float(ichi.ichimoku_base_line().iloc[-1]),
        "senkou_a":    float(ichi.ichimoku_a().iloc[-1]),
        "senkou_b":    float(ichi.ichimoku_b().iloc[-1]),
        "sma75":       float(sma75.iloc[-1]),
    }


# ===== 日経平均 予想レンジ計算 =====
def _calc_nikkei_range(df: pd.DataFrame) -> Dict:
    """
    ATRベースで本日の日経平均予想レンジを計算する
    予想レンジ = 前日終値 ± ATR(14) × 0.7
    """
    if len(df) < 20:
        return {}
    close     = df["Close"]
    high      = df["High"]
    low       = df["Low"]
    prev_close = float(close.iloc[-1])

    # ATR計算
    atr = AverageTrueRange(high, low, close, window=14).average_true_range()
    atr_val = float(atr.iloc[-1])

    # 予想レンジ（ATR × 0.7）
    upper = prev_close + atr_val * 0.7
    lower = prev_close - atr_val * 0.7

    # 直近5日の高値・安値もサポート・レジスタンスとして追加
    recent_high = float(high.tail(5).max())
    recent_low  = float(low.tail(5).min())

    return {
        "prev_close":   round(prev_close, 0),
        "atr":          round(atr_val, 0),
        "range_upper":  round(upper, 0),
        "range_lower":  round(lower, 0),
        "resistance":   round(recent_high, 0),
        "support":      round(recent_low, 0),
    }


# ===== Gemini API呼び出し =====
def _call_gemini(config: Dict, prompt: str) -> Dict:
    api_key   = str(config.get("gemini_api_key", "") or "")
    model     = config.get("gemini_model", "gemini-2.5-flash")
    wait_sec  = float(config.get("gemini_wait_sec", 4.0))
    max_retries = 3

    if not api_key or api_key.startswith("YOUR_"):
        return {}

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=float(config.get("http_timeout_sec", 10.0)),
            )

            if resp.status_code == 429:
                retry_wait = wait_sec * (2 ** attempt)
                logger.warning("Gemini 429 (attempt %d/%d). Waiting %.0f sec...", attempt + 1, max_retries, retry_wait)
                time.sleep(retry_wait)
                continue

            resp.raise_for_status()
            body = resp.json()
            text = (
                body.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            cleaned = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()
            return json.loads(cleaned)

        except requests.exceptions.HTTPError as e:
            if attempt < max_retries - 1:
                retry_wait = wait_sec * (2 ** attempt)
                logger.warning("Gemini HTTP error: %s. Retry in %.0f sec...", e, retry_wait)
                time.sleep(retry_wait)
            else:
                logger.warning("Gemini API call failed: %s", e)
        except Exception as e:
            logger.warning("Gemini API call failed: %s", e)
            break

    return {}


# ===== セクターAI解説 =====
def _get_sector_analysis_ai(config: Dict, macro_data: Dict) -> Dict:
    top_etf      = macro_data.get("top_sector_etf", "")
    sector_focus = macro_data.get("sector_focus", [])
    etf_perf     = macro_data.get("etf_performances", {})
    vix          = macro_data.get("vix", 0)
    sp500        = macro_data.get("sp500", {})
    perf_text    = "\n".join([f"  {etf}: {perf:+.2f}%" for etf, perf in etf_perf.items()])

    prompt = f"""あなたはプロの株式アナリストです。以下の昨晩の米国市場データをもとに、なぜ「{', '.join(sector_focus)}」セクターが本日注目されるのかを解説してください。

【市場データ】
VIX: {vix} / S&P500 RSI: {sp500.get('rsi14', 0):.1f}
MACD: {sp500.get('macd', 0):.3f} / シグナル: {sp500.get('macd_signal', 0):.3f}

【セクターETF騰落率】
{perf_text}

【最も強かったETF】{top_etf}

以下のJSON形式のみで回答（前置き不要）：
{{
  "summary": "なぜそのセクターが注目されるか2〜3文で",
  "key_reason": "最も重要な理由を1文で",
  "caution": "注意点を1文で（なければ空文字）"
}}"""
    return _call_gemini(config, prompt)


# ===== 資金流入・流出セクターAI解説 =====
def _get_sector_flow_ai(config: Dict, inflow_sectors: List[Dict], outflow_sectors: List[Dict], etf_perf: Dict, vix: float) -> Dict:
    """
    資金流入TOP3・流出TOP3のAI解説を生成する
    """
    inflow_text  = "\n".join([f"  {s['etf']}（{', '.join(s['sectors'])}）: {s['perf']:+.2f}%" for s in inflow_sectors])
    outflow_text = "\n".join([f"  {s['etf']}（{', '.join(s['sectors'])}）: {s['perf']:+.2f}%" for s in outflow_sectors])

    prompt = f"""あなたはプロの株式アナリストです。昨晩の米国市場のセクターETF動向から、資金の流れを分析してください。

【資金流入上位セクター（買われたセクター）】
{inflow_text}

【資金流出上位セクター（売られたセクター）】
{outflow_text}

【VIX】{vix}

以下のJSON形式のみで回答（前置き不要）：
{{
  "inflow_reason": "資金流入セクターが買われた理由を2文で",
  "outflow_reason": "資金流出セクターが売られた理由を2文で",
  "overall": "今日の日本株市場への影響を1文で"
}}"""
    return _call_gemini(config, prompt)


# ===== メイン =====
def run_macro_analysis(config: Dict) -> Dict:
    asof_date = config.get("analysis_asof_date")

    # 主要11セクター + SOXX
    sector_etfs = {
        "SOXX": ["半導体", "テクノロジー"],
        "XLK":  ["テクノロジー"],
        "XLF":  ["金融"],
        "XLE":  ["エネルギー"],
        "XLV":  ["ヘルスケア"],
        "XLI":  ["機械", "運輸"],
        "XLY":  ["小売", "サービス"],
        "XLB":  ["素材"],
        "XLRE": ["不動産"],
        "XLP":  ["生活必需品"],
        "XLU":  ["公益事業"],
        "XLC":  ["通信サービス"],
    }

    # コモディティ・債券・為替データ取得
    commodity_symbols = {
        "crude_oil": "CL=F",    # WTI原油
        "gold":      "GC=F",    # 金
        "bond_10y":  "^TNX",    # 米10年債利回り
        "usdjpy":    "JPY=X",   # USD/JPY
    }
    commodity_data = {}
    for name, ticker in commodity_symbols.items():
        df = download_ohlcv(ticker, period="1mo", interval="1d", asof_date=None)
        if len(df) >= 2:
            last  = float(df["Close"].iloc[-1])
            prev  = float(df["Close"].iloc[-2])
            chg_p = round((last - prev) / prev * 100, 2)
            commodity_data[name] = {"last": round(last, 2), "change_pct": chg_p}

    # NYSE休場判定（常に今日）
    today = dt.date.today()
    us_market_closed = is_nyse_holiday(today)

    # ===== 主要指数データ取得 =====
    indices = {
        "nikkei": "^N225",
        "dow":    "^DJI",
        "nasdaq": "^IXIC",
        "sp500":  "^GSPC",
    }
    index_stats = {}
    for name, ticker in indices.items():
        df = download_ohlcv(ticker, period="6mo", interval="1d", asof_date=asof_date)
        if not df.empty:
            index_stats[name] = _trend_snapshot(df)

    vix_df   = download_ohlcv("^VIX", period="6mo", interval="1d", asof_date=asof_date)
    vix_last = float(vix_df["Close"].iloc[-1]) if not vix_df.empty else 0.0

    # ===== 日経平均 予想レンジ =====
    nikkei_range = {}
    nikkei_df = download_ohlcv("^N225", period="3mo", interval="1d", asof_date=asof_date)
    if not nikkei_df.empty:
        nikkei_range = _calc_nikkei_range(nikkei_df)

    # ===== マクロ警告判定 =====
    nikkei_stats = index_stats.get("nikkei", {})
    sp500_stats  = index_stats.get("sp500", {})
    macro_warning = (
        vix_last >= float(config.get("macro_vix_warning", 25))
        and (
            nikkei_stats.get("last_close", 0) < nikkei_stats.get("sma75", 0)
            or sp500_stats.get("last_close", 0) < sp500_stats.get("sma75", 0)
        )
    )

    # ===== セクターETF騰落率 =====
    etf_performances = {}
    if not us_market_closed:
        for ticker in sector_etfs:
            df = download_ohlcv(ticker, period="1mo", interval="1d", asof_date=None)
            if len(df) < 2:
                continue
            ret = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-2]) - 1.0) * 100
            etf_performances[ticker] = round(ret, 2)

    # ===== 資金流入TOP3・流出TOP3 =====
    sorted_etfs = sorted(etf_performances.items(), key=lambda x: x[1], reverse=True)
    inflow_sectors  = [{"etf": etf, "sectors": sector_etfs[etf], "perf": perf} for etf, perf in sorted_etfs[:3]]
    outflow_sectors = [{"etf": etf, "sectors": sector_etfs[etf], "perf": perf} for etf, perf in sorted_etfs[-3:] if perf < 0]

    # 注目セクター（最も騰落率が高いETF）
    top_sector_etf = sorted_etfs[0][0] if sorted_etfs else None
    top_perf       = sorted_etfs[0][1] if sorted_etfs else 0.0
    sector_focus   = sector_etfs.get(top_sector_etf or "", [])

    macro_data = {
        "macro_warning":    macro_warning,
        "vix":              round(vix_last, 2),
        "indices":          index_stats,
        "nikkei_range":     nikkei_range,
        "commodities":      commodity_data,    # 原油・金・米10年債・為替
        "top_sector_etf":   top_sector_etf,
        "top_sector_perf":  round(top_perf, 2) if top_sector_etf else 0.0,
        "sector_focus":     sector_focus,
        "etf_performances": etf_performances,
        "inflow_sectors":   inflow_sectors,
        "outflow_sectors":  outflow_sectors,
        "us_market_closed": us_market_closed,
        "nikkei":           nikkei_stats,
        "sp500":            sp500_stats,
    }

    # ===== AIセクター解説 =====
    if sector_focus and not us_market_closed:
        logger.info("AIセクター解説を生成中: %s", sector_focus)
        macro_data["sector_ai"] = _get_sector_analysis_ai(config, macro_data)
    else:
        macro_data["sector_ai"] = {}

    # ===== 資金流入・流出AI解説 =====
    if (inflow_sectors or outflow_sectors) and not us_market_closed:
        logger.info("資金流入・流出AI解説を生成中")
        macro_data["sector_flow_ai"] = _get_sector_flow_ai(
            config, inflow_sectors, outflow_sectors, etf_performances, vix_last
        )
    else:
        macro_data["sector_flow_ai"] = {}

    return macro_data
