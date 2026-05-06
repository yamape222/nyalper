import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict
import time

import jpholiday
import yaml

from ai_verdict import get_ai_verdict
from backtest import run_backtest
from macro_analysis import run_macro_analysis
from portfolio_monitor import run_portfolio_monitor
from reporter import render_report
from risk_reward import select_by_risk_reward
from screener import run_screening


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_analysis_date(config: Dict[str, Any]) -> dt.date:
    asof = config.get("analysis_asof_date")
    if not asof:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(str(asof))
    except ValueError:
        return dt.date.today()


def _is_jp_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and not jpholiday.is_holiday(d)


def _prev_jp_business_day(d: dt.date) -> dt.date:
    """直前の日本営業日を返す"""
    prev = d - dt.timedelta(days=1)
    while not _is_jp_business_day(prev):
        prev -= dt.timedelta(days=1)
    return prev


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logger = logging.getLogger("nyalper_hisho")

    base_dir = Path(__file__).resolve().parent
    config   = load_config(base_dir / "config.yaml")

    today          = resolve_analysis_date(config)
    is_jp_holiday  = jpholiday.is_holiday(today) or today.weekday() >= 5

    # 日本株用の設定（祝日なら直前営業日のデータを使用）
    if is_jp_holiday:
        prev_biz = _prev_jp_business_day(today)
        logger.info("日本祝日（%s）を検出。日本株は直前営業日 %s のデータを使用。", today, prev_biz)
        config_jp = dict(config)
        config_jp["analysis_asof_date"] = prev_biz.isoformat()
        prev_biz_date = prev_biz.isoformat()
    else:
        config_jp     = config
        prev_biz_date = ""

    # 米国株用の設定（常に最新データ・asof_dateを使わない）
    config_us = dict(config)
    config_us.pop("analysis_asof_date", None)  # 常に今日の最新データ

    # マクロ分析は今日の最新データで実行（米国株基準）
    macro_result  = run_macro_analysis(config_us)
    macro_warning = macro_result.get("macro_warning", False)
    score_threshold = int(config.get("score_threshold", 65))

    screener_result  = {"jp": [], "us": []}
    screened_for_rr  = {"jp": [], "us": []}
    rr_result        = {"jp": [], "us": []}
    portfolio_result = []
    backtest_result  = {}

    if macro_warning:
        logger.warning("マクロ警告発動中：新規スクリーニングをスキップします。")
    else:
        # 日本祝日でも日本株は直前営業日のデータでスクリーニング実行
        if is_jp_holiday:
            logger.info("日本祝日のため日本株は直前営業日（%s）のデータでスクリーニング実行。", config_jp.get("analysis_asof_date"))
        
        # 日本株スクリーニング（常に実行・祝日時は直前営業日データ）
        screener_result["jp"] = run_screening(
            config=config_jp,
            universe_path=base_dir / "universe_jp.csv",
            market="jp",
            sector_focus=macro_result.get("sector_focus", []),
        )

        # 米国株スクリーニングは一旦保留（マクロ分析のみ）
        # screener_result["us"] = run_screening(...)

        # RR計算はscreener内で完了済み。rr>=rr_threshold かつ score>=score_threshold の銘柄をBUY候補とする
        rr_th = float(config.get("rr_threshold", 1.2))
        rr_result["jp"] = [x for x in screener_result["jp"]
                           if x.get("rr", 0) >= rr_th and int(x.get("score", 0)) >= score_threshold]
        screened_for_rr["jp"] = rr_result["jp"]

    # ポートフォリオ監視（祝日でも実行・直前営業日データ）
    portfolio_result = run_portfolio_monitor(config_jp)

    # AI総合判定（RR通過銘柄のみ）
    for market in ("jp", "us"):
        for item in rr_result.get(market, []):
            logger.info("AI判定: %s %s", item["ticker"], item["name"])
            item["ai_verdict"] = get_ai_verdict(config, item, macro_result)
            time.sleep(float(config.get("gemini_wait_sec", 4.0)))

    if config.get("dry_run_mode", False):
        backtest_result = run_backtest(config_jp, base_dir / "universe_jp.csv")

    render_report(
        config=config,
        report_payload={
            "date":             today.isoformat(),
            "is_jp_holiday":    is_jp_holiday,
            "prev_biz_date":    prev_biz_date,
            "macro":            macro_result,
            "macro_warning":    macro_warning,
            "screening":        screener_result,
            "screening_for_rr": screened_for_rr,
            "risk_reward":      rr_result,
            "portfolio":        portfolio_result,
            "backtest":         backtest_result,
        },
    )


if __name__ == "__main__":
    main()
