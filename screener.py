import logging
import time
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import jpholiday
import pandas as pd
import requests
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import IchimokuIndicator, MACD, SMAIndicator
from ta.volatility import BollingerBands

from market_data import download_ohlcv
from news_analyzer import analyze_sentiment

logger = logging.getLogger(__name__)


# ===== ユニバース読み込み =====
def _load_universe(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


# ===== 出来高フィルター =====
def _market_volume_ok(df: pd.DataFrame, market: str) -> bool:
    if len(df) < 20:
        return False
    avg_vol   = float(df["Volume"].tail(20).mean())
    avg_close = float(df["Close"].tail(20).mean())
    if market == "jp":
        return avg_vol * avg_close >= 100_000_000
    return avg_vol >= 1_000_000


# ===== テクニカルスコアリング =====
def _calc_technical_score(
    df: pd.DataFrame, market: str
) -> Tuple[int, Dict[str, float], Dict[str, int]]:
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]

    sma5  = SMAIndicator(close, 5).sma_indicator()
    sma25 = SMAIndicator(close, 25).sma_indicator()
    sma75 = SMAIndicator(close, 75).sma_indicator()
    macd  = MACD(close)
    rsi   = RSIIndicator(close, 14).rsi()
    bb    = BollingerBands(close)
    ichi  = IchimokuIndicator(high, low)

    score = 0
    parts = {"trend": 0, "momentum": 0, "heat": 0}
    price = float(close.iloc[-1])

    # トレンド 50点
    if price > float(sma5.iloc[-1]) > float(sma25.iloc[-1]) > float(sma75.iloc[-1]):
        score += 20; parts["trend"] += 20
    if float(sma75.iloc[-1]) > float(sma75.iloc[-5]):
        score += 10; parts["trend"] += 10
    if float(sma5.iloc[-2]) <= float(sma25.iloc[-2]) and float(sma5.iloc[-1]) > float(sma25.iloc[-1]):
        score += 10; parts["trend"] += 10
    try:
        sa = float(ichi.ichimoku_a().iloc[-1])
        sb = float(ichi.ichimoku_b().iloc[-1])
        if price > max(sa, sb):
            score += 10; parts["trend"] += 10
        if float(ichi.ichimoku_conversion_line().iloc[-1]) > float(ichi.ichimoku_base_line().iloc[-1]):
            score += 10; parts["trend"] += 10
    except Exception:
        pass

    # モメンタム 20点
    if float(macd.macd().iloc[-1]) > float(macd.macd_signal().iloc[-1]):
        score += 10; parts["momentum"] += 10
    if float(macd.macd().iloc[-1]) > 0:
        score += 10; parts["momentum"] += 10

    # 過熱感 20点
    rsi_val = float(rsi.iloc[-1])
    if 30 <= rsi_val <= 50:
        score += 10; parts["heat"] += 10
    if price <= float(bb.bollinger_lband().iloc[-1]):
        score += 10; parts["heat"] += 10
    if rsi_val >= 70:
        score -= 20; parts["heat"] -= 20

    return int(score), {
        "rsi14":       rsi_val,
        "macd":        float(macd.macd().iloc[-1]),
        "macd_signal": float(macd.macd_signal().iloc[-1]),
        "sma5":        float(sma5.iloc[-1]),
        "sma25":       float(sma25.iloc[-1]),
        "sma75":       float(sma75.iloc[-1]),
    }, parts


# ===== ファンダメンタル詳細分析 =====
def _get_fundamentals_detail(ticker: str, market: str) -> Dict:
    """
    yfinanceから詳細なファンダメンタルデータを取得する
    取得できない項目はNoneで返す
    """
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        logger.warning("yfinance info failed for %s: %s", ticker, e)
        return {}

    # 基本指標
    per = float(info.get("trailingPE", 0) or 0)
    pbr = float(info.get("priceToBook", 0) or 0)
    roe_raw = float(info.get("returnOnEquity", 0) or 0)
    roe = roe_raw * 100 if abs(roe_raw) <= 1 else roe_raw
    eps = float(info.get("trailingEps", 0) or 0)

    # 成長性
    revenue_growth = float(info.get("revenueGrowth", 0) or 0) * 100  # YoY売上成長率(%)
    earnings_growth = float(info.get("earningsGrowth", 0) or 0) * 100  # YoY利益成長率(%)

    # 収益性
    operating_margin = float(info.get("operatingMargins", 0) or 0) * 100  # 営業利益率(%)
    profit_margin    = float(info.get("profitMargins", 0) or 0) * 100      # 純利益率(%)

    # 配当
    dividend_yield = float(info.get("dividendYield", 0) or 0) * 100  # 配当利回り(%)

    # アナリスト目標株価とのかい離
    current_price  = float(info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0)
    target_price   = float(info.get("targetMeanPrice", 0) or 0)
    target_upside  = ((target_price - current_price) / current_price * 100) if current_price > 0 and target_price > 0 else 0

    # アナリスト推奨
    recommendation = str(info.get("recommendationKey", "") or "")

    # 52週高値比
    week52_high = float(info.get("fiftyTwoWeekHigh", 0) or 0)
    week52_low  = float(info.get("fiftyTwoWeekLow", 0) or 0)
    week52_pct  = ((current_price - week52_low) / (week52_high - week52_low) * 100) if (week52_high - week52_low) > 0 else 0

    # 時価総額
    market_cap = info.get("marketCap", 0) or 0

    return {
        "per":              round(per, 1),
        "pbr":              round(pbr, 2),
        "roe":              round(roe, 1),
        "eps":              round(eps, 2),
        "revenue_growth":   round(revenue_growth, 1),
        "earnings_growth":  round(earnings_growth, 1),
        "operating_margin": round(operating_margin, 1),
        "profit_margin":    round(profit_margin, 1),
        "dividend_yield":   round(dividend_yield, 2),
        "target_price":     round(target_price, 1),
        "target_upside":    round(target_upside, 1),
        "recommendation":   recommendation,
        "week52_pct":       round(week52_pct, 1),
        "market_cap":       market_cap,
        "current_price":    current_price,
    }


# ===== Gemini統合分析（ファンダメンタル＋ニュース＋総合判定を1回のAPIコールで） =====
def _gemini_unified_analysis(config: Dict, ticker: str, name: str, market: str,
                              tech_score: int, tech_parts: Dict, tech_diag: Dict,
                              fund: Dict, news_summary: str) -> Dict:
    api_key   = str(config.get("gemini_api_key", "") or "")
    model     = config.get("gemini_model", "gemini-2.5-flash")
    wait_sec  = float(config.get("gemini_wait_sec", 4.0))
    max_retries = 3

    if not api_key or api_key.startswith("YOUR_"):
        return {}

    currency = "円" if market == "jp" else "ドル"
    per_th   = 20 if market == "jp" else 40
    roe_th   = 8  if market == "jp" else 15

    prompt = f"""あなたはプロのスイングトレーダー兼株式アナリストです。
以下のデータをもとに{name}（{ticker}）への投資判断を行ってください。

【テクニカルスコア】
総合: {tech_score}点/90点
トレンド: {tech_parts.get('trend',0)}/50点 / モメンタム: {tech_parts.get('momentum',0)}/20点 / 過熱感: {tech_parts.get('heat',0)}/20点
RSI: {tech_diag.get('rsi14',0):.1f} / MACD: {tech_diag.get('macd',0):.3f}

【ファンダメンタル指標】
PER: {fund.get('per','-')}倍（割安基準: {per_th}倍以下）/ PBR: {fund.get('pbr','-')}倍
ROE: {fund.get('roe','-')}%（基準: {roe_th}%以上） / EPS: {fund.get('eps','-')}{currency}
売上成長率(YoY): {fund.get('revenue_growth','-')}% / 利益成長率(YoY): {fund.get('earnings_growth','-')}%
営業利益率: {fund.get('operating_margin','-')}% / 配当利回り: {fund.get('dividend_yield','-')}%
アナリスト目標株価かい離: {fund.get('target_upside','-')}% / 推奨: {fund.get('recommendation','-')}
52週レンジ内位置: {fund.get('week52_pct','-')}%

【最新ニュース要約】
{news_summary}

【分析指示】
テクニカル・ファンダメンタル・ニュースを総合評価してください。
スイングトレード（数日〜数週間）の観点で判断してください。

以下のJSON形式のみで回答（前置き不要）：
{{
  "verdict": "BUY推奨 / 様子見 / SELL推奨 のいずれか",
  "confidence": "高 / 中 / 低",
  "fundamental_score": 0から20の整数（ファンダメンタル評価点）,
  "sentiment_score": -10から10の整数（ニュースセンチメント点）,
  "reasons": ["根拠1", "根拠2", "根拠3"],
  "risk": "主なリスクを1文で",
  "valuation": "割安 / 適正 / 割高",
  "news_summary": "ニュースの要点を1文で（ニュースなしの場合は空文字）"
}}"""

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=float(config.get("http_timeout_sec", 15.0)),
            )

            if resp.status_code == 429:
                retry_wait = wait_sec * (2 ** attempt)
                logger.warning("Gemini 429 for %s (attempt %d/%d). Waiting %.0f sec...",
                               ticker, attempt + 1, max_retries, retry_wait)
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
            payload = json.loads(cleaned)
            return {
                "verdict":           str(payload.get("verdict", "様子見")),
                "confidence":        str(payload.get("confidence", "低")),
                "fundamental_score": int(payload.get("fundamental_score", 0)),
                "sentiment_score":   int(payload.get("sentiment_score", 0)),
                "reasons":           list(payload.get("reasons", [])),
                "risk":              str(payload.get("risk", "")),
                "valuation":         str(payload.get("valuation", "適正")),
                "news_summary":      str(payload.get("news_summary", "")),
            }

        except requests.exceptions.HTTPError as e:
            if attempt < max_retries - 1:
                retry_wait = wait_sec * (2 ** attempt)
                logger.warning("Gemini HTTP error for %s: %s. Retry in %.0f sec...", ticker, e, retry_wait)
                time.sleep(retry_wait)
            else:
                logger.warning("Gemini unified analysis failed for %s: %s", ticker, e)
        except Exception as e:
            logger.warning("Gemini unified analysis failed for %s: %s", ticker, e)
            break

    return {}


# ===== 旧関数（後方互換性のため残す） =====
def _gemini_fundamental_analysis(config, ticker, name, market, tech_score, tech_parts, tech_diag, fund):
    return _gemini_unified_analysis(config, ticker, name, market, tech_score, tech_parts, tech_diag, fund, "")


    """
    テクニカル＋ファンダメンタルデータをGeminiに渡して総合判定を得る
    """
    api_key = str(config.get("gemini_api_key", "") or "")
    model   = config.get("gemini_model", "gemini-2.5-flash")
    if not api_key or api_key.startswith("YOUR_"):
        return {}

    currency = "円" if market == "jp" else "ドル"
    per_th   = 20 if market == "jp" else 40
    roe_th   = 8  if market == "jp" else 15

    prompt = f"""あなたはプロのスイングトレーダー兼株式アナリストです。
以下のテクニカル・ファンダメンタルデータをもとに、{name}（{ticker}）への投資判断を行ってください。

【テクニカルスコア】
総合: {tech_score}点/90点
- トレンド: {tech_parts.get('trend',0)}/50点
- モメンタム: {tech_parts.get('momentum',0)}/20点
- 過熱感: {tech_parts.get('heat',0)}/20点
RSI: {tech_diag.get('rsi14',0):.1f} / MACD: {tech_diag.get('macd',0):.3f}

【ファンダメンタル指標】
PER: {fund.get('per','-')}倍（{currency}株基準: {per_th}倍以下が割安）
PBR: {fund.get('pbr','-')}倍
ROE: {fund.get('roe','-')}%（基準: {roe_th}%以上）
EPS: {fund.get('eps','-')}{currency}
売上成長率(YoY): {fund.get('revenue_growth','-')}%
利益成長率(YoY): {fund.get('earnings_growth','-')}%
営業利益率: {fund.get('operating_margin','-')}%
純利益率: {fund.get('profit_margin','-')}%
配当利回り: {fund.get('dividend_yield','-')}%
アナリスト目標株価: {fund.get('target_price','-')}{currency}（現在値から{fund.get('target_upside','-')}%）
アナリスト推奨: {fund.get('recommendation','-')}
52週レンジ内位置: {fund.get('week52_pct','-')}%（0%=52週安値、100%=52週高値）

【分析指示】
1. テクニカルとファンダメンタルを総合的に評価してください
2. 特に割安度・成長性・収益性を重視してください
3. スイングトレード（数日〜数週間）の観点で判断してください

以下のJSON形式のみで回答（前置き不要）：
{{
  "verdict": "BUY推奨 / 様子見 / SELL推奨 のいずれか",
  "confidence": "高 / 中 / 低",
  "fundamental_score": 0から30の整数（ファンダメンタル評価点）,
  "reasons": ["根拠1", "根拠2", "根拠3"],
  "risk": "主なリスクを1文で",
  "valuation": "割安 / 適正 / 割高 のいずれか"
}}"""

    max_retries = 3
    wait_sec    = float(config.get("gemini_wait_sec", 4.0))

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=float(config.get("http_timeout_sec", 15.0)),
            )

            # 429の場合はリトライ
            if resp.status_code == 429:
                retry_wait = wait_sec * (2 ** attempt)  # 指数バックオフ
                logger.warning(
                    "Gemini 429 for %s (attempt %d/%d). Waiting %.0f sec...",
                    ticker, attempt + 1, max_retries, retry_wait
                )
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
            payload = json.loads(cleaned)
            return {
                "verdict":           str(payload.get("verdict", "様子見")),
                "confidence":        str(payload.get("confidence", "低")),
                "fundamental_score": int(payload.get("fundamental_score", 0)),
                "reasons":           list(payload.get("reasons", [])),
                "risk":              str(payload.get("risk", "")),
                "valuation":         str(payload.get("valuation", "適正")),
            }

        except requests.exceptions.HTTPError as e:
            if attempt < max_retries - 1:
                retry_wait = wait_sec * (2 ** attempt)
                logger.warning("Gemini HTTP error for %s: %s. Retry in %.0f sec...", ticker, e, retry_wait)
                time.sleep(retry_wait)
            else:
                logger.warning("Gemini fundamental analysis failed for %s: %s", ticker, e)
        except Exception as e:
            logger.warning("Gemini fundamental analysis failed for %s: %s", ticker, e)
            break

    return {}


# ===== 決算日チェック =====
def _is_jp_business_day(d: date) -> bool:
    return d.weekday() < 5 and not jpholiday.is_holiday(d)

def _business_days_distance(a: date, b: date) -> int:
    if a == b: return 0
    start, end = (a, b) if a < b else (b, a)
    count, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        if _is_jp_business_day(cur): count += 1
    return count

def _get_earnings_date_yf(ticker: str) -> Optional[str]:
    try:
        t   = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None: return None
        if isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"].iloc[0]
                if pd.notna(val): return str(val)[:10]
        elif isinstance(cal, dict):
            val = cal.get("Earnings Date")
            if val is not None:
                if isinstance(val, (list, tuple)) and len(val) > 0: return str(val[0])[:10]
                return str(val)[:10]
    except Exception: pass
    return None

def _near_earnings(earnings_date_str: str, reference_date: datetime) -> bool:
    try:
        ed    = datetime.fromisoformat(earnings_date_str[:10]).date()
        delta = _business_days_distance(ed, reference_date.date())
        return delta <= 3
    except Exception: return False


# ===== メインスクリーニング =====
def run_screening(
    config: Dict,
    universe_path: Path,
    market: str,
    sector_focus: List[str],  # マクロ分析の参考情報（フィルタリングには使わない）
) -> List[Dict]:
    asof_date     = config.get("analysis_asof_date")
    reference_now = datetime.fromisoformat(asof_date) if asof_date else datetime.now()
    universe      = _load_universe(universe_path)
    top_n         = int(config.get("technical_top_n", 10))

    logger.info("スクリーニング開始: %s市場 %d銘柄（全銘柄スキャン）", market, len(universe))

    # ===== STEP1: テクニカルスコアリング（全銘柄） =====
    technical_results = []
    for _, row in universe.iterrows():
        ticker = str(row["ticker"])
        name   = str(row["name"])
        sector = str(row.get("sector", ""))
        try:
            hist = download_ohlcv(ticker=ticker, period="6mo", interval="1d", asof_date=asof_date)
            if hist.empty or not _market_volume_ok(hist, market):
                continue

            # EPSマイナス（赤字）は除外
            eps_raw = yf.Ticker(ticker).info.get("trailingEps", 0) or 0
            if float(eps_raw) < 0:
                logger.info("Skipping %s: EPS negative", ticker)
                time.sleep(float(config.get("api_wait_sec", 0.5)))
                continue

            # 決算前後3営業日は除外
            earnings_date = _get_earnings_date_yf(ticker)
            if earnings_date and _near_earnings(earnings_date, reference_now):
                logger.info("Skipping %s: near earnings (%s)", ticker, earnings_date)
                time.sleep(float(config.get("api_wait_sec", 0.5)))
                continue

            tech_score, tech_diag, tech_parts = _calc_technical_score(hist, market)

            technical_results.append({
                "ticker":        ticker,
                "name":          name,
                "market":        market,
                "sector":        sector,
                "tech_score":    tech_score,
                "tech_diag":     tech_diag,
                "tech_parts":    tech_parts,
                "current_price": float(hist["Close"].iloc[-1]),
                "high20":        float(hist["High"].tail(20).max()),
                "low20":         float(hist["Low"].tail(20).min()),
                "atr14":         float((hist["High"] - hist["Low"]).tail(14).mean()),
                "bb2_upper":     float(BollingerBands(hist["Close"]).bollinger_hband().iloc[-1]),
                # マクロ注目セクターかどうかのフラグ（表示用）
                "is_focus_sector": sector in sector_focus,
            })
        except Exception as e:
            logger.warning("Technical score failed %s: %s", ticker, e)

        time.sleep(float(config.get("api_wait_sec", 0.5)))

    # テクニカルスコア上位N銘柄を抽出
    technical_results.sort(key=lambda x: x["tech_score"], reverse=True)
    top_candidates = technical_results[:top_n]
    logger.info("テクニカルTOP%d抽出完了: %s", top_n, [r["ticker"] for r in top_candidates])

    # ===== STEP2: ファンダメンタル詳細分析 + Gemini総合判定 =====
    final_results = []
    for item in top_candidates:
        ticker = item["ticker"]
        name   = item["name"]
        logger.info("統合AI分析中: %s %s", ticker, name)

        # ファンダメンタルデータ取得（yfinance）
        fund = _get_fundamentals_detail(ticker, market)
        item["fundamentals"] = fund

        # ニュース取得（Gemini APIは使わずRSSのみ）
        from news_analyzer import _fetch_news_text
        news_texts = _fetch_news_text(name, ticker, int(config.get("news_max_articles", 5)))
        news_summary = " / ".join([t[:100] for t in news_texts[:3]]) if news_texts else "ニュースなし"

        # Gemini API 1回で ファンダメンタル＋ニュース＋総合判定を統合
        ai_result = _gemini_unified_analysis(
            config, ticker, name, market,
            item["tech_score"], item["tech_parts"], item["tech_diag"],
            fund, news_summary
        )
        item["ai_verdict"] = ai_result

        # スコア計算
        fund_score = ai_result.get("fundamental_score", 0) if ai_result else 0
        sent_score = ai_result.get("sentiment_score", 0)   if ai_result else 0
        item["score"]             = item["tech_score"] + fund_score + sent_score
        item["technical_score"]   = item["tech_score"]
        item["fundamental_score"] = fund_score
        item["sentiment_score"]   = sent_score
        item["sentiment_reason"]  = ai_result.get("news_summary", "") if ai_result else ""
        item["score_parts"]       = {
            **item["tech_parts"],
            "fundamental": fund_score,
            "sentiment":   sent_score,
        }

        # reporter.py互換フィールド
        item["tech_rsi14"]       = item["tech_diag"].get("rsi14", 0)
        item["tech_macd"]        = item["tech_diag"].get("macd", 0)
        item["tech_macd_signal"] = item["tech_diag"].get("macd_signal", 0)
        item["tech_sma5"]        = item["tech_diag"].get("sma5", 0)
        item["tech_sma25"]       = item["tech_diag"].get("sma25", 0)
        item["tech_sma75"]       = item["tech_diag"].get("sma75", 0)

        final_results.append(item)
        time.sleep(float(config.get("gemini_wait_sec", 4.0)))

    final_results.sort(key=lambda x: x["score"], reverse=True)
    logger.info("スクリーニング完了: %s市場 最終%d銘柄", market, len(final_results))
    return final_results
