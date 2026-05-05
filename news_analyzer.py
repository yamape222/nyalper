import json
import re
import time
from urllib.parse import quote_plus
from typing import Dict, List, Tuple

import feedparser
import requests


def _fetch_news_text(name: str, ticker: str, max_items: int) -> List[str]:
    query = quote_plus(f"{name} {ticker}")
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja")
    texts = []
    for entry in feed.entries[:max_items]:
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        texts.append(f"{title}\n{summary}")
    return texts


def _strip_markdown_codeblock(text: str) -> str:
    cleaned = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()
    return cleaned


def analyze_sentiment(config: Dict, name: str, ticker: str) -> Tuple[int, str]:
    if not config.get("news_enabled", True):
        return 0, "news disabled"

    articles = _fetch_news_text(name, ticker, int(config.get("news_max_articles", 5)))
    if not articles:
        return 0, "no news"

    api_key = str(config.get("gemini_api_key", "") or "")
    if not api_key or api_key.startswith("YOUR_"):
        return 0, "gemini api key not configured"

    scores: List[int] = []
    reasons: List[str] = []
    for article in articles:
        prompt = (
            f"以下のニュースを読み、{name}（{ticker}）の株価への短期的影響を判定してください。\n"
            f"ニュース：{article}\n"
            "以下のJSON形式のみで回答してください（前置き不要）：\n"
            '{"sentiment":"positive/negative/neutral","score":-10,"reason":"理由を1文で"}'
        )
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
            payload = json.loads(_strip_markdown_codeblock(text))
            score = int(payload.get("score", 0))
            scores.append(max(-10, min(10, score)))
            reasons.append(str(payload.get("reason", "")))
        except Exception:
            scores.append(0)
            reasons.append("fallback due to API/parse error")
        time.sleep(float(config.get("gemini_wait_sec", 4.0)))

    avg_score = int(round(sum(scores) / len(scores))) if scores else 0
    avg_score = max(-10, min(10, avg_score))
    return avg_score, " / ".join(reasons[:2]) if reasons else "no reason"
