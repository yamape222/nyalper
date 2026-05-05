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

    # ===== 日経平均 予想レンジ =====
    nr = macro.get("nikkei_range", {})
    if nr:
        ru = f'¥{nr["range_upper"]:,.0f}'
        rl = f'¥{nr["range_lower"]:,.0f}'
        rs = f'¥{nr["resistance"]:,.0f}'
        sp = f'¥{nr["support"]:,.0f}'
        at = f'¥{nr["atr"]:,.0f}'
        lines.append(_bold("【日経平均 本日予想レンジ】"))
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


def _render_screening(rr_result: Dict, screening: Dict, config: Dict) -> List[str]:
    lines = []
    lines.append(_section("📈 BUY候補"))
    all_picks = []
    for market in ("jp", "us"):
        for item in rr_result.get(market, []):
            item["_market"] = market
            all_picks.append(item)
    all_picks.sort(key=lambda x: (x["score"], x["rr"]), reverse=True)

    if not all_picks:
        lines.append(_dim("  条件一致なし（スコア/RR未達）"))
        lines.append("")
        lines.append(_dim("  📋 参考：テクニカル上位銘柄"))
        for market in ("jp", "us"):
            for item in screening.get(market, [])[:3]:
                parts = item.get("score_parts", {})
                flag = "🇯🇵" if market == "jp" else "🇺🇸"
                lines.append(f"  {flag} {item['ticker']} {item['name']} score={_yellow(str(item['score']))} T/M/H/F={parts.get('trend',0)}/{parts.get('momentum',0)}/{parts.get('heat',0)}/{parts.get('fundamental',0)}")
        return lines

    for i, item in enumerate(all_picks[:10], 1):
        is_jp = item["_market"] == "jp"
        cur    = f"¥{item['current_price']:,.1f}" if is_jp else f"${item['current_price']:,.2f}"
        upper  = f"¥{item.get('upper_target',0):,.1f}" if is_jp else f"${item.get('upper_target',0):,.2f}"
        lower  = f"¥{item.get('lower_target',0):,.1f}" if is_jp else f"${item.get('lower_target',0):,.2f}"
        tp_pct = (item.get('upper_target',0) - item['current_price']) / item['current_price'] * 100 if item['current_price'] > 0 else 0
        sl_pct = (item['current_price'] - item.get('lower_target',0)) / item['current_price'] * 100 if item['current_price'] > 0 else 0
        parts      = item.get("score_parts", {})
        sent_score = item.get("sentiment_score", 0)
        sent_reason= item.get("sentiment_reason", "")
        flag       = "🇯🇵" if is_jp else "🇺🇸"

        lines.append("")
        lines.append(_bold(f"{NUMS[i-1]} {item['ticker']}　{item['name']}　{flag}"))

        verdict_data = item.get("ai_verdict", {})
        if verdict_data:
            verdict    = verdict_data.get("verdict", "様子見")
            confidence = verdict_data.get("confidence", "低")
            reasons    = verdict_data.get("reasons", [])
            risk       = verdict_data.get("risk", "")
            lines.append(f"  🤖 AI判定: {_verdict_style(verdict)}　確信度: {_cyan(confidence)}")
            for r in reasons:
                lines.append(f"  　・{r}")
            if risk:
                lines.append(f"  　⚠️  リスク: {_yellow(risk)}")
            lines.append("")

        sent_icon = "💬 ポジティブ" if sent_score > 0 else "💬 ネガティブ" if sent_score < 0 else "💬 中立／なし"
        lines.append(f"  {_cyan(sent_icon)}")
        if sent_reason and sent_reason not in ("no news", "news disabled", "gemini api key not configured"):
            lines.append(f"  {_dim(sent_reason[:60])}")

        lines.append(f"  📍 現在値: {_white(cur)}")
        lines.append(f"  💰 エントリー目安: {_white(cur)}")
        lines.append(f"  🎯 利確: {_green(upper)} (+{tp_pct:.1f}%)")
        lines.append(f"  🛡️  損切: {_red(lower)} (-{sl_pct:.1f}%)")

        # ファンダメンタル詳細表示
        fund = item.get("fundamentals", {})
        if fund:
            lines.append(f"  {_bold('📋 ファンダメンタル')}")
            fa = []
            if fund.get("per"):    fa.append(f"PER:{fund['per']}倍")
            if fund.get("pbr"):    fa.append(f"PBR:{fund['pbr']}倍")
            if fund.get("roe"):    fa.append(f"ROE:{fund['roe']}%")
            if fund.get("eps"):    fa.append(f"EPS:{fund['eps']}")
            if fa:
                lines.append(f"  {_dim('  ' + ' / '.join(fa))}")
            fb = []
            if fund.get("revenue_growth"):   fb.append(f"売上YoY:{fund['revenue_growth']:+.1f}%")
            if fund.get("earnings_growth"):  fb.append(f"利益YoY:{fund['earnings_growth']:+.1f}%")
            if fund.get("operating_margin"): fb.append(f"営業利益率:{fund['operating_margin']:.1f}%")
            if fb:
                lines.append(f"  {_dim('  ' + ' / '.join(fb))}")
            fc = []
            if fund.get("dividend_yield"):  fc.append(f"配当:{fund['dividend_yield']:.1f}%")
            if fund.get("target_upside"):
                upside = fund['target_upside']
                fc.append(f"目標株価かい離:{_green(f'+{upside:.1f}%') if upside > 0 else _red(f'{upside:.1f}%')}")
            if fund.get("valuation") or item.get("ai_verdict", {}).get("valuation"):
                val = item.get("ai_verdict", {}).get("valuation", "")
                val_color = _green(val) if val == "割安" else _red(val) if val == "割高" else _yellow(val)
                fc.append(f"バリュエーション:{val_color}")
            if fc:
                lines.append(f"  {'  '}{' / '.join(fc)}")

        score_color = _green(str(item['score'])) if item['score'] >= 80 else _yellow(str(item['score']))
        rr_str = f"{item['rr']:.2f}"
        lines.append(f"  📊 スコア: {score_color}点　RR: {_cyan(rr_str)}　T:{parts.get('trend',0)} M:{parts.get('momentum',0)} H:{parts.get('heat',0)} F:{parts.get('fundamental',0)} N:{sent_score:+d}")
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

    ref_picks = []
    for market in ("jp", "us"):
        for item in screening.get(market, [])[:3]:
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

// BUY候補
const pl=document.getElementById('pl');
if(!picks.length){{
  let h='<div class="empty">条件一致なし（スコア/RR未達）<br><br>📋 参考：テクニカル上位銘柄</div>';
  refs.forEach(p=>{{const pt=p.score_parts||{{}};h+=`<div class="pc watch fi"><div class="ph"><div><div class="pname">${{p.name}}</div><div class="pticker">${{p.ticker}}</div></div><span class="tag ty">参考</span></div><div class="sd"><span>Score:${{p.score}}</span><span>T:${{pt.trend||0}}</span><span>M:${{pt.momentum||0}}</span><span>H:${{pt.heat||0}}</span><span>F:${{pt.fundamental||0}}</span></div></div>`;}});
  pl.innerHTML=h;
}}else{{
  picks.forEach((p,i)=>{{
    const jp=p.market==='jp',fmt=v=>jp?`¥${{Number(v).toLocaleString()}}`:`$${{Number(v).toFixed(2)}}`;
    const cur=fmt(p.current_price),up=fmt(p.upper_target||0),lo=fmt(p.lower_target||0);
    const tpp=p.current_price>0?(((p.upper_target||0)-p.current_price)/p.current_price*100).toFixed(1):0;
    const slp=p.current_price>0?((p.current_price-(p.lower_target||0))/p.current_price*100).toFixed(1):0;
    const flag=jp?'🇯🇵':'🇺🇸',pt=p.score_parts||{{}},ss=p.sentiment_score||0;
    const vd=p.ai_verdict||{{}},v=vd.verdict||'';
    const vc=v.includes('BUY')?'buy':v.includes('SELL')?'sell':'watch';
    const vcol=v.includes('BUY')?'green':v.includes('SELL')?'red':'yellow';
    const vi=v.includes('BUY')?'🟢':v.includes('SELL')?'🔴':'🟡';
    const sw=Math.round((p.score/120)*100),sc=p.score>=80?'var(--green)':'var(--yellow)';
    const cc=vd.confidence==='高'?'tg':vd.confidence==='中'?'ty':'tr';
    const d=document.createElement('div');
    d.className=`pc ${{vc}} fi`;d.style.animationDelay=`${{i*.1}}s`;
    d.innerHTML=`<div class="ph"><div><div style="font-size:.58rem;color:var(--text-dim);">${{NUMS[i]}} ${{flag}}</div><div class="pname">${{p.name}}</div><div class="pticker">${{p.ticker}}</div></div><div><div class="pprice">${{cur}}</div><div style="text-align:right;margin-top:4px;"><span class="tag t${{vcol[0]}}">${{vi}} ${{v||'様子見'}}</span></div></div></div>
<div class="pm"><div class="met"><div class="ml">🎯 利確</div><div class="mv2 green">${{up}} <span style="font-size:.58rem;">+${{tpp}}%</span></div></div><div class="met"><div class="ml">🛡️ 損切</div><div class="mv2 red">${{lo}} <span style="font-size:.58rem;">-${{slp}}%</span></div></div><div class="met"><div class="ml">⚖️ RR</div><div class="mv2 cyan">${{(p.rr||0).toFixed(2)}}</div></div></div>
${{v?`<div class="aiv"><div class="aih">🤖 AI分析　<span class="tag ${{cc}}" style="font-size:.54rem;">確信度:${{vd.confidence||'-'}}</span></div><ul class="air">${{(vd.reasons||[]).map(r=>`<li>${{r}}</li>`).join('')}}</ul>${{vd.risk?`<div class="rsk">⚠️ ${{vd.risk}}</div>`:''}}</div>`:''}}
<div class="sb"><div class="sbf" style="width:${{sw}}%;background:${{sc}};"></div></div>
<div class="sd"><span>Score:${{p.score}}/120</span><span>T:${{pt.trend||0}}</span><span>M:${{pt.momentum||0}}</span><span>H:${{pt.heat||0}}</span><span>F:${{pt.fundamental||0}}</span><span style="color:var(--${{ss>=0?'green':'red'}})">N:${{ss>=0?'+':''}}${{ss}}</span></div>`;
    pl.appendChild(d);
  }});
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

    for line in _render_macro(report_payload["macro"], report_payload.get("macro_warning", False), report_payload.get("prev_biz_date", "")):
        print(line); lines_plain.append(line)
    for line in _render_screening(report_payload.get("risk_reward", {}), report_payload.get("screening", {}), config):
        print(line); lines_plain.append(line)
    for line in _render_portfolio(report_payload.get("portfolio", [])):
        print(line); lines_plain.append(line)
    if report_payload.get("backtest"):
        for line in _render_backtest(report_payload["backtest"]):
            print(line); lines_plain.append(line)

    footer = f"\n{'='*50}"
    print(_dim(footer))
    lines_plain.append(footer)

    out_dir = Path(__file__).resolve().parent / "outputs"
    out_dir.mkdir(exist_ok=True)
    date_tag = datetime.now().strftime('%Y%m%d')

    clean = [ANSI_ESCAPE.sub("", l) for l in lines_plain]
    (out_dir / f"daily_report_{date_tag}.txt").write_text("\n".join(clean), encoding="utf-8")

    html_path = out_dir / "dashboard.html"
    html_path.write_text(_build_html(report_payload), encoding="utf-8")
    print(_green(f"\n🌐 ダッシュボード生成完了: {html_path}"))
