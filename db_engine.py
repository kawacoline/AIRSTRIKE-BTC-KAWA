import sqlite3
import os
import datetime

DB_PATH = "data/bot_stats.db"

def _get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = _get_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            ticket_id INTEGER PRIMARY KEY,
            symbol TEXT,
            lots REAL,
            type TEXT,
            open_price REAL,
            open_time TEXT,
            news_headline TEXT,
            news_url TEXT,
            news_source TEXT,
            status TEXT,
            close_price REAL,
            close_time TEXT,
            pnl REAL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS polymarket_trades (
            order_id TEXT PRIMARY KEY,
            market_question TEXT,
            bet_size REAL,
            status TEXT,
            open_time TEXT,
            close_time TEXT,
            pnl REAL,
            news_headline TEXT,
            news_url TEXT,
            news_source TEXT
        )
    ''')
    
    # Dynamic Schema Evolution
    try:
        c.execute('ALTER TABLE trades ADD COLUMN account_id TEXT DEFAULT "unknown"')
    except sqlite3.OperationalError:
        pass # Column already exists
        
    try:
        c.execute('ALTER TABLE polymarket_trades ADD COLUMN account_id TEXT DEFAULT "unknown"')
    except sqlite3.OperationalError:
        pass # Column already exists
        
    conn.commit()
    conn.close()

def record_new_trade(ticket_id: int, symbol: str, lots: float, type: str, open_price: float, headline: str, url: str, source: str, account_id: str = "unknown"):
    conn = _get_conn()
    c = conn.cursor()
    open_time = datetime.datetime.utcnow().isoformat()
    c.execute('''
        INSERT OR IGNORE INTO trades 
        (ticket_id, symbol, lots, type, open_price, open_time, news_headline, news_url, news_source, status, pnl, account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0.0, ?)
    ''', (ticket_id, symbol, lots, type, open_price, open_time, headline, url, source, account_id))
    conn.commit()
    conn.close()

def record_missing_trade(ticket_id: int, symbol: str, lots: float, trade_type: str, open_time: str, account_id: str = "unknown"):
    conn = _get_conn()
    c = conn.cursor()
    c.execute('''
        INSERT OR IGNORE INTO trades 
        (ticket_id, symbol, lots, type, open_price, open_time, news_headline, news_url, news_source, status, pnl, account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0.0, ?)
    ''', (ticket_id, symbol, lots, trade_type, 0.0, open_time, 'Ghost Trade / Recovered', '#', 'System', account_id))
    conn.commit()
    conn.close()

def update_closed_trade(ticket_id: int, close_price: float, pnl: float):
    conn = _get_conn()
    c = conn.cursor()
    close_time = datetime.datetime.utcnow().isoformat()
    c.execute('''
        UPDATE trades
        SET status = 'CLOSED', close_price = ?, pnl = ?, close_time = ?
        WHERE ticket_id = ?
    ''', (close_price, pnl, close_time, ticket_id))
    conn.commit()
    conn.close()

def update_trade_sync(ticket_id: int, status: str, pnl: float):
    conn = _get_conn()
    c = conn.cursor()
    if status == 'CLOSED':
        close_time = datetime.datetime.utcnow().isoformat()
        c.execute('''
            UPDATE trades
            SET status = ?, pnl = ?, close_time = COALESCE(close_time, ?)
            WHERE ticket_id = ?
        ''', (status, pnl, close_time, ticket_id))
    else:
        c.execute('''
            UPDATE trades
            SET status = ?, pnl = ?
            WHERE ticket_id = ?
        ''', (status, pnl, ticket_id))
    conn.commit()
    conn.close()

def get_all_mt5_tickets(account_id: str = None):
    conn = _get_conn()
    c = conn.cursor()
    if account_id:
        c.execute('SELECT ticket_id FROM trades WHERE account_id = ?', (account_id,))
    else:
        c.execute('SELECT ticket_id FROM trades')
    res = [r[0] for r in c.fetchall()]
    conn.close()
    return res

def get_open_trades(account_id: str = None):
    """Returns a list of ticket IDs for trades that are still open."""
    conn = _get_conn()
    c = conn.cursor()
    if account_id:
        c.execute('SELECT ticket_id, symbol, type, lots FROM trades WHERE status = "OPEN" AND account_id = ?', (account_id,))
    else:
        c.execute('SELECT ticket_id, symbol, type, lots FROM trades WHERE status = "OPEN"')
    res = c.fetchall()
    conn.close()
    return res

def get_stats_summary(account_id: str = None):
    conn = _get_conn()
    c = conn.cursor()
    
    if account_id:
        c.execute('SELECT COUNT(*) FROM trades WHERE status = "CLOSED" AND account_id = ?', (account_id,))
        total_closed = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM trades WHERE status = "CLOSED" AND pnl > 0 AND account_id = ?', (account_id,))
        wins = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM trades WHERE status = "CLOSED" AND pnl <= 0 AND account_id = ?', (account_id,))
        losses = c.fetchone()[0]
        
        c.execute('SELECT SUM(pnl) FROM trades WHERE status = "CLOSED" AND account_id = ?', (account_id,))
        total_pnl = c.fetchone()[0] or 0.0
    else:
        c.execute('SELECT COUNT(*) FROM trades WHERE status = "CLOSED"')
        total_closed = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM trades WHERE status = "CLOSED" AND pnl > 0')
        wins = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM trades WHERE status = "CLOSED" AND pnl <= 0')
        losses = c.fetchone()[0]
        
        c.execute('SELECT SUM(pnl) FROM trades WHERE status = "CLOSED"')
        total_pnl = c.fetchone()[0] or 0.0
        
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    
    conn.close()
    return {
        "total_trades": total_closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl
    }

def get_recent_trades(limit=5, offset=0, account_id: str = None):
    conn = _get_conn()
    c = conn.cursor()
    if account_id:
        c.execute('''
            SELECT ticket_id, symbol, lots, type, status, pnl, open_time, close_time, news_headline, news_url, news_source
            FROM trades
            WHERE account_id = ?
            ORDER BY open_time DESC
            LIMIT ? OFFSET ?
        ''', (account_id, limit, offset))
    else:
        c.execute('''
            SELECT ticket_id, symbol, lots, type, status, pnl, open_time, close_time, news_headline, news_url, news_source
            FROM trades
            ORDER BY open_time DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))
    rows = c.fetchall()
    conn.close()
    
    res = []
    for r in rows:
        res.append({
            "ticket_id": r[0],
            "symbol": r[1],
            "lots": r[2],
            "type": r[3],
            "status": r[4],
            "pnl": r[5],
            "open_time": r[6],
            "close_time": r[7],
            "headline": r[8],
            "url": r[9],
            "source": r[10]
        })
    return res

# ==========================================
# Polymarket Tracking Functions
# ==========================================

def record_poly_trade(order_id: str, market_question: str, bet_size: float, headline: str, url: str, source: str, account_id: str = "unknown"):
    conn = _get_conn()
    c = conn.cursor()
    open_time = datetime.datetime.utcnow().isoformat()
    c.execute('''
        INSERT OR IGNORE INTO polymarket_trades 
        (order_id, market_question, bet_size, status, open_time, pnl, news_headline, news_url, news_source, account_id)
        VALUES (?, ?, ?, 'OPEN', ?, 0.0, ?, ?, ?, ?)
    ''', (order_id, market_question, bet_size, open_time, headline, url, source, account_id))
    conn.commit()
    conn.close()

def update_poly_trade(order_id: str, pnl: float):
    conn = _get_conn()
    c = conn.cursor()
    close_time = datetime.datetime.utcnow().isoformat()
    c.execute('''
        UPDATE polymarket_trades
        SET status = 'CLOSED', pnl = ?, close_time = ?
        WHERE order_id = ?
    ''', (pnl, close_time, order_id))
    conn.commit()
    conn.close()

def get_open_poly_trades(account_id: str = None):
    conn = _get_conn()
    c = conn.cursor()
    if account_id:
        c.execute('SELECT order_id, market_question, bet_size FROM polymarket_trades WHERE status = "OPEN" AND account_id = ?', (account_id,))
    else:
        c.execute('SELECT order_id, market_question, bet_size FROM polymarket_trades WHERE status = "OPEN"')
    res = c.fetchall()
    conn.close()
    return res

def get_poly_stats_summary(account_id: str = None):
    conn = _get_conn()
    c = conn.cursor()
    
    if account_id:
        c.execute('SELECT COUNT(*) FROM polymarket_trades WHERE status = "CLOSED" AND account_id = ?', (account_id,))
        total_closed = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM polymarket_trades WHERE status = "CLOSED" AND pnl > 0 AND account_id = ?', (account_id,))
        wins = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM polymarket_trades WHERE status = "CLOSED" AND pnl <= 0 AND account_id = ?', (account_id,))
        losses = c.fetchone()[0]
        
        c.execute('SELECT SUM(pnl) FROM polymarket_trades WHERE status = "CLOSED" AND account_id = ?', (account_id,))
        total_pnl = c.fetchone()[0] or 0.0
    else:
        c.execute('SELECT COUNT(*) FROM polymarket_trades WHERE status = "CLOSED"')
        total_closed = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM polymarket_trades WHERE status = "CLOSED" AND pnl > 0')
        wins = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM polymarket_trades WHERE status = "CLOSED" AND pnl <= 0')
        losses = c.fetchone()[0]
        
        c.execute('SELECT SUM(pnl) FROM polymarket_trades WHERE status = "CLOSED"')
        total_pnl = c.fetchone()[0] or 0.0
        
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    
    conn.close()
    return {
        "total_trades": total_closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl
    }

def get_recent_poly_trades(limit=5, offset=0, account_id: str = None):
    conn = _get_conn()
    c = conn.cursor()
    if account_id:
        c.execute('''
            SELECT order_id, market_question, bet_size, status, pnl, open_time, close_time, news_headline, news_url, news_source
            FROM polymarket_trades
            WHERE account_id = ?
            ORDER BY open_time DESC
            LIMIT ? OFFSET ?
        ''', (account_id, limit, offset))
    else:
        c.execute('''
            SELECT order_id, market_question, bet_size, status, pnl, open_time, close_time, news_headline, news_url, news_source
            FROM polymarket_trades
            ORDER BY open_time DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))
    rows = c.fetchall()
    conn.close()
    
    res = []
    for r in rows:
        res.append({
            "order_id": r[0],
            "market_question": r[1],
            "bet_size": r[2],
            "status": r[3],
            "pnl": r[4],
            "open_time": r[5],
            "close_time": r[6],
            "headline": r[7],
            "url": r[8],
            "source": r[9]
        })
    return res

def export_csv_trades(mt5_account_id: str = None, poly_account_id: str = None):
    conn = _get_conn()
    c = conn.cursor()
    if mt5_account_id:
        c.execute('SELECT ticket_id, symbol, lots, type, status, pnl, open_time, close_time FROM trades WHERE account_id = ? ORDER BY open_time DESC', (mt5_account_id,))
    else:
        c.execute('SELECT ticket_id, symbol, lots, type, status, pnl, open_time, close_time FROM trades ORDER BY open_time DESC')
    mt5_rows = c.fetchall()
    
    if poly_account_id:
        c.execute('SELECT order_id, market_question, bet_size, status, pnl, open_time, close_time FROM polymarket_trades WHERE account_id = ? ORDER BY open_time DESC', (poly_account_id,))
    else:
        c.execute('SELECT order_id, market_question, bet_size, status, pnl, open_time, close_time FROM polymarket_trades ORDER BY open_time DESC')
    poly_rows = c.fetchall()
    conn.close()
    
    import csv
    import os
    os.makedirs('data', exist_ok=True)
    filepath = 'data/bot_trades_export.csv'
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Platform', 'ID', 'Symbol/Market', 'Size', 'Type', 'Status', 'PnL', 'Open Time', 'Close Time'])
        for r in mt5_rows:
            writer.writerow(['MT5', r[0], r[1], r[2], r[3], r[4], f"{r[5]:.2f}", r[6], r[7] or ''])
        for r in poly_rows:
            writer.writerow(['Polymarket', r[0], r[1], r[2], 'LIMIT', r[3], f"{r[4]:.2f}", r[5], r[6] or ''])
            
    return filepath
