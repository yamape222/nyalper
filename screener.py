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
from news_analyzer import _fetch_news_text

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


# ===== ファンダメンタルデータ取得（yfinanceのみ・90日キャッシュ）=====
FUND_CACHE_DIR  = Path(__file__).resolve().parent / "fund_cache"
FUND_CACHE_DAYS = 90

def _fund_cache_path(ticker: str) -> Path:
    FUND_CACHE_DIR.mkdir(exist_ok=True)
    return FUND_CACHE_DIR / f"{ticker.replace('.', '_')}.json"

def _load_fund_cache(ticker: str) -> Optional[Dict]:
    path = _fund_cache_path(ticker)
    if not path.exists():
        return None
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        cached_at = datetime.fromisoformat(cached.get("_cached_at", "2000-01-01"))
        if (datetime.now() - cached_at).days <= FUND_CACHE_DAYS:
            logger.debug("ファンダメンタルキャッシュ使用: %s（%d日前）", ticker, (datetime.now()-cached_at).days)
            return {k: v for k, v in cached.items() if not k.startswith("_")}
    except Exception:
        pass
    return None

def _save_fund_cache(ticker: str, data: Dict) -> None:
    try:
        import json
        cache_data = {**data, "_cached_at": datetime.now().isoformat()}
        with open(_fund_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("ファンダメンタルキャッシュ保存失敗: %s", e)

def _get_fundamentals(ticker: str, market: str, current_price: float = 0) -> Dict:
    """
    yfinanceからファンダメンタルデータを取得。
    90日以内のキャッシュがあればキャッシュから返す。
    """
    # キャッシュチェック
    cached = _load_fund_cache(ticker)
    if cached:
        # PER・PBRは株価が変わるので毎回再計算
        if current_price > 0:
            eps = cached.get("eps", 0)
            bps = cached.get("pbr", 0)
            if eps and eps != 0:
                cached["per"] = round(current_price / eps, 1)
        return cached

    # yfinanceから取得
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        logger.warning("yfinance info failed for %s: %s", ticker, e)
        return {}

    roe_raw = float(info.get("returnOnEquity", 0) or 0)
    roe     = roe_raw * 100 if abs(roe_raw) <= 1 else roe_raw
    per     = float(info.get("trailingPE", 0) or 0)
    pbr     = float(info.get("priceToBook", 0) or 0)
    eps     = float(info.get("trailingEps", 0) or 0)
    revenue_growth   = float(info.get("revenueGrowth", 0) or 0) * 100
    earnings_growth  = float(info.get("earningsGrowth", 0) or 0) * 100
    operating_margin = float(info.get("operatingMargins", 0) or 0) * 100
    profit_margin    = float(info.get("profitMargins", 0) or 0) * 100
    dividend_yield   = float(info.get("dividendYield", 0) or 0) * 100
    target_price     = float(info.get("targetMeanPrice", 0) or 0)
    target_upside    = ((target_price - current_price) / current_price * 100) if current_price > 0 and target_price > 0 else 0
    recommendation   = str(info.get("recommendationKey", "") or "")
    week52_high      = float(info.get("fiftyTwoWeekHigh", 0) or 0)
    week52_low       = float(info.get("fiftyTwoWeekLow", 0) or 0)
    week52_pct       = ((current_price - week52_low) / (week52_high - week52_low) * 100) if (week52_high - week52_low) > 0 else 0

    result = {
        "eps": round(eps, 2), "per": round(per, 1), "pbr": round(pbr, 2),
        "roe": round(roe, 1), "revenue_growth": round(revenue_growth, 1),
        "earnings_growth": round(earnings_growth, 1),
        "operating_margin": round(operating_margin, 1),
        "profit_margin": round(profit_margin, 1),
        "dividend_yield": round(dividend_yield, 2),
        "target_price": round(target_price, 1),
        "target_upside": round(target_upside, 1),
        "recommendation": recommendation,
        "week52_pct": round(week52_pct, 1),
    }

    # キャッシュ保存（90日間有効）
    _save_fund_cache(ticker, result)
    return result


# ===== ファンダメンタルフィルター =====
def _fundamental_filter(fund: Dict, market: str) -> Tuple[bool, str]:
    """
    バランス型ファンダメンタルフィルター。
    - EPS > 0（赤字除外）
    - ROE >= 5%（最低限の収益性）
    - PER <= 50倍（極端な割高除外）
    戻り値：(通過するか, 理由)
    """
    eps = fund.get("eps", 0)
    roe = fund.get("roe", 0)
    per = fund.get("per", 0)

    # 赤字企業除外
    if eps < 0:
        return False, f"赤字企業（EPS:{eps}）"

    # ROEが取得できている場合のみチェック
    if roe != 0 and roe < 5:
        return False, f"ROE低すぎ（ROE:{roe}%）"

    # PERが取得できている場合のみチェック
    if per != 0 and per > 50:
        return False, f"割高すぎ（PER:{per}倍）"

    return True, "通過"


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

def _check_near_earnings(earnings_date_str: str, reference_date: datetime) -> Tuple[bool, int]:
    """決算前後3営業日以内かチェック。(近いか, 残り営業日数)を返す"""
    try:
        ed    = datetime.fromisoformat(earnings_date_str[:10]).date()
        delta = _business_days_distance(ed, reference_date.date())
        return delta <= 3, delta
    except Exception:
        return False, 999


# ===== テクニカルスコアリング（緩和版） =====
def _calc_technical_score(df: pd.DataFrame, market: str) -> Tuple[int, Dict, Dict]:
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

    # ===== トレンド 50点（緩和版）=====
    # 5>25日線（パーフェクトオーダー不要に緩和）
    if price > float(sma5.iloc[-1]) > float(sma25.iloc[-1]):
        score += 20; parts["trend"] += 20
    # 75日線上向き
    if float(sma75.iloc[-1]) > float(sma75.iloc[-5]):
        score += 10; parts["trend"] += 10
    # ゴールデンクロス直後
    if float(sma5.iloc[-2]) <= float(sma25.iloc[-2]) and float(sma5.iloc[-1]) > float(sma25.iloc[-1]):
        score += 10; parts["trend"] += 10
    # 一目均衡表：雲の上 OR 雲の中も可（緩和）
    try:
        sa = float(ichi.ichimoku_a().iloc[-1])
        sb = float(ichi.ichimoku_b().iloc[-1])
        cloud_top = max(sa, sb)
        cloud_bot = min(sa, sb)
        if price > cloud_bot:  # 雲の中以上でOK
            score += 10; parts["trend"] += 10
        # 転換線>基準線
        if float(ichi.ichimoku_conversion_line().iloc[-1]) > float(ichi.ichimoku_base_line().iloc[-1]):
            score += 10; parts["trend"] += 10
    except Exception:
        pass

    # ===== モメンタム 20点（緩和版）=====
    # MACD > シグナル（GCじゃなくてもOK）
    if float(macd.macd().iloc[-1]) > float(macd.macd_signal().iloc[-1]):
        score += 10; parts["momentum"] += 10
    # MACD 0ライン以上
    if float(macd.macd().iloc[-1]) > 0:
        score += 10; parts["momentum"] += 10

    # ===== 過熱感 20点（緩和版）=====
    rsi_val = float(rsi.iloc[-1])
    # RSI 25〜55（範囲拡大）
    if 25 <= rsi_val <= 55:
        score += 10; parts["heat"] += 10
    # BB -2σタッチ
    if price <= float(bb.bollinger_lband().iloc[-1]):
        score += 10; parts["heat"] += 10
    # RSI 75以上で減点（閾値を緩和）
    if rsi_val >= 75:
        score -= 20; parts["heat"] -= 20

    return int(score), {
        "rsi14":       rsi_val,
        "macd":        float(macd.macd().iloc[-1]),
        "macd_signal": float(macd.macd_signal().iloc[-1]),
        "sma5":        float(sma5.iloc[-1]),
        "sma25":       float(sma25.iloc[-1]),
        "sma75":       float(sma75.iloc[-1]),
    }, parts


# ===== Gemini統合分析 =====
def _gemini_unified_analysis(
    config: Dict, ticker: str, name: str, market: str,
    tech_score: int, tech_parts: Dict, tech_diag: Dict,
    fund: Dict, news_summary: str, near_earnings: bool, earnings_days: int
) -> Dict:
    api_key    = str(config.get("gemini_api_key", "") or "")
    model      = config.get("gemini_model", "gemini-2.5-flash")
    wait_sec   = float(config.get("gemini_wait_sec", 4.0))
    max_retries = 3

    if not api_key or api_key.startswith("YOUR_"):
        return {}

    currency = "円" if market == "jp" else "ドル"
    per_th   = 20 if market == "jp" else 40
    roe_th   = 8  if market == "jp" else 15

    earnings_note = f"⚠️ 決算発表まで{earnings_days}営業日（スコアに注意）" if near_earnings else "決算発表：当面なし"

    prompt = f"""あなたはプロのスイングトレーダー兼株式アナリストです。
以下のデータをもとに{name}（{ticker}）への投資判断を行ってください。

【決算情報】
{earnings_note}

【テクニカルスコア（90点満点）】
総合: {tech_score}点
トレンド: {tech_parts.get('trend',0)}/50点
モメンタム: {tech_parts.get('momentum',0)}/20点
過熱感: {tech_parts.get('heat',0)}/20点
RSI: {tech_diag.get('rsi14',0):.1f} / MACD: {tech_diag.get('macd',0):.3f}

【ファンダメンタル指標】
PER: {fund.get('per','-')}倍（割安基準: {per_th}倍以下）
PBR: {fund.get('pbr','-')}倍
ROE: {fund.get('roe','-')}%（基準: {roe_th}%以上）
EPS: {fund.get('eps','-')}{currency}
売上成長率(YoY): {fund.get('revenue_growth','-')}%
利益成長率(YoY): {fund.get('earnings_growth','-')}%
営業利益率: {fund.get('operating_margin','-')}%
配当利回り: {fund.get('dividend_yield','-')}%
アナリスト目標株価かい離: {fund.get('target_upside','-')}%
推奨: {fund.get('recommendation','-')}
52週レンジ内位置: {fund.get('week52_pct','-')}%

【最新ニュース】
{news_summary}

スイングトレード（数日〜数週間）の観点で総合判断してください。
以下のJSON形式のみで回答（前置き不要）：
{{
  "verdict": "BUY推奨 / 様子見 / SELL推奨",
  "confidence": "高 / 中 / 低",
  "fundamental_score": 0から20の整数,
  "fundamental_reason": "ファンダスコアの根拠を1文で（例：PER15倍・ROE12%と割安成長株）",
  "sentiment_score": -10から10の整数,
  "reasons": ["根拠1", "根拠2", "根拠3"],
  "risk": "主なリスクを1文で",
  "valuation": "割安 / 適正 / 割高",
  "news_summary": "ニュースの要点1文（なければ空文字）"
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
                logger.warning("Gemini 429 for %s (attempt %d/%d). Waiting %.0f sec...", ticker, attempt+1, max_retries, retry_wait)
                time.sleep(retry_wait)
                continue
            resp.raise_for_status()
            body = resp.json()
            text = (body.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text",""))
            cleaned = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()
            payload = json.loads(cleaned)
            return {
                "verdict":            str(payload.get("verdict", "様子見")),
                "confidence":         str(payload.get("confidence", "低")),
                "fundamental_score":  int(payload.get("fundamental_score", 0)),
                "fundamental_reason": str(payload.get("fundamental_reason", "")),
                "sentiment_score":    int(payload.get("sentiment_score", 0)),
                "reasons":            list(payload.get("reasons", [])),
                "risk":               str(payload.get("risk", "")),
                "valuation":          str(payload.get("valuation", "適正")),
                "news_summary":       str(payload.get("news_summary", "")),
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


# ===== 日次RRキャッシュ =====
RR_CACHE_DIR = Path(__file__).resolve().parent / "daily_rr_cache"

def _rr_cache_path(market: str, asof_date: str) -> Path:
    RR_CACHE_DIR.mkdir(exist_ok=True)
    date_str = asof_date if asof_date else date.today().isoformat()
    return RR_CACHE_DIR / f"rr_{market}_{date_str}.json"

def _load_rr_cache(market: str, asof_date: str) -> Optional[Dict]:
    path = _rr_cache_path(market, asof_date)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _save_rr_cache(market: str, asof_date: str, data: Dict) -> None:
    try:
        with open(_rr_cache_path(market, asof_date), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("RRキャッシュ保存失敗: %s", e)

def _calc_rr_for_item(item: Dict, atr_mult: float) -> Dict:
    """1銘柄のRRを計算して結果をdictで返す"""
    current = float(item["current_price"])
    upper   = min(float(item["high20"]), float(item["bb2_upper"]))
    lower   = max(current - float(item["atr14"]) * atr_mult, float(item["low20"]))
    risk    = current - lower
    reward  = upper - current
    rr      = reward / risk if risk > 0 else 0.0
    return {"upper_target": upper, "lower_target": lower, "rr": rr}


# ===== メインスクリーニング =====
def run_screening(
    config: Dict,
    universe_path: Path,
    market: str,
    sector_focus: List[str],
) -> List[Dict]:
    asof_date     = config.get("analysis_asof_date") or date.today().isoformat()
    reference_now = datetime.fromisoformat(asof_date) if asof_date else datetime.now()
    universe      = _load_universe(universe_path)
    top_n         = int(config.get("technical_top_n", 5))
    score_th      = int(config.get("score_threshold", 55))
    rr_th         = float(config.get("rr_threshold", 1.2))
    atr_mult      = float(config.get("atr_multiplier_jp", 2.0)) if market == "jp" else float(config.get("atr_multiplier_us", 2.5))

    logger.info("スクリーニング開始: %s市場 %d銘柄", market, len(universe))

    # ===== STEP1: 全銘柄スキャン（ファンダ先フィルター → テクニカル → RR）=====
    # 日次RRキャッシュ確認
    rr_cache = _load_rr_cache(market, asof_date)
    if rr_cache:
        logger.info("日次RRキャッシュ使用: %s市場 %s (%d銘柄)", market, asof_date, len(rr_cache))

    technical_results = []
    rr_cache_new = {}

    for _, row in universe.iterrows():
        ticker = str(row["ticker"])
        name   = str(row["name"])
        sector = str(row.get("sector", ""))

        try:
            # ===== ファンダメンタル先フィルター（キャッシュから高速取得）=====
            fund_quick = _load_fund_cache(ticker) or {}
            if fund_quick:
                ok, reason = _fundamental_filter(fund_quick, market)
                if not ok:
                    logger.info("Skipping %s: %s（キャッシュ判定）", ticker, reason)
                    time.sleep(float(config.get("api_wait_sec", 0.5)))
                    continue

            # 株価データ取得
            hist = download_ohlcv(ticker=ticker, period="6mo", interval="1d", asof_date=asof_date)
            if hist.empty or not _market_volume_ok(hist, market):
                time.sleep(float(config.get("api_wait_sec", 0.5)))
                continue

            # ファンダメンタルデータ取得（PER再計算含む）
            current_price = float(hist["Close"].iloc[-1])
            fund = _get_fundamentals(ticker, market, current_price)

            # ファンダメンタルフィルター（最終判定）
            ok, reason = _fundamental_filter(fund, market)
            if not ok:
                logger.info("Skipping %s: %s", ticker, reason)
                time.sleep(float(config.get("api_wait_sec", 0.5)))
                continue

            # 決算チェック（除外せず、フラグのみ）
            earnings_date = _get_earnings_date_yf(ticker)
            near_earnings = False
            earnings_days = 999
            earnings_label = ""
            if earnings_date:
                near_earnings, earnings_days = _check_near_earnings(earnings_date, reference_now)
                if near_earnings:
                    earnings_label = f"⚠️ 決算{earnings_days}営業日以内"
                    logger.info("Near earnings %s: %s (%d days)", ticker, earnings_date, earnings_days)

            # テクニカルスコアリング
            tech_score, tech_diag, tech_parts = _calc_technical_score(hist, market)

            item = {
                "ticker":          ticker,
                "name":            name,
                "market":          market,
                "sector":          sector,
                "tech_score":      tech_score,
                "tech_diag":       tech_diag,
                "tech_parts":      tech_parts,
                "fundamentals":    fund,
                "near_earnings":   near_earnings,
                "earnings_days":   earnings_days,
                "earnings_label":  earnings_label,
                "is_focus_sector": sector in sector_focus,
                "current_price":   current_price,
                "high20":          float(hist["High"].tail(20).max()),
                "low20":           float(hist["Low"].tail(20).min()),
                "atr14":           float((hist["High"] - hist["Low"]).tail(14).mean()),
                "bb2_upper":       float(BollingerBands(hist["Close"]).bollinger_hband().iloc[-1]),
            }

            # RR計算（日次キャッシュあれば使用）
            if rr_cache and ticker in rr_cache:
                rr_data = rr_cache[ticker]
            else:
                rr_data = _calc_rr_for_item(item, atr_mult)
                rr_cache_new[ticker] = rr_data

            item["upper_target"] = rr_data["upper_target"]
            item["lower_target"] = rr_data["lower_target"]
            item["rr"]           = rr_data["rr"]

            logger.info("RR計算 %s: score=%d rr=%.2f (上値=%.1f 現在=%.1f 下値=%.1f)",
                ticker, tech_score, rr_data["rr"], rr_data["upper_target"], current_price, rr_data["lower_target"])

            technical_results.append(item)

        except Exception as e:
            logger.warning("スキャン失敗 %s: %s", ticker, e)

        time.sleep(float(config.get("api_wait_sec", 0.5)))

    # 日次RRキャッシュ保存
    if rr_cache_new:
        merged = {**(rr_cache or {}), **rr_cache_new}
        _save_rr_cache(market, asof_date, merged)
        logger.info("日次RRキャッシュ保存: %d銘柄", len(merged))

    # RR閾値 + スコア閾値で絞り込み
    rr_passed = [x for x in technical_results if x["rr"] >= rr_th and x["tech_score"] >= score_th]
    rr_passed.sort(key=lambda x: (x["tech_score"], x["rr"]), reverse=True)
    logger.info("RR+スコア通過: %d銘柄 / 全通過: %d銘柄", len(rr_passed), len(technical_results))

    # テクニカル上位（参考用・RR未達含む全銘柄）
    technical_results.sort(key=lambda x: x["tech_score"], reverse=True)

    # Gemini分析対象はRR通過上位N銘柄
    top_candidates = rr_passed[:top_n]
    logger.info("Gemini分析対象: %s", [r["ticker"] for r in top_candidates])

    # ===== STEP2: Gemini統合分析（RR通過銘柄のみ）=====
    final_results = []
    for item in top_candidates:
        ticker = item["ticker"]
        name   = item["name"]
        logger.info("Gemini統合分析中: %s %s", ticker, name)

        news_texts   = _fetch_news_text(name, ticker, int(config.get("news_max_articles", 5)))
        news_summary = " / ".join([t[:100] for t in news_texts[:3]]) if news_texts else "ニュースなし"

        ai_result = _gemini_unified_analysis(
            config, ticker, name, item["market"],
            item["tech_score"], item["tech_parts"], item["tech_diag"],
            item["fundamentals"], news_summary,
            item["near_earnings"], item["earnings_days"]
        )
        item["ai_verdict"] = ai_result

        fund_score = ai_result.get("fundamental_score", 0) if ai_result else 0
        sent_score = ai_result.get("sentiment_score", 0)   if ai_result else 0
        item["score"]             = item["tech_score"] + fund_score + sent_score
        item["technical_score"]   = item["tech_score"]
        item["fundamental_score"] = fund_score
        item["sentiment_score"]   = sent_score
        item["sentiment_reason"]  = ai_result.get("news_summary", "") if ai_result else ""
        item["score_parts"]       = {**item["tech_parts"], "fundamental": fund_score}
        item["tech_rsi14"]        = item["tech_diag"].get("rsi14", 0)
        item["tech_macd"]         = item["tech_diag"].get("macd", 0)
        item["tech_macd_signal"]  = item["tech_diag"].get("macd_signal", 0)
        item["tech_sma5"]         = item["tech_diag"].get("sma5", 0)
        item["tech_sma25"]        = item["tech_diag"].get("sma25", 0)
        item["tech_sma75"]        = item["tech_diag"].get("sma75", 0)

        final_results.append(item)
        time.sleep(float(config.get("gemini_wait_sec", 4.0)))

    final_results.sort(key=lambda x: x["score"], reverse=True)

    # テクニカル上位（reporter用・RR未達銘柄も含む全銘柄TOP10）
    for item in technical_results:
        if not item.get("score"):
            item["score"]        = item["tech_score"]
            item["score_parts"]  = {**item["tech_parts"], "fundamental": 0}
            item["sentiment_score"] = 0
            item["sentiment_reason"] = ""
            item["ai_verdict"]   = {}

    logger.info("スクリーニング完了: %s市場 BUY候補=%d件 / 参考=%d件",
        market, len(final_results), len(technical_results))

    # final_resultsにtechnical_resultsを「参考」として付加
    # reporter.pyはscreening["jp"]からテクニカル上位を取得するので全件返す
    all_results = final_results + [x for x in technical_results if x["ticker"] not in {r["ticker"] for r in final_results}]
    return all_results
    return final_results
