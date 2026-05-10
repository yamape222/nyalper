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

# ===== スコア正規化定数 =====
# テクニカル195点 + ファンダ85点 = 280点満点 → 100点満点に正規化
# 7:3の比率（テクニカル70点：ファンダ30点）
_SCORE_DIVISOR = 2.8   # 280 ÷ 2.8 = 100点満点
_TECH_MAX      = 195   # テクニカル最大点
_FUND_MAX      = 85    # ファンダ最大点（Geminiが100点で返したものを×0.85）

def normalize_score(raw_score: int) -> int:
    """生スコア（最大280点）を100点満点に正規化"""
    return min(100, max(0, round(raw_score / _SCORE_DIVISOR)))



# ===== ADR対応表（adr_map.jsonから動的に読み込み）=====
_ADR_MAP_PATH = Path(__file__).resolve().parent / "adr_map.json"

def _load_adr_map() -> Dict:
    """adr_map.jsonを読み込む。なければ空dictを返す"""
    if not _ADR_MAP_PATH.exists():
        return {}
    try:
        with open(_ADR_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Noneの銘柄（ADRなし）を除外して返す
        return {k: v for k, v in data.items() if v is not None}
    except Exception:
        return {}

ADR_MAP = _load_adr_map()

def _get_adr(ticker: str) -> Dict:
    """ADR価格を取得して日本株との比較を返す"""
    adr_ticker = ADR_MAP.get(ticker)
    if not adr_ticker:
        return {}
    try:
        df = yf.download(adr_ticker, period="5d", interval="1d",
                        auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.dropna()
        if len(df) < 2:
            return {}
        last = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2])
        chg_pct = (last - prev) / prev * 100 if prev > 0 else 0
        return {
            "ticker":     adr_ticker,
            "last":       round(last, 2),
            "change_pct": round(chg_pct, 2),
            "gap_up":     chg_pct > 0.5,
            "gap_down":   chg_pct < -0.5,
        }
    except Exception as e:
        logger.debug("ADR取得失敗 %s(%s): %s", ticker, adr_ticker, e)
        return {}

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
# ===== ホワイトリスト（ファンダフィルターをスキップする銘柄）=====
# 半導体・成長株など、PERが高くても優良な銘柄を登録
WHITELIST = {
    "6857.T",  # アドバンテスト
    "8035.T",  # 東京エレクトロン
    "6920.T",  # レーザーテック
    "4063.T",  # 信越化学工業
    "6146.T",  # ディスコ
    "6600.T",  # キオクシアHD
    "4062.T",  # イビデン
    "5803.T",  # フジクラ
    "5016.T",  # JX金属
}


def _fundamental_filter(fund: Dict, market: str, ticker: str = "", mode: str = "normal") -> Tuple[bool, str]:
    """
    ファンダメンタルフィルター。
    mode="growth": グロース株モード（赤字・高PER許容・出来高急増重視）
    mode="normal": 通常モード（EPS>0・ROE>=5%・PER<=50倍）
    ホワイトリスト銘柄はフィルターをスキップ。
    """
    if ticker in WHITELIST:
        return True, "ホワイトリスト銘柄"

    # グロースモードは大幅緩和（成長企業は赤字でも上場している）
    if mode == "growth":
        per = fund.get("per", 0)
        # PERが異常に高い（1000倍超）場合のみ除外
        if per and per > 1000:
            return False, f"PER異常値（{per}倍）"
        return True, "グロース銘柄（ファンダフィルター緩和）"

    eps = fund.get("eps", 0)
    roe = fund.get("roe", 0)
    per = fund.get("per", 0)

    if eps < 0:
        return False, f"赤字企業（EPS:{eps}）"
    if roe != 0 and roe < 5:
        return False, f"ROE低すぎ（ROE:{roe}%）"
    if per != 0 and per > 50:
        return False, f"割高すぎ（PER:{per}倍）"

    return True, "通過"

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


# ===== テクニカルスコアリング v2（押し目・ブレイクアウト・週足対応） =====
#
# スコア構成（最大215点）
#   ベーススコア    : トレンド50 + モメンタム20 + 過熱感20 = 90点満点
#   出来高急増      : 最大 +15点
#   押し目ボーナス  : 最大 +50点（パターンA）
#   BOボーナス      : 最大 +40点（パターンB）
#   週足ボーナス    : 最大 +20点（ペナルティなし・下降は⚠️警告のみ）
#
# 推奨閾値: score_threshold = 70

def _calc_weekly_trend(ticker: str, market: str) -> Tuple[int, Dict]:
    """
    週足データで上位足トレンドを判定してボーナスを返す。
    ペナルティなし・週足下降は⚠️警告フラグのみ。
    返り値: (週足スコア, 週足診断dict)
      週足スコア: 0〜+20点
    """
    try:
        from market_data import download_ohlcv
        df_w = download_ohlcv(ticker=ticker, period="2y", interval="1wk")
        if df_w is None or len(df_w) < 30:
            return 0, {"weekly_trend": "データ不足", "weekly_score": 0, "weekly_warning": False}

        close_w = df_w["Close"]
        sma5w   = SMAIndicator(close_w, 5).sma_indicator()
        sma13w  = SMAIndicator(close_w, 13).sma_indicator()
        sma25w  = SMAIndicator(close_w, 25).sma_indicator()
        macd_w  = MACD(close_w)

        price_w   = float(close_w.iloc[-1])
        s5w       = float(sma5w.iloc[-1])
        s13w      = float(sma13w.iloc[-1])
        s25w      = float(sma25w.iloc[-1])
        s25w_prev = float(sma25w.iloc[-5])
        macd_wval = float(macd_w.macd().iloc[-1])

        weekly_score = 0
        signals      = []

        # ─── ボーナス（上昇トレンド）───
        if s5w > s25w:
            weekly_score += 10
            signals.append("週足5>25線")
        if macd_wval > 0:
            weekly_score += 5
            signals.append("週足MACD正")
        if price_w > s13w:
            weekly_score += 5
            signals.append("週足>13週線")

        weekly_score = min(weekly_score, 20)   # 上限+20点・マイナスなし

        # ─── 警告フラグ（ペナルティなし）───
        weekly_warning = (s5w < s25w and s25w < s25w_prev)
        if weekly_warning:
            signals.append("⚠️週足下降トレンド")

        # トレンド判定ラベル
        if weekly_warning:
            weekly_trend = "⚠️下降中"
        elif weekly_score >= 15:
            weekly_trend = "強い上昇"
        elif weekly_score >= 5:
            weekly_trend = "上昇"
        else:
            weekly_trend = "中立"

        return weekly_score, {
            "weekly_trend":   weekly_trend,
            "weekly_score":   weekly_score,
            "weekly_warning": weekly_warning,
            "weekly_signals": signals,
            "sma5w":          round(s5w, 1),
            "sma13w":         round(s13w, 1),
            "sma25w":         round(s25w, 1),
        }

    except Exception as e:
        logger.debug("週足取得失敗 %s: %s", ticker, e)
        return 0, {"weekly_trend": "取得失敗", "weekly_score": 0, "weekly_warning": False}


def _calc_technical_score(
    df: pd.DataFrame,
    market: str,
    ticker: str = "",        # 週足取得用（省略時は週足スキップ）
) -> Tuple[int, Dict, Dict]:
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    open_ = df["Open"]

    sma5  = SMAIndicator(close, 5).sma_indicator()
    sma25 = SMAIndicator(close, 25).sma_indicator()
    sma75 = SMAIndicator(close, 75).sma_indicator()
    macd  = MACD(close)
    rsi   = RSIIndicator(close, 14).rsi()
    bb    = BollingerBands(close)
    ichi  = IchimokuIndicator(high, low)

    parts = {"trend": 0, "momentum": 0, "heat": 0, "volume": 0, "pullback": 0, "breakout": 0}
    score = 0
    price    = float(close.iloc[-1])
    rsi_val  = float(rsi.iloc[-1])
    macd_val = float(macd.macd().iloc[-1])
    macd_sig = float(macd.macd_signal().iloc[-1])
    s5       = float(sma5.iloc[-1])
    s25      = float(sma25.iloc[-1])
    s75      = float(sma75.iloc[-1])
    s75_prev = float(sma75.iloc[-5])
    bb_mid   = float(bb.bollinger_mavg().iloc[-1])
    bb_low   = float(bb.bollinger_lband().iloc[-1])

    # 出来高データ（ベーススコア・BOボーナス共通で使う）
    vol_today  = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
    vol_avg20  = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 21 else 0
    vol_ratio  = vol_today / vol_avg20 if vol_avg20 > 0 else 0

    # ─────────────────────────────────────────
    # ベーススコア（既存ロジックそのまま維持）
    # ─────────────────────────────────────────

    # トレンド 50点
    if price > s5 > s25:
        score += 20; parts["trend"] += 20
    if s75 > s75_prev:
        score += 10; parts["trend"] += 10
    # GC（当日）
    gc_today = (float(sma5.iloc[-2]) <= float(sma25.iloc[-2])) and (s5 > s25)
    if gc_today:
        score += 10; parts["trend"] += 10
    try:
        sa        = float(ichi.ichimoku_a().iloc[-1])
        sb        = float(ichi.ichimoku_b().iloc[-1])
        cloud_bot = min(sa, sb)
        if price > cloud_bot:
            score += 10; parts["trend"] += 10
        if float(ichi.ichimoku_conversion_line().iloc[-1]) > float(ichi.ichimoku_base_line().iloc[-1]):
            score += 10; parts["trend"] += 10
    except Exception:
        pass

    # モメンタム 20点
    if macd_val > macd_sig:
        score += 10; parts["momentum"] += 10
    if macd_val > 0:
        score += 10; parts["momentum"] += 10

    # 過熱感 20点
    if 25 <= rsi_val <= 55:
        score += 10; parts["heat"] += 10
    if price <= bb_low:
        score += 10; parts["heat"] += 10
    if rsi_val >= 75:
        score -= 20; parts["heat"] -= 20

    # ─────────────────────────────────────────
    # 出来高急増スコア（最大 +15点）
    #   20日平均の2倍以上 → +15点（強いシグナル）
    #   20日平均の1.5倍以上 → +10点
    #   ※押し目・BOどちらのパターンでも有効
    # ─────────────────────────────────────────
    vol_score = 0
    if vol_ratio >= 2.0:
        vol_score = 15
    elif vol_ratio >= 1.5:
        vol_score = 10
    score += vol_score
    parts["volume"] = vol_score

    # ─────────────────────────────────────────
    # パターンA：押し目ボーナス（最大 +50点）
    # 前提：75日線が上向き かつ 現在値 > 75日線
    # ─────────────────────────────────────────
    pullback_bonus = 0
    in_uptrend = (s75 > s75_prev) and (price > s75)

    if in_uptrend:
        # STEP1 押し目の深さ（最大15点）
        high10     = float(high.tail(10).max())
        drop_pct   = (high10 - price) / high10 * 100 if high10 > 0 else 0
        if 5.0 <= drop_pct <= 8.0:
            pullback_bonus += 15   # ちょうどいい押し
        elif 8.0 < drop_pct <= 15.0:
            pullback_bonus += 10   # やや深め
        elif 3.0 <= drop_pct < 5.0:
            pullback_bonus += 5    # 浅めだが有効

        # STEP2 反転シグナル（最大25点・加算式）
        reversal_pts = 0
        # MACDがGC直後（0〜2日以内）
        macd_line   = macd.macd()
        macd_signal = macd.macd_signal()
        macd_gc = False
        for k in range(1, 3):
            if len(macd_line) > k + 1:
                if float(macd_line.iloc[-(k+1)]) <= float(macd_signal.iloc[-(k+1)]) \
                        and float(macd_line.iloc[-k]) > float(macd_signal.iloc[-k]):
                    macd_gc = True
                    break
        if macd_gc:
            reversal_pts += 15
        # 5日線が下げ止まり〜上向き転換
        if len(sma5) >= 4:
            s5_2d_ago = float(sma5.iloc[-3])
            s5_1d_ago = float(sma5.iloc[-2])
            if s5_1d_ago <= s5_2d_ago and s5 >= s5_1d_ago:  # 下げが止まった
                reversal_pts += 10
        # 直近2日以内に陽線
        for k in range(1, 3):
            if len(close) > k and len(open_) > k:
                if float(close.iloc[-k]) > float(open_.iloc[-k]):
                    reversal_pts += 8
                    break
        # RSI 適温ゾーン（押し目後の反転に最適）
        if 40 <= rsi_val <= 55:
            reversal_pts += 7
        pullback_bonus += min(reversal_pts, 25)

        # STEP3 支持線での反発（最大10点・高い方を採用）
        support_pts = 0
        # 25日線付近±2%
        if abs(price - s25) / s25 <= 0.02:
            support_pts = max(support_pts, 10)
        # BB中心線付近±1%
        if bb_mid > 0 and abs(price - bb_mid) / bb_mid <= 0.01:
            support_pts = max(support_pts, 6)
        pullback_bonus += support_pts

    pullback_bonus = min(pullback_bonus, 50)
    score += pullback_bonus
    parts["pullback"] = pullback_bonus

    # ─────────────────────────────────────────
    # パターンB：ブレイクアウトボーナス（最大 +40点）
    # ─────────────────────────────────────────
    breakout_bonus = 0

    # STEP1 ブレイクのきっかけ（最大20点・高い方を採用）
    trigger_pts = 0
    if gc_today:
        trigger_pts = max(trigger_pts, 15)
    # 直近20日高値を終値で上抜け
    high20_ex_today = float(high.iloc[-21:-1].max()) if len(high) >= 21 else float(high.iloc[:-1].max())
    if price > high20_ex_today:
        trigger_pts = max(trigger_pts, 20)
    breakout_bonus += trigger_pts

    # STEP2 勢いの確認（最大15点・加算式）
    if trigger_pts > 0:   # ブレイクのきっかけがある場合のみ加点
        momentum_pts = 0
        # 出来高が20日平均の1.5倍以上（上で計算済みのvol_ratioを再利用）
        if vol_ratio >= 1.5:
            momentum_pts += 10
        # MACD 0ライン以上かつ上向き
        if macd_val > 0 and len(macd_line) >= 2 and macd_val > float(macd_line.iloc[-2]):
            momentum_pts += 5
        # 一目均衡表の雲を上抜け（当日〜3日以内）
        try:
            ichi_a  = ichi.ichimoku_a()
            ichi_b  = ichi.ichimoku_b()
            cloud_crossed = False
            for k in range(1, 4):
                if len(close) > k and len(ichi_a) > k and len(ichi_b) > k:
                    prev_price  = float(close.iloc[-(k+1)])
                    prev_top    = max(float(ichi_a.iloc[-(k+1)]), float(ichi_b.iloc[-(k+1)]))
                    cur_price   = float(close.iloc[-k])
                    cur_top     = max(float(ichi_a.iloc[-k]), float(ichi_b.iloc[-k]))
                    if prev_price <= prev_top and cur_price > cur_top:
                        cloud_crossed = True
                        break
            if cloud_crossed:
                momentum_pts += 8
        except Exception:
            pass
        breakout_bonus += min(momentum_pts, 15)

    # STEP3 トレンドの地盤（最大5点）
    if trigger_pts > 0 and s75 > s75_prev:
        breakout_bonus += 5

    breakout_bonus = min(breakout_bonus, 40)
    score += breakout_bonus
    parts["breakout"] = breakout_bonus

    # ─────────────────────────────────────────
    # パターン判定ラベル（ログ・表示用）
    # ─────────────────────────────────────────
    if pullback_bonus >= 20 and breakout_bonus >= 15:
        pattern = "押し目+BO"
    elif pullback_bonus >= 20:
        pattern = "押し目"
    elif breakout_bonus >= 15:
        pattern = "ブレイクアウト"
    else:
        pattern = "ベース"

    # ─────────────────────────────────────────
    # 週足トレンドボーナス/ペナルティ（最大+20点・最小-10点）
    # tickerが渡された場合のみ実行
    # ─────────────────────────────────────────
    weekly_score = 0
    weekly_diag  = {}
    if ticker:
        weekly_score, weekly_diag = _calc_weekly_trend(ticker, market)
        score += weekly_score
        parts["weekly"] = weekly_score

    return int(score), {
        "rsi14":          rsi_val,
        "macd":           macd_val,
        "macd_signal":    macd_sig,
        "sma5":           s5,
        "sma25":          s25,
        "sma75":          s75,
        "pattern":        pattern,
        "drop_pct":       round((float(high.tail(10).max()) - price) / float(high.tail(10).max()) * 100, 1)
                          if float(high.tail(10).max()) > 0 else 0.0,
        "vol_ratio":      round(vol_ratio, 2),
        "weekly_trend":   weekly_diag.get("weekly_trend", ""),
        "weekly_score":   weekly_score,
        "weekly_signals": weekly_diag.get("weekly_signals", []),
        "sma5w":          weekly_diag.get("sma5w", 0),
        "sma13w":         weekly_diag.get("sma13w", 0),
        "sma25w":         weekly_diag.get("sma25w", 0),
    }, parts


# ===== SELLスコアリング v1（空売りシグナル・BUYの対称設計）=====
#
# スコア構成（最大195点・BUYと対称）
#   ベーススコア    : トレンド下落50 + モメンタム下落20 + 過熱感(RSI高)20 = 90点
#   出来高急増      : 最大 +15点
#   戻り売りボーナス: 最大 +50点（パターンA）
#   DCボーナス      : 最大 +40点（パターンB）
#
# ⚠️ 貸借銘柄チェックはyfinance非対応のため⚠️フラグのみ表示

def _calc_sell_score(df: pd.DataFrame, market: str) -> Tuple[int, Dict, Dict]:
    """
    空売りシグナルのスコアリング。
    返り値は _calc_technical_score と同じ形式（score, diag, parts）。
    """
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    open_ = df["Open"]

    sma5  = SMAIndicator(close, 5).sma_indicator()
    sma25 = SMAIndicator(close, 25).sma_indicator()
    sma75 = SMAIndicator(close, 75).sma_indicator()
    macd  = MACD(close)
    rsi   = RSIIndicator(close, 14).rsi()
    bb    = BollingerBands(close)
    ichi  = IchimokuIndicator(high, low)

    parts = {"trend": 0, "momentum": 0, "heat": 0, "volume": 0, "pullback": 0, "breakout": 0}
    score = 0
    price    = float(close.iloc[-1])
    rsi_val  = float(rsi.iloc[-1])
    macd_val = float(macd.macd().iloc[-1])
    macd_sig = float(macd.macd_signal().iloc[-1])
    s5       = float(sma5.iloc[-1])
    s25      = float(sma25.iloc[-1])
    s75      = float(sma75.iloc[-1])
    s75_prev = float(sma75.iloc[-5])
    bb_mid   = float(bb.bollinger_mavg().iloc[-1])
    bb_high  = float(bb.bollinger_hband().iloc[-1])

    # 出来高（共通）
    vol_today = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
    vol_avg20 = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 21 else 0
    vol_ratio = vol_today / vol_avg20 if vol_avg20 > 0 else 0

    # ─────────────────────────────────────────
    # ベーススコア（BUYの逆）
    # ─────────────────────────────────────────

    # トレンド下落 50点
    if price < s5 < s25:                    # 価格 < 5日線 < 25日線
        score += 20; parts["trend"] += 20
    if s75 < s75_prev:                      # 75日線が下向き
        score += 10; parts["trend"] += 10
    # デッドクロス当日
    dc_today = (float(sma5.iloc[-2]) >= float(sma25.iloc[-2])) and (s5 < s25)
    if dc_today:
        score += 10; parts["trend"] += 10
    # 一目均衡表：雲の下 OR 雲の中
    try:
        sa        = float(ichi.ichimoku_a().iloc[-1])
        sb        = float(ichi.ichimoku_b().iloc[-1])
        cloud_top = max(sa, sb)
        if price < cloud_top:               # 雲の中以下でOK
            score += 10; parts["trend"] += 10
        if float(ichi.ichimoku_conversion_line().iloc[-1]) < float(ichi.ichimoku_base_line().iloc[-1]):
            score += 10; parts["trend"] += 10
    except Exception:
        pass

    # モメンタム下落 20点
    if macd_val < macd_sig:                 # MACD < シグナル
        score += 10; parts["momentum"] += 10
    if macd_val < 0:                        # MACD 0ライン以下
        score += 10; parts["momentum"] += 10

    # 過熱感（買われすぎ）20点
    if rsi_val >= 70:                       # RSI 70以上（買われすぎ）
        score += 15; parts["heat"] += 15
    elif rsi_val >= 65:
        score += 8;  parts["heat"] += 8
    if price >= bb_high:                    # BB +2σタッチ（天井圏）
        score += 10; parts["heat"] += 10
    if rsi_val <= 30:                       # RSI 30以下なら売りすぎで減点
        score -= 15; parts["heat"] -= 15

    # 出来高急増 最大15点
    vol_score = 0
    if vol_ratio >= 2.0:
        vol_score = 15
    elif vol_ratio >= 1.5:
        vol_score = 10
    score += vol_score
    parts["volume"] = vol_score

    # ─────────────────────────────────────────
    # パターンA：戻り売りボーナス（最大 +50点）
    # 前提：75日線が下向き かつ 現在値 < 75日線
    # ─────────────────────────────────────────
    pullback_bonus = 0
    in_downtrend = (s75 < s75_prev) and (price < s75)

    if in_downtrend:
        # STEP1 戻りの高さ（最大15点）
        low10     = float(low.tail(10).min())
        rally_pct = (price - low10) / low10 * 100 if low10 > 0 else 0
        if 5.0 <= rally_pct <= 8.0:
            pullback_bonus += 15   # ちょうどいい戻り
        elif 8.0 < rally_pct <= 15.0:
            pullback_bonus += 10   # やや戻りすぎ
        elif 3.0 <= rally_pct < 5.0:
            pullback_bonus += 5    # 浅めだが有効

        # STEP2 下落再開シグナル（最大25点・加算式）
        reversal_pts = 0
        # MACDがDC直後（0〜2日以内）
        macd_line   = macd.macd()
        macd_signal_line = macd.macd_signal()
        macd_dc = False
        for k in range(1, 3):
            if len(macd_line) > k + 1:
                if float(macd_line.iloc[-(k+1)]) >= float(macd_signal_line.iloc[-(k+1)]) \
                        and float(macd_line.iloc[-k]) < float(macd_signal_line.iloc[-k]):
                    macd_dc = True
                    break
        if macd_dc:
            reversal_pts += 15
        # 5日線が上げ止まり〜下向き転換
        if len(sma5) >= 4:
            s5_2d_ago = float(sma5.iloc[-3])
            s5_1d_ago = float(sma5.iloc[-2])
            if s5_1d_ago >= s5_2d_ago and s5 <= s5_1d_ago:
                reversal_pts += 10
        # 直近2日以内に陰線
        for k in range(1, 3):
            if len(close) > k and len(open_) > k:
                if float(close.iloc[-k]) < float(open_.iloc[-k]):
                    reversal_pts += 8
                    break
        # RSI 過熱ゾーン（戻り売りに最適）
        if 60 <= rsi_val <= 75:
            reversal_pts += 7
        pullback_bonus += min(reversal_pts, 25)

        # STEP3 抵抗線での反落（最大10点・高い方を採用）
        resist_pts = 0
        if abs(price - s25) / s25 <= 0.02:         # 25日線付近±2%
            resist_pts = max(resist_pts, 10)
        if bb_mid > 0 and abs(price - bb_mid) / bb_mid <= 0.01:  # BB中心線付近±1%
            resist_pts = max(resist_pts, 6)
        pullback_bonus += resist_pts

    pullback_bonus = min(pullback_bonus, 50)
    score += pullback_bonus
    parts["pullback"] = pullback_bonus

    # ─────────────────────────────────────────
    # パターンB：DCボーナス（最大 +40点）
    # ─────────────────────────────────────────
    breakout_bonus = 0

    # STEP1 下落のきっかけ（最大20点・高い方を採用）
    trigger_pts = 0
    if dc_today:
        trigger_pts = max(trigger_pts, 15)
    # 直近20日安値を終値で下抜け
    low20_ex_today = float(low.iloc[-21:-1].min()) if len(low) >= 21 else float(low.iloc[:-1].min())
    if price < low20_ex_today:
        trigger_pts = max(trigger_pts, 20)
    breakout_bonus += trigger_pts

    # STEP2 勢いの確認（最大15点・加算式）
    if trigger_pts > 0:
        momentum_pts = 0
        if vol_ratio >= 1.5:                # 出来高急増（売り圧力）
            momentum_pts += 10
        if macd_val < 0 and len(macd.macd()) >= 2 and macd_val < float(macd.macd().iloc[-2]):
            momentum_pts += 5               # MACD 0以下かつ下向き
        # 一目均衡表の雲を下抜け（当日〜3日以内）
        try:
            ichi_a = ichi.ichimoku_a()
            ichi_b = ichi.ichimoku_b()
            cloud_broken = False
            for k in range(1, 4):
                if len(close) > k and len(ichi_a) > k and len(ichi_b) > k:
                    prev_price  = float(close.iloc[-(k+1)])
                    prev_bot    = min(float(ichi_a.iloc[-(k+1)]), float(ichi_b.iloc[-(k+1)]))
                    cur_price   = float(close.iloc[-k])
                    cur_bot     = min(float(ichi_a.iloc[-k]), float(ichi_b.iloc[-k]))
                    if prev_price >= prev_bot and cur_price < cur_bot:
                        cloud_broken = True
                        break
            if cloud_broken:
                momentum_pts += 8
        except Exception:
            pass
        breakout_bonus += min(momentum_pts, 15)

    # STEP3 トレンドの地盤（最大5点）
    if trigger_pts > 0 and s75 < s75_prev:
        breakout_bonus += 5

    breakout_bonus = min(breakout_bonus, 40)
    score += breakout_bonus
    parts["breakout"] = breakout_bonus

    # ─────────────────────────────────────────
    # パターン判定ラベル
    # ─────────────────────────────────────────
    if pullback_bonus >= 20 and breakout_bonus >= 15:
        pattern = "戻り売り+DC"
    elif pullback_bonus >= 20:
        pattern = "戻り売り"
    elif breakout_bonus >= 15:
        pattern = "デッドクロス"
    else:
        pattern = "ベース"

    rally_pct_val = 0.0
    if len(low) >= 10:
        low10v = float(low.tail(10).min())
        rally_pct_val = round((price - low10v) / low10v * 100, 1) if low10v > 0 else 0.0

    return int(score), {
        "rsi14":       rsi_val,
        "macd":        macd_val,
        "macd_signal": macd_sig,
        "sma5":        s5,
        "sma25":       s25,
        "sma75":       s75,
        "pattern":     pattern,
        "rally_pct":   rally_pct_val,   # 戻り幅（押し目のdrop_pctに対応）
        "vol_ratio":   round(vol_ratio, 2),
        "side":        "sell",          # BUY/SELLを区別するフラグ
    }, parts


def _calc_rr_sell(item: Dict, atr_mult: float) -> Dict:
    """
    空売り用RR計算（BUYの上下逆）。
    - 利確目標（下値）: min(20日安値, BB下限2σ)
    - 損切りライン（上値）: max(現在値+ATR×atr_mult, 20日高値)
    """
    current  = float(item["current_price"])
    low20    = float(item["low20"])
    high20   = float(item["high20"])
    bb2_low  = float(item.get("bb2_lower", current * 0.95))
    atr14    = float(item["atr14"])
    sma25    = float(item.get("tech_diag", {}).get("sma25", 0) or 0)

    # 利確（下値目標）
    candidates_lower = [low20, bb2_low]
    if sma25 > 0 and sma25 * 0.93 < current:
        candidates_lower.append(sma25 * 0.93)  # 25日線乖離-7%
    target = max(c for c in candidates_lower if c < current) if any(c < current for c in candidates_lower) else low20

    # 損切り（上値）
    stop = min(current + atr14 * atr_mult, high20)
    if stop <= current:
        stop = current + atr14 * 1.5

    risk   = stop - current
    reward = current - target
    rr     = reward / risk if risk > 0 else 0.0

    return {
        "upper_target": round(stop, 1),    # 損切りライン（上）
        "lower_target": round(target, 1),  # 利確ライン（下）
        "entry_price":  round(current, 1),
        "rr":           rr,
    }


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
    """
    改善版RR計算：
    - 利確: min(20日高値, BB上限2σ, 25日線乖離+7%)
    - 損切: max(25日線, 20日安値, 現在値-ATR×1.5)
    - 寄り付き予想: ADRまたはCME先物から計算
    """
    current  = float(item["current_price"])
    high20   = float(item["high20"])
    low20    = float(item["low20"])
    bb_upper = float(item["bb2_upper"])
    atr14    = float(item["atr14"])
    sma25    = float(item.get("tech_diag", {}).get("sma25", 0) or
                     item.get("tech_sma25", 0) or 0)

    # ===== 利確目標（上値）=====
    candidates_upper = [high20, bb_upper]
    # 25日線乖離+7%（過熱ライン）
    if sma25 > 0:
        candidates_upper.append(sma25 * 1.07)
    upper = min(c for c in candidates_upper if c > current) if any(c > current for c in candidates_upper) else high20

    # ===== 損切りライン（下値）=====
    candidates_lower = [low20, current - atr14 * 1.5]
    # 25日線（トレンド崩れライン）
    if sma25 > 0 and sma25 < current:
        candidates_lower.append(sma25)
    lower = max(candidates_lower)
    # 損切りが現在値より上になったらATRで計算
    if lower >= current:
        lower = current - atr14 * 1.5

    # ===== 寄り付き予想価格 =====
    entry_price = current  # デフォルトは現在値
    adr = item.get("adr", {})
    if adr and adr.get("change_pct") is not None:
        # ADRの騰落率から寄り付きを予想
        adr_pct     = float(adr["change_pct"])
        entry_price = round(current * (1 + adr_pct / 100), 1)

    risk   = entry_price - lower
    reward = upper - entry_price
    rr     = reward / risk if risk > 0 else 0.0

    return {
        "upper_target": round(upper, 1),
        "lower_target": round(lower, 1),
        "entry_price":  round(entry_price, 1),
        "rr":           rr,
    }


# ===== トレンドフォロースクリーニング =====
# メイコー的な右肩上がり銘柄を探す
# プライム+グロース全銘柄から高速スキャンして上位10銘柄を返す

def run_trend_follow_screening(
    config: Dict,
    universe_paths: list,
    asof_date: str = None,
    top_n: int = 10,
) -> List[Dict]:
    """
    全ユニバースから右肩上がり銘柄をスキャンして返す。

    スコアリング（最大100点）：
      過去1ヶ月リターン +5%以上   → +10点
      過去1ヶ月リターン +10%以上  → +20点
      過去3ヶ月リターン +15%以上  → +20点
      過去3ヶ月リターン +30%以上  → +30点
      過去6ヶ月リターン +30%以上  → +20点
      パーフェクトオーダー完全成立 → +20点
        （価格 > 5日線 > 25日線 > 75日線）
      週足上昇中                  → +10点
    """
    import pandas as pd

    results = []

    for universe_path in universe_paths:
        try:
            universe = pd.read_csv(universe_path, dtype={"ticker": str})
        except Exception:
            continue

        for _, row in universe.iterrows():
            ticker = str(row["ticker"])
            name   = str(row.get("name", ticker))
            sector = str(row.get("sector", ""))

            try:
                # 6ヶ月分の日足データ取得（OHLCVキャッシュ活用）
                hist = download_ohlcv(ticker=ticker, period="6mo",
                                      interval="1d", asof_date=asof_date)
                if hist is None or len(hist) < 60:
                    continue

                close = hist["Close"]
                price = float(close.iloc[-1])

                # リターン計算
                ret_1m = (price / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0
                ret_3m = (price / float(close.iloc[-63]) - 1) * 100 if len(close) >= 63 else 0
                ret_6m = (price / float(close.iloc[0])   - 1) * 100

                # スコアリング
                tf_score = 0

                # 1ヶ月リターン
                if ret_1m >= 10:
                    tf_score += 20
                elif ret_1m >= 5:
                    tf_score += 10

                # 3ヶ月リターン
                if ret_3m >= 30:
                    tf_score += 30
                elif ret_3m >= 15:
                    tf_score += 20

                # 6ヶ月リターン
                if ret_6m >= 30:
                    tf_score += 20

                # 最低条件：3ヶ月で+10%未満は除外
                if ret_3m < 10:
                    continue

                # パーフェクトオーダー判定
                sma5  = SMAIndicator(close, 5).sma_indicator()
                sma25 = SMAIndicator(close, 25).sma_indicator()
                sma75 = SMAIndicator(close, 75).sma_indicator()
                s5    = float(sma5.iloc[-1])
                s25   = float(sma25.iloc[-1])
                s75   = float(sma75.iloc[-1])
                perfect_order = price > s5 > s25 > s75
                if perfect_order:
                    tf_score += 20

                # 週足上昇確認（簡易版・SMAのみ）
                try:
                    hist_w = download_ohlcv(ticker=ticker, period="1y",
                                            interval="1wk", asof_date=asof_date)
                    if hist_w is not None and len(hist_w) >= 10:
                        close_w = hist_w["Close"]
                        sma5w   = float(SMAIndicator(close_w, 5).sma_indicator().iloc[-1])
                        sma13w  = float(SMAIndicator(close_w, 13).sma_indicator().iloc[-1])
                        if sma5w > sma13w:
                            tf_score += 10
                except Exception:
                    pass

                if tf_score < 30:   # 最低スコアフィルター
                    continue

                results.append({
                    "ticker":        ticker,
                    "name":          name,
                    "sector":        sector,
                    "market":        "jp",
                    "current_price": round(price, 1),
                    "ret_1m":        round(ret_1m, 1),
                    "ret_3m":        round(ret_3m, 1),
                    "ret_6m":        round(ret_6m, 1),
                    "tf_score":      tf_score,
                    "perfect_order": perfect_order,
                    "sma5":          round(s5, 1),
                    "sma25":         round(s25, 1),
                    "sma75":         round(s75, 1),
                    "score":         tf_score,
                })

            except Exception as e:
                logger.debug("トレンドフォロー %s スキップ: %s", ticker, e)
                continue

            time.sleep(float(config.get("api_wait_sec", 0.3)))

    # スコア降順でソートして上位N件
    results.sort(key=lambda x: (x["tf_score"], x["ret_3m"]), reverse=True)
    logger.info("トレンドフォロー候補: %d銘柄", len(results))
    return results[:top_n]


# ===== メインスクリーニング =====
def run_screening(
    config: Dict,
    universe_path: Path,
    market: str,
    sector_focus: List[str],
) -> List[Dict]:
    asof_date       = config.get("analysis_asof_date") or date.today().isoformat()
    reference_now   = datetime.fromisoformat(asof_date) if asof_date else datetime.now()
    universe        = _load_universe(universe_path)
    top_n           = int(config.get("technical_top_n", 5))
    score_th        = int(config.get("score_threshold", 55))
    rr_th           = float(config.get("rr_threshold", 1.2))
    atr_mult        = float(config.get("atr_multiplier_jp", 2.0)) if market == "jp" else float(config.get("atr_multiplier_us", 2.5))
    fund_mode       = config.get("fund_filter_mode", "normal")  # "normal" or "growth"
    is_growth_mode  = fund_mode == "growth"

    logger.info("スクリーニング開始: %s市場 %d銘柄 (ファンダモード:%s)",
                market, len(universe), fund_mode)

    # ===== STEP1: 全銘柄スキャン =====
    # グロースモードは高速フィルター（出来高急増+価格上昇）で先に絞る
    # これにより596銘柄→20〜30銘柄に絞ってから詳細スコアリング

    rr_cache = _load_rr_cache(market, asof_date)
    if rr_cache:
        logger.info("日次RRキャッシュ使用: %s市場 %s (%d銘柄)", market, asof_date, len(rr_cache))

    technical_results = []
    rr_cache_new      = {}

    # グロース高速フィルター用の通過銘柄セット
    if is_growth_mode:
        logger.info("グロース高速フィルター実行中...")
        growth_candidates = set()
        for _, row in universe.iterrows():
            ticker = str(row["ticker"])
            try:
                hist_quick = download_ohlcv(ticker=ticker, period="3mo",
                                            interval="1d", asof_date=asof_date)
                if hist_quick.empty or len(hist_quick) < 21:
                    continue
                # 出来高急増（20日平均の1.5倍以上）
                vol_today = float(hist_quick["Volume"].iloc[-1])
                vol_avg20 = float(hist_quick["Volume"].iloc[-21:-1].mean())
                vol_ratio = vol_today / vol_avg20 if vol_avg20 > 0 else 0
                # 過去20日で+5%以上上昇（緩和版）
                price_now  = float(hist_quick["Close"].iloc[-1])
                price_20d  = float(hist_quick["Close"].iloc[-21])
                rise_20d   = (price_now - price_20d) / price_20d * 100 if price_20d > 0 else 0
                if vol_ratio >= 1.5 and rise_20d >= 5.0:
                    growth_candidates.add(ticker)
            except Exception:
                continue
            time.sleep(float(config.get("api_wait_sec", 0.3)))

        logger.info("グロース高速フィルター通過: %d銘柄 / %d銘柄",
                    len(growth_candidates), len(universe))
        # 高速フィルター通過銘柄のみに絞る
        universe = universe[universe["ticker"].astype(str).isin(growth_candidates)]

    for _, row in universe.iterrows():
        ticker = str(row["ticker"])
        name   = str(row["name"])
        sector = str(row.get("sector", ""))

        try:
            # ===== ファンダメンタル先フィルター（キャッシュから高速取得）=====
            fund_quick = _load_fund_cache(ticker) or {}
            if fund_quick:
                ok, reason = _fundamental_filter(fund_quick, market, ticker, mode=fund_mode)
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
            ok, reason = _fundamental_filter(fund, market, ticker, mode=fund_mode)
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

            # テクニカルスコアリング（BUY）・週足ボーナス含む
            tech_score, tech_diag, tech_parts = _calc_technical_score(hist, market, ticker)

            # SELLスコアリング（空売りシグナル）・週足はBUYと共通なので渡さない
            sell_score, sell_diag, sell_parts = _calc_sell_score(hist, market)

            # ADR取得（対応銘柄のみ）
            adr_data = _get_adr(ticker) if market == "jp" else {}

            bb_obj = BollingerBands(hist["Close"])
            item = {
                "ticker":          ticker,
                "name":            name,
                "market":          market,
                "sector":          sector,
                "tech_score":      tech_score,
                "tech_diag":       tech_diag,
                "tech_parts":      tech_parts,
                "sell_score":      sell_score,
                "sell_diag":       sell_diag,
                "sell_parts":      sell_parts,
                "fundamentals":    fund,
                "near_earnings":   near_earnings,
                "earnings_days":   earnings_days,
                "earnings_label":  earnings_label,
                "is_focus_sector": sector in sector_focus,
                "current_price":   current_price,
                "high20":          float(hist["High"].tail(20).max()),
                "low20":           float(hist["Low"].tail(20).min()),
                "atr14":           float((hist["High"] - hist["Low"]).tail(14).mean()),
                "bb2_upper":       float(bb_obj.bollinger_hband().iloc[-1]),
                "bb2_lower":       float(bb_obj.bollinger_lband().iloc[-1]),
                "adr":             adr_data,
            }

            # BUY用RR計算（日次キャッシュあれば使用）
            if rr_cache and ticker in rr_cache:
                rr_data = rr_cache[ticker]
            else:
                rr_data = _calc_rr_for_item(item, atr_mult)
                rr_cache_new[ticker] = rr_data

            item["upper_target"] = rr_data["upper_target"]
            item["lower_target"] = rr_data["lower_target"]
            item["rr"]           = rr_data["rr"]

            # SELL用RR計算（空売り・上下逆）
            sell_rr_data = _calc_rr_sell(item, atr_mult)
            item["sell_upper_target"] = sell_rr_data["upper_target"]  # 損切りライン
            item["sell_lower_target"] = sell_rr_data["lower_target"]  # 利確ライン
            item["sell_rr"]           = sell_rr_data["rr"]

            logger.info(
                "RR計算 %s: BUY score=%d rr=%.2f / SELL score=%d rr=%.2f",
                ticker, tech_score, rr_data["rr"], sell_score, sell_rr_data["rr"]
            )

            technical_results.append(item)

        except Exception as e:
            logger.warning("スキャン失敗 %s: %s", ticker, e)

        time.sleep(float(config.get("api_wait_sec", 0.5)))

    # 日次RRキャッシュ保存
    if rr_cache_new:
        merged = {**(rr_cache or {}), **rr_cache_new}
        _save_rr_cache(market, asof_date, merged)
        logger.info("日次RRキャッシュ保存: %d銘柄", len(merged))

    # RR閾値 + スコア閾値で絞り込み（BUY）
    rr_passed = [x for x in technical_results if x["rr"] >= rr_th and x["tech_score"] >= score_th]
    rr_passed.sort(key=lambda x: (x["tech_score"], x["rr"]), reverse=True)
    logger.info("RR+スコア通過: %d銘柄 / 全スキャン: %d銘柄", len(rr_passed), len(technical_results))

    # テクニカル上位（参考用・RR未達含む全銘柄）
    technical_results.sort(key=lambda x: x["tech_score"], reverse=True)

    # Gemini分析対象はBUY上位N銘柄
    top_candidates = rr_passed[:top_n]
    logger.info("Gemini分析対象: %s", [r["ticker"] for r in top_candidates])

    # ===== STEP2: Gemini統合分析（BUY候補のみ）=====
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

        # ファンダスコア：Geminiが0〜100点で返す → 0〜85点に換算（7:3比率）
        fund_score_scaled = round(fund_score * 0.85)

        raw_score = item["tech_score"] + fund_score_scaled + sent_score
        item["score"]             = normalize_score(raw_score)
        item["raw_score"]         = raw_score
        item["technical_score"]   = item["tech_score"]
        item["fundamental_score"] = fund_score_scaled
        item["sentiment_score"]   = sent_score
        item["sentiment_reason"]  = ai_result.get("news_summary", "") if ai_result else ""
        item["score_parts"]       = {**item["tech_parts"], "fundamental": fund_score_scaled}
        item["tech_rsi14"]        = item["tech_diag"].get("rsi14", 0)
        item["tech_macd"]         = item["tech_diag"].get("macd", 0)
        item["tech_macd_signal"]  = item["tech_diag"].get("macd_signal", 0)
        item["tech_sma5"]         = item["tech_diag"].get("sma5", 0)
        item["tech_sma25"]        = item["tech_diag"].get("sma25", 0)
        item["tech_sma75"]        = item["tech_diag"].get("sma75", 0)

        final_results.append(item)
        time.sleep(float(config.get("gemini_wait_sec", 4.0)))

    final_results.sort(key=lambda x: x["score"], reverse=True)

    # テクニカル上位（reporter用・RR未達銘柄も含む全銘柄）
    for item in technical_results:
        if not item.get("score"):
            item["score"]            = normalize_score(item["tech_score"])
            item["raw_score"]        = item["tech_score"]
            item["score_parts"]      = {**item["tech_parts"], "fundamental": 0}
            item["sentiment_score"]  = 0
            item["sentiment_reason"] = ""
            item["ai_verdict"]       = {}

    logger.info("スクリーニング完了: %s市場 BUY候補=%d件 / 参考=%d件",
        market, len(final_results), len(technical_results))

    all_results = final_results + [
        x for x in technical_results
        if x["ticker"] not in {r["ticker"] for r in final_results}
    ]
    return all_results
