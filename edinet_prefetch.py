"""
edinet_prefetch.py
EDINETの全銘柄のdocIDを事前に一括取得してキャッシュするスクリプト。

使い方：
  python edinet_prefetch.py

実行タイミング：
  - 初回セットアップ時
  - 3ヶ月に1回（決算シーズン後）

所要時間：約5〜10分（2年分の書類一覧を取得）
"""

import json
import logging
import time
import yaml
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("edinet_prefetch")

BASE_DIR      = Path(__file__).resolve().parent
CACHE_DIR     = BASE_DIR / "edinet_cache"
DOCID_CACHE   = CACHE_DIR / "_docid_map.json"
CODE_CACHE    = BASE_DIR / "edinet_codes.csv"
DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"


def load_config():
    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_edinet_codes() -> dict:
    """EDINETコードリストを読み込む"""
    if not CODE_CACHE.exists():
        logger.error("edinet_codes.csvが見つかりません。先にEdinetcodeDlInfo.csvを配置してください。")
        return {}
    df = pd.read_csv(CODE_CACHE, dtype=str)
    return dict(zip(df["edinet_code"], df["sec_code"]))


def fetch_docid_map(api_key: str, edinet_codes: set) -> dict:
    """
    過去2年間の書類一覧を取得し、
    EDINETコード → docIDのマッピングを作成する。
    """
    docid_map = {}
    today = date.today()

    # 週単位で過去2年間をサンプリング
    check_dates = [today - timedelta(weeks=w) for w in range(0, 104)]
    total = len(check_dates)

    logger.info("書類一覧取得開始: %d週分を検索", total)

    for i, target_date in enumerate(check_dates):
        if i % 10 == 0:
            logger.info("進捗: %d/%d週 完了 (取得済み: %d銘柄)", i, total, len(docid_map))

        # 未取得のEDINETコードがなくなったら終了
        remaining = edinet_codes - set(docid_map.keys())
        if not remaining:
            logger.info("全銘柄のdocIDを取得完了！")
            break

        try:
            resp = requests.get(
                DOCUMENTS_URL,
                params={
                    "date": target_date.isoformat(),
                    "type": 2,
                    "Subscription-Key": api_key,
                },
                timeout=15.0
            )
            if resp.status_code != 200:
                time.sleep(0.3)
                continue

            results = resp.json().get("results", [])
            for doc in results:
                edinet_code = doc.get("edinetCode", "")
                if (edinet_code in remaining and
                        doc.get("ordinanceCode") == "010" and
                        doc.get("formCode") in ("030000", "030001")):
                    docid_map[edinet_code] = doc["docID"]

            time.sleep(0.15)

        except Exception as e:
            logger.warning("取得失敗 %s: %s", target_date, e)
            time.sleep(1.0)

    return docid_map


def main():
    config   = load_config()
    api_key  = config.get("edinet_api_key", "")

    if not api_key or api_key.startswith("YOUR_"):
        logger.error("edinet_api_keyが設定されていません")
        return

    CACHE_DIR.mkdir(exist_ok=True)

    # 対象銘柄のEDINETコードを取得
    code_to_sec = load_edinet_codes()
    if not code_to_sec:
        return

    edinet_codes = set(code_to_sec.keys())
    logger.info("対象EDINETコード数: %d件", len(edinet_codes))

    # 既存キャッシュを読み込み
    existing = {}
    if DOCID_CACHE.exists():
        try:
            with open(DOCID_CACHE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            logger.info("既存キャッシュ: %d件", len(existing))
            # 未取得のものだけ検索
            edinet_codes -= set(existing.keys())
            logger.info("未取得: %d件", len(edinet_codes))
        except Exception:
            pass

    if not edinet_codes:
        logger.info("全銘柄取得済みです！")
        return

    # docIDを一括取得
    new_map = fetch_docid_map(api_key, edinet_codes)
    existing.update(new_map)

    # キャッシュ保存
    with open(DOCID_CACHE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.info("docIDキャッシュ保存完了: %d件", len(existing))
    logger.info("次回からにゃるぱー秘書が高速でEDINETデータを取得できます！")


if __name__ == "__main__":
    main()
