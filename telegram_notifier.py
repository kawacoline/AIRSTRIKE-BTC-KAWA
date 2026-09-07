import os
import requests
import time
import urllib3
from dotenv import load_dotenv

# Suppress SSL certificate verification warnings for VPS compatibility
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
user_env = os.getenv("TELEGRAM_USERS", "")
TELEGRAM_USERS = [int(x.strip()) for x in user_env.split(",") if x.strip().isdigit()]

def send_telegram_message(text: str, reply_markup: dict = None):
    """Sends a markdown-formatted message to the configured Telegram users."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_USERS:
        return []
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    sent = []
    
    for user_id in TELEGRAM_USERS:
        payload = {
            "chat_id": user_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            resp = requests.post(url, json=payload, timeout=5, verify=False)
            if resp.status_code == 200:
                data = resp.json()
                msg_id = data.get("result", {}).get("message_id")
                if msg_id:
                    sent.append((user_id, msg_id))
        except Exception as e:
            print(f"[Telegram] Failed to send message to {user_id}: {e}")
    return sent

def edit_telegram_message(chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    """Edits an existing Telegram message's text and inline keyboard."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=5, verify=False)
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram] Failed to edit message {message_id}: {e}")
        return False

def answer_callback_query(callback_query_id: str, text: str = None):
    """Answers an inline callback query to stop the loading spinner."""
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        requests.post(url, json=payload, timeout=5, verify=False)
    except Exception as e:
        print(f"[Telegram] Failed to answer callback {callback_query_id}: {e}")

def send_document(chat_id: int, file_path: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            files = {'document': f}
            payload = {'chat_id': chat_id}
            requests.post(url, data=payload, files=files, timeout=10, verify=False)
    except Exception as e:
        print(f"[Telegram] Failed to send document: {e}")
def log_successful_trade(source: str, headline_obj, symbol: str, lots: float, is_retraction: bool = False, trade_direction: str = 'SELL', ai_result: dict = None):
    """Logs the execution locally and fires off a Telegram alert."""
    
    headline_text = getattr(headline_obj, "text", str(headline_obj))
    headline_url = getattr(headline_obj, "url", "")
    if not headline_url:
        headline_url = "No URL provided"
        
    news_time = "Unknown"
    if hasattr(headline_obj, "timestamp") and headline_obj.timestamp:
        news_time = headline_obj.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        
    exec_time = time.strftime('%Y-%m-%d %H:%M:%S Local')
    
    # 1. Write to local log file
    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", "successful_trades.log")
    
    action = "EMERGENCY CLOSE (RETRACTION)" if is_retraction else f"SNIPER {trade_direction} {lots} {symbol}"
    
    log_line = f"[{exec_time}] SOURCE: {source} | ACTION: {action} | HEADLINE: {headline_text}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log_line)
        
    # 2. Send Telegram Alert
    if is_retraction:
        msg = (
            f"🚨 <b>FAKE NEWS RETRACTION DETECTED</b> 🚨\n\n"
            f"<b>Source:</b> {source}\n"
            f"<b>News Time:</b> {news_time}\n"
            f"<b>Exec Time:</b> {exec_time}\n"
            f"<b>Headline:</b> {headline_text}\n"
            f"<b>URL:</b> {headline_url}\n\n"
            f"<b>Action Executed:</b> Emergency Close All Positions\n"
            f"<b>Status:</b> SUCCESS ✅"
        )
    else:
        msg = (
            f"🎯 <b>TRADE EVENT EXECUTED</b> 🎯\n\n"
            f"<b>Source:</b> {source}\n"
            f"<b>News Time:</b> {news_time}\n"
            f"<b>Exec Time:</b> {exec_time}\n"
            f"<b>Headline:</b> {headline_text}\n"
            f"<b>URL:</b> {headline_url}\n\n"
            f"<b>Action Executed:</b> Sniper {trade_direction} {lots} lots on {symbol}\n"
            f"<b>Status:</b> SUCCESS ✅\n\n"
            f"<i>AI confidently classified this as a CURRENT tradeable geopolitical event.</i>"
        )
        
    if ai_result:
        msg += f"\n\n<b>AI Reasoning breakdown:</b>\n"
        for k, v in ai_result.items():
            msg += f"- {k}: {v}\n"

    send_telegram_message(msg)

def log_failed_trade(source: str, headline_obj, symbol: str, lots: float, error_detail: str = "", trade_direction: str = "SELL"):
    """Sends a CRITICAL Telegram alert when MT5 fails to execute a trade."""
    
    headline_text = getattr(headline_obj, "text", str(headline_obj))
    headline_url = getattr(headline_obj, "url", "")
    if not headline_url:
        headline_url = "No URL provided"
        
    news_time = "Unknown"
    if hasattr(headline_obj, "timestamp") and headline_obj.timestamp:
        news_time = headline_obj.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        
    exec_time = time.strftime('%Y-%m-%d %H:%M:%S Local')
    
    # 1. Write to local log file
    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", "failed_trades.log")
    
    log_line = f"[{exec_time}] FAILED | SOURCE: {source} | HEADLINE: {headline_text} | ERROR: {error_detail}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log_line)
        
    # 2. Send CRITICAL Telegram Alert
    msg = (
        f"🔴🔴🔴 <b>TRADE EXECUTION FAILED</b> 🔴🔴🔴\n\n"
        f"<b>Source:</b> {source}\n"
        f"<b>News Time:</b> {news_time}\n"
        f"<b>Exec Time:</b> {exec_time}\n"
        f"<b>Headline:</b> {headline_text}\n"
        f"<b>URL:</b> {headline_url}\n\n"
        f"<b>Intended Action:</b> Sniper {trade_direction} {lots} lots on {symbol}\n"
        f"<b>Status:</b> ❌ FAILED ❌\n"
        f"<b>Error:</b> {error_detail if error_detail else 'MT5 returned False — check terminal'}\n\n"
        f"⚠️ <b>ACTION REQUIRED:</b> Check MT5 terminal immediately!\n"
        f"<i>The bot detected a real airstrike but could not execute the trade.</i>"
    )
    
    send_telegram_message(msg)

def log_trade_event_detected(source: str, headline_obj, ai_result: dict = None, trade_direction: str = 'SELL'):
    """Sends a Telegram alert the moment AI confirms a real geopolitical airstrike.
    This fires BEFORE any trade attempt, so users always see breaking news."""
    
    headline_text = getattr(headline_obj, "text", str(headline_obj))
    headline_url = getattr(headline_obj, "url", "")
    if not headline_url:
        headline_url = "No URL provided"
        
    news_time = "Unknown"
    if hasattr(headline_obj, "timestamp") and headline_obj.timestamp:
        news_time = headline_obj.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        
    detect_time = time.strftime('%Y-%m-%d %H:%M:%S Local')
    
    msg = (
        f"🚨 <b>TRADE EVENT DETECTED ({trade_direction})</b> 🚨\n\n"
        f"<b>Source:</b> {source}\n"
        f"<b>News Time:</b> {news_time}\n"
        f"<b>Detected:</b> {detect_time}\n"
        f"<b>Headline:</b> {headline_text}\n"
        f"<b>URL:</b> {headline_url}\n\n"
        f"<i>AI confirmed: Trade event — attempting {trade_direction} execution...</i>"
    )
    
    send_telegram_message(msg)

def log_ai_rejection(source: str, headline_obj, ai_result: dict = None):
    """Sends a Telegram alert when a headline passes regex but is rejected by Gemini AI."""
    
    headline_text = getattr(headline_obj, "text", str(headline_obj))
    headline_url = getattr(headline_obj, "url", "")
    if not headline_url:
        headline_url = "No URL provided"
        
    news_time = "Unknown"
    if hasattr(headline_obj, "timestamp") and headline_obj.timestamp:
        news_time = headline_obj.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        
    msg = (
        f"🤖 <b>AI REJECTED HEADLINE</b> 🤖\n\n"
        f"<b>Source:</b> {source}\n"
        f"<b>News Time:</b> {news_time}\n"
        f"<b>Headline:</b> {headline_text}\n"
        f"<b>URL:</b> {headline_url}\n\n"
        f"<i>AI concluded this does NOT meet the criteria for a tradeable geopolitical event.</i>"
    )
    
    # Optionally include the raw AI boolean results if provided
    if ai_result:
        msg += f"\n\n<b>AI Reasoning breakdown:</b>\n"
        for k, v in ai_result.items():
            msg += f"- {k}: {v}\n"
            
    send_telegram_message(msg)

def initialize_bot_commands_and_menu():
    """Configures the Telegram bot's command list and sets the menu button to open commands."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    
    # 1. Set commands
    commands_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands"
    commands_payload = {
        "commands": [
            {"command": "menu", "description": "Show premium interactive control panel"},
            {"command": "status", "description": "Scrapers, MT5 & live Polymarket balance/equity"},
            {"command": "poly", "description": "Polymarket account: balance, equity, W/L, history"},
            {"command": "stats", "description": "View total bot performance & trade history"},
            {"command": "help", "description": "Show available bot commands"},
            {"command": "test_trigger", "description": "Inject test kinetic airstrike headline"}
        ]
    }
    
    # 2. Set Menu Button to Commands
    menu_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setChatMenuButton"
    menu_payload = {
        "menu_button": {
            "type": "commands"
        }
    }
    
    success = True
    try:
        resp1 = requests.post(commands_url, json=commands_payload, timeout=5, verify=False)
        if resp1.status_code == 200:
            print("[Telegram] Bot commands registered successfully.")
        else:
            print(f"[Telegram] Failed to register commands: {resp1.status_code} - {resp1.text}")
            success = False
    except Exception as e:
        print(f"[Telegram] Exception during setMyCommands: {e}")
        success = False
        
    try:
        resp2 = requests.post(menu_url, json=menu_payload, timeout=5, verify=False)
        if resp2.status_code == 200:
            print("[Telegram] Menu button set to commands successfully.")
        else:
            print(f"[Telegram] Failed to set menu button: {resp2.status_code} - {resp2.text}")
            success = False
    except Exception as e:
        print(f"[Telegram] Exception during setChatMenuButton: {e}")
        success = False
        
    return success


