from datetime import datetime
from pathlib import Path
from typing import Dict, List
import json
import re

from colorama import Fore, Style, init

init(autoreset=True)

def _red(t):    return f"{Fore.RED}{t}{Style.RESET_ALL}"
def _yellow(t): return f"{Fore.YELLOW}{t}{Style.RESET_ALL}"
def _green(t):  return f"{Fore.GREEN}{t}{Style.RESET_ALL}"
def _cyan(t):   return f"{Fore.CYAN}{t}{Style.RESET_ALL}"
def _white(t):  return f"{Fore.WHITE}{Style.BRIGHT}{t}{Style.RESET_ALL}"
def _dim(t):    return f"{Style.DIM}{t}{Style.RESET_ALL}"
def _bold(t):   return f"{Style.BRIGHT}{t}{Style.RESET_ALL}"
def _divider(char="─", width=50): return _dim(char * width)
def _section(title: str): return f"\n{_bold(title)}\n{_divider()}"

NUMS = "①②③④⑤⑥⑦⑧⑨⑩"
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _verdict_style(verdict: str):
    if "BUY" in verdict:    return _green(f"🟢 {verdict}")
    elif "SELL" in verdict: return _red(f"🔴 {verdict}")
    else:                   return _yellow(f"🟡 {verdict}")


def _render_macro(macro: Dict, macro_warning: bool, prev_biz_date: str = "") -> List[str]:
    lines = []
    lines.append(_section("📊 マクロ環境"))
    lines.append(f"🕐 {datetime.now().strftime('%H:%M:%S')}")

    if prev_biz_date:
        lines.append(_dim(f"  📅 直前営業日（{prev_biz_date}）のデータを使用"))

    # ===== 主要指数 現在値・前日比 =====
    lines.append("")
    lines.append(_bold("【主要指数】"))
    indices = macro.get("indices", {})
    index_map = [
        ("nikkei", "日経平均", "¥", 0),
        ("dow",    "NYダウ",   "$", 0),
        ("nasdaq", "NASDAQ",  "",  0),
        ("sp500",  "S&P500",  "",  0),
    ]
    for key, name, prefix, _ in index_map:
        s = indices.get(key, {})
        if not s:
            continue
        last   = s.get("last_close", 0)
        chg    = s.get("change", 0)
        chg_p  = s.get("change_pct", 0)
        arrow  = "▲" if chg >= 0 else "▼"
        color  = _green if chg >= 0 else _red
        val_str = f"{prefix}{last:,.2f}" if key in ("dow", "nasdaq", "sp500") else f"{prefix}{last:,.0f}"
        chg_str = f"{arrow}{abs(chg):,.2f}（{chg_p:+.2f}%）"
        lines.append(f"  {name:<10} {_white(val_str):<20} {color(chg_str)}")

    vix = macro.get("vix", 0)
    lines.append(f"  {'VIX':<10} {_red(str(vix)) if vix >= 25 else _green(str(vix))}")
    lines.append("")

    # ===== コモディティ・債券・為替 =====
    comm = macro.get("commodities", {})
    if comm:
        lines.append(_bold("【コモディティ・債券・為替】"))
        comm_map = [
            ("crude_oil", "WTI原油",   "$", ""),
            ("gold",      "金",        "$", ""),
            ("bond_10y",  "米10年債",  "",  "%"),
            ("usdjpy",    "USD/JPY",   "",  "円"),
        ]
        for key, name, prefix, suffix in comm_map:
            d = comm.get(key, {})
            if not d:
                continue
            last  = d.get("last", 0)
            chg_p = d.get("change_pct", 0)
            arrow = "▲" if chg_p >= 0 else "▼"
            color = _green if chg_p >= 0 else _red
            lines.append(f"  {name:<10} {_white(f'{prefix}{last:,.2f}{suffix}'):<20} {color(f'{arrow}{abs(chg_p):.2f}%')}")
        lines.append("")

    # ===== 日経平均 予想レンジ + CME先物 =====
    nr  = macro.get("nikkei_range", {})
    cme = macro.get("cme_futures", {})
    if nr:
        ru = f'¥{nr["range_upper"]:,.0f}'
        rl = f'¥{nr["range_lower"]:,.0f}'
        rs = f'¥{nr["resistance"]:,.0f}'
        sp = f'¥{nr["support"]:,.0f}'
        at = f'¥{nr["atr"]:,.0f}'
        lines.append(_bold("【日経平均 本日予想レンジ】"))
        if cme:
            cme_last = f'¥{cme["last"]:,.0f}'
            cme_pct  = cme["change_pct"]
            cme_vs   = cme["vs_nikkei_pct"]
            gap_icon = "⬆️ ギャップアップ予想" if cme.get("gap_up") else "⬇️ ギャップダウン予想" if cme.get("gap_up") is False else ""
            cme_color = _green(f'{cme_last} ({cme_pct:+.2f}%)') if cme_pct >= 0 else _red(f'{cme_last} ({cme_pct:+.2f}%)')
            lines.append(f"  CME日経先物: {cme_color}　前日比vs日経: {cme_vs:+.2f}%　{gap_icon}")
        lines.append(f"  上値メド  : {_green(ru)}")
        lines.append(f"  下値メド  : {_red(rl)}")
        lines.append(f"  レジスタンス: {_dim(rs)}（直近5日高値）")
        lines.append(f"  サポート  : {_dim(sp)}（直近5日安値）")
        lines.append(f"  ATR(14)   : {_dim(at)}")
        lines.append("")

    # ===== 資金流入・流出セクター =====
    inflow  = macro.get("inflow_sectors", [])
    outflow = macro.get("outflow_sectors", [])

    if inflow:
        lines.append(_bold("【資金流入セクター TOP3（買われたセクター）】"))
        for i, s in enumerate(inflow[:3], 1):
            sectors_str = " / ".join(s["sectors"])
            etf_str = f"{s['etf']} {s['perf']:+.2f}%"
            lines.append(f"  {i}. {_green(etf_str)}　{sectors_str}")

    if outflow:
        lines.append(_bold("【資金流出セクター TOP3（売られたセクター）】"))
        for i, s in enumerate(outflow[:3], 1):
            sectors_str = " / ".join(s["sectors"])
            etf_str = f"{s['etf']} {s['perf']:+.2f}%"
            lines.append(f"  {i}. {_red(etf_str)}　{sectors_str}")

    # 資金流入・流出AI解説
    flow_ai = macro.get("sector_flow_ai", {})
    if flow_ai and isinstance(flow_ai, dict):
        lines.append("")
        if flow_ai.get("inflow_reason"):
            lines.append(f"  {_cyan('🤖 流入理由：')}{flow_ai['inflow_reason']}")
        if flow_ai.get("outflow_reason"):
            lines.append(f"  {_cyan('🤖 流出理由：')}{flow_ai['outflow_reason']}")
        if flow_ai.get("overall"):
            lines.append(f"  {_yellow('💡 日本株への影響：')}{flow_ai['overall']}")

    lines.append("")

    # ===== 注目セクター =====
    sector_focus = macro.get("sector_focus", [])
    top_etf      = macro.get("top_sector_etf", "-")
    top_perf     = macro.get("top_sector_perf", 0)
    if sector_focus:
        lines.append(f"🔥 本日の注目セクター：{' / '.join(sector_focus)}（{top_etf} {top_perf:+.1f}%）")
    else:
        lines.append("📌 注目セクター：なし")

    sector_ai = macro.get("sector_ai", {})
    if sector_ai and isinstance(sector_ai, dict):
        if sector_ai.get("key_reason"):
            lines.append(f"  {_cyan('🤖 注目理由：')}{sector_ai['key_reason']}")
        if sector_ai.get("summary"):
            lines.append(f"  {_dim(sector_ai['summary'])}")
        if sector_ai.get("caution"):
            caution_str = sector_ai["caution"]
            lines.append(f"  {_yellow(f'⚠️  {caution_str}')}")

    if macro.get("us_market_closed"):
        lines.append(_yellow("🇺🇸 米国市場：本日休場"))

    lines.append("")
    lines.append(_red("⚠️  本日は新規エントリー非推奨（マクロ警告）") if macro_warning else _green("✅ 地合い：新規エントリー可"))
    return lines


def _render_item(item: Dict, i: int, label: str = "") -> List[str]:
    """BUY候補・テクニカル上位共通の銘柄描画"""
    lines = []
    is_jp  = item.get("market", item.get("_market", "jp")) == "jp"
    cur    = f"¥{item['current_price']:,.1f}" if is_jp else f"${item['current_price']:,.2f}"
    parts  = item.get("score_parts", {})
    sent_score  = item.get("sentiment_score", 0)
    sent_reason = item.get("sentiment_reason", "")
    flag   = "🇯🇵" if is_jp else "🇺🇸"

    # RR・利確・損切（あれば）
    upper    = item.get("upper_target", 0)
    lower    = item.get("lower_target", 0)
    rr       = item.get("rr", 0)
    has_rr   = upper > 0 and lower > 0

    # RRがない場合はATRで計算して表示
    if not has_rr:
        current = item.get("current_price", 0)
        atr14   = item.get("atr14", 0)
        high20  = item.get("high20", 0)
        low20   = item.get("low20", 0)
        bb_upper = item.get("bb2_upper", 0)
        if current > 0 and atr14 > 0:
            upper = min(high20, bb_upper) if bb_upper > 0 else high20
            lower = max(current - atr14 * 2.0, low20)
            risk   = current - lower
            reward = upper - current
            rr     = reward / risk if risk > 0 else 0
            has_rr = upper > 0 and lower > 0

    tp_pct = (upper - item['current_price']) / item['current_price'] * 100 if item['current_price'] > 0 and upper > 0 else 0
    sl_pct = (item['current_price'] - lower) / item['current_price'] * 100 if item['current_price'] > 0 and lower > 0 else 0
    upper_str = f"¥{upper:,.1f}" if is_jp else f"${upper:,.2f}"
    lower_str = f"¥{lower:,.1f}" if is_jp else f"${lower:,.2f}"

    num = NUMS[i-1] if i <= len(NUMS) else f"{i}."
    header = f"{num} {item['ticker']}　{item['name']}　{flag}"
    if label:
        header += f"  {_dim(label)}"
    lines.append("")
    lines.append(_bold(header))

    if item.get("earnings_label"):
        lines.append(_yellow(f"  {item['earnings_label']}（決算跨ぎに注意）"))

    # AI判定
    verdict_data = item.get("ai_verdict", {})
    if verdict_data:
        verdict    = verdict_data.get("verdict", "様子見")
        confidence = verdict_data.get("confidence", "低")
        reasons    = verdict_data.get("reasons", [])
        risk       = verdict_data.get("risk", "")
        fund_reason = verdict_data.get("fundamental_reason", "")
        lines.append(f"  🤖 AI判定: {_verdict_style(verdict)}　確信度: {_cyan(confidence)}")
        for r in reasons:
            lines.append(f"  　・{r}")
        if fund_reason:
            lines.append(f"  　📊 ファンダ評価根拠: {_dim(fund_reason)}")
        if risk:
            lines.append(f"  　⚠️  リスク: {_yellow(risk)}")
        lines.append("")

    # ADR表示
    adr = item.get("adr", {})
    if adr:
        adr_pct  = adr.get("change_pct", 0)
        adr_icon = "⬆️" if adr.get("gap_up") else "⬇️" if adr.get("gap_down") else "➡️"
        adr_str  = _green(f'${adr["last"]} ({adr_pct:+.2f}%)') if adr_pct >= 0 else _red(f'${adr["last"]} ({adr_pct:+.2f}%)')
        lines.append(f"  🌐 ADR({adr['ticker']}): {adr_str} {adr_icon}")

    # エントリー・利確・損切
    entry_price = item.get("entry_price", item["current_price"])
    is_jp_entry = item.get("market", "jp") == "jp"
    entry_str   = f"¥{entry_price:,.1f}" if is_jp_entry else f"${entry_price:,.2f}"
    entry_diff  = (entry_price - item["current_price"]) / item["current_price"] * 100 if item["current_price"] > 0 else 0
    entry_note  = f"（ADR予想 {entry_diff:+.1f}%）" if abs(entry_diff) > 0.1 else "（現在値）"

    if has_rr:
        lines.append(f"  📍 現在値: {_white(cur)}")
        lines.append(f"  💰 エントリー目安: {_cyan(entry_str)} {_dim(entry_note)}")
        lines.append(f"  🎯 利確: {_green(upper_str)} (+{tp_pct:.1f}%)")
        lines.append(f"  🛡️  損切: {_red(lower_str)} (-{sl_pct:.1f}%)")

    # ファンダメンタル
    fund = item.get("fundamentals", {})
    if fund:
        lines.append(f"  {_bold('📋 ファンダメンタル')}")
        fa = []
        if fund.get("per") is not None and fund["per"] != 0:   fa.append(f"PER:{fund['per']}倍")
        if fund.get("pbr") is not None and fund["pbr"] != 0:   fa.append(f"PBR:{fund['pbr']}倍")
        if fund.get("roe") is not None and fund["roe"] != 0:   fa.append(f"ROE:{fund['roe']}%")
        if fund.get("eps") is not None and fund["eps"] != 0:   fa.append(f"EPS:{fund['eps']}")
        if fa:
            lines.append(f"  {_dim('  ' + ' / '.join(fa))}")
        else:
            lines.append(f"  {_dim('  （取得データなし）')}")
        fb = []
        if fund.get("revenue_growth") is not None and fund["revenue_growth"] != 0:
            fb.append(f"売上YoY:{fund['revenue_growth']:+.1f}%")
        if fund.get("earnings_growth") is not None and fund["earnings_growth"] != 0:
            fb.append(f"利益YoY:{fund['earnings_growth']:+.1f}%")
        if fund.get("operating_margin") is not None and fund["operating_margin"] != 0:
            fb.append(f"営業利益率:{fund['operating_margin']:.1f}%")
        if fb:
            lines.append(f"  {_dim('  ' + ' / '.join(fb))}")
        fc = []
        if fund.get("dividend_yield") is not None and fund["dividend_yield"] != 0:
            fc.append(f"配当:{fund['dividend_yield']:.1f}%")
        if fund.get("target_upside") is not None and fund["target_upside"] != 0:
            upside = fund["target_upside"]
            fc.append(f"目標株価かい離:{_green(f'+{upside:.1f}%') if upside > 0 else _red(f'{upside:.1f}%')}")
        val = item.get("ai_verdict", {}).get("valuation", "")
        if val:
            val_color = _green(val) if val == "割安" else _red(val) if val == "割高" else _yellow(val)
            fc.append(f"バリュエーション:{val_color}")
        if fc:
            lines.append(f"  {'  '}{' / '.join(fc)}")

    # スコア内訳（180点満点対応・パターン表示）
    t        = parts.get("trend", 0)
    m        = parts.get("momentum", 0)
    h        = parts.get("heat", 0)
    f_score  = parts.get("fundamental", 0)
    v_score  = parts.get("volume", 0)
    pb_score = parts.get("pullback", 0)
    bo_score = parts.get("breakout", 0)
    wk_score = parts.get("weekly", 0)

    score_color = _green(str(item["score"])) if item["score"] >= 60 else _yellow(str(item["score"]))
    rr_str = f"{rr:.2f}" if rr > 0 else "-"

    # パターンラベル（tech_diagから取得）
    diag          = item.get("tech_diag", {})
    pattern       = diag.get("pattern", "ベース")
    drop_pct      = diag.get("drop_pct", 0)
    vol_ratio     = diag.get("vol_ratio", 0)
    weekly_trend  = diag.get("weekly_trend", "")
    weekly_sigs   = diag.get("weekly_signals", [])
    pattern_map = {
        "押し目+BO":      "🔥 押し目+ブレイクアウト",
        "押し目":         "📉 押し目買いパターン",
        "ブレイクアウト": "🚀 ブレイクアウトパターン",
        "ベース":         "📊 ベーススコアのみ",
    }
    pattern_label = pattern_map.get(pattern, f"📊 {pattern}")

    trend_bar = "█" * (t // 10) + "░" * ((50 - t) // 10)
    mom_bar   = "█" * (m // 10) + "░" * ((20 - max(m, 0)) // 10)
    heat_bar  = "█" * (max(h, 0) // 10) + "░" * ((20 - max(h, 0)) // 10)

    lines.append(f"  📊 スコア: {score_color}/100点　RR: {_cyan(rr_str)}　{_yellow(pattern_label)}")
    lines.append(f"  {_dim('  トレンド  ')} {_green(trend_bar)} {t}/50点")
    lines.append(f"  {_dim('  モメンタム')} {_cyan(mom_bar)} {m}/20点")
    lines.append(f"  {_dim('  過熱感   ')} {heat_bar} {h}/20点")
    lines.append(f"  {_dim('  ファンダ ')} F={f_score}点　N={sent_score:+d}点")
    if v_score > 0:
        lines.append(f"  {_dim('  出来高   ')} {_yellow('📊')} +{v_score}点 {_dim(f'（平均比 {vol_ratio:.1f}倍）')}")
    if pb_score > 0:
        drop_str = f"（押し幅:{drop_pct:.1f}%）" if drop_pct > 0 else ""
        lines.append(f"  {_dim('  押し目   ')} {_yellow('▼')} +{pb_score}/50点 {_dim(drop_str)}")
    if bo_score > 0:
        lines.append(f"  {_dim('  BO      ')} {_green('▲')} +{bo_score}/40点")
    # 週足トレンド
    if weekly_trend:
        if isinstance(diag, dict) and diag.get("weekly_warning"):
            wk_color = _red
            wk_label = f"週足: {weekly_trend} ⚠️ 逆張り注意"
        else:
            wk_color = _green if wk_score > 0 else _dim
            wk_label = f"週足: {weekly_trend} ({wk_score:+d}点)"
        if weekly_sigs:
            wk_label += f" [{' / '.join(s for s in weekly_sigs if '⚠️' not in s)}]"
        lines.append(f"  {_dim('  週足    ')} {wk_color(wk_label)}")
    if sent_reason and sent_reason not in ("no news", "news disabled", "gemini api key not configured"):
        lines.append(f"  {_dim(f'  ニュース: {sent_reason[:60]}')}")
    return lines


def _render_sell_item(item: Dict, i: int) -> List[str]:
    """SELL候補専用の描画（BUYとは上下が逆）"""
    lines  = []
    is_jp  = item.get("market", "jp") == "jp"
    cur    = f"¥{item['current_price']:,.1f}" if is_jp else f"${item['current_price']:,.2f}"
    flag   = "🇯🇵" if is_jp else "🇺🇸"
    num    = NUMS[i-1] if i <= len(NUMS) else f"{i}."
    diag   = item.get("tech_diag", {})
    parts  = item.get("score_parts", {})
    rr     = item.get("rr", 0)
    upper  = item.get("upper_target", 0)  # 損切りライン
    lower  = item.get("lower_target", 0)  # 利確ライン

    pattern = diag.get("pattern", "ベース")
    pattern_map = {
        "戻り売り+DC": "🔥 戻り売り+DC",
        "戻り売り":    "📈 戻り売りパターン",
        "デッドクロス":"💀 DCパターン",
        "ベース":      "📊 ベース",
    }
    pattern_label = pattern_map.get(pattern, pattern)
    rally_pct  = diag.get("rally_pct", 0)
    vol_ratio  = diag.get("vol_ratio", 0)
    score_val  = item.get("score", 0)
    rr_str     = f"{rr:.2f}" if rr > 0 else "-"
    score_color = _red(str(score_val)) if score_val >= 80 else _yellow(str(score_val))

    tp_pct = (item["current_price"] - lower) / item["current_price"] * 100 if item["current_price"] > 0 and lower > 0 else 0
    sl_pct = (upper - item["current_price"]) / item["current_price"] * 100 if item["current_price"] > 0 and upper > 0 else 0
    upper_str = f"¥{upper:,.1f}" if is_jp else f"${upper:,.2f}"
    lower_str = f"¥{lower:,.1f}" if is_jp else f"${lower:,.2f}"

    lines.append("")
    lines.append(_bold(f"{num} {item['ticker']}　{item['name']}　{flag}  ⚠️ 貸借銘柄を確認してください"))
    if item.get("earnings_label"):
        lines.append(_yellow(f"  {item['earnings_label']}（空売りは特に注意）"))

    lines.append(f"  📍 現在値: {_white(cur)}")
    if upper > 0 and lower > 0:
        lines.append(f"  💰 空売りエントリー目安: {_white(cur)}")
        lines.append(f"  🎯 利確（下値目標）: {_green(lower_str)} (-{tp_pct:.1f}%)")
        lines.append(f"  🛡️  損切（上値ライン）: {_red(upper_str)} (+{sl_pct:.1f}%)")

    # スコア内訳
    t  = parts.get("trend", 0)
    m  = parts.get("momentum", 0)
    h  = parts.get("heat", 0)
    vs = parts.get("volume", 0)
    pb = parts.get("pullback", 0)
    bo = parts.get("breakout", 0)

    lines.append(f"  📊 SELLスコア: {score_color}/195点　RR: {_cyan(rr_str)}　{_red(pattern_label)}")
    lines.append(f"  {_dim('  トレンド下落')} {'█'*(t//10)}{'░'*((50-t)//10)} {t}/50点")
    lines.append(f"  {_dim('  モメンタム  ')} {'█'*(m//10)}{'░'*((20-max(m,0))//10)} {m}/20点")
    lines.append(f"  {_dim('  過熱感(高)  ')} {'█'*(max(h,0)//10)}{'░'*((20-max(h,0))//10)} {h}/20点")
    if vs > 0:
        lines.append(f"  {_dim('  出来高     ')} {_yellow('📊')} +{vs}点 {_dim(f'（平均比 {vol_ratio:.1f}倍）')}")
    if pb > 0:
        rally_str = f"（戻り幅:{rally_pct:.1f}%）" if rally_pct > 0 else ""
        lines.append(f"  {_dim('  戻り売り   ')} {_red('▲')} +{pb}/50点 {_dim(rally_str)}")
    if bo > 0:
        lines.append(f"  {_dim('  DC        ')} {_red('▼')} +{bo}/40点")
    return lines


def _render_trend_follow(trend_follow: List[Dict]) -> List[str]:
    """🚀 トレンドフォロー枠の表示"""
    lines = []
    lines.append(_section("🚀 トレンドフォロー候補（右肩上がり銘柄）"))
    if not trend_follow:
        lines.append(_dim("  条件一致なし（3ヶ月+10%以上の銘柄なし）"))
        return lines

    nums = "①②③④⑤⑥⑦⑧⑨⑩"
    for i, item in enumerate(trend_follow[:10], 1):
        num    = nums[i-1] if i <= len(nums) else str(i)
        is_jp  = item.get("market", "jp") == "jp"
        flag   = "🇯🇵" if is_jp else "🇺🇸"
        price  = item.get("current_price", 0)
        cur    = f"¥{price:,.1f}" if is_jp else f"${price:,.2f}"
        po     = "✅ PO" if item.get("perfect_order") else ""
        score  = item.get("tf_score", 0)
        r1m    = item.get("ret_1m", 0)
        r3m    = item.get("ret_3m", 0)
        r6m    = item.get("ret_6m", 0)
        sector = item.get("sector", "")

        lines.append("")
        lines.append(_bold(f"{num} {flag}  {item.get('name', '')}（{item.get('ticker', '')}）  {po}"))
        lines.append(f"  📍 現在値: {_white(cur)}  業種: {_dim(sector)}")
        lines.append(f"  📈 リターン: 1ヶ月 {_green(f'+{r1m:.1f}%') if r1m >= 0 else _red(f'{r1m:.1f}%')}  "
                     f"3ヶ月 {_green(f'+{r3m:.1f}%') if r3m >= 0 else _red(f'{r3m:.1f}%')}  "
                     f"6ヶ月 {_green(f'+{r6m:.1f}%') if r6m >= 0 else _red(f'{r6m:.1f}%')}")
        lines.append(f"  🏆 トレンドスコア: {_yellow(str(score))}/100点")
    return lines


def _render_theme_stocks(theme_ai: Dict, jp_sector_strength: Dict) -> List[str]:
    """🎯 テーマ株枠の表示"""
    lines = []
    lines.append(_section("🎯 今日のテーマ株"))

    if not theme_ai:
        lines.append(_dim("  テーマ判定データなし（米国市場休場または取得失敗）"))
        return lines

    theme     = theme_ai.get("theme", "")
    sub_theme = theme_ai.get("sub_theme", "")
    reason    = theme_ai.get("reason", "")
    caution   = theme_ai.get("caution", "")
    sectors   = theme_ai.get("target_sectors_jp", [])

    lines.append(f"  🔥 メインテーマ: {_green(_bold(theme))}")
    if sub_theme:
        lines.append(f"  💡 サブテーマ: {_yellow(sub_theme)}")
    if reason:
        lines.append(f"  📝 {reason}")
    if caution:
        lines.append(f"  ⚠️  {_yellow(caution)}")
    if sectors:
        lines.append(f"  🎯 注目業種: {_cyan('・'.join(sectors))}")

    # 東証セクター強弱
    if jp_sector_strength:
        top3    = jp_sector_strength.get("top3", [])
        bottom3 = jp_sector_strength.get("bottom3", [])
        lines.append("")
        lines.append(_dim("  【東証セクター強弱（前日比）】"))
        for s in top3:
            ret = s["return"]
            lines.append(f"  {_green('▲')} {s['sector']:12s} {_green(f'+{ret:.2f}%')}")
        for s in bottom3:
            ret = s["return"]
            lines.append(f"  {_red('▼')} {s['sector']:12s} {_red(f'{ret:.2f}%')}")

    return lines


def _render_screening(rr_result: Dict, screening: Dict, config: Dict) -> List[str]:
    lines = []

    # ===== プライムBUY候補 =====
    lines.append(_section("📈 BUY候補（プライム）"))
    all_picks = []
    for market in ("jp", "us"):
        for item in rr_result.get(market, []):
            item["_market"] = market
            all_picks.append(item)
    all_picks.sort(key=lambda x: (x["score"], x["rr"]), reverse=True)

    if not all_picks:
        lines.append(_dim("  条件一致なし（スコア/RR未達）"))
    else:
        for i, item in enumerate(all_picks[:5], 1):
            lines.extend(_render_item(item, i))

    # ===== グロースBUY候補 =====
    growth_picks = rr_result.get("growth", [])
    if growth_picks:
        lines.append("")
        lines.append(_section("🌱 BUY候補（グロース）⚠️ 流動性・損切り注意"))
        growth_picks_sorted = sorted(growth_picks,
                                     key=lambda x: (x["score"], x["rr"]), reverse=True)
        for i, item in enumerate(growth_picks_sorted[:5], 1):
            item["_market"] = "jp"
            lines.extend(_render_item(item, i, label="🌱グロース"))

    return lines


def _render_portfolio(portfolio: List[Dict]) -> List[str]:
    lines = []
    lines.append(_section("🗂️  ポートフォリオ診断"))
    if not portfolio:
        lines.append(_dim("  登録なし"))
        return lines
    for item in portfolio:
        is_jp  = item["ticker"].endswith(".T")
        cur    = f"¥{item['current_price']:,.2f}" if is_jp else f"${item['current_price']:,.2f}"
        tp     = f"¥{item.get('take_profit',0):,.2f}" if is_jp else f"${item.get('take_profit',0):,.2f}"
        sl     = f"¥{item.get('stop_loss',0):,.2f}" if is_jp else f"${item.get('stop_loss',0):,.2f}"
        alerts = item.get("alerts", ["-"])
        alert_text = " ".join(alerts) if alerts != ["-"] else ""
        buy_price  = item.get("buy_price", 0)
        pnl_str = ""
        if buy_price and buy_price > 0:
            pnl = (item['current_price'] - buy_price) / buy_price * 100
            pnl_str = _green(f"  +{pnl:.1f}%") if pnl >= 0 else _red(f"  {pnl:.1f}%")
        lines.append("")
        header = f"  {item['ticker']}　{item.get('name','')} {pnl_str}　{alert_text}"
        if "🔴" in alert_text:   lines.append(_red(header))
        elif "🟡" in alert_text: lines.append(_yellow(header))
        else:                    lines.append(_green(header))
        lines.append(f"    📍 現在値: {_white(cur)}　RSI: {item['rsi14']:.1f}")
        lines.append(f"    🎯 利確: {_green(tp)} (+{item.get('tp_dist_pct',0):.1f}%)　🛡️  損切: {_red(sl)} (-{item.get('sl_dist_pct',0):.1f}%)")
    return lines


def _render_backtest(bt: Dict) -> List[str]:
    lines = []
    lines.append(_section("🔬 バックテスト結果"))
    if not bt or bt.get("trades", 0) == 0:
        lines.append(_dim("  データなし"))
        return lines
    win_rate = bt.get("win_rate", 0)
    pf       = bt.get("profit_factor", 0)
    final    = bt.get("final_performance", 0)
    lines.append(f"  📈 総トレード数: {bt.get('trades',0)}回")
    lines.append(f"  🎯 勝率: {_green(f'{win_rate}%') if win_rate >= 50 else _red(f'{win_rate}%')}")
    lines.append(f"  ⚖️  プロフィットファクター: {_green(str(pf)) if pf >= 1.5 else _yellow(str(pf))}")
    lines.append(f"  💹 最終パフォーマンス: {_green(f'+{final:.2%}') if final >= 0 else _red(f'{final:.2%}')}")
    avg_gain_str = f"{bt.get('avg_gain',0):.2%}"
    avg_loss_str = f"{bt.get('avg_loss',0):.2%}"
    lines.append(f"  📊 平均利益: {_green(avg_gain_str)}　平均損失: {_red(avg_loss_str)}")
    return lines


def _build_html(report_payload: Dict) -> str:
    macro         = report_payload.get("macro", {})
    macro_warning = report_payload.get("macro_warning", False)
    rr_result     = report_payload.get("risk_reward", {})
    screening     = report_payload.get("screening", {})
    portfolio     = report_payload.get("portfolio", [])
    bt            = report_payload.get("backtest", {})
    date_str      = report_payload.get("date", "")
    prev_biz_date = report_payload.get("prev_biz_date", "")

    all_picks = []
    for market in ("jp", "us"):
        for item in rr_result.get(market, []):
            item["_market"] = market
            all_picks.append(item)
    all_picks.sort(key=lambda x: (x["score"], x["rr"]), reverse=True)

    pick_tickers = {item["ticker"] for item in all_picks}
    ref_picks = []
    for market in ("jp", "us"):
        for item in screening.get(market, []):
            if item["ticker"] not in pick_tickers and len(ref_picks) < 5:
                item["_market"] = market
                ref_picks.append(item)

    picks_json    = json.dumps(all_picks[:10],  ensure_ascii=False, default=str)
    portfolio_json= json.dumps(portfolio,        ensure_ascii=False, default=str)
    macro_json    = json.dumps(macro,            ensure_ascii=False, default=str)
    ref_json      = json.dumps(ref_picks,        ensure_ascii=False, default=str)
    bt_json       = json.dumps(bt or {},         ensure_ascii=False, default=str)
    mw_js         = "true" if macro_warning else "false"
    has_bt        = bt and bt.get("trades", 0) > 0

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>にゃるぱー秘書 {date_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Zen+Kaku+Gothic+New:wght@400;700;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0a0c10;--bg2:#0f1117;--bg3:#161b22;--border:#21262d;--green:#3fb950;--green-dim:#1a4a23;--red:#f85149;--red-dim:#4a1a1a;--yellow:#e3b341;--yellow-dim:#4a3a1a;--blue:#58a6ff;--blue-dim:#1a2f4a;--cyan:#79c0ff;--text:#e6edf3;--text-dim:#8b949e;--accent:#f0c040;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:'Zen Kaku Gothic New',sans-serif;min-height:100vh;}}
body::before{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(63,185,80,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(63,185,80,.03) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0;}}
.wrap{{max-width:1280px;margin:0 auto;padding:16px;position:relative;z-index:1;}}
header{{display:flex;align-items:center;justify-content:space-between;padding:16px 0 24px;border-bottom:1px solid var(--border);margin-bottom:24px;flex-wrap:wrap;gap:12px;}}
.logo{{display:flex;align-items:center;gap:12px;}}
.logo-icon{{font-size:1.8rem;animation:float 3s ease-in-out infinite;}}
@keyframes float{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-5px)}}}}
.logo-text{{font-size:1.3rem;font-weight:900;color:var(--accent);}}
.logo-sub{{font-size:.65rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;}}
.badge{{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--text-dim);background:var(--bg3);border:1px solid var(--border);padding:5px 12px;border-radius:6px;}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s ease-in-out infinite;}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px;}}
.grid2{{display:grid;grid-template-columns:2fr 1fr;gap:12px;}}
@media(max-width:900px){{.grid2{{grid-template-columns:1fr;}}}}
@media(max-width:600px){{.grid3{{grid-template-columns:1fr 1fr;}}}}
@media(max-width:400px){{.grid3{{grid-template-columns:1fr;}}}}
.card{{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;position:relative;overflow:hidden;}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(63,185,80,.3),transparent);}}
.card-title{{font-size:.62rem;font-family:'JetBrains Mono',monospace;color:var(--text-dim);text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px;}}
.mv{{font-size:1.9rem;font-weight:900;font-family:'JetBrains Mono',monospace;line-height:1;margin-bottom:4px;}}
.tag{{display:inline-flex;align-items:center;gap:3px;font-size:.62rem;padding:2px 8px;border-radius:20px;font-weight:700;font-family:'JetBrains Mono',monospace;}}
.tg{{background:var(--green-dim);color:var(--green);border:1px solid rgba(63,185,80,.3);}}
.tr{{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,81,73,.3);}}
.ty{{background:var(--yellow-dim);color:var(--yellow);border:1px solid rgba(227,179,65,.3);}}
.tb{{background:var(--blue-dim);color:var(--blue);border:1px solid rgba(88,166,255,.3);}}
.sec{{font-size:.62rem;font-family:'JetBrains Mono',monospace;color:var(--text-dim);text-transform:uppercase;letter-spacing:.12em;margin-bottom:12px;display:flex;align-items:center;gap:10px;}}
.sec::after{{content:'';flex:1;height:1px;background:var(--border);}}
.picks{{display:flex;flex-direction:column;gap:10px;}}
.pc{{background:var(--bg3);border:1px solid var(--border);border-radius:10px;padding:14px;border-left:3px solid var(--border);transition:border-color .2s;}}
.pc.buy{{border-left-color:var(--green);}} .pc.sell{{border-left-color:var(--red);}} .pc.watch{{border-left-color:var(--yellow);}}
.ph{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px;}}
.pname{{font-weight:700;font-size:.88rem;}} .pticker{{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--cyan);}}
.pprice{{font-family:'JetBrains Mono',monospace;font-size:.95rem;font-weight:700;text-align:right;}}
.pm{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:10px 0;}}
.met{{background:var(--bg);border-radius:6px;padding:6px 8px;}}
.ml{{font-size:.54rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;margin-bottom:2px;}}
.mv2{{font-size:.78rem;font-weight:700;font-family:'JetBrains Mono',monospace;}}
.aiv{{background:var(--bg);border-radius:8px;padding:10px;margin-top:8px;border:1px solid var(--border);}}
.aih{{display:flex;align-items:center;gap:6px;font-size:.62rem;color:var(--text-dim);margin-bottom:6px;font-family:'JetBrains Mono',monospace;}}
.air{{list-style:none;}} .air li{{font-size:.7rem;color:var(--text-dim);padding:2px 0;display:flex;gap:6px;}}
.air li::before{{content:'·';color:var(--green);font-weight:900;}}
.rsk{{font-size:.66rem;color:var(--yellow);margin-top:6px;}}
.sb{{height:3px;background:var(--border);border-radius:2px;overflow:hidden;margin-top:8px;}}
.sbf{{height:100%;border-radius:2px;}}
.sd{{display:flex;gap:8px;margin-top:4px;flex-wrap:wrap;}}
.sd span{{font-size:.56rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;}}
.pi{{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border);}}
.pi:last-child{{border-bottom:none;}}
.pn{{font-weight:700;font-size:.83rem;}} .pt{{font-family:'JetBrains Mono',monospace;font-size:.63rem;color:var(--cyan);}}
.ptsl{{font-family:'JetBrains Mono',monospace;font-size:.58rem;color:var(--text-dim);text-align:right;margin-top:2px;}}
.btg{{display:grid;grid-template-columns:1fr 1fr;gap:8px;}}
.bti{{background:var(--bg3);border-radius:8px;padding:10px;}}
.btl{{font-size:.58rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;margin-bottom:4px;}}
.btv{{font-size:.95rem;font-weight:700;font-family:'JetBrains Mono',monospace;}}
.green{{color:var(--green);}} .red{{color:var(--red);}} .yellow{{color:var(--yellow);}} .cyan{{color:var(--cyan);}}
.fi{{animation:fi .4s ease forwards;opacity:0;}}
@keyframes fi{{to{{opacity:1;transform:translateY(0)}}from{{opacity:0;transform:translateY(8px)}}}}
.empty{{text-align:center;padding:32px;color:var(--text-dim);font-size:.8rem;}}
::-webkit-scrollbar{{width:4px;}} ::-webkit-scrollbar-track{{background:var(--bg);}} ::-webkit-scrollbar-thumb{{background:var(--border);border-radius:2px;}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="logo">
    <div class="logo-icon">🐱</div>
    <div><div class="logo-text">にゃるぱー秘書</div><div class="logo-sub">NYARUPAR HISHO TRADING ASSISTANT v3.0</div></div>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <div class="badge">{date_str}</div>
    <div class="dot"></div>
  </div>
</header>

<div class="sec">📊 マクロ環境</div>
<div class="grid3" id="mg"></div>
<div id="macro-detail"></div>

<div class="grid2">
  <div>
    <div class="sec">📈 BUY候補</div>
    <div class="picks" id="pl"></div>
  </div>
  <div>
    <div class="sec">🗂️ ポートフォリオ診断</div>
    <div class="card"><div id="port"></div></div>
    {"<div style='margin-top:16px;'><div class='sec'>🔬 バックテスト</div><div class='card' id='bt'></div></div>" if has_bt else ""}
  </div>
</div>
</div>

<script>
const macro={macro_json},picks={picks_json},portfolio={portfolio_json},refs={ref_json},bt={bt_json};
const mw={mw_js},NUMS="①②③④⑤⑥⑦⑧⑨⑩";
const prev_biz_date="{prev_biz_date}";

// マクロ
const vix=macro.vix||0,vok=vix<25;
document.getElementById('mg').innerHTML=`
<div class="card fi"><div class="card-title">💹 VIX 恐怖指数</div>
<div class="mv ${{vok?'green':'red'}}">${{vix}}</div>
<div style="font-size:.7rem;color:var(--text-dim);margin-bottom:8px;">${{vok?'安定域':'警戒域'}}</div>
<span class="tag ${{vok?'tg':'tr'}}">${{vok?'✅ 安定':'⚠️ 警戒'}}</span></div>
<div class="card fi" style="animation-delay:.1s"><div class="card-title">🔥 注目セクター</div>
${{prev_biz_date?`<div style="font-size:.6rem;color:var(--text-dim);margin-bottom:6px;">📅 直前営業日（${{prev_biz_date}}）のデータ</div>`:''}}
<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;">${{(macro.sector_focus||[]).map(s=>`<span class="tag tb">${{s}}</span>`).join('')||'<span style="color:var(--text-dim);font-size:.8rem;">なし</span>'}}</div>
<div style="margin-top:6px;font-size:.68rem;color:var(--text-dim);">${{macro.top_sector_etf||'-'}}　${{macro.top_sector_perf>=0?'+':''}}${{macro.top_sector_perf||0}}%</div>
${{macro.sector_ai&&macro.sector_ai.key_reason?`
<div style="margin-top:8px;background:var(--bg);border-radius:6px;padding:8px;border:1px solid var(--border);">
  <div style="font-size:.6rem;color:var(--text-dim);margin-bottom:4px;font-family:'JetBrains Mono',monospace;">🤖 AI解説</div>
  <div style="font-size:.7rem;color:var(--cyan);line-height:1.6;">${{macro.sector_ai.key_reason}}</div>
  ${{macro.sector_ai.summary?`<div style="font-size:.65rem;color:var(--text-dim);margin-top:4px;line-height:1.5;">${{macro.sector_ai.summary}}</div>`:''}}
  ${{macro.sector_ai.caution?`<div style="font-size:.63rem;color:var(--yellow);margin-top:4px;">⚠️ ${{macro.sector_ai.caution}}</div>`:''}}
</div>`:''}}
</div>
<div class="card fi" style="animation-delay:.2s"><div class="card-title">✅ 地合い判定</div>
<div style="font-size:1rem;font-weight:900;color:var(--${{mw?'red':'green'}});">${{mw?'⚠️ エントリー非推奨':'✅ エントリー可'}}</div>
<div style="font-size:.7rem;color:var(--text-dim);margin-top:6px;">決算跨ぎ銘柄は自動除外済み</div></div>`;

// コモディティ・日経レンジ・資金流入流出
const macroDetail=document.getElementById('macro-detail');
if(macroDetail){{
  const comm=macro.commodities||{{}};
  const nr=macro.nikkei_range||{{}};
  const inflow=macro.inflow_sectors||[];
  const outflow=macro.outflow_sectors||[];
  const flowAi=macro.sector_flow_ai||{{}};
  let html='';

  // コモディティ
  const commMap=[['crude_oil','🛢️ WTI原油','$',''],['gold','🥇 金','$',''],['bond_10y','📊 米10年債','','%'],['usdjpy','💱 USD/JPY','','円']];
  const commItems=commMap.filter(([k])=>comm[k]).map(([k,name,pre,suf])=>{{
    const d=comm[k],chg=d.change_pct||0,up=chg>=0;
    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border);"><span style="font-size:.72rem;">${{name}}</span><span style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;">${{pre}}${{d.last}}${{suf}} <span style="color:var(--${{up?'green':'red'}})">${{up?'▲':'▼'}}${{Math.abs(chg).toFixed(2)}}%</span></span></div>`;
  }});
  if(commItems.length){{
    html+=`<div class="card" style="margin-bottom:12px;"><div class="card-title">💹 コモディティ・債券・為替</div>${{commItems.join('')}}</div>`;
  }}

  // 日経レンジ + CME先物
  const cme=macro.cme_futures||{{}};
  if(nr.range_upper){{
    const cmeHtml=cme.last?`<div style="background:${{cme.gap_up?'var(--green-dim)':'var(--red-dim)'}};border-radius:8px;padding:10px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
      <div><div style="font-size:.58rem;color:var(--text-dim);">🌙 CME日経先物</div><div style="font-family:'JetBrains Mono',monospace;font-weight:700;font-size:1rem;">¥${{Number(cme.last).toLocaleString()}}</div></div>
      <div style="text-align:right;"><div style="font-size:.78rem;font-weight:700;color:var(--${{cme.change_pct>=0?'green':'red'}});">${{cme.change_pct>=0?'▲':'▼'}}${{Math.abs(cme.change_pct).toFixed(2)}}%</div>
      <div style="font-size:.63rem;color:var(--${{cme.vs_nikkei_pct>=0?'green':'red'}});">日経比: ${{cme.vs_nikkei_pct>=0?'+':''}}${{cme.vs_nikkei_pct.toFixed(2)}}%</div>
      <div style="font-size:.68rem;font-weight:700;">${{cme.gap_up?'⬆️ ギャップアップ予想':'⬇️ ギャップダウン予想'}}</div></div></div>`:'';
    html+=`<div class="card" style="margin-bottom:12px;"><div class="card-title">📐 日経平均 本日予想レンジ</div>
    ${{cmeHtml}}
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px;">
      <div style="background:var(--green-dim);border-radius:6px;padding:8px;text-align:center;"><div style="font-size:.58rem;color:var(--text-dim);">上値メド</div><div style="font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--green);">¥${{Number(nr.range_upper).toLocaleString()}}</div></div>
      <div style="background:var(--red-dim);border-radius:6px;padding:8px;text-align:center;"><div style="font-size:.58rem;color:var(--text-dim);">下値メド</div><div style="font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--red);">¥${{Number(nr.range_lower).toLocaleString()}}</div></div>
      <div style="background:var(--bg3);border-radius:6px;padding:8px;text-align:center;"><div style="font-size:.58rem;color:var(--text-dim);">レジスタンス</div><div style="font-family:'JetBrains Mono',monospace;font-size:.78rem;">¥${{Number(nr.resistance).toLocaleString()}}</div></div>
      <div style="background:var(--bg3);border-radius:6px;padding:8px;text-align:center;"><div style="font-size:.58rem;color:var(--text-dim);">サポート</div><div style="font-family:'JetBrains Mono',monospace;font-size:.78rem;">¥${{Number(nr.support).toLocaleString()}}</div></div>
    </div>
    <div style="font-size:.6rem;color:var(--text-dim);margin-top:6px;">ATR(14): ¥${{Number(nr.atr).toLocaleString()}}</div></div>`;
  }}

  // 資金流入・流出
  if(inflow.length||outflow.length){{
    let flowHtml=`<div class="card" style="margin-bottom:12px;"><div class="card-title">💰 資金フロー</div>`;
    if(inflow.length){{
      flowHtml+=`<div style="font-size:.63rem;color:var(--green);font-weight:700;margin-bottom:4px;">▲ 流入TOP3</div>`;
      inflow.forEach((s,i)=>{{
        flowHtml+=`<div style="display:flex;justify-content:space-between;padding:3px 0;"><span style="font-size:.68rem;">${{i+1}}. ${{s.etf}} (${{(s.sectors||[]).join('/')}})</span><span style="font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--green);">+${{s.perf.toFixed(2)}}%</span></div>`;
      }});
    }}
    if(outflow.length){{
      flowHtml+=`<div style="font-size:.63rem;color:var(--red);font-weight:700;margin-top:6px;margin-bottom:4px;">▼ 流出TOP3</div>`;
      outflow.forEach((s,i)=>{{
        flowHtml+=`<div style="display:flex;justify-content:space-between;padding:3px 0;"><span style="font-size:.68rem;">${{i+1}}. ${{s.etf}} (${{(s.sectors||[]).join('/')}})</span><span style="font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--red);">${{s.perf.toFixed(2)}}%</span></div>`;
      }});
    }}
    if(flowAi.inflow_reason){{
      flowHtml+=`<div style="margin-top:8px;background:var(--bg);border-radius:6px;padding:8px;border:1px solid var(--border);font-size:.65rem;color:var(--text-dim);line-height:1.6;">
        ${{flowAi.inflow_reason?`<div style="color:var(--cyan);margin-bottom:4px;">🤖 流入理由: ${{flowAi.inflow_reason}}</div>`:''}}
        ${{flowAi.outflow_reason?`<div style="color:var(--text-dim);margin-bottom:4px;">🤖 流出理由: ${{flowAi.outflow_reason}}</div>`:''}}
        ${{flowAi.overall?`<div style="color:var(--yellow);">💡 日本株への影響: ${{flowAi.overall}}</div>`:''}}
      </div>`;
    }}
    flowHtml+='</div>';
    html+=flowHtml;
  }}

  macroDetail.innerHTML=html;
}}

// 共通カード描画関数
function buildCard(p, i, isRef) {{
  const jp=p.market==='jp',fmt=v=>jp?`¥${{Number(v).toLocaleString()}}`:`$${{Number(v).toFixed(2)}}`;
  const cur=fmt(p.current_price);
  const upper=p.upper_target||0, lower=p.lower_target||0;
  const entry=p.entry_price||p.current_price;
  const up=fmt(upper),lo=fmt(lower),ent=fmt(entry);
  const tpp=entry>0&&upper>0?(((upper)-entry)/entry*100).toFixed(1):0;
  const slp=entry>0&&lower>0?((entry-(lower))/entry*100).toFixed(1):0;
  const entDiff=p.current_price>0?((entry-p.current_price)/p.current_price*100).toFixed(1):0;
  const entNote=Math.abs(entDiff)>0.1?`ADR予想 ${{entDiff>=0?'+':''}}${{entDiff}}%`:'現在値';
  const flag=jp?'🇯🇵':'🇺🇸',pt=p.score_parts||{{}},ss=p.sentiment_score||0;
  const vd=p.ai_verdict||{{}},v=vd.verdict||'';
  const vc=v.includes('BUY')?'buy':v.includes('SELL')?'sell':'watch';
  const vcol=v.includes('BUY')?'green':v.includes('SELL')?'red':'yellow';
  const vi=v.includes('BUY')?'🟢':v.includes('SELL')?'🔴':'🟡';
  const sw=Math.round((p.score/120)*100),sc=p.score>=80?'var(--green)':'var(--yellow)';
  const cc=vd.confidence==='高'?'tg':vd.confidence==='中'?'ty':'tr';
  // ファンダメンタル
  const fd=p.fundamentals||{{}};
  const faItems=[];
  if(fd.per&&fd.per!==0) faItems.push(`PER: <b>${{fd.per}}倍</b>`);
  if(fd.pbr&&fd.pbr!==0) faItems.push(`PBR: <b>${{fd.pbr}}倍</b>`);
  if(fd.roe&&fd.roe!==0) faItems.push(`ROE: <b class="${{fd.roe>=8?'green':'red'}}">${{fd.roe}}%</b>`);
  if(fd.eps&&fd.eps!==0) faItems.push(`EPS: <b>${{fd.eps}}</b>`);
  if(fd.revenue_growth&&fd.revenue_growth!==0) faItems.push(`売上YoY: <b class="${{fd.revenue_growth>=0?'green':'red'}}">${{fd.revenue_growth>=0?'+':''}}${{fd.revenue_growth}}%</b>`);
  if(fd.earnings_growth&&fd.earnings_growth!==0) faItems.push(`利益YoY: <b class="${{fd.earnings_growth>=0?'green':'red'}}">${{fd.earnings_growth>=0?'+':''}}${{fd.earnings_growth}}%</b>`);
  if(fd.operating_margin&&fd.operating_margin!==0) faItems.push(`営業利益率: <b>${{fd.operating_margin}}%</b>`);
  if(fd.dividend_yield&&fd.dividend_yield!==0) faItems.push(`配当: <b>${{fd.dividend_yield}}%</b>`);
  if(fd.target_upside&&fd.target_upside!==0) faItems.push(`目標株価: <b class="${{fd.target_upside>=0?'green':'red'}}">${{fd.target_upside>=0?'+':''}}${{fd.target_upside}}%</b>`);
  const valStr=vd.valuation||'';
  if(valStr) faItems.push(`評価: <b class="${{valStr==='割安'?'green':valStr==='割高'?'red':'yellow'}}">${{valStr}}</b>`);
  const fundHtml=`<div style="background:var(--bg);border-radius:8px;padding:8px 10px;margin-top:8px;border:1px solid var(--border);"><div style="font-size:.58rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;margin-bottom:6px;">📋 ファンダメンタル</div>${{faItems.length?`<div style="display:flex;flex-wrap:wrap;gap:6px 14px;">${{faItems.map(f=>`<span style="font-size:.68rem;color:var(--text-dim);">${{f}}</span>`).join('')}}</div>`:'<span style="font-size:.63rem;color:var(--text-dim);">取得データなし</span>'}}</div>`;
  // ファンダ根拠
  const freason=vd.fundamental_reason?`<div style="font-size:.63rem;color:var(--cyan);margin-top:4px;">📊 ファンダ根拠: ${{vd.fundamental_reason}}</div>`:'';
  // ADR
  const adr=p.adr||{{}};
  const adrHtml=adr.last?`<div style="background:${{adr.gap_up?'var(--green-dim)':adr.gap_down?'var(--red-dim)':'var(--bg3)'}};border-radius:8px;padding:8px 10px;margin-top:8px;display:flex;justify-content:space-between;align-items:center;">
    <div style="font-size:.63rem;color:var(--text-dim);">🌐 ADR(${{adr.ticker}})</div>
    <div style="text-align:right;">
      <span style="font-family:'JetBrains Mono',monospace;font-weight:700;font-size:.78rem;">$${{adr.last}}</span>
      <span style="font-size:.68rem;color:var(--${{adr.change_pct>=0?'green':'red'}});margin-left:6px;">${{adr.change_pct>=0?'▲':'▼'}}${{Math.abs(adr.change_pct).toFixed(2)}}%</span>
      <span style="font-size:.72rem;margin-left:6px;">${{adr.gap_up?'⬆️ ギャップアップ示唆':adr.gap_down?'⬇️ ギャップダウン示唆':'➡️ 中立'}}</span>
    </div>
  </div>`:'';
  // スコアバー
  const t=pt.trend||0,m=pt.momentum||0,h=pt.heat||0,f=pt.fundamental||0;
  const pb=pt.pullback||0,bo=pt.breakout||0,vs=pt.volume||0,wk=pt.weekly||0;
  const patternMap={{'押し目+BO':'🔥 押し目+BO','押し目':'📉 押し目','ブレイクアウト':'🚀 BO','ベース':'📊 ベース'}};
  const diag=p.tech_diag||{{}};
  const pattern=diag.pattern||'ベース';
  const dropPct=diag.drop_pct||0;
  const volRatio=diag.vol_ratio||0;
  const weeklyTrend=diag.weekly_trend||'';
  const weeklyScore=diag.weekly_score||0;
  const weeklySigs=(diag.weekly_signals||[]).join(' / ');
  const patLabel=patternMap[pattern]||pattern;
  const patColor={{'押し目+BO':'var(--yellow)','押し目':'var(--cyan)','ブレイクアウト':'var(--green)','ベース':'var(--text-dim)'}}[pattern]||'var(--text-dim)';
  const wkColor=weeklyScore>0?'var(--green)':weeklyScore<0?'var(--red)':'var(--text-dim)';
  const scoreBar=`<div style="margin-top:8px;background:var(--bg);border-radius:6px;padding:8px;border:1px solid var(--border);">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <span style="font-size:.58rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;">📊 スコア内訳 ${{p.score}}/100点　RR:${{(p.rr||0)>0?(p.rr||0).toFixed(2):'-'}}</span>
      <span style="font-size:.6rem;font-weight:700;color:${{patColor}};">${{patLabel}}</span>
    </div>
    <div style="display:flex;flex-direction:column;gap:3px;">
      <div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">トレンド</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(t/50*100,100)}}%;height:100%;background:var(--green);border-radius:3px;"></div></div><span style="font-size:.55rem;color:var(--green);width:36px;">${{t}}/50</span></div>
      <div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">モメンタム</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(Math.max(m,0)/20*100,100)}}%;height:100%;background:var(--cyan);border-radius:3px;"></div></div><span style="font-size:.55rem;color:var(--cyan);width:36px;">${{m}}/20</span></div>
      <div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">過熱感</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(Math.max(h,0)/20*100,100)}}%;height:100%;background:var(--yellow);border-radius:3px;"></div></div><span style="font-size:.55rem;color:var(--yellow);width:36px;">${{h}}/20</span></div>
      <div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">ファンダ</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(Math.max(f,0)/20*100,100)}}%;height:100%;background:var(--blue);border-radius:3px;"></div></div><span style="font-size:.55rem;color:var(--blue);width:36px;">F=${{f}}</span></div>
      ${{vs>0?`<div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">出来高</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(vs/15*100,100)}}%;height:100%;background:var(--yellow);border-radius:3px;"></div></div><span style="font-size:.55rem;color:var(--yellow);width:54px;">+${{vs}} (${{volRatio.toFixed(1)}}x)</span></div>`:''}}<br>
      ${{pb>0?`<div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">押し目</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(pb/50*100,100)}}%;height:100%;background:var(--yellow);border-radius:3px;"></div></div><span style="font-size:.55rem;color:var(--yellow);width:36px;">+${{pb}}</span></div>`:''}}<br>
      ${{bo>0?`<div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">BO</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(bo/40*100,100)}}%;height:100%;background:var(--green);border-radius:3px;"></div></div><span style="font-size:.55rem;color:var(--green);width:36px;">+${{bo}}</span></div>`:''}}<br>
      ${{weeklyTrend?`<div style="display:flex;align-items:center;gap:6px;"><span style="font-size:.55rem;color:var(--text-dim);width:52px;">週足</span><div style="flex:1;height:6px;background:var(--border);border-radius:3px;"><div style="width:${{Math.min(Math.abs(wk)/20*100,100)}}%;height:100%;background:${{wkColor}};border-radius:3px;"></div></div><span style="font-size:.55rem;color:${{wkColor}};width:90px;">${{weeklyScore>=0?'+':''}}${{weeklyScore}} ${{weeklyTrend}}</span></div>`:''}}<br>
    </div>
    <div style="font-size:.55rem;color:var(--text-dim);margin-top:4px;">N(ニュース): <span style="color:var(--${{ss>=0?'green':'red'}})">${{ss>=0?'+':''}}${{ss}}点</span>${{dropPct>0?`　押し幅: <span style="color:var(--yellow);">${{dropPct.toFixed(1)}}%</span>`:''}}</div>
    ${{weeklySigs?`<div style="font-size:.52rem;color:var(--text-dim);margin-top:2px;">週足シグナル: ${{weeklySigs}}</div>`:''}}
  </div>`;
  const refLabel=isRef?`<span class="tag ty" style="font-size:.54rem;">※RR未達</span>`:'';
  const earningsLabel=p.earnings_label?`<div style="font-size:.63rem;color:var(--yellow);margin-top:4px;">${{p.earnings_label}}（決算跨ぎに注意）</div>`:'';
  const d=document.createElement('div');
  d.className=`pc ${{isRef?'watch':vc}} fi`;d.style.animationDelay=`${{i*.1}}s`;
  d.innerHTML=`<div class="ph"><div><div style="font-size:.58rem;color:var(--text-dim);">${{NUMS[i]}} ${{flag}} ${{refLabel}}</div><div class="pname">${{p.name}}</div><div class="pticker">${{p.ticker}}</div></div><div><div class="pprice">${{cur}}</div><div style="text-align:right;margin-top:4px;"><span class="tag t${{isRef?'y':vcol[0]}}">${{isRef?'参考':(vi+' '+(v||'様子見'))}}</span></div></div></div>
${{earningsLabel}}
${{upper>0&&lower>0?`<div class="pm"><div class="met"><div class="ml">💰 エントリー</div><div class="mv2 cyan">${{ent}} <span style="font-size:.52rem;color:var(--text-dim);">${{entNote}}</span></div></div></div>
<div class="pm"><div class="met"><div class="ml">🎯 利確</div><div class="mv2 green">${{up}} <span style="font-size:.58rem;">+${{tpp}}%</span></div></div><div class="met"><div class="ml">🛡️ 損切</div><div class="mv2 red">${{lo}} <span style="font-size:.58rem;">-${{slp}}%</span></div></div><div class="met"><div class="ml">⚖️ RR</div><div class="mv2 cyan">${{(p.rr||0).toFixed(2)}}</div></div></div>`:'<div style="padding:4px 0;font-size:.63rem;color:var(--text-dim);">📍 現在値: ${{cur}}</div>'}}
${{adrHtml}}
${{v&&!isRef?`<div class="aiv"><div class="aih">🤖 AI分析　<span class="tag ${{cc}}" style="font-size:.54rem;">確信度:${{vd.confidence||'-'}}</span></div><ul class="air">${{(vd.reasons||[]).map(r=>`<li>${{r}}</li>`).join('')}}</ul>${{freason}}${{vd.risk?`<div class="rsk">⚠️ ${{vd.risk}}</div>`:''}}</div>`:''}}
${{fundHtml}}
${{scoreBar}}`;
  return d;
}}

// BUY候補
const pl=document.getElementById('pl');
if(!picks.length){{
  pl.innerHTML='<div class="empty">条件一致なし（スコア/RR未達）</div>';
}}else{{
  picks.forEach((p,i)=>{{ pl.appendChild(buildCard(p,i,false)); }});
}}

// テクニカル上位（参考）
const refsEl=document.getElementById('refs');
if(refsEl){{
  if(!refs.length){{
    refsEl.innerHTML='<div class="empty">データなし</div>';
  }}else{{
    refs.forEach((p,i)=>{{ refsEl.appendChild(buildCard(p,i,true)); }});
  }}
}}

// ポートフォリオ
const port=document.getElementById('port');
if(!portfolio.length){{port.innerHTML='<div class="empty">登録なし</div>';}}
else{{portfolio.forEach(p=>{{
  const jp=p.ticker.endsWith('.T'),fmt=v=>jp?`¥${{Number(v).toLocaleString()}}`:`$${{Number(v).toFixed(2)}}`;
  const al=(p.alerts||['-']).join(' '),hd=al.includes('🔴'),hw=al.includes('🟡');
  const col=hd?'red':hw?'yellow':'green';
  let pnl='';if(p.buy_price>0){{const pv=((p.current_price-p.buy_price)/p.buy_price*100).toFixed(1);pnl=`<span class="${{pv>=0?'green':'red'}}">${{pv>=0?'+':''}}${{pv}}%</span>`;}}
  const d=document.createElement('div');d.className='pi';
  d.innerHTML=`<div><div class="pn ${{col}}">${{p.name||p.ticker}}</div><div class="pt">${{p.ticker}}</div></div><div><div style="text-align:right;font-family:'JetBrains Mono',monospace;font-size:.82rem;font-weight:700;">${{fmt(p.current_price)}} ${{pnl}}</div><div style="text-align:right;font-size:.72rem;margin-top:2px;">${{al}}</div><div class="ptsl">TP +${{(p.tp_dist_pct||0).toFixed(1)}}% / SL -${{(p.sl_dist_pct||0).toFixed(1)}}%</div></div>`;
  port.appendChild(d);
}});}}

// バックテスト
const btel=document.getElementById('bt');
if(btel&&bt.trades>0){{
  const wc=bt.win_rate>=50?'green':'red',pc=bt.profit_factor>=1.5?'green':'yellow',fc=bt.final_performance>=0?'green':'red';
  btel.innerHTML=`<div class="btg"><div class="bti"><div class="btl">総トレード数</div><div class="btv">${{bt.trades}}回</div></div><div class="bti"><div class="btl">勝率</div><div class="btv ${{wc}}">${{bt.win_rate}}%</div></div><div class="bti"><div class="btl">PF</div><div class="btv ${{pc}}">${{bt.profit_factor}}</div></div><div class="bti"><div class="btl">最終成績</div><div class="btv ${{fc}}">${{bt.final_performance>=0?'+':''}}${{(bt.final_performance*100).toFixed(2)}}%</div></div></div>`;
}}
</script>
</body>
</html>"""


def render_report(config: Dict, report_payload: Dict) -> None:
    lines_plain = []

    date_str = report_payload["date"]
    header = f"{'='*50}\n🐱 にゃるぱー秘書　Daily Report　{date_str}\n{'='*50}"
    print(_bold(_cyan(header)))
    lines_plain += [f"{'='*50}", f"にゃるぱー秘書 Daily Report {date_str}", f"{'='*50}"]

    if report_payload.get("is_jp_holiday"):
        msg = "🇯🇵 本日は日本祝日：マクロ分析＋米国株のみ実行"
        print(_yellow(msg))
        lines_plain.append(msg)

    def _safe_render(fn, *args, section_name="セクション"):
        """エラーが出ても他のセクションに影響しないラッパー"""
        try:
            return fn(*args)
        except Exception as e:
            err_msg = f"  ⚠️ [{section_name}] 表示エラー: {e}"
            logger.warning(err_msg)
            return [err_msg]

    for line in _safe_render(
        _render_macro,
        report_payload["macro"],
        report_payload.get("macro_warning", False),
        report_payload.get("prev_biz_date", ""),
        section_name="マクロ分析"
    ):
        print(line); lines_plain.append(line)

    # テーマ株・東証セクター強弱
    theme_ai           = report_payload.get("theme_ai", {})
    jp_sector_strength = report_payload.get("jp_sector_strength", {})
    if theme_ai or jp_sector_strength:
        for line in _safe_render(
            _render_theme_stocks, theme_ai, jp_sector_strength,
            section_name="テーマ株"
        ):
            print(line); lines_plain.append(line)

    for line in _safe_render(
        _render_screening,
        report_payload.get("risk_reward", {}),
        report_payload.get("screening", {}),
        config,
        section_name="BUY候補"
    ):
        print(line); lines_plain.append(line)

    # トレンドフォロー
    trend_follow = report_payload.get("trend_follow", [])
    if trend_follow:
        for line in _safe_render(
            _render_trend_follow, trend_follow,
            section_name="トレンドフォロー"
        ):
            print(line); lines_plain.append(line)

    for line in _safe_render(
        _render_portfolio, report_payload.get("portfolio", []),
        section_name="ポートフォリオ"
    ):
        print(line); lines_plain.append(line)

    if report_payload.get("backtest"):
        for line in _safe_render(
            _render_backtest, report_payload["backtest"],
            section_name="バックテスト"
        ):
            print(line); lines_plain.append(line)

    footer = f"\n{'='*50}"
    print(_dim(footer))
    lines_plain.append(footer)

    out_dir = Path(__file__).resolve().parent / "outputs"
    out_dir.mkdir(exist_ok=True)
    date_tag = datetime.now().strftime('%Y%m%d')

    clean = [ANSI_ESCAPE.sub("", l) for l in lines_plain]
    (out_dir / f"daily_report_{date_tag}.txt").write_text("\n".join(clean), encoding="utf-8")

    # JSONレポート保存（アプリ内表示用）
    json_path = out_dir / f"daily_report_{date_tag}.json"
    json_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8"
    )

    html_path = out_dir / "dashboard.html"
    html_path.write_text(_build_html(report_payload), encoding="utf-8")
    print(_green(f"\n🌐 ダッシュボード生成完了: {html_path}"))
