"""
mt5_engine.py
MetaTrader 5 connection, trade execution, and process watchdog engine.

Features:
  - Connects to a standard MT5 installation (Exness by default)
  - PID tracking: locates and tracks the exact MT5 process by executable path
  - Process watchdog: MT5Monitor daemon thread restarts MT5 if it dies
  - AutoTrading verification via API
  - Retry logic: waits for MT5 to boot before giving up
"""
import os
import time
import threading
from pathlib import Path
from typing import Callable, Optional

import psutil
import MetaTrader5 as mt5
from dotenv import load_dotenv
import db_engine

load_dotenv()

# ─── Configuration ──────────────────────────────────────────────────────────────
MT5_INSTALL_PATH = os.getenv("MT5_INSTALL_PATH", r"C:\Program Files\Exness MT5 Terminal")
MT5_TERMINAL_EXE = os.path.join(MT5_INSTALL_PATH, "terminal64.exe")

# Connection retry settings
MAX_INIT_RETRIES = 5
RETRY_DELAY = 3       # seconds between retries
MONITOR_INTERVAL = 5   # seconds between process health checks


def _verify_autotrading():
    """
    Check if AutoTrading is actually enabled via the MT5 API after connecting.
    Returns True if trading is allowed.
    """
    try:
        info = mt5.terminal_info()
        if info is None:
            return False
        trade_allowed = info.trade_allowed
        if trade_allowed:
            print("[MT5] AutoTrading is ENABLED ✓")
        else:
            print("[MT5] WARNING: AutoTrading is DISABLED in MT5 terminal!")
            print("[MT5] Please enable it manually: Tools → Options → Expert Advisors → Allow Algo Trading")
        return trade_allowed
    except Exception as e:
        print(f"[MT5] Could not verify AutoTrading status: {e}")
        return False


# ─── PID Tracking ────────────────────────────────────────────────────────────────

def _find_mt5_pid() -> Optional[int]:
    """
    Find the PID of our specific MT5 instance by matching the exe path.
    Returns the PID or None if not found.
    """
    target_exe = os.path.normpath(MT5_TERMINAL_EXE).lower()
    try:
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                if proc.info["name"] and "terminal64" in proc.info["name"].lower():
                    if proc.info["exe"]:
                        proc_exe = os.path.normpath(proc.info["exe"]).lower()
                        if proc_exe == target_exe:
                            return proc.info["pid"]
                    else:
                        # Can't verify path (access denied) — return it anyway
                        return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return None


def _is_pid_alive(pid: int) -> bool:
    """Check if a specific PID is still running."""
    if pid is None:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _is_mt5_running() -> bool:
    """Check if any terminal64.exe is currently running."""
    return _find_mt5_pid() is not None


# ─── Initialize ──────────────────────────────────────────────────────────────────

def initialize_mt5() -> bool:
    """
    Initialize MT5 with the specified path.
    """
    exe_exists = os.path.exists(MT5_TERMINAL_EXE)

    if not exe_exists:
        print(f"[MT5] WARNING: Exness MT5 not found at {MT5_TERMINAL_EXE}")
        print("[MT5] Will attempt to connect to any active global MT5 instance instead.")

    # Retry loop for initialization
    initialized = False
    for attempt in range(1, MAX_INIT_RETRIES + 1):
        try:
            if exe_exists:
                # This will automatically launch Exness if it's not running
                initialized = mt5.initialize(path=MT5_TERMINAL_EXE)
            else:
                initialized = mt5.initialize()

            if initialized:
                break
        except Exception as e:
            print(f"[MT5] initialize() exception on attempt {attempt}: {e}")

        if attempt < MAX_INIT_RETRIES:
            print(
                f"[MT5] initialize() failed (attempt {attempt}/{MAX_INIT_RETRIES}), "
                f"retrying in {RETRY_DELAY}s... error={mt5.last_error()}"
            )
            time.sleep(RETRY_DELAY)

    if not initialized:
        print(f"[MT5] initialize() failed after {MAX_INIT_RETRIES} attempts, error={mt5.last_error()}")
        return False

    # Login
    account = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    if not account or not password or not server:
        print("[MT5] Missing MT5 credentials in .env (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)")
        return False

    authorized = mt5.login(int(account), password=password, server=server)
    if not authorized:
        print(f"[MT5] login failed, error={mt5.last_error()}")
        return False

    pid = _find_mt5_pid()
    print(f"[MT5] Connected successfully! Account: {account}, Server: {server}, PID: {pid}")

    # Verify AutoTrading
    _verify_autotrading()

    return True


# ─── MT5 Process Monitor (Watchdog) ─────────────────────────────────────────────

class MT5Monitor:
    def __init__(
        self,
        on_reconnect: Optional[Callable[[bool], None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        interval: int = MONITOR_INTERVAL,
    ):
        self.on_reconnect = on_reconnect
        self.log_callback = log_callback
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._tracked_pid: Optional[int] = None
        self.restarts: int = 0
        self.status: str = "Starting..."

    def _log(self, msg: str):
        full = f"[MT5 Monitor] {msg}"
        if self.log_callback:
            self.log_callback(full)
        else:
            print(full)

    def start(self, initial_pid: Optional[int] = None):
        """Start the watchdog thread."""
        if self._thread and self._thread.is_alive():
            return
        self._tracked_pid = initial_pid or _find_mt5_pid()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="MT5Monitor",
            daemon=True,
        )
        self._thread.start()
        if self._tracked_pid:
            self._log(f"Watching MT5 process (PID: {self._tracked_pid})")
            self.status = f"Watching PID {self._tracked_pid}"
        else:
            self._log("Started — no MT5 PID found yet, will attempt to launch")
            self.status = "No PID — will launch"

    def stop(self):
        """Stop the watchdog thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._log("Stopped.")

    def _monitor_loop(self):
        """Main watchdog loop."""
        # Wait a bit for everything to settle on startup
        self._stop_event.wait(10)

        while not self._stop_event.is_set():
            try:
                alive = False

                # Check tracked PID
                if self._tracked_pid:
                    alive = _is_pid_alive(self._tracked_pid)
                else:
                    # No tracked PID — try to find one
                    found = _find_mt5_pid()
                    if found:
                        self._tracked_pid = found
                        alive = True
                        self._log(f"Found MT5 process (PID: {found})")
                        self.status = f"Watching PID {found}"

                if alive:
                    self.status = f"OK — PID {self._tracked_pid}"
                else:
                    # MT5 is dead — restart it
                    self._log(f"MT5 process DIED (was PID: {self._tracked_pid}) — restarting...")
                    self.status = "RESTARTING..."
                    self.restarts += 1
                    self._tracked_pid = None

                    # Shutdown old connection
                    try:
                        mt5.shutdown()
                    except Exception:
                        pass

                    # Relaunch
                    self._log("Relaunching MT5 terminal...")
                    time.sleep(5)

                    # Reconnect (this automatically launches it)
                    success = initialize_mt5()
                    new_pid = _find_mt5_pid()
                    
                    if new_pid:
                        self._tracked_pid = new_pid
                        self.status = f"Reconnected PID {new_pid}" if success else "Reconnect FAILED"
                        self._log(
                            f"Reconnection {'succeeded' if success else 'FAILED'} "
                            f"(PID: {new_pid}, restarts: {self.restarts})"
                        )
                    else:
                        self.status = "Launch FAILED"
                        self._log("Failed to relaunch MT5 — will retry next cycle")

                    if self.on_reconnect:
                        try:
                            self.on_reconnect(success)
                        except Exception:
                            pass

            except Exception as e:
                self._log(f"Monitor error: {e}")
                self.status = f"Error: {str(e)[:30]}"

            # Sleep in small increments
            for _ in range(self.interval):
                if self._stop_event.is_set():
                    return
                time.sleep(1)


# ─── Trade Execution ─────────────────────────────────────────────────────────────

def get_fill_mode(symbol):
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return mt5.ORDER_FILLING_FOK
    if getattr(symbol_info, 'filling_mode', 0) & 1:
        return mt5.ORDER_FILLING_FOK
    elif getattr(symbol_info, 'filling_mode', 0) & 2:
        return mt5.ORDER_FILLING_IOC
    else:
        return mt5.ORDER_FILLING_RETURN


def execute_sniper_trade(symbol="BTCUSD", lots=0.1, direction="SELL"):
    from config import SL_PIPS, TP_PIPS, BURST_TPS_PIPS

    # MT5 retcode → human-readable descriptions
    _RETCODE_NAMES = {
        10004: "REQUOTE",
        10006: "REJECTED",
        10007: "CANCELED",
        10010: "REQUEST_ERROR",
        10011: "TIMEOUT",
        10012: "INVALID_EXPIRATION",
        10013: "INVALID_VOLUME",
        10014: "INVALID_PRICE",
        10015: "INVALID_STOPS",
        10016: "TRADE_DISABLED",
        10017: "MARKET_CLOSED",
        10018: "NOT_ENOUGH_MONEY",
        10019: "PRICES_CHANGED",
        10020: "NO_QUOTE",
        10021: "ORDER_CHANGED",
        10022: "TOO_MANY_REQUESTS",
        10024: "FROZEN",
        10026: "CONNECTION_LOST",
        10027: "LIMIT_ORDERS_EXCEEDED",
        10030: "INVALID_FILL",
        10031: "NO_CONNECTION",
        10032: "ONLY_REAL",
        10033: "LIMIT_VOLUME",
    }
    
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        err = f"Symbol {symbol} not found in Market Watch"
        print(f"[MT5] {err}")
        return False, err
        
    if not mt5.symbol_select(symbol, True):
        err = f"Failed to add {symbol} to Market Watch"
        print(f"[MT5] {err}")
        return False, err
        
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        err = f"No tick data for {symbol} — market may be closed"
        print(f"[MT5] {err}")
        return False, err

    # Check account balance before attempting trade
    account_info = mt5.account_info()
    if account_info:
        balance = account_info.balance
        free_margin = account_info.margin_free
        print(f"[MT5] Account balance: ${balance:.2f} | Free margin: ${free_margin:.2f}")
        if free_margin < 1.0:
            err = f"NOT ENOUGH MONEY — Balance: ${balance:.2f}, Free margin: ${free_margin:.2f}"
            print(f"[MT5] {err}")
            return False, err
    
    digits = symbol_info.digits
    import config
    
    if direction == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        sl_price = round(price - config.SL_PIPS, digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        sl_price = round(price + config.SL_PIPS, digits)
    
    if getattr(config, 'BURST_EXECUTION', False):
        print(f"Preparing BURST {direction} {lots} {symbol} @ {price} | SL: {sl_price}")
        
        # Calculate split lots
        min_vol = symbol_info.volume_min
        step_vol = symbol_info.volume_step
        num_tps = len(BURST_TPS_PIPS)
        if num_tps == 0:
            num_tps = 1
            
        chunk = round((lots / num_tps) / step_vol) * step_vol
        chunk = max(min_vol, chunk)
        
        if chunk * num_tps > lots + step_vol * (num_tps / 2.0): 
            print(f"[MT5] Trade lot size {lots} too small for Burst Execution splitting (min {num_tps}x {chunk}). Falling back to single order.")
            burst_mode = False
        else:
            burst_mode = True
            
        if burst_mode:
            volumes = [chunk] * (num_tps - 1)
            last_chunk = lots - sum(volumes)
            last_chunk = round(last_chunk / step_vol) * step_vol
            if last_chunk < min_vol:
                last_chunk = min_vol
            volumes.append(last_chunk)
            
            tps = []
            for tp_pip in BURST_TPS_PIPS:
                if direction == "BUY":
                    tps.append(round(price + tp_pip, digits))
                else:
                    tps.append(round(price - tp_pip, digits))
                    
            tickets = []
            errors = []
            import uuid
            burst_id = str(uuid.uuid4())[:6]
            
            for i in range(num_tps):
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": volumes[i],
                    "type": order_type,
                    "price": price,
                    "sl": sl_price,
                    "tp": tps[i],
                    "deviation": 20,
                    "magic": 234000,
                    "comment": f"B[{burst_id}] {i+1}/{num_tps}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": get_fill_mode(symbol),
                }
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    tickets.append(str(res.order))
                else:
                    if res:
                        errors.append(f"Retcode {res.retcode} ({_RETCODE_NAMES.get(res.retcode, 'UNKNOWN')})")
                    else:
                        errors.append(f"order_send returned None. last_error={mt5.last_error()}")
            
            if tickets:
                ticket_str = ",".join(tickets)
                print(f"Burst orders executed successfully! Tickets: {ticket_str}")
                return True, ticket_str
            else:
                err_str = " | ".join(errors)
                print(f"[MT5] All burst orders failed: {err_str}")
                return False, err_str
    
    # --- Standard Execution Fallback ---
    if direction == "BUY":
        tp_price = round(price + config.TP_PIPS, digits)
    else:
        tp_price = round(price - config.TP_PIPS, digits)
    
    print(f"Preparing {direction} {lots} {symbol} @ {price} | SL: {sl_price} | TP: {tp_price}")
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": sl_price,
        "tp": tp_price,
        "deviation": 20,
        "magic": 234000,
        "comment": f"Sniper {direction}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": get_fill_mode(symbol),
    }
    result = mt5.order_send(request)
    if result is None:
        err = f"order_send returned None — MT5 disconnected? last_error={mt5.last_error()}"
        print(f"[MT5] {err}")
        return False, err
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        code_name = _RETCODE_NAMES.get(result.retcode, "UNKNOWN")
        err = f"Retcode {result.retcode} ({code_name}) — {result.comment}"
        print(f"[MT5] Order failed: {err}")
        return False, err
    print(f"Order executed successfully! Ticket: {result.order}")
    return True, str(result.order)


def sync_all_trades_with_mt5():
    """Forces a sync of all MT5 trades in the DB with their actual MT5 deal history to capture manual tweaks and true Net PnL."""
    import db_engine
    import datetime
    if mt5.terminal_info() is None:
        return
        
    # 1. Recover any ghost trades (trades executed by bot but not saved to DB)
    date_from = datetime.datetime.now() - datetime.timedelta(days=30)
    date_to = datetime.datetime.now() + datetime.timedelta(days=1)
    all_deals = mt5.history_deals_get(date_from, date_to)
    
    import config
    account_id = str(config.MT5_LOGIN)
    
    if all_deals:
        known_tickets = set(db_engine.get_all_mt5_tickets(account_id))
        for d in all_deals:
            if d.magic == 234000 and d.entry == mt5.DEAL_ENTRY_IN:
                # This is a bot-initiated trade entry
                if d.position_id not in known_tickets:
                    # Ghost trade found! Insert into DB
                    trade_type = 'SELL' if d.type == mt5.ORDER_TYPE_SELL else 'BUY'
                    open_time = datetime.datetime.utcfromtimestamp(d.time).isoformat()
                    db_engine.record_missing_trade(d.position_id, d.symbol, d.volume, trade_type, open_time, account_id)
                    known_tickets.add(d.position_id)
                    print(f"[MT5 Sync] Recovered missing ghost trade! Ticket: {d.position_id}")
        
    # 2. Sync PnL and Status for all trades in DB
    tickets = db_engine.get_all_mt5_tickets(account_id)
    for ticket_id in tickets:
        deals = mt5.history_deals_get(position=ticket_id)
        if deals:
            total_net_pnl = sum(d.profit + d.commission + d.swap for d in deals)
            # Check if position is still open
            pos = mt5.positions_get(ticket=ticket_id)
            status = 'OPEN' if (pos and len(pos) > 0) else 'CLOSED'
            db_engine.update_trade_sync(ticket_id, status, total_net_pnl)


def poll_open_trades_pnl():
    """Background task to check MT5 for closed trades and update SQLite DB."""
    while True:
        try:
            # Check if mt5 is connected
            if mt5.terminal_info() is None:
                time.sleep(10)
                continue
                
            import config
            account_id = str(config.MT5_LOGIN)
            open_trades = db_engine.get_open_trades(account_id)
            if not open_trades:
                time.sleep(10)
                continue
                
            for ticket_id, symbol, trade_type, lots in open_trades:
                deals = mt5.history_deals_get(position=ticket_id)
                if deals:
                    out_deals = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
                    if out_deals:
                        # Position is closed
                        total_pnl = sum(d.profit + d.commission + d.swap for d in deals)
                        close_price = out_deals[-1].price
                        db_engine.update_closed_trade(ticket_id, close_price, total_pnl)
                        print(f"[MT5 PnL Tracker] Trade #{ticket_id} CLOSED. PnL: ${total_pnl:.2f}")
        except Exception as e:
            print(f"[MT5 PnL Tracker] Error: {e}")
        time.sleep(10)


def manage_trailing_sl():
    """Background task to shift SL to break even when TP hit."""
    import config
    import re
    while True:
        try:
            if mt5.terminal_info() is None:
                time.sleep(10)
                continue
            
            positions = mt5.positions_get()
            if not positions:
                time.sleep(5)
                continue
                
            burst_groups = {}
            for p in positions:
                if p.magic == 234000 and p.comment.startswith("B["):
                    m = re.match(r"B\[(.*?)\]\s+(\d+)/(\d+)", p.comment)
                    if m:
                        b_id, chunk_idx, total_chunks = m.groups()
                        chunk_idx = int(chunk_idx)
                        if b_id not in burst_groups:
                            burst_groups[b_id] = {}
                        burst_groups[b_id][chunk_idx] = p
            
            shift_tp_idx = getattr(config, 'SHIFT_SL_AFTER_TP', 2)
            be_offset = getattr(config, 'BREAKEVEN_PROFIT_PIPS', 2000.0)
            
            for b_id, open_chunks in burst_groups.items():
                tp_hit = True
                for i in range(1, shift_tp_idx + 1):
                    if i in open_chunks:
                        tp_hit = False
                        break
                        
                if tp_hit:
                    for idx, p in open_chunks.items():
                        digits = mt5.symbol_info(p.symbol).digits
                        point = mt5.symbol_info(p.symbol).point
                        if p.type == mt5.ORDER_TYPE_BUY:
                            new_sl = round(p.price_open + (be_offset * point), digits)
                            if p.sl < new_sl and p.price_current > new_sl:
                                req = {
                                    "action": mt5.TRADE_ACTION_SLTP,
                                    "position": p.ticket,
                                    "symbol": p.symbol,
                                    "sl": new_sl,
                                    "tp": p.tp
                                }
                                res = mt5.order_send(req)
                                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                    print(f"[MT5 SL Shift] Shifted SL of {p.ticket} to {new_sl} (+{be_offset} pips)")
                        else:
                            new_sl = round(p.price_open - (be_offset * point), digits)
                            if p.sl > new_sl and p.price_current < new_sl:
                                req = {
                                    "action": mt5.TRADE_ACTION_SLTP,
                                    "position": p.ticket,
                                    "symbol": p.symbol,
                                    "sl": new_sl,
                                    "tp": p.tp
                                }
                                res = mt5.order_send(req)
                                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                    print(f"[MT5 SL Shift] Shifted SL of {p.ticket} to {new_sl} (+{be_offset} pips)")
        except Exception as e:
            print(f"[MT5 SL Shift] Error: {e}")
        
        time.sleep(5)


def emergency_close_all(symbol="BTCUSD"):
    positions = mt5.positions_get(symbol=symbol)
    if positions is None or len(positions) == 0:
        print("No open positions to close.")
        return
    for position in positions:
        if position.type == mt5.ORDER_TYPE_SELL:
            close_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).ask
        else:
            close_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).bid
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": close_type,
            "position": position.ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "Emergency Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": get_fill_mode(symbol),
        }
        result = mt5.order_send(close_request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Failed to close position {position.ticket}, retcode: {result.retcode}")
        else:
            print(f"Successfully closed position {position.ticket}")


if __name__ == "__main__":
    if initialize_mt5():
        pid = _find_mt5_pid()
        print(f"Engine Ready. MT5 PID: {pid}")
        print("Starting watchdog (Ctrl+C to stop)...")
        monitor = MT5Monitor(
            on_reconnect=lambda ok: print(f"  Reconnect result: {ok}"),
            log_callback=print,
        )
        monitor.start(initial_pid=pid)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            monitor.stop()
            print("Done.")
