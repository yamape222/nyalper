"""
fund_prefetch.py
universe_jp.csvの全銘柄のファンダメンタルデータを一括取得してキャッシュする。

使い方：
  python fund_prefetch.py

実行タイミング：
  - 初回セットアップ時（必須）
  - 90日に1回（キャッシュ期限切れ前に）

所要時間：約10〜20分（181銘柄 × yfinance取得）
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("fund_prefetch")

BASE_DIR       = Path(__file__).resolve().parent
FUND_CACHE_DIR = BASE_DIR / "fund_cache"
UNIVERSE_PATH  = BASE_DIR / "universe_jp.csv"
FUND_CACHE_DAYS = 90


def load_config():
    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fund_cache_path(ticker: str) -> Path:
    return FUND_CACHE_DIR / f"{ticker.replace('.', '_')}.json"


def is_cache_valid(ticker: str) -> bool:
    path = fund_cache_path(ticker)
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        cached_at = datetime.fromisoformat(cached.get("_cached_at", "2000-01-01"))
        return (datetime.now() - cached_at).days <= FUND_CACHE_DAYS
    except Exception:
        return False


def fetch_and_cache(ticker: str, current_price: float = 0) -> bool:
    """1銘柄のファンダメンタルを取得してキャッシュ保存。成功したらTrue。"""
    try:
        info = yf.Ticker(ticker).info or {}
        if not info:
            logger.warning("  情報なし: %s", ticker)
            return False

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

        data = {
            "eps": round(eps, 2),
            "per": round(per, 1),
            "pbr": round(pbr, 2),
            "roe": round(roe, 1),
            "revenue_growth":   round(revenue_growth, 1),
            "earnings_growth":  round(earnings_growth, 1),
            "operating_margin": round(operating_margin, 1),
            "profit_margin":    round(profit_margin, 1),
            "dividend_yield":   round(dividend_yield, 2),
            "target_price":     round(target_price, 1),
            "target_upside":    round(target_upside, 1),
            "recommendation":   recommendation,
            "week52_pct":       round(week52_pct, 1),
            "_cached_at":       datetime.now().isoformat(),
        }

        with open(fund_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 簡易サマリー表示
        eps_str = f"EPS:{eps:.1f}"
        roe_str = f"ROE:{roe:.1f}%"
        per_str = f"PER:{per:.1f}倍"
        logger.info("  ✅ %s  %s / %s / %s", ticker, eps_str, roe_str, per_str)
        return True

    except Exception as e:
        logger.warning("  ❌ %s 取得失敗: %s", ticker, e)
        return False


def main():
    FUND_CACHE_DIR.mkdir(exist_ok=True)

    # ユニバース読み込み
    if not UNIVERSE_PATH.exists():
        logger.error("universe_jp.csvが見つかりません")
        return

    universe = pd.read_csv(UNIVERSE_PATH)
    total    = len(universe)
    logger.info("対象銘柄数: %d件", total)

    # キャッシュ済み / 未取得 を仕分け
    need_fetch = []
    skip_count = 0
    for _, row in universe.iterrows():
        ticker = str(row["ticker"])
        if is_cache_valid(ticker):
            skip_count += 1
        else:
            need_fetch.append((ticker, str(row["name"])))

    logger.info("キャッシュ済み: %d件 / 新規取得: %d件", skip_count, len(need_fetch))

    if not need_fetch:
        logger.info("全銘柄のキャッシュが有効です！スクリーニングをそのまま実行できます。")
        return

    # 一括取得
    success = 0
    fail    = 0
    for i, (ticker, name) in enumerate(need_fetch, 1):
        logger.info("[%d/%d] %s %s", i, len(need_fetch), ticker, name)
        ok = fetch_and_cache(ticker)
        if ok:
            success += 1
        else:
            fail += 1
        time.sleep(0.5)  # yfinanceへの負荷を抑える

    logger.info("=" * 50)
    logger.info("取得完了！ 成功: %d件 / 失敗: %d件", success, fail)
    logger.info("次回からファンダメンタル先スクリーニングが高速で動きます！")


if __name__ == "__main__":
    main()
