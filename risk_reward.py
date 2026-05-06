import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def select_by_risk_reward(config: Dict, items: List[Dict], market: str) -> List[Dict]:
    rr_threshold = float(config.get("rr_threshold", 1.5))
    score_threshold = int(config.get("score_threshold", 65))
    atr_mult = (
        float(config.get("atr_multiplier_jp", 2.0))
        if market == "jp"
        else float(config.get("atr_multiplier_us", 2.5))
    )

    picked = []
    for x in items:
        current = float(x["current_price"])
        upper = min(float(x["high20"]), float(x["bb2_upper"]))
        lower = max(current - float(x["atr14"]) * atr_mult, float(x["low20"]))
        risk = current - lower
        reward = upper - current
        if risk <= 0:
            continue
        rr = reward / risk
        logger.info("RR計算 %s: score=%d rr=%.2f (上値=%.1f 現在=%.1f 下値=%.1f) threshold=%.1f",
            x.get("ticker","?"), int(x.get("score",0)), rr, upper, current, lower, rr_threshold)
        if rr >= rr_threshold and int(x["score"]) >= score_threshold:
            y = dict(x)
            y["upper_target"] = upper
            y["lower_target"] = lower
            y["rr"] = rr
            picked.append(y)

    picked.sort(key=lambda z: (z["score"], z["rr"]), reverse=True)
    return picked[:10]
