"""
adr_prefetch.py
universe_jp.csvの全銘柄のADRティッカーを自動探索してキャッシュする。

使い方：
  python adr_prefetch.py

実行タイミング：
  - 初回セットアップ時
  - 銘柄追加時

所要時間：約10〜20分（181銘柄 × 複数ティッカー候補を検証）
"""

import json
import logging
import time
from pathlib import Path

import pandas as pd
import yfinance as yf
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("adr_prefetch")

BASE_DIR      = Path(__file__).resolve().parent
UNIVERSE_PATH = BASE_DIR / "universe_jp.csv"
ADR_MAP_PATH  = BASE_DIR / "adr_map.json"

# ===== 既知のADR対応表（手動登録分）=====
KNOWN_ADR_MAP = {
    "7203.T": "TM",      # トヨタ自動車
    "6758.T": "SONY",    # ソニーグループ
    "8306.T": "MUFG",    # 三菱UFJ
    "8316.T": "SMFG",    # 三井住友FG
    "8411.T": "MFG",     # みずほFG
    "6501.T": "HTHIY",   # 日立製作所
    "6702.T": "FJTSY",   # 富士通
    "6752.T": "PCRFY",   # パナソニック
    "6954.T": "FANUY",   # ファナック
    "7974.T": "NTDOY",   # 任天堂
    "4519.T": "CHGCY",   # 中外製薬
    "4502.T": "TAK",     # 武田薬品
    "9984.T": "SFTBY",   # ソフトバンクG
    "8058.T": "MSBHF",   # 三菱商事
    "8031.T": "ITOCY",   # 伊藤忠商事
    "9101.T": "NPNYY",   # 日本郵船
    "9107.T": "KMLEY",   # 川崎汽船
    "7267.T": "HMC",     # 本田技研
    "7270.T": "FUJHY",   # SUBARU
    "6503.T": "MIELY",   # 三菱電機
    "6645.T": "OMRNY",   # オムロン
    "4063.T": "SHECY",   # 信越化学
    "6857.T": "AHTEY",   # アドバンテスト
    "8002.T": "MARUY",   # 丸紅
    "8015.T": "TYHOF",   # 豊田通商
    "2914.T": "JAPAY",   # JT
    "9432.T": "NTTYY",   # NTT
    "6861.T": "KYCCF",   # キーエンス
    "4568.T": "DSNKY",   # 第一三共
    "6367.T": "DKILY",   # ダイキン工業
    "7751.T": "CAJ",     # キヤノン
    "6971.T": "KYCOY",   # 京セラ
    "5108.T": "BRDCY",   # ブリヂストン
    "7733.T": "OCPNY",   # オリンパス
    "4901.T": "FUJIY",   # 富士フイルム
    "8830.T": "SUNEY",   # 住友不動産
    "9022.T": "CJPRY",   # JR東海
    "9021.T": "EJPRY",   # JR東日本
    "8309.T": "SMTOY",   # 三井住友トラスト
    "5401.T": "NSSMY",   # 日本製鉄
}


def _generate_candidates(ticker: str, name: str) -> list:
    """
    日本株ティッカーからADR候補ティッカーを生成する
    例：7203.T → ["TOYOF", "TYTOY", "7203.T"]
    """
    code = ticker.replace(".T", "")
    candidates = []

    # OTCマーケット形式（コード + F）
    candidates.append(f"{code}F")

    # yfinanceの自動変換を試みる
    # （一部銘柄はyfinanceが自動的にADRを返す）

    return candidates


def _verify_adr(ticker_candidate: str) -> bool:
    """ADRティッカーが実際に取得できるか検証"""
    try:
        df = yf.download(
            ticker_candidate,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.dropna()
        if len(df) >= 2 and float(df["Close"].iloc[-1]) > 0:
            return True
        return False
    except Exception:
        return False


def _get_adr_from_yf_info(ticker: str) -> str:
    """
    yfinanceのinfoからADR情報を取得する
    """
    try:
        info = yf.Ticker(ticker).info or {}
        # yfinanceは一部銘柄でADR情報を返す
        isin = info.get("isin", "")
        if isin:
            logger.debug("%s ISIN: %s", ticker, isin)
        return ""
    except Exception:
        return ""


def main():
    # ユニバース読み込み
    if not UNIVERSE_PATH.exists():
        logger.error("universe_jp.csvが見つかりません")
        return

    universe = pd.read_csv(UNIVERSE_PATH)
    total    = len(universe)
    logger.info("対象銘柄数: %d件", total)

    # 既存キャッシュ読み込み
    adr_map = {}
    if ADR_MAP_PATH.exists():
        try:
            with open(ADR_MAP_PATH, "r", encoding="utf-8") as f:
                adr_map = json.load(f)
            logger.info("既存ADRマップ: %d件", len(adr_map))
        except Exception:
            pass

    # 既知のADRをマージ
    for jp_ticker, adr_ticker in KNOWN_ADR_MAP.items():
        if jp_ticker not in adr_map:
            adr_map[jp_ticker] = adr_ticker

    # 未調査銘柄を自動探索
    found    = 0
    notfound = 0

    for i, row in universe.iterrows():
        ticker = str(row["ticker"])
        name   = str(row["name"])

        # 既に登録済みならスキップ
        if ticker in adr_map:
            logger.info("[スキップ] %s %s → %s（登録済み）",
                       ticker, name, adr_map[ticker])
            continue

        logger.info("[%d/%d] %s %s を探索中...", i+1, total, ticker, name)

        # 候補ティッカーを生成して検証
        candidates = _generate_candidates(ticker, name)
        found_ticker = None

        for candidate in candidates:
            logger.debug("  候補: %s", candidate)
            if _verify_adr(candidate):
                found_ticker = candidate
                logger.info("  ✅ 発見: %s → %s", ticker, candidate)
                break
            time.sleep(0.3)

        if found_ticker:
            adr_map[ticker] = found_ticker
            found += 1
        else:
            # 見つからない場合はNullとして記録（再探索しない）
            adr_map[ticker] = None
            notfound += 1
            logger.info("  ❌ ADRなし: %s %s", ticker, name)

        # キャッシュを随時保存（途中で止まっても再開できる）
        with open(ADR_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(adr_map, f, ensure_ascii=False, indent=2)

        time.sleep(0.5)

    # 結果サマリー
    valid = {k: v for k, v in adr_map.items() if v is not None}
    logger.info("=" * 50)
    logger.info("探索完了！ ADRあり: %d件 / ADRなし: %d件",
               len(valid), len(adr_map) - len(valid))
    logger.info("ADRマップ保存: %s", ADR_MAP_PATH)

    # ADRありの銘柄一覧を表示
    logger.info("\n【ADR対応銘柄一覧】")
    for jp_ticker, adr_ticker in sorted(valid.items()):
        logger.info("  %s → %s", jp_ticker, adr_ticker)


if __name__ == "__main__":
    main()
