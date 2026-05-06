"""
edinet_client.py
EDINET API v2 を使って日本株のファンダメンタルデータを取得するモジュール。

取得するデータ：
  - 売上高・営業利益・経常利益・純利益（直近4期）
  - EPS・BPS・ROE・ROA・自己資本比率
  - YoY成長率（売上・営業利益）
  - 営業利益率・純利益率

フロー：
  1. EDINETコードリストCSVから証券コード→EDINETコードを変換
  2. 書類一覧APIで直近の有価証券報告書のdocIDを取得
  3. 書類取得APIでXBRLデータをダウンロード・解析
  4. 財務数値を抽出してdictで返す
"""

import io
import json
import logging
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
import re

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# EDINET API エンドポイント
EDINET_BASE    = "https://api.edinet-fsa.go.jp/api/v2"
DOCUMENTS_URL  = f"{EDINET_BASE}/documents.json"
DOCUMENT_URL   = f"{EDINET_BASE}/documents/{{doc_id}}"

# キャッシュ設定
EDINET_CODE_CACHE = Path(__file__).resolve().parent / "edinet_codes.csv"
EDINET_DATA_CACHE = Path(__file__).resolve().parent / "edinet_cache"  # 財務データキャッシュフォルダ
CACHE_DAYS = 90  # キャッシュ有効期間（日）


class EdinetClient:
    def __init__(self, api_key: str, timeout: float = 15.0):
        self.api_key  = api_key
        self.timeout  = timeout
        self._disabled = not api_key or api_key.startswith("YOUR_")
        self._code_map: Dict[str, str] = {}  # 証券コード → EDINETコード

    # ===== EDINETコードリスト取得 =====
    def _load_edinet_codes(self) -> bool:
        """
        EDINETコードリストを読み込む。

        取得方法：
        1. キャッシュファイル（edinet_codes.csv）があれば読み込む
        2. なければ手動ダウンロードを案内する
        """
        if self._disabled:
            return False

        if self._code_map:
            return True

        # キャッシュファイルがあれば読み込む
        if EDINET_CODE_CACHE.exists():
            try:
                df = pd.read_csv(EDINET_CODE_CACHE, dtype=str)
                self._code_map = dict(zip(df["sec_code"], df["edinet_code"]))
                logger.info("EDINETコードリストをキャッシュから読み込み: %d件", len(self._code_map))
                return True
            except Exception as e:
                logger.warning("EDINETコードキャッシュ読み込み失敗: %s", e)

        # EDINETが提供するCSVファイルを探す
        edinet_csv_candidates = [
            Path(__file__).resolve().parent / "EdinetcodeDlInfo.csv",
            Path(__file__).resolve().parent / "edinet_code_list.csv",
        ]

        for csv_path in edinet_csv_candidates:
            if csv_path.exists():
                try:
                    # Shift-JISで読み込み（EDINETのCSVはShift-JIS）
                    df = pd.read_csv(csv_path, encoding="cp932", dtype=str, skiprows=1)
                    df.columns = [c.strip() for c in df.columns]

                    # 列名を正規化（全角→半角に変換して検索）
                    import unicodedata
                    def normalize(s):
                        return unicodedata.normalize("NFKC", s).strip()

                    cols_normalized = {normalize(c): c for c in df.columns}

                    sec_col    = cols_normalized.get("証券コード")
                    edinet_col = cols_normalized.get("EDINETコード")

                    if not sec_col or not edinet_col:
                        logger.warning("列名が見つかりません: %s", list(df.columns))
                        continue

                    df_clean = pd.DataFrame({
                        "sec_code":    df[sec_col].astype(str).str.strip(),
                        "edinet_code": df[edinet_col].astype(str).str.strip(),
                    })

                    df_clean = df_clean.dropna().query("sec_code != '0' and sec_code != 'nan' and sec_code != ''")
                    self._code_map = dict(zip(df_clean["sec_code"], df_clean["edinet_code"]))

                    # キャッシュ保存
                    df_clean.to_csv(EDINET_CODE_CACHE, index=False)
                    logger.info("EDINETコードリストを読み込み: %d件", len(self._code_map))
                    return True

                except Exception as e:
                    logger.warning("EDINETコードCSV読み込み失敗 %s: %s", csv_path, e)

        logger.warning(
            "EDINETコードリストが見つかりません。\n"
            "以下からダウンロードしてプロジェクトフォルダに保存してください：\n"
            "https://disclosure2.edinet-fsa.go.jp/weee0010.aspx\n"
            "→「EDINETコードリスト」→「EdinetcodeDlInfo.csv」として保存\n"
            "それまでyfinanceにフォールバックします。"
        )
        return False

    def _sec_code_to_edinet(self, ticker: str) -> Optional[str]:
        """
        yfinanceのティッカー（例：7203.T）→ EDINETコードに変換。
        EDINETの証券コードは5桁（末尾0埋め）なので対応する。
        例：7203.T → 72030（5桁）→ EDINETコードに変換
        """
        # 4桁の証券コードを取得
        sec_code_4 = ticker.replace(".T", "").strip()
        # 5桁版（末尾に0を追加）でも検索
        sec_code_5 = sec_code_4 + "0"

        return self._code_map.get(sec_code_5) or self._code_map.get(sec_code_4)

    # ===== 有価証券報告書のdocID取得 =====
    def _get_doc_id(self, edinet_code: str) -> Optional[str]:
        """
        直近の有価証券報告書のdocIDを取得。
        事前取得キャッシュ（_docid_map.json）があればそこから即返す。
        なければAPIで検索（週単位・2年間）。
        """
        # 事前取得キャッシュを確認
        docid_cache_path = EDINET_DATA_CACHE / "_docid_map.json"
        if docid_cache_path.exists():
            try:
                with open(docid_cache_path, "r", encoding="utf-8") as f:
                    docid_map = json.load(f)
                if edinet_code in docid_map:
                    return docid_map[edinet_code]
            except Exception:
                pass

        # キャッシュになければAPIで検索（週単位）
        logger.info("docIDをAPIで検索中: %s（edinet_prefetch.pyを実行すると高速化できます）", edinet_code)
        today = date.today()
        for weeks_ago in range(0, 104):
            target_date = today - timedelta(weeks=weeks_ago)
            try:
                resp = requests.get(
                    DOCUMENTS_URL,
                    params={
                        "date": target_date.isoformat(),
                        "type": 2,
                        "Subscription-Key": self.api_key,
                    },
                    timeout=self.timeout
                )
                if resp.status_code != 200:
                    time.sleep(0.2)
                    continue

                results = resp.json().get("results", [])
                for doc in results:
                    if (doc.get("edinetCode") == edinet_code and
                            doc.get("ordinanceCode") == "010" and
                            doc.get("formCode") in ("030000", "030001")):
                        return doc["docID"]

                time.sleep(0.1)

            except Exception as e:
                logger.warning("書類一覧取得失敗 %s: %s", target_date, e)
                time.sleep(0.5)

        return None

    # ===== XBRLから財務データ抽出 =====
    def _extract_financials_from_xbrl(self, zip_content: bytes) -> Dict:
        """
        XBRLのZIPファイルから財務数値を抽出する。
        簡易的にXBRLのテキストを正規表現で解析。
        """
        financials = {}
        try:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
                # XBRLファイルを探す
                xbrl_files = [n for n in z.namelist() if n.endswith(".xbrl") and "AuditDoc" not in n]
                if not xbrl_files:
                    return {}

                with z.open(xbrl_files[0]) as f:
                    content = f.read().decode("utf-8", errors="ignore")

            # 主要財務項目を抽出（XBRL タグ名で検索）
            patterns = {
                # 損益計算書
                "revenue":          r'NetSales[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9]+)<',
                "operating_income": r'OperatingIncome[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9-]+)<',
                "ordinary_income":  r'OrdinaryIncome[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9-]+)<',
                "net_income":       r'NetIncome[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9-]+)<',
                # 前期
                "prev_revenue":     r'NetSales[^>]*contextRef="[^"]*Prior1Year[^"]*"[^>]*>([0-9]+)<',
                "prev_op_income":   r'OperatingIncome[^>]*contextRef="[^"]*Prior1Year[^"]*"[^>]*>([0-9-]+)<',
                # 貸借対照表
                "total_assets":     r'Assets[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9]+)<',
                "equity":           r'NetAssets[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9]+)<',
                # 1株指標
                "eps":              r'BasicEarningsLossPerShare[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9.-]+)<',
                "bps":              r'BookValuePerShare[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9.-]+)<',
                # 株式数
                "shares":           r'NumberOfSharesIssuedAndOutstanding[^>]*contextRef="[^"]*CurrentYear[^"]*"[^>]*>([0-9]+)<',
            }

            for key, pattern in patterns.items():
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    try:
                        financials[key] = float(match.group(1))
                    except ValueError:
                        pass

        except Exception as e:
            logger.warning("XBRL解析失敗: %s", e)

        return financials

    # ===== 派生指標の計算 =====
    def _calc_derived(self, raw: Dict, current_price: float = 0) -> Dict:
        result = {}

        revenue     = raw.get("revenue", 0)
        op_income   = raw.get("operating_income", 0)
        net_income  = raw.get("net_income", 0)
        equity      = raw.get("equity", 0)
        total_assets= raw.get("total_assets", 0)
        prev_rev    = raw.get("prev_revenue", 0)
        prev_op     = raw.get("prev_op_income", 0)
        eps         = raw.get("eps", 0)
        bps         = raw.get("bps", 0)

        # EPS・BPS（XBRLから直接取れる場合）
        result["eps"] = round(eps, 1) if eps else 0.0
        result["bps"] = round(bps, 1) if bps else 0.0

        # YoY成長率
        if prev_rev and prev_rev != 0:
            result["revenue_growth"] = round((revenue - prev_rev) / prev_rev * 100, 1)
        else:
            result["revenue_growth"] = 0.0

        if prev_op and prev_op != 0:
            result["op_income_growth"] = round((op_income - prev_op) / prev_op * 100, 1)
        else:
            result["op_income_growth"] = 0.0

        # 利益率
        if revenue and revenue != 0:
            result["operating_margin"] = round(op_income / revenue * 100, 1)
            result["profit_margin"]    = round(net_income / revenue * 100, 1)
        else:
            result["operating_margin"] = 0.0
            result["profit_margin"]    = 0.0

        # ROE・ROA
        if equity and equity != 0:
            result["roe"] = round(net_income / equity * 100, 1)
        else:
            result["roe"] = 0.0

        if total_assets and total_assets != 0:
            result["roa"] = round(net_income / total_assets * 100, 1)
        else:
            result["roa"] = 0.0

        # 自己資本比率
        if total_assets and total_assets != 0:
            result["equity_ratio"] = round(equity / total_assets * 100, 1)
        else:
            result["equity_ratio"] = 0.0

        # PER・PBR（株価がある場合）
        if current_price > 0 and eps and eps != 0:
            result["per"] = round(current_price / eps, 1)
        else:
            result["per"] = 0.0

        if current_price > 0 and bps and bps != 0:
            result["pbr"] = round(current_price / bps, 2)
        else:
            result["pbr"] = 0.0

        # 生データも保持
        result["revenue"]        = revenue
        result["operating_income"]= op_income
        result["net_income"]     = net_income

        return result

    def _cache_path(self, ticker: str) -> Path:
        """銘柄ごとのキャッシュファイルパスを返す"""
        EDINET_DATA_CACHE.mkdir(exist_ok=True)
        return EDINET_DATA_CACHE / f"{ticker.replace('.', '_')}.json"

    def _load_cache(self, ticker: str) -> Optional[Dict]:
        """キャッシュから財務データを読み込む（90日以内なら有効）"""
        cache_path = self._cache_path(ticker)
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_date = datetime.fromisoformat(cached.get("_cached_at", "2000-01-01"))
            if (datetime.now() - cached_date).days <= CACHE_DAYS:
                logger.info("EDINETキャッシュ使用: %s（%d日前）",
                           ticker, (datetime.now() - cached_date).days)
                data = {k: v for k, v in cached.items() if not k.startswith("_")}
                return data
            logger.info("EDINETキャッシュ期限切れ: %s（%d日前）", ticker, (datetime.now() - cached_date).days)
        except Exception as e:
            logger.warning("キャッシュ読み込み失敗: %s", e)
        return None

    def _save_cache(self, ticker: str, data: Dict) -> None:
        """財務データをキャッシュに保存"""
        try:
            cache_data = {**data, "_cached_at": datetime.now().isoformat()}
            with open(self._cache_path(ticker), "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            logger.info("EDINETキャッシュ保存: %s", ticker)
        except Exception as e:
            logger.warning("キャッシュ保存失敗: %s", e)

    # ===== メイン取得関数 =====
    def get_fundamentals(self, ticker: str, current_price: float = 0) -> Dict:
        """
        日本株のティッカー（例：7203.T）からEDINETで財務データを取得する。
        90日以内のキャッシュがあればキャッシュから返す。
        取得できない場合は空dictを返す（yfinanceへフォールバック）。
        """
        if self._disabled:
            return {}

        # キャッシュチェック（90日以内なら即返す）
        cached = self._load_cache(ticker)
        if cached:
            # 株価は毎日変わるのでPER・PBRだけ再計算
            if current_price > 0:
                eps = cached.get("eps", 0)
                bps = cached.get("bps", 0)
                if eps and eps != 0:
                    cached["per"] = round(current_price / eps, 1)
                if bps and bps != 0:
                    cached["pbr"] = round(current_price / bps, 2)
            return cached

        # コードリスト読み込み
        if not self._load_edinet_codes():
            return {}

        # EDINETコードに変換
        edinet_code = self._sec_code_to_edinet(ticker)
        if not edinet_code:
            logger.warning("EDINETコードが見つかりません: %s", ticker)
            return {}

        # docID取得
        doc_id = self._get_doc_id(edinet_code)
        if not doc_id:
            logger.warning("有価証券報告書が見つかりません: %s (%s)", ticker, edinet_code)
            return {}

        # XBRLダウンロード
        try:
            resp = requests.get(
                DOCUMENT_URL.format(doc_id=doc_id),
                params={"type": 1, "Subscription-Key": self.api_key},
                timeout=30.0
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("XBRL取得失敗 %s: %s", ticker, e)
            return {}

        # 財務データ抽出
        raw = self._extract_financials_from_xbrl(resp.content)
        if not raw:
            logger.warning("財務データ抽出失敗: %s", ticker)
            return {}

        # 派生指標計算
        result = self._calc_derived(raw, current_price)
        logger.info("EDINETファンダメンタル取得成功: %s (ROE:%.1f%% 営業利益率:%.1f%%)",
                    ticker, result.get("roe", 0), result.get("operating_margin", 0))

        # キャッシュ保存（90日間有効）
        self._save_cache(ticker, result)
        return result


def build_edinet_client(config: Dict) -> EdinetClient:
    return EdinetClient(
        api_key=config.get("edinet_api_key", ""),
        timeout=float(config.get("http_timeout_sec", 15.0)),
    )
