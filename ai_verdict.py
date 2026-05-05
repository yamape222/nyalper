import json
import re
import time
from typing import Dict

import requests


def _strip_markdown(text: str) -> str:
    return re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()


def get_ai_verdict(config: Dict, item: Dict, macro: Dict) -> Dict:
    """
    銘柄データ・マクロ情報をGeminiに渡し、買い/売り/様子見の判定と根拠を返す。
    失敗時は {"verdict": "様子見", "reason": "判定不可"} を返す。
    """
    api_key = str(config.get("gemini_api_key", "") or "")
    if not api_key or api_key.startswith("YOUR_"):
        return {"verdict": "様子見", "reason": "Gemini APIキー未設定"}

    if not config.get("news_enabled", True):
        return {"verdict": "様子見", "reason": "ニュース分析無効"}

    ticker = item.get("ticker", "")
    name = item.get("name", "")
    market = item.get("market", "jp")
    score = item.get("score", 0)
    rr = item.get("rr", 0)
    current = item.get("current_price", 0)
    upper = item.get("upper_target", 0)
    lower = item.get("lower_target", 0)
    parts = item.get("score_parts", {})
    sent_score = item.get("sentiment_score", 0)
    sent_reason = item.get("sentiment_reason", "なし")
    rsi = item.get("tech_rsi14", 0)
    macd = item.get("tech_macd", 0)
    macd_sig = item.get("tech_macd_signal", 0)
    sector_focus = "、".join(macro.get("sector_focus", [])) or "なし"
    vix = macro.get("vix", 0)
    currency = "円" if market == "jp" else "ドル"

    tp_pct = (upper - current) / current * 100 if current > 0 else 0
    sl_pct = (current - lower) / current * 100 if current > 0 else 0

    prompt = f"""あなたはプロのスイングトレーダーです。以下のデータをもとに、この銘柄への投資判断を行ってください。

【銘柄情報】
銘柄: {name}（{ticker}）
市場: {"日本株" if market == "jp" else "米国株"}
現在値: {current:,.1f}{currency}

【テクニカル分析】
総合スコア: {score}点（120点満点）
- トレンド: {parts.get("trend", 0)}点 / 50点
- モメンタム: {parts.get("momentum", 0)}点 / 20点
- 過熱感: {parts.get("heat", 0)}点 / 20点
- ファンダメンタル: {parts.get("fundamental", 0)}点 / 20点
RSI: {rsi:.1f}
MACD: {macd:.3f}（シグナル: {macd_sig:.3f}）
リスクリワード: {rr:.2f}
利確目標: {upper:,.1f}{currency}（+{tp_pct:.1f}%）
損切ライン: {lower:,.1f}{currency}（-{sl_pct:.1f}%）

【ニュース・センチメント】
センチメントスコア: {sent_score:+d}点
内容: {sent_reason}

【マクロ環境】
VIX: {vix}
本日の注目セクター: {sector_focus}

以下のJSON形式のみで回答してください（前置き・後置き不要）：
{{
  "verdict": "BUY推奨 / 様子見 / SELL推奨 のいずれか1つ",
  "confidence": "高 / 中 / 低 のいずれか1つ",
  "reasons": ["根拠1（具体的に）", "根拠2", "根拠3"],
  "risk": "主なリスクを1文で"
}}"""

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.get('gemini_model', 'gemini-2.0-flash')}:generateContent",
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=float(config.get("http_timeout_sec", 10.0)),
        )
        resp.raise_for_status()
        body = resp.json()
        text = (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        payload = json.loads(_strip_markdown(text))
        return {
            "verdict": str(payload.get("verdict", "様子見")),
            "confidence": str(payload.get("confidence", "低")),
            "reasons": list(payload.get("reasons", [])),
            "risk": str(payload.get("risk", "")),
        }
    except Exception as e:
        return {"verdict": "様子見", "reason": f"判定エラー: {e}"}
