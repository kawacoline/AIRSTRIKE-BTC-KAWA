"""
main.py
Airstrike BTC Kawa — Entry point.

Runs four concurrent tasks:
  1. scraper_engine.run()  — all 7 news scrapers + AI + MT5 (fully automatic)
  2. update_live_display() — refreshes the Rich TUI every 0.5s
  3. terminal_command_reader() — reads stdin for interactive commands:
       /test_trigger  → injects a fake airstrike headline into the live pipeline
       /status        → prints scraper status summary
       /help          → lists commands
  4. auto_updater     — background git pull every 30s (daemon thread)
  5. mt5_monitor      — background watchdog: restarts MT5 if it dies (daemon thread)

The scrapers run AUTOMATICALLY — no commands needed during normal operation.
"""
import asyncio
import os
import sys
import time
import warnings
from collections import deque
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=UserWarning)

from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

import mt5_engine
from mt5_engine import MT5Monitor
from scraper_engine import ScraperEngine
from auto_updater import AutoUpdater

load_dotenv()
console = Console()

headlines    = deque(maxlen=8)
trades_log   = deque(maxlen=8)
mt5_connected = False
telegram_user_states = {}
_engine: ScraperEngine = None
_updater: AutoUpdater = None
_mt5_monitor: MT5Monitor = None


def _on_mt5_reconnect(success: bool):
    """Callback invoked by MT5Monitor when MT5 is restarted and reconnected."""
    global mt5_connected
    mt5_connected = success
    if success:
        log_trade("[MT5 Monitor] MT5 restarted & reconnected ✓")
    else:
        log_trade("[MT5 Monitor] MT5 restart FAILED — trading disabled")


def fetch_active_trades():
    if not mt5_connected:
        return []
    import MetaTrader5 as mt5
    symbol    = os.getenv("TRADE_SYMBOL", "BTCUSD")
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return []
    result = []
    for p in positions:
        ptype = "SELL" if p.type == mt5.ORDER_TYPE_SELL else "BUY"
        result.append(
            f"#{p.ticket} {ptype} {p.volume}lot @ {p.price_open:.2f} | "
            f"Now: {p.price_current:.2f} | PnL: {p.profit:.2f}"
        )
    return result


def generate_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="main"),
        Layout(name="sources", size=12),
        Layout(name="footer",  size=7),
    )
    layout["main"].split_row(
        Layout(name="news",   ratio=2),
        Layout(name="trades", ratio=1),
    )

    # ── Header — MT5 + Monitor + Updater status ──
    mt5_state = (
        "[bold green]CONNECTED[/bold green]"
        if mt5_connected
        else "[bold red]DISCONNECTED[/bold red]"
    )

    # MT5 Monitor status
    monitor_state = ""
    if _mt5_monitor:
        mon_status = _mt5_monitor.status
        pid = _mt5_monitor._tracked_pid
        restarts = _mt5_monitor.restarts
        if "OK" in mon_status:
            mon_color = "green"
        elif "RESTART" in mon_status or "FAILED" in mon_status:
            mon_color = "red"
        else:
            mon_color = "yellow"
        pid_str = f"PID:{pid}" if pid else "No PID"
        restart_str = f" R:{restarts}" if restarts > 0 else ""
        monitor_state = f" | Watchdog: [{mon_color}]{pid_str}{restart_str}[/{mon_color}]"

    # Git updater status
    updater_state = ""
    if _updater:
        status = _updater.last_pull_status
        if _updater.last_pull_time:
            age = int(time.time() - _updater.last_pull_time)
            age_str = f"{age}s ago" if age < 60 else f"{age//60}m ago"
        else:
            age_str = "pending"
        color = "green" if status in ("Up to date", "Updated!") else "yellow"
        updater_state = f" | Git: [{color}]{status}[/{color}] ({age_str})"

    layout["header"].update(Panel(
        f"[bold white]Airstrike BTC Kawa[/bold white]\n"
        f"MT5: {mt5_state}{monitor_state}{updater_state}\n"
        f"[dim]Commands: [white]/test_trigger[/white] | [white]/status[/white] | [white]/help[/white][/dim]",
        style="blue"
    ))

    # News panel
    news_table = Table(box=box.MINIMAL, expand=True, show_header=True)
    news_table.add_column("Time",   style="cyan",  width=9, no_wrap=True)
    news_table.add_column("Source", style="yellow", width=18, no_wrap=True)
    news_table.add_column("Event",  style="white")
    for h in headlines:
        news_table.add_row(h["time"], h["source"], h["text"])
    layout["news"].update(Panel(
        news_table, title="[bold]Candidates & Events[/bold]", border_style="green"
    ))

    # Trades panel
    trades_table = Table(box=box.MINIMAL, expand=True, show_header=True)
    trades_table.add_column("Time",   style="cyan",   width=9, no_wrap=True)
    trades_table.add_column("Action", style="yellow")
    for t in trades_log:
        trades_table.add_row(t["time"], t["text"])
    layout["trades"].update(Panel(
        trades_table, title="[bold]Engine Actions[/bold]", border_style="yellow"
    ))

    # Sources panel
    src_table = Table(box=box.MINIMAL, expand=True, show_header=True)
    src_table.add_column("Source",       style="white",  width=20)
    src_table.add_column("Interval",     style="cyan",   width=10)
    src_table.add_column("Last Poll",    style="green",  width=12)
    src_table.add_column("Headlines",   style="yellow", width=12)
    src_table.add_column("Status",      style="white")
    if _engine:
        for scraper in _engine._scrapers:
            st = _engine.scraper_status.get(scraper.name, {})
            last_ts = st.get("last_poll")
            if last_ts:
                age = int(time.time() - last_ts)
                last_str = f"{age}s ago" if age < 60 else f"{age//60}m ago"
            else:
                last_str = "Pending..."
            status_raw = st.get("status", "Starting...")
            status_color = "green" if status_raw == "OK" else "red"
            src_table.add_row(
                scraper.name,
                f"{scraper.interval}s",
                last_str,
                str(st.get("headlines_session", 0)),
                f"[{status_color}]{status_raw}[/{status_color}]",
            )
    layout["sources"].update(Panel(
        src_table, title="[bold magenta]Active Scrapers[/bold magenta]",
        border_style="magenta"
    ))

    # Footer — open positions
    symbol      = os.getenv("TRADE_SYMBOL", "BTCUSD")
    open_trades = fetch_active_trades()
    active_str  = (
        "\n".join(open_trades)
        if open_trades
        else f"[dim]No open {symbol} positions.[/dim]"
    )
    layout["footer"].update(Panel(
        active_str, title="[bold red]Open MT5 Positions[/bold red]",
        border_style="red"
    ))
    return layout


def log_headline(msg: str, is_error: bool = False):
    t = time.strftime("%H:%M:%S")
    color = "red" if is_error else "white"
    source = "System"
    text = msg
    if msg.startswith("[") and "]" in msg:
        bracket_end = msg.index("]")
        source = msg[1:bracket_end]
        text   = msg[bracket_end + 1:].strip()
    headlines.append({"time": t, "source": source[:17], "text": f"[{color}]{text[:120]}[/{color}]"})
    with open("logs/bot_history.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d')} {t}] [NEWS] {msg}\n")


def log_trade(msg: str):
    t = time.strftime("%H:%M:%S")
    trades_log.append({"time": t, "text": str(msg)[:80]})
    with open("logs/bot_history.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d')} {t}] [ACTION] {msg}\n")


async def update_live_display(live: Live):
    while True:
        live.update(generate_layout())
        await asyncio.sleep(0.5)


_TEST_HEADLINE = (
    "BREAKING: U.S. Military launches massive retaliatory airstrikes across "
    "the Middle East targeting key militant infrastructure — Pentagon confirms."
)

async def terminal_command_reader():
    loop = asyncio.get_event_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            cmd  = line.strip().lower()
            if cmd == "/test_trigger":
                log_trade("Manual /test_trigger fired!")
                log_headline("[Manual] Injecting test airstrike headline into pipeline...")
                await _engine.inject_headline(_TEST_HEADLINE, source="Manual /test_trigger")
            elif cmd == "/status":
                if _engine:
                    for name, st in _engine.scraper_status.items():
                        log_headline(
                            f"[Status] {name}: {st.get('status','?')} | "
                            f"{st.get('headlines_session',0)} headlines found"
                        )
                if _mt5_monitor:
                    log_headline(
                        f"[Status] MT5 Monitor: {_mt5_monitor.status} | "
                        f"Restarts: {_mt5_monitor.restarts}"
                    )
            elif cmd == "/help":
                log_headline("[Help] /test_trigger | /status | /help")
            elif cmd:
                log_headline(f"[Terminal] Unknown command: '{cmd}'. Type /help.", is_error=True)
        except Exception:
            await asyncio.sleep(1)


# ─── Telegram Helper Markup Functions ─────────────────────────────────────────────

def _fmt_usd(val, signed: bool = False) -> str:
    if val is None:
        return "—"
    try:
        n = float(val)
    except (TypeError, ValueError):
        return "—"
    if signed:
        sign = "+" if n > 0 else ""
        return f"{sign}${n:,.2f}"
    return f"${n:,.2f}"


def _poly_account_summary_lines(snap: dict, detailed: bool = False) -> list:
    """Format live Polymarket account lines for Telegram (HTML)."""
    if snap.get("error") and not snap.get("address"):
        return [f"<b>Poly Account:</b> ❌ {snap['error']}"]

    conn = "Connected ✅" if snap.get("connected") else "Offline ❌"
    lines = [
        f"<b>Wallet:</b> <code>{snap.get('address_short', '?')}</code>",
        f"<b>CLOB:</b> {conn}",
        f"<b>Cash Balance:</b> {_fmt_usd(snap.get('cash_balance'))}",
        f"<b>Positions Value:</b> {_fmt_usd(snap.get('positions_value'))}",
        f"<b>Equity:</b> {_fmt_usd(snap.get('equity'))}",
        f"<b>Unrealized PnL:</b> {_fmt_usd(snap.get('unrealized_pnl'), signed=True)}",
        f"<b>Open Positions:</b> {snap.get('open_count', 0)}",
    ]
    cs = snap.get("closed_stats") or {}
    if cs:
        lines.append(
            f"<b>Closed W/L:</b> {cs.get('wins', 0)}✅ / {cs.get('losses', 0)}❌ "
            f"({cs.get('win_rate', 0):.0f}%) | Realized {_fmt_usd(cs.get('realized_pnl'), signed=True)}"
        )
    if detailed:
        opens = snap.get("open_positions") or []
        if opens:
            lines.append("\n<b>Open Positions:</b>")
            for p in opens[:8]:
                title = (p.get("title") or "Market")[:48]
                outcome = p.get("outcome") or "?"
                size = float(p.get("size") or 0)
                cur = float(p.get("currentValue") or 0)
                pnl = float(p.get("cashPnl") or 0)
                lines.append(
                    f"• {title} | {outcome} x{size:.1f}\n"
                    f"  Value {_fmt_usd(cur)} | PnL {_fmt_usd(pnl, signed=True)}"
                )
        activity = snap.get("activity") or []
        if activity:
            lines.append("\n<b>Recent History:</b>")
            for a in activity[:8]:
                ts = a.get("timestamp")
                try:
                    from datetime import datetime, timezone
                    date_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%m-%d %H:%M")
                except Exception:
                    date_str = "?"
                typ = a.get("type") or "?"
                side = a.get("side") or ""
                title = (a.get("title") or a.get("outcome") or typ)[:40]
                cash = a.get("usdcSize")
                cash_s = _fmt_usd(cash) if cash is not None else ""
                side_s = f" {side}" if side else ""
                lines.append(f"• <b>{date_str}</b> | {typ}{side_s} | {title} {cash_s}".rstrip())
    return lines


def build_poly_dashboard_message(page: str = "overview"):
    """Live Polymarket account dashboard for Telegram admins."""
    import polymarket_engine
    snap = polymarket_engine.fetch_live_account_status()

    lines = ["🪙 <b>POLYMARKET ACCOUNT</b> 🪙\n"]
    if page == "positions":
        lines.append("<i>Open positions (live)</i>\n")
        opens = snap.get("open_positions") or []
        if not opens:
            lines.append("<i>No open positions.</i>")
        else:
            for p in opens[:15]:
                title = (p.get("title") or "Market")[:60]
                outcome = p.get("outcome") or "?"
                size = float(p.get("size") or 0)
                avg = float(p.get("avgPrice") or 0)
                cur_p = float(p.get("curPrice") or 0)
                cur_v = float(p.get("currentValue") or 0)
                pnl = float(p.get("cashPnl") or 0)
                redeem = " ♻️" if p.get("redeemable") else ""
                lines.append(
                    f"• <b>{title}</b>{redeem}\n"
                    f"  {outcome} | size {size:.2f} @ {avg:.3f} → {cur_p:.3f}\n"
                    f"  Value {_fmt_usd(cur_v)} | PnL {_fmt_usd(pnl, signed=True)}"
                )
    elif page == "history":
        lines.append("<i>Recent wallet activity (live)</i>\n")
        activity = snap.get("activity") or []
        if not activity:
            lines.append("<i>No recent activity.</i>")
        else:
            from datetime import datetime, timezone
            for a in activity[:12]:
                ts = a.get("timestamp")
                try:
                    date_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    date_str = "?"
                typ = a.get("type") or "?"
                side = a.get("side") or ""
                title = (a.get("title") or "")[:50]
                outcome = a.get("outcome") or ""
                cash = a.get("usdcSize")
                price = a.get("price")
                bits = [f"<b>{date_str}</b>", typ]
                if side:
                    bits.append(side)
                if title:
                    bits.append(title)
                if outcome:
                    bits.append(f"({outcome})")
                detail = " | ".join(bits)
                extras = []
                if cash is not None:
                    extras.append(_fmt_usd(cash))
                if price is not None:
                    extras.append(f"@{float(price):.3f}")
                extra_s = ("  " + " ".join(extras)) if extras else ""
                lines.append(f"• {detail}{extra_s}")
        closed = snap.get("closed_positions") or []
        if closed:
            lines.append("\n<b>Recent Closed (realized):</b>")
            for p in closed[:8]:
                title = (p.get("title") or "Market")[:48]
                pnl = float(p.get("realizedPnl") or 0)
                outcome = p.get("outcome") or "?"
                lines.append(f"• {title} | {outcome} | {_fmt_usd(pnl, signed=True)}")
    else:
        lines.extend(_poly_account_summary_lines(snap, detailed=True))

    markup = {
        "inline_keyboard": [
            [
                {"text": "🔄 Refresh", "callback_data": "poly_dash_overview"},
                {"text": "📂 Positions", "callback_data": "poly_dash_positions"},
                {"text": "📜 History", "callback_data": "poly_dash_history"},
            ],
            [
                {"text": "📈 Bot Stats", "callback_data": "stats_page_1"},
                {"text": "⬅️ Back", "callback_data": "menu_main"},
            ],
        ]
    }
    return "\n".join(lines), markup


def make_main_menu_markup():
    import config
    import os
    import polymarket_engine
    mt5_state = "Connected ✅" if mt5_connected else "Failed ❌"
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    bot_state = "ON 🟢" if config.BOT_ACTIVE else "OFF 🔴"

    poly_snap = polymarket_engine.fetch_live_account_status(history_limit=0, closed_sample=50)
    poly_equity = _fmt_usd(poly_snap.get("equity"))
    poly_cash = _fmt_usd(poly_snap.get("cash_balance"))
    poly_wl = poly_snap.get("closed_stats") or {}
    poly_wl_s = f"{poly_wl.get('wins', 0)}W/{poly_wl.get('losses', 0)}L"
    
    text = (
        f"🤖 <b>AIRSTRIKE CONTROL PANEL</b> 🤖\n\n"
        f"<b>Bot Execution:</b> {bot_state}\n"
        f"<b>MT5 Status:</b> {mt5_state}\n"
        f"<b>Active Model:</b> <code>{model}</code>\n"
        f"<b>Fallback Model:</b> <code>{fallback}</code>\n\n"
        f"<b>Lot Size:</b> <code>{config.TRADE_LOTS}</code> lots\n"
        f"<b>Take Profit:</b> <code>{config.TP_PIPS:.0f} pips</code>\n"
        f"<b>Stop Loss:</b> <code>{config.SL_PIPS:.0f} pips</code>\n"
        f"<b>Poly Size:</b> <code>${config.POLY_BET_SIZE:.2f}</code>\n"
        f"<b>Trade Cooldown:</b> <code>{config.TRADE_COOLDOWN_SECONDS}s</code>\n\n"
        f"<b>🪙 Poly Cash:</b> {poly_cash} | <b>Equity:</b> {poly_equity}\n"
        f"<b>Poly Closed W/L:</b> {poly_wl_s} | Open: {poly_snap.get('open_count', 0)}\n\n"
        f"<i>Select an action below to configure the bot:</i>"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "📊 Refresh Status", "callback_data": "status_refresh"},
                {"text": "🎯 Test Snipe", "callback_data": "test_trigger_run"}
            ],
            [
                {"text": "🪙 Poly Account", "callback_data": "poly_dash_overview"}
            ],
            [
                {"text": f"Toggle Execution ({bot_state})", "callback_data": "toggle_bot_active"}
            ],
            [
                {"text": "💰 Lot Size", "callback_data": "edit_lot_menu"},
                {"text": "🪙 Poly Size", "callback_data": "edit_poly_menu"}
            ],
            [
                {"text": "📈 Edit TP", "callback_data": "edit_tp_menu"},
                {"text": "📉 Edit SL", "callback_data": "edit_sl_menu"}
            ],
            [
                {"text": "⚙️ Time Settings", "callback_data": "edit_time_settings_menu"}
            ],
            [
                {"text": "📚 Manage Keywords", "callback_data": "manage_keywords_menu"},
                {"text": "🌍 AI Priorities", "callback_data": "ai_priorities_menu"}
            ],
            [
                {"text": "❌ Close Panel", "callback_data": "close_menu"}
            ]
        ]
    }
    return text, markup


def make_time_settings_menu_markup():
    import config
    text = (
        "⚙️ <b>ADVANCED TIME SETTINGS</b> ⚙️\n\n"
        "Here you can control the bot's speed limits and safety constraints:\n\n"
        "<b>1. Trade Cooldown (Seconds)</b>\n"
        "• Prevents duplicate trades on the exact same event. If multiple scrapers report the same strike within this window, the bot ignores the duplicates.\n"
        f"• Current: <code>{config.TRADE_COOLDOWN_SECONDS}s</code>\n\n"
        "<b>2. Max Trades Per Hour</b>\n"
        "• Protects against over-trading during massive escalations when the market gets desensitized.\n"
        f"• Current: <code>{getattr(config, 'MAX_TRADES_PER_HOUR', 3)} trades/hr</code>\n\n"
        "<b>3. Max News Age (Seconds)</b>\n"
        "• If a headline is scraped but was published longer ago than this limit, it is ignored (prevents trading \"late\" news).\n"
        f"• Current: <code>{config.MAX_NEWS_AGE_SECONDS}s</code>\n"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "⏱ Edit Cooldown", "callback_data": "custom_cooldown_prompt"},
                {"text": "🛑 Edit Max Trades", "callback_data": "custom_maxtrades_prompt"}
            ],
            [
                {"text": "🕰 Edit Max News Age", "callback_data": "custom_newsage_prompt"}
            ],
            [
                {"text": "🔙 Back to Main", "callback_data": "menu_main"}
            ]
        ]
    }
    return text, markup


def make_keyword_menu_markup():
    text = (
        "📚 <b>KEYWORD MANAGEMENT</b> 📚\n\n"
        "<b>Prefilters:</b> Words that trigger an AI check (e.g., <i>airstrike</i>, <i>missile</i>).\n"
        "<b>Exclusions:</b> Words that auto-reject the headline immediately (e.g., <i>drug boat</i>).\n\n"
        "Select an option below:"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "📄 View Prefilters", "callback_data": "view_prefilters"},
                {"text": "📄 View Exclusions", "callback_data": "view_exclusions"}
            ],
            [
                {"text": "➕ Add Prefilter", "callback_data": "prompt_add_prefilter"},
                {"text": "➖ Remove Prefilter", "callback_data": "prompt_remove_prefilter"}
            ],
            [
                {"text": "➕ Add Exclusion", "callback_data": "prompt_add_exclusion"},
                {"text": "➖ Remove Exclusion", "callback_data": "prompt_remove_exclusion"}
            ],
            [
                {"text": "🔙 Back to Main", "callback_data": "menu_main"}
            ]
        ]
    }
    return text, markup

def make_ai_priority_menu_markup():
    import config
    req_geo = getattr(config, 'REQUIRE_GEOPOLITICAL', True)
    geo_state = "ON 🟢" if req_geo else "OFF 🔴"
    text = (
        "🌍 <b>AI PRIORITIES</b> 🌍\n\n"
        f"<b>Geopolitical Requirement:</b> {geo_state}\n"
        "If ON, the AI will reject US airstrikes that hit tiny outposts with no global market significance. "
        "If OFF, the bot trades EVERY US airstrike it sees.\n\n"
        "<b>Current AI Geopolitical Prompt:</b>\n"
        f"<i>\"{getattr(config, 'GEOPOLITICAL_PROMPT', 'Default')}\"</i>\n"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": f"Toggle Geopolitical ({geo_state})", "callback_data": "toggle_geo_req"}
            ],
            [
                {"text": "✏️ Edit Geopolitical Prompt", "callback_data": "prompt_edit_geo_text"}
            ],
            [
                {"text": "🔙 Back to Main", "callback_data": "menu_main"}
            ]
        ]
    }
    return text, markup


def make_lot_menu_markup():
    import config
    burst_state = "ON 🟢" if getattr(config, 'BURST_EXECUTION', False) else "OFF 🔴"
    tps = getattr(config, 'BURST_TPS_PIPS', [10000, 15000, 30000])
    num_tps = len(tps)
    tp_lines = "\n".join([f"Burst TP{i+1}: <b>{tps[i]:.0f} pips</b>" for i in range(num_tps)])
    shift_tp = getattr(config, 'SHIFT_SL_AFTER_TP', 2)
    shift_offset = getattr(config, 'BREAKEVEN_PROFIT_PIPS', 2000.0)
    
    text = (
        f"💰 <b>TRADE SIZING & EXECUTION</b> 💰\n\n"
        f"<b>Current Base Size:</b> {config.TRADE_LOTS} lots\n\n"
        f"<b>Burst Execution Mode:</b> {burst_state}\n"
        f"<i>Splits the base size into {num_tps} equal MT5 tickets with staggered Take Profits.</i>\n"
        f"<i>Trailing SL: Auto-moves SL to Break-Even +{shift_offset:.0f}pips when TP{shift_tp} hits.</i>\n\n"
        f"{tp_lines}\n\n"
        f"Select an option below:"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": f"Toggle Burst Mode ({burst_state})", "callback_data": "toggle_burst_mode"}
            ],
            [
                {"text": "0.01", "callback_data": "set_lot:0.01"},
                {"text": "0.02", "callback_data": "set_lot:0.02"},
                {"text": "0.05", "callback_data": "set_lot:0.05"},
                {"text": "0.10", "callback_data": "set_lot:0.10"}
            ],
            [
                {"text": "✏️ Edit Custom Lot Size", "callback_data": "custom_lot_prompt"}
            ],
            [
                {"text": "✏️ Edit Burst TPs", "callback_data": "prompt_edit_btp_all"}
            ],
            [
                {"text": "✏️ Edit Shift TP #", "callback_data": "prompt_edit_shift_tp"},
                {"text": "✏️ Edit BE Offset", "callback_data": "prompt_edit_be_offset"}
            ],
            [
                {"text": "« Back to Menu", "callback_data": "menu_main"}
            ]
        ]
    }
    return text, markup


def make_tp_menu_markup():
    import config
    text = (
        f"📈 <b>EDIT TAKE PROFIT (TP)</b> 📈\n\n"
        f"Current TP: <b>{config.TP_PIPS:.0f} pips</b>\n\n"
        f"Select a new TP value in pips below or choose custom:"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "50 pips", "callback_data": "set_tp:50"},
                {"text": "100 pips", "callback_data": "set_tp:100"},
                {"text": "150 pips", "callback_data": "set_tp:150"}
            ],
            [
                {"text": "200 pips", "callback_data": "set_tp:200"},
                {"text": "250 pips", "callback_data": "set_tp:250"},
                {"text": "300 pips", "callback_data": "set_tp:300"}
            ],
            [
                {"text": "✏️ Custom TP Value", "callback_data": "custom_tp_prompt"}
            ],
            [
                {"text": "« Back to Menu", "callback_data": "menu_main"}
            ]
        ]
    }
    return text, markup


def make_sl_menu_markup():
    import config
    text = (
        f"📉 <b>EDIT STOP LOSS (SL)</b> 📉\n\n"
        f"Current SL: <b>{config.SL_PIPS:.0f} pips</b>\n\n"
        f"Select a new SL value in pips below or choose custom:"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "300 pips", "callback_data": "set_sl:300"},
                {"text": "400 pips", "callback_data": "set_sl:400"},
                {"text": "500 pips", "callback_data": "set_sl:500"}
            ],
            [
                {"text": "600 pips", "callback_data": "set_sl:600"},
                {"text": "700 pips", "callback_data": "set_sl:700"},
                {"text": "800 pips", "callback_data": "set_sl:800"}
            ],
            [
                {"text": "✏️ Custom SL Value", "callback_data": "custom_sl_prompt"}
            ],
            [
                {"text": "« Back to Menu", "callback_data": "menu_main"}
            ]
        ]
    }
    return text, markup

def make_poly_menu_markup():
    import config
    text = (
        f"🪙 <b>EDIT POLYMARKET BET SIZE</b> 🪙\n\n"
        f"Current Size: <b>${config.POLY_BET_SIZE:.2f}</b>\n\n"
        f"Select a new size below or choose custom:"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "$2.50", "callback_data": "set_poly:2.50"},
                {"text": "$5.00", "callback_data": "set_poly:5.00"},
                {"text": "$10.00", "callback_data": "set_poly:10.00"}
            ],
            [
                {"text": "$25.00", "callback_data": "set_poly:25.00"},
                {"text": "$50.00", "callback_data": "set_poly:50.00"},
                {"text": "$100.00", "callback_data": "set_poly:100.00"}
            ],
            [
                {"text": "✏️ Custom Poly Size", "callback_data": "custom_poly_prompt"}
            ],
            [
                {"text": "« Back to Menu", "callback_data": "menu_main"}
            ]
        ]
    }
    return text, markup

def build_stats_message(page_num=1):
    import db_engine
    import config
    import polymarket_engine
    mt5_acc = str(config.MT5_LOGIN)
    poly_acc = str(config.POLY_FUNDER_ADDRESS)
    
    summary = db_engine.get_stats_summary(mt5_acc)
    poly_summary = db_engine.get_poly_stats_summary(poly_acc)
    
    total_trades = summary['total_trades'] + poly_summary['total_trades']
    total_wins = summary['wins'] + poly_summary['wins']
    total_losses = summary['losses'] + poly_summary['losses']
    total_pnl = summary['total_pnl'] + poly_summary['total_pnl']
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
    
    items_per_page = 5
    
    # Get all trades
    all_mt5 = db_engine.get_recent_trades(1000, 0, mt5_acc)
    all_poly = db_engine.get_recent_poly_trades(1000, 0, poly_acc)
    
    combined = all_mt5 + all_poly
    combined.sort(key=lambda x: x['open_time'], reverse=True)
    
    total_pages = max(1, (len(combined) + items_per_page - 1) // items_per_page)
    if page_num > total_pages: page_num = total_pages
    if page_num < 1: page_num = 1
    
    offset = (page_num - 1) * items_per_page
    page_items = combined[offset:offset+items_per_page]

    live = polymarket_engine.fetch_live_account_status(history_limit=0, closed_sample=50)
    
    lines = [
        "📈 <b>BOT PERFORMANCE STATS</b> 📈\n",
        "🪙 <b>Live Poly Account</b>",
        f"Cash {_fmt_usd(live.get('cash_balance'))} | Equity {_fmt_usd(live.get('equity'))} | Open {live.get('open_count', 0)}",
        f"Account W/L: {(live.get('closed_stats') or {}).get('wins', 0)}✅ / {(live.get('closed_stats') or {}).get('losses', 0)}❌ | Realized {_fmt_usd((live.get('closed_stats') or {}).get('realized_pnl'), signed=True)}\n",
        "<b>Bot-tracked trades (MT5 + Poly):</b>",
        f"<b>Total Trades:</b> {total_trades}",
        f"<b>Wins:</b> {total_wins} ✅",
        f"<b>Losses:</b> {total_losses} ❌",
        f"<b>Win Rate:</b> {win_rate:.1f}%",
        f"<b>Total PnL:</b> {_fmt_usd(total_pnl, signed=True)}\n",
        f"<b>Recent Trades (Page {page_num}/{total_pages}):</b>"
    ]
    
    if not page_items:
        lines.append("<i>No trades recorded yet.</i>")
    else:
        for t in page_items:
            pnl_str = f"+${t['pnl']:.2f}" if t['pnl'] > 0 else f"-${abs(t['pnl']):.2f}" if t['pnl'] < 0 else "$0.00"
            date_str = t['open_time'][:16].replace('T', ' ')
            if 'order_id' in t:
                lines.append(
                    f"• <b>{date_str}</b> | POLY Limit | {t['status']}\n"
                    f"  <b>Market:</b> {t['market_question']} (${t['bet_size']:.2f})\n"
                    f"  <b>PnL:</b> {pnl_str}\n"
                    f"  <b>News:</b> <a href=\"{t['url']}\">{t['headline']}</a>"
                )
            else:
                lines.append(
                    f"• <b>{date_str}</b> | {t['type']} {t['lots']} {t['symbol']} | {t['status']}\n"
                    f"  <b>PnL:</b> {pnl_str}\n"
                    f"  <b>News:</b> <a href=\"{t['url']}\">{t['headline']}</a>"
                )
                
    buttons = []
    if page_num > 1:
        buttons.append({"text": "⬅️ Prev", "callback_data": f"stats_page_{page_num-1}"})
    if page_num < total_pages:
        buttons.append({"text": "Next ➡️", "callback_data": f"stats_page_{page_num+1}"})
        
    markup = {
        "inline_keyboard": [
            buttons if buttons else [],
            [
                {"text": "🪙 Poly Account", "callback_data": "poly_dash_overview"},
                {"text": "📥 Export CSV", "callback_data": "export_csv"},
            ],
        ]
    }
    return "\n".join(lines), markup

async def telegram_command_reader():
    """Background loop to long-poll Telegram for commands and inline menu callbacks."""
    import telegram_notifier
    import aiohttp
    
    log_headline("[Telegram] Initializing command reader loop...")
    
    if not telegram_notifier.TELEGRAM_BOT_TOKEN:
        log_headline("[Telegram] ERROR: TELEGRAM_BOT_TOKEN is empty! Reader disabled.", is_error=True)
        return
        
    if not telegram_notifier.TELEGRAM_USERS:
        log_headline("[Telegram] ERROR: TELEGRAM_USERS is empty! Reader disabled.", is_error=True)
        return
        
    log_headline(f"[Telegram] Bot reader active. Token: {telegram_notifier.TELEGRAM_BOT_TOKEN[:10]}... Users: {telegram_notifier.TELEGRAM_USERS}")
    
    # Initialize bot commands list and menu button
    log_headline("[Telegram] Registering commands and menu button...")
    await asyncio.to_thread(telegram_notifier.initialize_bot_commands_and_menu)
        
    url = f"https://api.telegram.org/bot{telegram_notifier.TELEGRAM_BOT_TOKEN}/getUpdates"
    offset = None
    
    # Disable SSL verification for aiohttp on VPS environment to avoid SSLCertVerificationError
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                params = {"timeout": 30}
                if offset: params["offset"] = offset
                
                async with session.get(url, params=params, timeout=35) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(5)
                        continue
                        
                    data = await resp.json()
                    
                    for result in data.get("result", []):
                        offset = result["update_id"] + 1
                        
                        # 1. Handle standard text/slash messages
                        message = result.get("message")
                        if message:
                            chat_id = message.get("chat", {}).get("id")
                            if chat_id not in telegram_notifier.TELEGRAM_USERS:
                                continue
                                
                            # Check if the user is in a waiting state for a custom input
                            user_state = telegram_user_states.get(chat_id)
                            if user_state:
                                raw_val = message.get("text", "").strip()
                                success = False
                                error_msg = ""
                                
                                if user_state == "WAIT_CUSTOM_LOT":
                                    try:
                                        val = float(raw_val)
                                        if val <= 0:
                                            raise ValueError("Lot must be positive")
                                        
                                        import config
                                        old_val = config.TRADE_LOTS
                                        config.TRADE_LOTS = val
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            env_content = re.sub(r"^TRADE_LOTS=.*$", f"TRADE_LOTS={val}", env_content, flags=re.MULTILINE)
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Custom lot size set via Telegram input: {val}")
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>TRADE_LOTS Updated!</b>\n\nOld Size: {old_val}\n<b>New Size: {val}</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        error_msg = f"❌ <b>Invalid custom lot size:</b> <code>{raw_val}</code>. Aborting custom edit workflow."
                                
                                elif user_state == "WAIT_CUSTOM_TP":
                                    try:
                                        val = float(raw_val)
                                        if val <= 0:
                                            raise ValueError("TP must be positive")
                                        
                                        import config
                                        old_val = config.TP_PIPS
                                        config.TP_PIPS = val
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "TP_PIPS=" in env_content:
                                                env_content = re.sub(r"^TP_PIPS=.*$", f"TP_PIPS={val}", env_content, flags=re.MULTILINE)
                                            elif "TP_PERCENT=" in env_content:
                                                env_content = re.sub(r"^TP_PERCENT=.*$", f"TP_PIPS={val}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nTP_PIPS={val}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Custom TP pips set via Telegram input: {val}")
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>TP_PIPS Updated!</b>\n\nOld TP: {old_val:.0f} pips\n<b>New TP: {val:.0f} pips</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        error_msg = f"❌ <b>Invalid custom TP:</b> <code>{raw_val}</code>. Aborting custom edit workflow."
                                        
                                elif user_state == "WAIT_CUSTOM_SL":
                                    try:
                                        val = float(raw_val)
                                        if val <= 0:
                                            raise ValueError("SL must be positive")
                                        
                                        import config
                                        old_val = config.SL_PIPS
                                        config.SL_PIPS = val
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "SL_PIPS=" in env_content:
                                                env_content = re.sub(r"^SL_PIPS=.*$", f"SL_PIPS={val}", env_content, flags=re.MULTILINE)
                                            elif "SL_PERCENT=" in env_content:
                                                env_content = re.sub(r"^SL_PERCENT=.*$", f"SL_PIPS={val}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nSL_PIPS={val}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Custom SL pips set via Telegram input: {val}")
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>SL_PIPS Updated!</b>\n\nOld SL: {old_val:.0f} pips\n<b>New SL: {val:.0f} pips</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        error_msg = f"❌ <b>Invalid custom SL:</b> <code>{raw_val}</code>. Aborting custom edit workflow."
                                        
                                elif user_state == "WAIT_CUSTOM_POLY":
                                    try:
                                        clean_val = raw_val.replace("$", "").replace(",", "").strip()
                                        val = float(clean_val)
                                        if val < 1.00:
                                            raise ValueError("Minimum bet size is $1.00")
                                        
                                        import config
                                        old_val = config.POLY_BET_SIZE
                                        config.POLY_BET_SIZE = val
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "POLY_BET_SIZE=" in env_content:
                                                env_content = re.sub(r"^POLY_BET_SIZE=.*$", f"POLY_BET_SIZE={val}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nPOLY_BET_SIZE={val}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Custom Poly Size set via Telegram input: ${val:.2f}")
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>POLY_BET_SIZE Updated!</b>\n\nOld Size: ${old_val:.2f}\n<b>New Size: ${val:.2f}</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        msg = str(e) if str(e) else "Invalid custom format."
                                        error_msg = f"❌ <b>{msg}</b>\n<code>{raw_val}</code>. Aborting custom edit workflow."
                                
                                elif user_state == "WAIT_CUSTOM_COOLDOWN":
                                    try:
                                        val = int(raw_val)
                                        if val < 0:
                                            raise ValueError("Cooldown cannot be negative")
                                        
                                        import config
                                        old_val = config.TRADE_COOLDOWN_SECONDS
                                        config.TRADE_COOLDOWN_SECONDS = val
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "TRADE_COOLDOWN_SECONDS=" in env_content:
                                                env_content = re.sub(r"^TRADE_COOLDOWN_SECONDS=.*$", f"TRADE_COOLDOWN_SECONDS={val}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nTRADE_COOLDOWN_SECONDS={val}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Custom Cooldown set via Telegram input: {val}s")
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>TRADE_COOLDOWN_SECONDS Updated!</b>\n\nOld Cooldown: {old_val}s\n<b>New Cooldown: {val}s</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        msg = str(e) if str(e) else "Invalid custom format."
                                        error_msg = f"❌ <b>{msg}</b>\n<code>{raw_val}</code>. Aborting custom edit workflow."

                                elif user_state == "WAIT_CUSTOM_MAXTRADES":
                                    try:
                                        val = int(raw_val)
                                        if val < 0:
                                            raise ValueError("Max Trades cannot be negative")
                                        
                                        import config
                                        old_val = getattr(config, 'MAX_TRADES_PER_HOUR', 3)
                                        config.MAX_TRADES_PER_HOUR = val
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "MAX_TRADES_PER_HOUR=" in env_content:
                                                env_content = re.sub(r"^MAX_TRADES_PER_HOUR=.*$", f"MAX_TRADES_PER_HOUR={val}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nMAX_TRADES_PER_HOUR={val}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Custom Max Trades/Hr set via Telegram input: {val}")
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>MAX_TRADES_PER_HOUR Updated!</b>\n\nOld Limit: {old_val}\n<b>New Limit: {val}</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        msg = str(e) if str(e) else "Invalid custom format."
                                        error_msg = f"❌ <b>{msg}</b>\n<code>{raw_val}</code>. Aborting custom edit workflow."

                                elif user_state == "WAIT_CUSTOM_NEWSAGE":
                                    try:
                                        val = int(raw_val)
                                        if val < 0:
                                            raise ValueError("News Age cannot be negative")
                                        
                                        import config
                                        old_val = config.MAX_NEWS_AGE_SECONDS
                                        config.MAX_NEWS_AGE_SECONDS = val
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "MAX_NEWS_AGE_SECONDS=" in env_content:
                                                env_content = re.sub(r"^MAX_NEWS_AGE_SECONDS=.*$", f"MAX_NEWS_AGE_SECONDS={val}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nMAX_NEWS_AGE_SECONDS={val}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Custom Max News Age set via Telegram input: {val}s")
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>MAX_NEWS_AGE_SECONDS Updated!</b>\n\nOld Age Limit: {old_val}s\n<b>New Age Limit: {val}s</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        msg = str(e) if str(e) else "Invalid custom format."
                                        error_msg = f"❌ <b>{msg}</b>\n<code>{raw_val}</code>. Aborting custom edit workflow."
                                
                                elif user_state in ["WAIT_CUSTOM_ADDPRE", "WAIT_CUSTOM_REMPRE", "WAIT_CUSTOM_ADDEXC", "WAIT_CUSTOM_REMEXC"]:
                                    import config
                                    import json
                                    val = raw_val.strip().lower()
                                    if not val:
                                        error_msg = "❌ <b>Empty word.</b> Aborting."
                                    else:
                                        if user_state == "WAIT_CUSTOM_ADDPRE":
                                            if val not in config.KEYWORD_PREFILTER: config.KEYWORD_PREFILTER.append(val)
                                            msg_text = f"✅ Added '{val}' to Prefilters!"
                                        elif user_state == "WAIT_CUSTOM_REMPRE":
                                            if val in config.KEYWORD_PREFILTER: config.KEYWORD_PREFILTER.remove(val)
                                            msg_text = f"✅ Removed '{val}' from Prefilters!"
                                        elif user_state == "WAIT_CUSTOM_ADDEXC":
                                            if val not in config.KEYWORD_EXCLUSIONS: config.KEYWORD_EXCLUSIONS.append(val)
                                            msg_text = f"✅ Added '{val}' to Exclusions!"
                                        elif user_state == "WAIT_CUSTOM_REMEXC":
                                            if val in config.KEYWORD_EXCLUSIONS: config.KEYWORD_EXCLUSIONS.remove(val)
                                            msg_text = f"✅ Removed '{val}' from Exclusions!"
                                            
                                        # Save to json
                                        data = {"prefilter": config.KEYWORD_PREFILTER, "exclusions": config.KEYWORD_EXCLUSIONS}
                                        try:
                                            with open("keywords.json", "w", encoding="utf-8") as f:
                                                json.dump(data, f, indent=4)
                                        except Exception as e:
                                            print(f"Error saving keywords.json: {e}")
                                            
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, msg_text)
                                        success = True

                                elif user_state == "WAIT_CUSTOM_GEOTEXT":
                                    val = raw_val.strip()
                                    if not val:
                                        error_msg = "❌ <b>Empty prompt.</b> Aborting."
                                    else:
                                        import config
                                        config.GEOPOLITICAL_PROMPT = val
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "GEOPOLITICAL_PROMPT=" in env_content:
                                                env_content = re.sub(r"^GEOPOLITICAL_PROMPT=.*$", f"GEOPOLITICAL_PROMPT=\"{val}\"", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nGEOPOLITICAL_PROMPT=\"{val}\""
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, "✅ <b>AI Prompt Updated!</b>")
                                        success = True

                                elif user_state in ["WAIT_CUSTOM_BTP1", "WAIT_CUSTOM_BTP2", "WAIT_CUSTOM_BTP3"]:
                                    try:
                                        val = float(raw_val)
                                        if val <= 0:
                                            raise ValueError("TP must be greater than 0")
                                        
                                        import config
                                        var_map = {
                                            "WAIT_CUSTOM_BTP1": "BURST_TP1_PIPS",
                                            "WAIT_CUSTOM_BTP2": "BURST_TP2_PIPS",
                                            "WAIT_CUSTOM_BTP3": "BURST_TP3_PIPS"
                                        }
                                        target_var = var_map[user_state]
                                        old_val = getattr(config, target_var, 0)
                                        setattr(config, target_var, val)
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if f"{target_var}=" in env_content:
                                                env_content = re.sub(rf"^{target_var}=.*$", f"{target_var}={val}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\n{target_var}={val}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>{target_var} Updated!</b>\n\nOld TP: {old_val:.0f} pips\n<b>New TP: {val:.0f} pips</b>\n\n<i>This takes effect instantly.</i>")
                                        success = True
                                    except Exception as e:
                                        msg = str(e) if str(e) else "Invalid custom format."
                                        error_msg = f"❌ <b>{msg}</b>\n<code>{raw_val}</code>. Aborting custom edit workflow."

                                # Reset state
                                telegram_user_states.pop(chat_id, None)
                                
                                if not success:
                                    # Send failure message
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, error_msg)
                                    
                                # Reprompt main menu
                                menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                await asyncio.to_thread(telegram_notifier.send_telegram_message, menu_text, menu_markup)
                                continue
                                
                            text = message.get("text", "").strip().lower()
                            
                            if text in ("/start", "/menu"):
                                menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                await asyncio.to_thread(telegram_notifier.send_telegram_message, menu_text, menu_markup)
                                
                            elif text == "/test_trigger":
                                log_trade("Telegram /test_trigger fired!")
                                log_headline("[Telegram] Injecting test airstrike headline...")
                                if _engine:
                                    await _engine.inject_headline(_TEST_HEADLINE, source="Telegram /test_trigger")
                                await asyncio.to_thread(telegram_notifier.send_telegram_message, "✅ Injecting test airstrike headline into pipeline...")
                                
                            elif text == "/status":
                                lines = ["📊 <b>Bot Status</b>\n"]
                                if _engine:
                                    for name, st in _engine.scraper_status.items():
                                        lines.append(f"• <b>{name}:</b> {st.get('status','?')} | {st.get('headlines_session',0)} headlines")
                                if _mt5_monitor:
                                    lines.append(f"\n• <b>MT5 Monitor:</b> {_mt5_monitor.status} | Restarts: {_mt5_monitor.restarts}")
                                try:
                                    import polymarket_engine
                                    snap = await asyncio.to_thread(polymarket_engine.fetch_live_account_status, 5, 50)
                                    lines.append("\n🪙 <b>Polymarket Account</b>")
                                    lines.extend(_poly_account_summary_lines(snap, detailed=False))
                                except Exception as e:
                                    lines.append(f"\n🪙 <b>Polymarket:</b> ❌ fetch failed ({e})")
                                poly_markup = {
                                    "inline_keyboard": [[
                                        {"text": "🪙 Full Poly Dashboard", "callback_data": "poly_dash_overview"}
                                    ]]
                                }
                                await asyncio.to_thread(telegram_notifier.send_telegram_message, "\n".join(lines), poly_markup)

                            elif text == "/poly":
                                text_msg, markup = await asyncio.to_thread(build_poly_dashboard_message, "overview")
                                await asyncio.to_thread(telegram_notifier.send_telegram_message, text_msg, markup)
                                
                            elif text == "/stats":
                                import mt5_engine
                                await asyncio.to_thread(mt5_engine.sync_all_trades_with_mt5)
                                
                                text_msg, markup = await asyncio.to_thread(build_stats_message, 1)
                                await asyncio.to_thread(telegram_notifier.send_telegram_message, text_msg, markup)
                                
                            elif text.startswith("/edit_lot"):
                                try:
                                    parts = text.split()
                                    if len(parts) != 2:
                                        raise ValueError()
                                    new_lot = float(parts[1])
                                    if new_lot <= 0:
                                        raise ValueError()
                                        
                                    import config
                                    old_lot = config.TRADE_LOTS
                                    config.TRADE_LOTS = new_lot
                                    
                                    env_path = ".env"
                                    if os.path.exists(env_path):
                                        with open(env_path, "r", encoding="utf-8") as f:
                                            env_content = f.read()
                                        import re
                                        env_content = re.sub(r"^TRADE_LOTS=.*$", f"TRADE_LOTS={new_lot}", env_content, flags=re.MULTILINE)
                                        with open(env_path, "w", encoding="utf-8") as f:
                                            f.write(env_content)
                                            
                                    log_trade(f"Lot size changed via Telegram: {old_lot} -> {new_lot}")
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>TRADE_LOTS Updated!</b>\n\nOld Size: {old_lot}\n<b>New Size: {new_lot}</b>\n\n<i>This takes effect instantly.</i>")
                                except Exception:
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, "❌ <b>Invalid format.</b>\nUsage: <code>/edit_lot 0.05</code>")

                            elif text.startswith("/edit_poly_bet"):
                                try:
                                    parts = text.split()
                                    if len(parts) != 2:
                                        raise ValueError()
                                    clean_size = parts[1].replace("$", "").replace(",", "").strip()
                                    new_size = float(clean_size)
                                    if new_size < 1.00:
                                        raise ValueError("Minimum bet size is $1.00")
                                        
                                    import config
                                    old_size = config.POLY_BET_SIZE
                                    config.POLY_BET_SIZE = new_size
                                    
                                    env_path = ".env"
                                    if os.path.exists(env_path):
                                        with open(env_path, "r", encoding="utf-8") as f:
                                            env_content = f.read()
                                        import re
                                        if "POLY_BET_SIZE=" in env_content:
                                            env_content = re.sub(r"^POLY_BET_SIZE=.*$", f"POLY_BET_SIZE={new_size}", env_content, flags=re.MULTILINE)
                                        else:
                                            env_content += f"\nPOLY_BET_SIZE={new_size}"
                                        with open(env_path, "w", encoding="utf-8") as f:
                                            f.write(env_content)
                                            
                                    log_trade(f"Polymarket bet size changed via Telegram: ${old_size} -> ${new_size}")
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>POLY_BET_SIZE Updated!</b>\n\nOld Size: ${old_size}\n<b>New Size: ${new_size}</b>\n\n<i>This takes effect instantly.</i>")
                                except ValueError as ve:
                                    msg = str(ve) if str(ve) else "Invalid format."
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, f"❌ <b>{msg}</b>\nUsage: <code>/edit_poly_bet 5.00</code>")
                                except Exception:
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, "❌ <b>Invalid format.</b>\nUsage: <code>/edit_poly_bet 5.00</code>")
                                    
                            elif text.startswith("/edit_tp"):
                                try:
                                    parts = text.split()
                                    if len(parts) != 2:
                                        raise ValueError()
                                    new_tp = float(parts[1])
                                    if new_tp <= 0:
                                        raise ValueError()
                                        
                                    import config
                                    old_tp = config.TP_PIPS
                                    config.TP_PIPS = new_tp
                                    
                                    env_path = ".env"
                                    if os.path.exists(env_path):
                                        with open(env_path, "r", encoding="utf-8") as f:
                                            env_content = f.read()
                                        import re
                                        if "TP_PIPS=" in env_content:
                                            env_content = re.sub(r"^TP_PIPS=.*$", f"TP_PIPS={new_tp}", env_content, flags=re.MULTILINE)
                                        elif "TP_PERCENT=" in env_content:
                                            env_content = re.sub(r"^TP_PERCENT=.*$", f"TP_PIPS={new_tp}", env_content, flags=re.MULTILINE)
                                        else:
                                            env_content += f"\nTP_PIPS={new_tp}"
                                        with open(env_path, "w", encoding="utf-8") as f:
                                            f.write(env_content)
                                            
                                    log_trade(f"TP pips changed via Telegram: {old_tp} -> {new_tp}")
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>TP_PIPS Updated!</b>\n\nOld TP: {old_tp:.0f} pips\n<b>New TP: {new_tp:.0f} pips</b>\n\n<i>This takes effect instantly.</i>")
                                except Exception:
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, "❌ <b>Invalid format.</b>\nUsage: <code>/edit_tp 200</code>")
                                    
                            elif text.startswith("/edit_sl"):
                                try:
                                    parts = text.split()
                                    if len(parts) != 2:
                                        raise ValueError()
                                    new_sl = float(parts[1])
                                    if new_sl <= 0:
                                        raise ValueError()
                                        
                                    import config
                                    old_sl = config.SL_PIPS
                                    config.SL_PIPS = new_sl
                                    
                                    env_path = ".env"
                                    if os.path.exists(env_path):
                                        with open(env_path, "r", encoding="utf-8") as f:
                                            env_content = f.read()
                                        import re
                                        if "SL_PIPS=" in env_content:
                                            env_content = re.sub(r"^SL_PIPS=.*$", f"SL_PIPS={new_sl}", env_content, flags=re.MULTILINE)
                                        elif "SL_PERCENT=" in env_content:
                                            env_content = re.sub(r"^SL_PERCENT=.*$", f"SL_PIPS={new_sl}", env_content, flags=re.MULTILINE)
                                        else:
                                            env_content += f"\nSL_PIPS={new_sl}"
                                        with open(env_path, "w", encoding="utf-8") as f:
                                            f.write(env_content)
                                            
                                    log_trade(f"SL pips changed via Telegram: {old_sl} -> {new_sl}")
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, f"✅ <b>SL_PIPS Updated!</b>\n\nOld SL: {old_sl:.0f} pips\n<b>New SL: {new_sl:.0f} pips</b>\n\n<i>This takes effect instantly.</i>")
                                except Exception:
                                    await asyncio.to_thread(telegram_notifier.send_telegram_message, "❌ <b>Invalid format.</b>\nUsage: <code>/edit_sl 700</code>")
                                    
                            elif text == "/help":
                                await asyncio.to_thread(
                                    telegram_notifier.send_telegram_message,
                                    "🤖 <b>Available Commands</b>\n"
                                    "/menu - Show interactive control panel\n"
                                    "/status - Scrapers + live Polymarket balance/equity\n"
                                    "/poly - Full Polymarket account dashboard\n"
                                    "/stats - Bot performance & trade history\n"
                                    "/edit_lot 0.01 - Change trade size instantly\n"
                                    "/edit_tp 200 - Change TP in pips instantly\n"
                                    "/edit_sl 700 - Change SL in pips instantly\n"
                                    "/test_trigger - Inject fake airstrike headline\n"
                                    "/help - Show this message",
                                )

                        # 2. Handle Inline Button Callback Queries
                        callback_query = result.get("callback_query")
                        if callback_query:
                            cq_id = callback_query.get("id")
                            cq_msg = callback_query.get("message")
                            data_val = callback_query.get("data", "")
                            
                            if cq_msg:
                                chat_id = cq_msg.get("chat", {}).get("id")
                                msg_id = cq_msg.get("message_id")
                                
                                if chat_id in telegram_notifier.TELEGRAM_USERS:
                                    
                                    if data_val == "menu_main" or data_val == "status_refresh":
                                        telegram_user_states.pop(chat_id, None)
                                        menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, menu_text, menu_markup)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "📊 Status Refreshed!" if data_val == "status_refresh" else None)

                                    elif data_val.startswith("poly_dash_"):
                                        page = data_val.replace("poly_dash_", "", 1) or "overview"
                                        if page not in ("overview", "positions", "history"):
                                            page = "overview"
                                        text_msg, mk = await asyncio.to_thread(build_poly_dashboard_message, page)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, text_msg, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "🪙 Poly refreshed")
                                        
                                    elif data_val == "toggle_bot_active":
                                        import config
                                        config.BOT_ACTIVE = not config.BOT_ACTIVE
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "BOT_ACTIVE=" in env_content:
                                                env_content = re.sub(r"^BOT_ACTIVE=.*$", f"BOT_ACTIVE={config.BOT_ACTIVE}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nBOT_ACTIVE={config.BOT_ACTIVE}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                        
                                        state_str = "ON 🟢" if config.BOT_ACTIVE else "OFF 🔴"
                                        log_trade(f"Bot trading execution toggled to: {state_str}")
                                        
                                        menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, menu_text, menu_markup)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, f"Execution is now {state_str}")
                                        
                                    elif data_val == "edit_time_settings_menu":
                                        txt, mk = make_time_settings_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "custom_cooldown_prompt":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_COOLDOWN"
                                         txt = (
                                             "⏱ <b>ENTER TRADE COOLDOWN</b>\n\n"
                                             "Please reply with your desired cooldown in seconds (e.g. <code>60</code>).\n\n"
                                             "<i>If you send an invalid value, the workflow will be aborted.</i>"
                                         )
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_time_settings_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "custom_maxtrades_prompt":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_MAXTRADES"
                                         txt = (
                                             "🛑 <b>ENTER MAX TRADES PER HOUR</b>\n\n"
                                             "Please reply with your desired maximum trades per hour (e.g. <code>3</code>).\n\n"
                                             "<i>If you send an invalid value, the workflow will be aborted.</i>"
                                         )
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_time_settings_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "toggle_burst_mode":
                                        import config
                                        config.BURST_EXECUTION = not getattr(config, 'BURST_EXECUTION', False)
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "BURST_EXECUTION=" in env_content:
                                                env_content = re.sub(r"^BURST_EXECUTION=.*$", f"BURST_EXECUTION={config.BURST_EXECUTION}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nBURST_EXECUTION={config.BURST_EXECUTION}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        txt, mk = make_lot_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "Burst Mode Toggled!")

                                    elif data_val == "prompt_edit_btp_all":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_BTP_ALL"
                                         txt = "✏️ <b>EDIT BURST TPs</b>\n\nReply with a comma-separated list of pips. Example:\n<code>10000,15000,30000</code>"
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_lot_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "prompt_edit_shift_tp":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_SHIFT_TP"
                                         txt = "✏️ <b>EDIT SL SHIFT TARGET</b>\n\nReply with the TP index (integer) that triggers the Stop Loss move to Break-Even. Example: <code>2</code> means wait for TP2."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_lot_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "prompt_edit_be_offset":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_BE_OFFSET"
                                         txt = "✏️ <b>EDIT BREAK-EVEN OFFSET</b>\n\nReply with the profit buffer in pips to add to Break-Even. Example: <code>2000</code>."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_lot_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)


                                    elif data_val == "prompt_edit_btp1":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_BTP1"
                                         txt = "✏️ <b>EDIT BURST TP 1</b>\n\nReply with the new Take Profit value in pips."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_lot_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "prompt_edit_btp2":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_BTP2"
                                         txt = "✏️ <b>EDIT BURST TP 2</b>\n\nReply with the new Take Profit value in pips."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_lot_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "prompt_edit_btp3":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_BTP3"
                                         txt = "✏️ <b>EDIT BURST TP 3</b>\n\nReply with the new Take Profit value in pips."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_lot_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "custom_newsage_prompt":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_NEWSAGE"
                                         txt = (
                                             "🕰 <b>ENTER MAX NEWS AGE</b>\n\n"
                                             "Please reply with your desired maximum news age in seconds (e.g. <code>180</code>).\n\n"
                                             "<i>If you send an invalid value, the workflow will be aborted.</i>"
                                         )
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "edit_time_settings_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "manage_keywords_menu":
                                        txt, mk = make_keyword_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                        
                                    elif data_val == "ai_priorities_menu":
                                        txt, mk = make_ai_priority_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "view_prefilters":
                                        import config
                                        pre = "\n".join(f"• {k}" for k in config.KEYWORD_PREFILTER)
                                        txt = f"📄 <b>ACTIVE PREFILTERS</b> 📄\n\n{pre}"
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, txt)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                        
                                    elif data_val == "view_exclusions":
                                        import config
                                        exc = "\n".join(f"• {k}" for k in config.KEYWORD_EXCLUSIONS)
                                        txt = f"📄 <b>ACTIVE EXCLUSIONS</b> 📄\n\n{exc}"
                                        await asyncio.to_thread(telegram_notifier.send_telegram_message, txt)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "prompt_add_prefilter":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_ADDPRE"
                                         txt = "➕ <b>ADD PREFILTER</b>\n\nReply with the exact word/phrase to add."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "manage_keywords_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                         
                                    elif data_val == "prompt_remove_prefilter":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_REMPRE"
                                         txt = "➖ <b>REMOVE PREFILTER</b>\n\nReply with the exact word/phrase to remove."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "manage_keywords_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "prompt_add_exclusion":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_ADDEXC"
                                         txt = "➕ <b>ADD EXCLUSION</b>\n\nReply with the exact word/phrase to add."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "manage_keywords_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                         
                                    elif data_val == "prompt_remove_exclusion":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_REMEXC"
                                         txt = "➖ <b>REMOVE EXCLUSION</b>\n\nReply with the exact word/phrase to remove."
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "manage_keywords_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                         
                                    elif data_val == "toggle_geo_req":
                                        import config
                                        config.REQUIRE_GEOPOLITICAL = not getattr(config, 'REQUIRE_GEOPOLITICAL', True)
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "REQUIRE_GEOPOLITICAL=" in env_content:
                                                env_content = re.sub(r"^REQUIRE_GEOPOLITICAL=.*$", f"REQUIRE_GEOPOLITICAL={config.REQUIRE_GEOPOLITICAL}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nREQUIRE_GEOPOLITICAL={config.REQUIRE_GEOPOLITICAL}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        txt, mk = make_ai_priority_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "Toggled!")

                                    elif data_val == "prompt_edit_geo_text":
                                         import config
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_GEOTEXT"
                                         current_prompt = getattr(config, 'GEOPOLITICAL_PROMPT', 'Default')
                                         txt = (
                                             "✏️ <b>EDIT GEOPOLITICAL PROMPT</b>\n\n"
                                             "<b>How it works:</b>\n"
                                             "The AI reads breaking news and evaluates the instruction you provide below. "
                                             "It must answer `True` or `False`. If it answers `True`, the bot executes the trade. If `False`, it aborts.\n\n"
                                             "<b>Example 1 (Market Impact):</b>\n"
                                             "<i>\"True if the event involves geopolitical escalation that could move global markets. False for routine strikes against small targets.\"</i>\n\n"
                                             "<b>Example 2 (Specific Targets):</b>\n"
                                             "<i>\"True only if a major US military base or embassy was attacked. False otherwise.\"</i>\n\n"
                                             f"<b>Current Prompt:</b>\n<i>\"{current_prompt}\"</i>\n\n"
                                             "Reply with the new prompt instruction for the AI."
                                         )
                                         mk = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "ai_priorities_menu"}]]}
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)



                                    elif data_val.startswith("stats_page_"):
                                        try:
                                            page_num = int(data_val.split("_")[2])
                                            text_msg, mk = await asyncio.to_thread(build_stats_message, page_num)
                                            await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, text_msg, mk)
                                            await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                        except Exception as e:
                                            print(f"Stats page error: {e}")
                                            
                                    elif data_val == "export_csv":
                                        try:
                                            import db_engine
                                            import config
                                            mt5_acc = str(config.MT5_LOGIN)
                                            poly_acc = str(config.POLY_FUNDER_ADDRESS)
                                            csv_path = db_engine.export_csv_trades(mt5_acc, poly_acc)
                                            await asyncio.to_thread(telegram_notifier.send_document, chat_id, csv_path)
                                            await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "📥 CSV Exported!")
                                        except Exception as e:
                                            print(f"Export CSV error: {e}")
                                            await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "❌ Export Failed")

                                    elif data_val == "test_trigger_run":
                                        log_trade("Telegram Panel /test_trigger fired!")
                                        log_headline("[Telegram Panel] Injecting test airstrike headline...")
                                        if _engine:
                                            await _engine.inject_headline(_TEST_HEADLINE, source="Telegram Panel")
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "🎯 Snipe Triggered!")
                                        
                                    elif data_val == "edit_lot_menu":
                                        txt, mk = make_lot_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                        
                                    elif data_val.startswith("set_lot:"):
                                        new_lot = float(data_val.split(":")[1])
                                        import config
                                        old_lot = config.TRADE_LOTS
                                        config.TRADE_LOTS = new_lot
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            env_content = re.sub(r"^TRADE_LOTS=.*$", f"TRADE_LOTS={new_lot}", env_content, flags=re.MULTILINE)
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Lot size changed via Telegram Panel: {old_lot} -> {new_lot}")
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, f"✅ Lot set to {new_lot}")
                                        
                                        menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, menu_text, menu_markup)

                                    elif data_val == "custom_lot_prompt":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_LOT"
                                         txt = (
                                             "✏️ <b>ENTER CUSTOM LOT SIZE</b>\n\n"
                                             "Please reply with your desired lot size (e.g. <code>0.08</code> or <code>1.5</code>).\n\n"
                                             "<i>If you send an invalid value, the workflow will be aborted.</i>"
                                         )
                                         mk = {
                                             "inline_keyboard": [
                                                 [{"text": "❌ Cancel", "callback_data": "menu_main"}]
                                             ]
                                         }
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "edit_tp_menu":
                                        txt, mk = make_tp_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val.startswith("set_tp:"):
                                        new_tp = float(data_val.split(":")[1])
                                        import config
                                        old_tp = config.TP_PIPS
                                        config.TP_PIPS = new_tp
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "TP_PIPS=" in env_content:
                                                env_content = re.sub(r"^TP_PIPS=.*$", f"TP_PIPS={new_tp}", env_content, flags=re.MULTILINE)
                                            elif "TP_PERCENT=" in env_content:
                                                env_content = re.sub(r"^TP_PERCENT=.*$", f"TP_PIPS={new_tp}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nTP_PIPS={new_tp}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"TP pips changed via Telegram Panel: {old_tp} -> {new_tp}")
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, f"✅ TP set to {new_tp:.0f} pips")
                                        
                                        menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, menu_text, menu_markup)

                                    elif data_val == "custom_tp_prompt":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_TP"
                                         txt = (
                                             "✏️ <b>ENTER CUSTOM TAKE PROFIT (TP)</b>\n\n"
                                             "Please reply with your desired TP in pips (e.g. <code>120</code>).\n\n"
                                             "<i>If you send an invalid value, the workflow will be aborted.</i>"
                                         )
                                         mk = {
                                             "inline_keyboard": [
                                                 [{"text": "❌ Cancel", "callback_data": "menu_main"}]
                                             ]
                                         }
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val == "edit_sl_menu":
                                        txt, mk = make_sl_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val.startswith("set_sl:"):
                                        new_sl = float(data_val.split(":")[1])
                                        import config
                                        old_sl = config.SL_PIPS
                                        config.SL_PIPS = new_sl
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "SL_PIPS=" in env_content:
                                                env_content = re.sub(r"^SL_PIPS=.*$", f"SL_PIPS={new_sl}", env_content, flags=re.MULTILINE)
                                            elif "SL_PERCENT=" in env_content:
                                                env_content = re.sub(r"^SL_PERCENT=.*$", f"SL_PIPS={new_sl}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nSL_PIPS={new_sl}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"SL pips changed via Telegram Panel: {old_sl} -> {new_sl}")
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, f"✅ SL set to {new_sl:.0f} pips")
                                        
                                        menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, menu_text, menu_markup)

                                    elif data_val == "custom_sl_prompt":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_SL"
                                         txt = (
                                             "✏️ <b>ENTER CUSTOM STOP LOSS (SL)</b>\n\n"
                                             "Please reply with your desired SL in pips (e.g. <code>450</code>).\n\n"
                                             "<i>If you send an invalid value, the workflow will be aborted.</i>"
                                         )
                                         mk = {
                                             "inline_keyboard": [
                                                 [{"text": "❌ Cancel", "callback_data": "menu_main"}]
                                             ]
                                         }
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                         
                                    elif data_val == "edit_poly_menu":
                                        txt, mk = make_poly_menu_markup()
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)

                                    elif data_val.startswith("set_poly:"):
                                        new_poly = float(data_val.split(":")[1])
                                        import config
                                        old_poly = config.POLY_BET_SIZE
                                        config.POLY_BET_SIZE = new_poly
                                        
                                        env_path = ".env"
                                        if os.path.exists(env_path):
                                            with open(env_path, "r", encoding="utf-8") as f:
                                                env_content = f.read()
                                            import re
                                            if "POLY_BET_SIZE=" in env_content:
                                                env_content = re.sub(r"^POLY_BET_SIZE=.*$", f"POLY_BET_SIZE={new_poly}", env_content, flags=re.MULTILINE)
                                            else:
                                                env_content += f"\nPOLY_BET_SIZE={new_poly}"
                                            with open(env_path, "w", encoding="utf-8") as f:
                                                f.write(env_content)
                                                
                                        log_trade(f"Polymarket bet size changed via Telegram Panel: ${old_poly} -> ${new_poly}")
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, f"✅ Poly Size set to ${new_poly:.2f}")
                                        
                                        menu_text, menu_markup = await asyncio.to_thread(make_main_menu_markup)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, menu_text, menu_markup)

                                    elif data_val == "custom_poly_prompt":
                                         telegram_user_states[chat_id] = "WAIT_CUSTOM_POLY"
                                         txt = (
                                             "✏️ <b>ENTER CUSTOM POLYMARKET BET SIZE</b>\n\n"
                                             "Please reply with your desired Polymarket size in USD (e.g. <code>15.50</code>).\n"
                                             "<i>Minimum: $1.00</i>\n\n"
                                             "<i>If you send an invalid value, the workflow will be aborted.</i>"
                                         )
                                         mk = {
                                             "inline_keyboard": [
                                                 [{"text": "❌ Cancel", "callback_data": "menu_main"}]
                                             ]
                                         }
                                         await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, txt, mk)
                                         await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id)
                                        
                                    elif data_val == "close_menu":
                                        telegram_user_states.pop(chat_id, None)
                                        await asyncio.to_thread(telegram_notifier.edit_telegram_message, chat_id, msg_id, "🔒 <b>Airstrike Control Panel Closed</b>\n\n<i>Send /menu to open it again.</i>", None)
                                        await asyncio.to_thread(telegram_notifier.answer_callback_query, cq_id, "Closed")
            except Exception as e:
                log_headline(f"[Telegram Error] {e}", is_error=True)
                await asyncio.sleep(5)


async def main():
    global mt5_connected, _engine, _updater, _mt5_monitor
    os.makedirs("logs", exist_ok=True)
    
    import db_engine
    db_engine.init_db()
    
    import polymarket_engine
    polymarket_engine.init_clients()
    import technical_overlay

    # Start auto-updater daemon (background thread)
    _updater = AutoUpdater(log_callback=lambda msg: log_headline(msg))
    _updater.start()

    # Initialize MT5 (with portable auto-launch + retries + AutoTrading)
    mt5_connected = mt5_engine.initialize_mt5()
    if mt5_connected:
        log_trade("MT5 Engine Connected")
    else:
        log_trade("MT5 Engine FAILED — trading disabled")
        
    try:
        import telegram_notifier
        telegram_notifier.send_telegram_message(
            f"🟢 <b>AIRSTRIKE BOT ONLINE</b> 🟢\n\n"
            f"<b>MT5 Status:</b> {'Connected ✅' if mt5_connected else 'Failed ❌'}\n"
            f"<b>Model:</b> {os.getenv('GEMINI_MODEL', 'Default')}\n\n"
            f"<i>Listening for news...</i>"
        )
    except Exception as e:
        log_headline(f"[Telegram] Failed to send startup msg: {e}")

    # Start MT5 process watchdog (monitors PID, restarts if dead)
    _mt5_monitor = MT5Monitor(
        on_reconnect=_on_mt5_reconnect,
        log_callback=lambda msg: log_headline(msg),
    )
    initial_pid = mt5_engine._find_mt5_pid()
    _mt5_monitor.start(initial_pid=initial_pid)
    if initial_pid:
        log_trade(f"MT5 Watchdog active — PID: {initial_pid}")
    else:
        log_trade("MT5 Watchdog active — watching for MT5 process")

    # Start scraper engine
    _engine = ScraperEngine(
        log_callback=log_headline,
        trade_callback=log_trade,
    )
    log_headline("[System] All scrapers starting — running automatically...")

    with Live(generate_layout(), refresh_per_second=4, console=console) as live:
        await asyncio.gather(
            update_live_display(live),
            _engine.run(),
            terminal_command_reader(),
            telegram_command_reader(),
            asyncio.to_thread(mt5_engine.poll_open_trades_pnl),
            asyncio.to_thread(mt5_engine.manage_trailing_sl),
            asyncio.to_thread(technical_overlay.poll_technical_scale_in),
            polymarket_engine.poll_polymarket_resolutions(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        try:
            import telegram_notifier
            telegram_notifier.send_telegram_message(f"🚨 <b>BOT CRASHED</b> 🚨\n\n<pre>{str(e)}</pre>")
        except:
            pass
    finally:
        if _mt5_monitor:
            _mt5_monitor.stop()
        if _updater:
            _updater.stop()
            
        try:
            import telegram_notifier
            telegram_notifier.send_telegram_message("🔴 <b>AIRSTRIKE BOT OFFLINE</b> 🔴\n\n<i>Bot has been shut down or restarted.</i>")
        except Exception:
            pass
            
        console.print("\n[bold yellow]Bot stopped. Goodbye.[/bold yellow]")
        os._exit(0)

