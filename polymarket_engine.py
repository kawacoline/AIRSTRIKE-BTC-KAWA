import os
import time
import httpx
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Import PyClob for order signing and execution
from py_clob_client_v2 import ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions, ApiCreds
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
from py_clob_client_v2.order_builder.constants import BUY

from web3 import Web3
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.models import DepositWalletCall, TransactionType
from py_builder_signing_sdk.config import BuilderApiKeyCreds, BuilderConfig

load_dotenv()

POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY")
POLY_FUNDER_ADDRESS = os.getenv("POLY_FUNDER_ADDRESS")
POLY_API_KEY = os.getenv("POLY_API_KEY")
POLY_API_SECRET = os.getenv("POLY_API_SECRET")
POLY_API_PASSPHRASE = os.getenv("POLY_API_PASSPHRASE")
BUILDER_CODE = os.getenv("BUILDER_CODE", "0x1188aca0b468060c943ce6f6c9f2956223c611d5808c54b507e403d2dc6cf9af")

# Global variables
_clob_client = None
_gamma_client = None
_relayer_client = None

# Smart Contract Constants
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
NORMAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"

ctf_abi = [
    {"name": "safeTransferFrom", "type": "function", "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"}, {"name": "data", "type": "bytes"}], "outputs": []},
    {"name": "balanceOf", "type": "function", "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "isApprovedForAll", "type": "function", "inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}], "outputs": [{"name": "", "type": "bool"}]},
    {"name": "setApprovalForAll", "type": "function", "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}], "outputs": []}
]

adapter_abi = [
    {"name": "redeemPositions", "type": "function", "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"}, {"name": "conditionId", "type": "bytes32"}, {"name": "indexSets", "type": "uint256[]"}], "outputs": []}
]

w3 = Web3(Web3.HTTPProvider("https://polygon.drpc.org"))

def init_relayer():
    global _relayer_client
    if not POLY_PRIVATE_KEY:
        return False
        
    try:
        builder_config = BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=POLY_API_KEY,
                secret=POLY_API_SECRET,
                passphrase=POLY_API_PASSPHRASE,
            )
        )
        _relayer_client = RelayClient(
            "https://relayer-v2.polymarket.com",
            137,
            POLY_PRIVATE_KEY.strip("'"),
            builder_config
        )
        print("[Polymarket] Relayer initialized successfully.")
        return True
    except Exception as e:
        print(f"[Polymarket] Failed to init relayer: {e}")
        return False

def init_clients():
    global _clob_client, _gamma_client
    if not POLY_PRIVATE_KEY:
        print("[Polymarket] Missing credentials. Cannot initialize.")
        return False
        
    try:
        # Initialize CLOB client with Signature Type 3 for deposit wallets
        _clob_client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=POLY_PRIVATE_KEY,
            creds=ApiCreds(
                api_key=POLY_API_KEY,
                api_secret=POLY_API_SECRET,
                api_passphrase=POLY_API_PASSPHRASE
            ),
            signature_type=3,
            funder=POLY_FUNDER_ADDRESS
        )
        
        # Initialize resilient HTTP client for Gamma API
        _gamma_client = httpx.Client(
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        
        # Initialize the Relayer
        init_relayer()
        
        print("[Polymarket] Clients initialized successfully.")
        return True
    except Exception as e:
        print(f"[Polymarket] Failed to init clients: {e}")
        return False

def get_active_btc_markets():
    """Fetches active, unclosed BTC markets from Gamma API that resolve relatively soon."""
    if not _gamma_client:
        return []
        
    try:
        events = []
        offset = 0
        while offset < 2000:
            url = f"https://gamma-api.polymarket.com/events?tag_slug=bitcoin&active=true&closed=false&limit=100&offset={offset}"
            resp = None
            for attempt in range(3):
                try:
                    resp = _gamma_client.get(url, timeout=15.0)
                    if resp.status_code == 200:
                        break
                    print(f"[Polymarket] Gamma API error: {resp.status_code}. Retrying...")
                    time.sleep(1)
                except Exception as e:
                    print(f"[Polymarket] Gamma API exception: {e}. Retrying...")
                    time.sleep(1)
                    
            if resp and resp.status_code == 200:
                data = resp.json()
                if not data:
                    break
                events.extend(data)
                if len(data) < 100:
                    break
            else:
                # If all 3 retries failed, break to avoid infinite loops
                break
                
            offset += 100
                    
        if not events:
            print("[Polymarket] Failed to fetch Gamma API or no events.")
            return []
            
        target_markets = []
        now = datetime.now(timezone.utc)
        
        for event in events:
            title = event.get("title", "").lower()
            if "bitcoin" not in title and "btc" not in title:
                continue
                
            end_date_str = event.get("endDate")
            if not end_date_str:
                continue
                
            try:
                # 2024-06-03T18:00:00Z
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            except Exception:
                continue
                
            # Filter for markets expiring in the future
            time_left = (end_date - now).total_seconds()
            if time_left > 0:
                # Event contains markets
                for market in event.get("markets", []):
                    if market.get("active") and not market.get("closed"):
                        question = market.get("question", "")
                        is_5m = False
                        
                        import re
                        m = re.search(r'(\d{1,2}(?::\d{2})?[AP]M)-(\d{1,2}(?::\d{2})?[AP]M)', question)
                        if m:
                            s, e = m.groups()
                            if ':' not in s: s = s[:-2] + ':00' + s[-2:]
                            if ':' not in e: e = e[:-2] + ':00' + e[-2:]
                            try:
                                dt_s = datetime.strptime(s, '%I:%M%p')
                                dt_e = datetime.strptime(e, '%I:%M%p')
                                diff = (dt_e - dt_s).total_seconds() / 60
                                if diff < 0: diff += 24*60
                                if diff == 5.0:
                                    is_5m = True
                            except Exception:
                                pass
                                
                        if is_5m:
                            target_markets.append({
                                "condition_id": market.get("conditionId"),
                                "question": question,
                                "outcomes": market.get("outcomes", []),
                                "clobTokenIds": market.get("clobTokenIds", []),
                                "end_date": end_date,
                                "time_left_sec": time_left
                            })
                        
        # Sort by shortest time left
        target_markets.sort(key=lambda x: x["time_left_sec"])
        return target_markets
        
    except Exception as e:
        print(f"[Polymarket] get_active_btc_markets error: {e}")
        return []

def execute_polymarket_bet(market, bet_size_usd=2.50, max_slippage_price=0.99):
    """
    Executes a FOK (Fill Or Kill) limit order to buy "No" (or "Down") shares.
    bet_size_usd: the amount of pUSD to spend.
    max_slippage_price: the worst price we are willing to accept.
    """
    if not _clob_client:
        print("[Polymarket] CLOB client not initialized.")
        return False, "Not initialized"
        
    try:
        # Identify the "No" or "Down" token ID
        import json
        outcomes_raw = market.get("outcomes", "[]")
        token_ids_raw = market.get("clobTokenIds", "[]")
        
        try:
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
        except:
            outcomes = []
            token_ids = []
            
        no_token_id = None
        for i, outcome in enumerate(outcomes):
            if str(outcome).lower() in ("no", "down") and i < len(token_ids):
                no_token_id = token_ids[i]
                break
                
        if not no_token_id:
            return False, "Could not find 'No' outcome token ID"
            
        print(f"[Polymarket] Executing bet on: {market['question']}")
        
        # Create GTC Limit Order so it stays open on the book
        size_shares = round(bet_size_usd / max_slippage_price, 2)
        
        order_args = OrderArgs(
            token_id=no_token_id,
            side=BUY,
            price=max_slippage_price,
            size=size_shares
        )
        
        buy_order = _clob_client.create_order(
            order_args=order_args,
            options=PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)
        )
        
        # Submit as GTC so it rests on the orderbook
        resp = _clob_client.post_order(buy_order, OrderType.GTC)
        if resp and resp.get("success"):
            print(f"[Polymarket] Bet executed successfully! Order ID: {resp.get('orderID')}")
            return True, resp.get("orderID")
        else:
            err = resp.get('errorMsg', 'Unknown error')
            print(f"[Polymarket] Bet failed: {err}")
            return False, err
            
    except Exception as e:
        print(f"[Polymarket] execute_polymarket_bet exception: {e}")
        return False, str(e)


async def resolve_open_poly_trades():
    """
    Paper-tracking: Checks open Polymarket trades in the DB via Gamma API.
    If the market is closed, calculates PnL based on outcomePrices and marks as closed.
    """
    import db_engine
    
    if not _clob_client:
        return
        
    open_trades = db_engine.get_open_poly_trades()
    if not open_trades:
        return
        
    import httpx
    import json
    async with httpx.AsyncClient(timeout=15.0) as client:
        for trade in open_trades:
            order_id = trade[0]
            market_question = trade[1]
            bet_size = float(trade[2])
            
            try:
                order = _clob_client.get_order(order_id)
                if not order or not order.get("market"):
                    continue
                    
                condition_id = order.get("market")
                url = "https://gamma-api.polymarket.com/markets"
                resp = await client.get(url, params={"condition_id": condition_id})
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        market_data = data[0]
                        if market_data.get("closed"):
                            outcomes_raw = market_data.get("outcomes", "[]")
                            prices_raw = market_data.get("outcomePrices", "[]")
                            
                            try:
                                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                            except:
                                continue
                                
                            # Find the 'No' or 'Down' index
                            no_idx = -1
                            for i, outcome in enumerate(outcomes):
                                if str(outcome).lower() in ("no", "down"):
                                    no_idx = i
                                    break
                                    
                            if no_idx != -1 and no_idx < len(prices):
                                no_price = float(prices[no_idx])
                                # If No/Down won, price is 1.0 (or >0.5)
                                # If No/Down won, price is 1.0 (or >0.5)
                                is_win = (no_price > 0.5)
                                pnl = bet_size if is_win else -bet_size
                                
                                print(f"[Polymarket] Market '{market_question}' resolved. PnL: ${pnl}")
                                
                                # Auto-Redemption and Ghost Token Cleanup
                                if _relayer_client and POLY_FUNDER_ADDRESS:
                                    try:
                                        token_ids = market_data.get("clobTokenIds", [])
                                        if no_idx < len(token_ids):
                                            token_id = int(token_ids[no_idx])
                                            ctf_contract = w3.eth.contract(address=CTF_ADDRESS, abi=ctf_abi)
                                            balance = ctf_contract.functions.balanceOf(POLY_FUNDER_ADDRESS, token_id).call()
                                            
                                            if balance > 0:
                                                calls = []
                                                nonce_payload = _relayer_client.get_nonce(
                                                    _relayer_client.signer.address(),
                                                    TransactionType.WALLET.value,
                                                )
                                                wallet_nonce = str(nonce_payload["nonce"])
                                                
                                                if is_win:
                                                    adapter = NEG_RISK_ADAPTER if market_data.get("negRisk") else NORMAL_ADAPTER
                                                    is_approved = ctf_contract.functions.isApprovedForAll(POLY_FUNDER_ADDRESS, adapter).call()
                                                    
                                                    if not is_approved:
                                                        approve_data = ctf_contract.encode_abi(
                                                            abi_element_identifier="setApprovalForAll",
                                                            args=[adapter, True]
                                                        )
                                                        calls.append(DepositWalletCall(
                                                            target=CTF_ADDRESS,
                                                            value="0",
                                                            data=approve_data
                                                        ))
                                                    
                                                    adapter_contract = w3.eth.contract(address=adapter, abi=adapter_abi)
                                                    cond_id_bytes = Web3.to_bytes(hexstr=condition_id)
                                                    redeem_data = adapter_contract.encode_abi(
                                                        abi_element_identifier="redeemPositions",
                                                        args=[PUSD_ADDRESS, b'\x00'*32, cond_id_bytes, [1, 2]]
                                                    )
                                                    calls.append(DepositWalletCall(
                                                        target=adapter,
                                                        value="0",
                                                        data=redeem_data
                                                    ))
                                                    print(f"[Polymarket] Redeeming {balance} winning tokens...")
                                                else:
                                                    # Burn ghost tokens
                                                    transfer_data = ctf_contract.encode_abi(
                                                        abi_element_identifier="safeTransferFrom",
                                                        args=[POLY_FUNDER_ADDRESS, DEAD_ADDRESS, token_id, balance, b""]
                                                    )
                                                    calls.append(DepositWalletCall(
                                                        target=CTF_ADDRESS,
                                                        value="0",
                                                        data=transfer_data
                                                    ))
                                                    print(f"[Polymarket] Burning {balance} losing tokens to dead address...")
                                                    
                                                if calls:
                                                    resp = _relayer_client.execute_deposit_wallet_batch(
                                                        calls=calls,
                                                        wallet_address=POLY_FUNDER_ADDRESS,
                                                        nonce=wallet_nonce,
                                                        deadline=str(int(time.time()) + 600),
                                                    )
                                                    resp.wait()
                                                    print("[Polymarket] On-chain batch executed successfully.")
                                    except Exception as e:
                                        print(f"[Polymarket] Auto-redemption error: {e}")
                                        
                                db_engine.update_poly_trade(order_id, pnl)
                                
            except Exception as e:
                print(f"[Polymarket] Error resolving trade {order_id}: {e}")

async def poll_polymarket_resolutions():
    import asyncio
    while True:
        try:
            await resolve_open_poly_trades()
        except Exception as e:
            print(f"[Polymarket] Error in poll loop: {e}")
        await asyncio.sleep(60)


DATA_API = "https://data-api.polymarket.com"
PUSD_DECIMALS = 1_000_000


def _short_addr(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr or "?"
    return f"{addr[:6]}…{addr[-4:]}"


def _parse_clob_balance(raw) -> float:
    """Normalize CLOB collateral balance to dollars (pUSD/USDC 6 decimals)."""
    if raw is None:
        return 0.0
    if isinstance(raw, dict):
        raw = raw.get("balance", raw.get("Balance", 0))
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    # CLOB returns atomic units for collateral; values under 1e4 are already dollars
    if val >= 10000 or (isinstance(raw, str) and raw.isdigit() and len(raw) > 4):
        return val / PUSD_DECIMALS
    return val


def get_cash_balance() -> float | None:
    """Live collateral (cash) balance via authenticated CLOB client."""
    if not _clob_client:
        return None
    try:
        resp = _clob_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return _parse_clob_balance(resp)
    except Exception as e:
        print(f"[Polymarket] get_cash_balance error: {e}")
        return None


def get_onchain_pusd_balance() -> float | None:
    """Fallback cash read from on-chain pUSD balanceOf(funder)."""
    if not POLY_FUNDER_ADDRESS:
        return None
    try:
        erc20_abi = [
            {
                "name": "balanceOf",
                "type": "function",
                "stateMutability": "view",
                "inputs": [{"name": "account", "type": "address"}],
                "outputs": [{"name": "", "type": "uint256"}],
            }
        ]
        contract = w3.eth.contract(address=Web3.to_checksum_address(PUSD_ADDRESS), abi=erc20_abi)
        raw = contract.functions.balanceOf(Web3.to_checksum_address(POLY_FUNDER_ADDRESS)).call()
        return float(raw) / PUSD_DECIMALS
    except Exception as e:
        print(f"[Polymarket] on-chain pUSD balance error: {e}")
        return None


def _data_api_get(path: str, params: dict) -> list | dict | None:
    client = _gamma_client or httpx.Client(
        timeout=15.0,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    try:
        resp = client.get(f"{DATA_API}{path}", params=params, timeout=15.0)
        if resp.status_code != 200:
            print(f"[Polymarket] Data API {path} -> {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json()
    except Exception as e:
        print(f"[Polymarket] Data API {path} error: {e}")
        return None


def get_positions_value(user: str | None = None) -> float | None:
    """Total mark-to-market value of open positions (Data API /value)."""
    addr = user or POLY_FUNDER_ADDRESS
    if not addr:
        return None
    data = _data_api_get("/value", {"user": addr})
    if not data:
        return None
    try:
        if isinstance(data, list) and data:
            return float(data[0].get("value", 0) or 0)
        if isinstance(data, dict):
            return float(data.get("value", 0) or 0)
    except (TypeError, ValueError):
        return None
    return 0.0


def get_open_positions(user: str | None = None, limit: int = 50) -> list:
    addr = user or POLY_FUNDER_ADDRESS
    if not addr:
        return []
    data = _data_api_get(
        "/positions",
        {"user": addr, "limit": limit, "sizeThreshold": 0, "sortBy": "CURRENT", "sortDirection": "DESC"},
    )
    return data if isinstance(data, list) else []


def get_closed_positions(user: str | None = None, limit: int = 50, offset: int = 0) -> list:
    addr = user or POLY_FUNDER_ADDRESS
    if not addr:
        return []
    data = _data_api_get(
        "/closed-positions",
        {
            "user": addr,
            "limit": min(limit, 50),
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        },
    )
    return data if isinstance(data, list) else []


def get_user_activity(user: str | None = None, limit: int = 20, offset: int = 0) -> list:
    addr = user or POLY_FUNDER_ADDRESS
    if not addr:
        return []
    data = _data_api_get(
        "/activity",
        {
            "user": addr,
            "limit": min(limit, 100),
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        },
    )
    return data if isinstance(data, list) else []


def summarize_closed_pnl(closed: list) -> dict:
    wins = losses = 0
    realized = 0.0
    for p in closed:
        pnl = float(p.get("realizedPnl") or 0)
        realized += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    total = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "total": total,
        "win_rate": (wins / total * 100.0) if total else 0.0,
        "realized_pnl": realized,
    }


def fetch_live_account_status(history_limit: int = 8, closed_sample: int = 50) -> dict:
    """
    Live Polymarket account snapshot for Telegram admins.
    Uses CLOB keys for cash balance + public Data API for equity/positions/history.
    """
    addr = POLY_FUNDER_ADDRESS or ""
    connected = _clob_client is not None and bool(addr)

    cash = get_cash_balance()
    cash_source = "clob"
    if cash is None:
        cash = get_onchain_pusd_balance()
        cash_source = "onchain" if cash is not None else None

    positions_value = get_positions_value(addr) if addr else None
    open_positions = get_open_positions(addr) if addr else []
    closed = get_closed_positions(addr, limit=closed_sample) if addr and closed_sample > 0 else []
    activity = get_user_activity(addr, limit=history_limit) if addr and history_limit > 0 else []

    unrealized = 0.0
    for p in open_positions:
        try:
            unrealized += float(p.get("cashPnl") or 0)
        except (TypeError, ValueError):
            pass

    cash_f = float(cash) if cash is not None else None
    pos_f = float(positions_value) if positions_value is not None else None
    equity = None
    if cash_f is not None or pos_f is not None:
        equity = (cash_f or 0.0) + (pos_f or 0.0)

    return {
        "connected": connected,
        "address": addr,
        "address_short": _short_addr(addr),
        "cash_balance": cash_f,
        "cash_source": cash_source,
        "positions_value": pos_f,
        "equity": equity,
        "unrealized_pnl": unrealized,
        "open_positions": open_positions,
        "open_count": len(open_positions),
        "closed_stats": summarize_closed_pnl(closed),
        "closed_positions": closed,
        "activity": activity,
        "error": None if addr else "POLY_FUNDER_ADDRESS not configured",
    }

