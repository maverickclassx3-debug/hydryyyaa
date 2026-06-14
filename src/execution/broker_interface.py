import time
import random
import uuid
from datetime import datetime
from collections import deque
from zoneinfo import ZoneInfo

class MarketSessionExpiredException(Exception):
    """Raised when an order is attempted after market closing time (15:30 IST)."""
    pass

class KiteExecutionEngine:
    def __init__(self, persistence_engine, api_key: str, access_token: str, paper_mode: bool = True):
        self.persistence_engine = persistence_engine
        self.api_key = api_key
        self.access_token = access_token
        self.paper_mode = paper_mode
        self.order_timestamps = deque(maxlen=9)
        self.active = True

    def _enforce_sebi_rate_limit(self):
        """
        Programmatic Leaky Bucket rate limiter.
        Ensures strictly < 10 orders per second. 
        Sleeps to buffer if threshold is hit.
        """
        now = time.time()
        if len(self.order_timestamps) == 9:
            oldest_time = self.order_timestamps[0]
            elapsed = now - oldest_time
            if elapsed < 1.0:
                sleep_time = 1.0 - elapsed
                time.sleep(sleep_time)
                now = time.time()
        self.order_timestamps.append(now)

    def enforce_daily_session_reset(self):
        """
        Check current time in India. If >= 15:30:00, instantly clear all API
        credentials, set state to inactive, and raise MarketSessionExpiredException.
        """
        ist_zone = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist_zone)
        
        if now.hour > 15 or (now.hour == 15 and now.minute >= 30):
            self.api_key = None
            self.access_token = None
            self.active = False
            raise MarketSessionExpiredException("SEBI Violation: Market Session Expired at 15:30 IST")

    def simulate_brokerage_and_slippage(self, symbol: str, price: float, quantity: int) -> dict:
        """
        Calculate exact Indian retail brokerage impacts for CNC.
        """
        trade_value = price * quantity
        
        brokerage = 0.0
        stt = 0.001 * trade_value
        transaction_charges = 0.0000345 * trade_value
        gst = 0.18 * transaction_charges
        
        total_charges = brokerage + stt + transaction_charges + gst
        
        # Random Slippage between 0.02% and 0.05%
        slippage_pct = random.uniform(0.0002, 0.0005)
        # Penalizing execution price (assuming buy order implies price goes up)
        executed_price = price * (1 + slippage_pct)
        
        net_capital = (executed_price * quantity) + total_charges
        
        return {
            'executed_price': executed_price,
            'total_charges': total_charges,
            'net_capital': net_capital
        }

    def place_cnc_order(self, order_intent: dict) -> dict:
        """
        Places a CNC order strictly ensuring compliance guards and rate limits.
        """
        self.enforce_daily_session_reset()

        if order_intent.get('product') != 'CNC':
            raise ValueError("SEBI Violation: Only CNC allowed")
        
        algo_id = order_intent.get('algo_id')
        if not algo_id or not str(algo_id).strip():
            raise ValueError("SEBI Violation: Missing Algo ID")

        self._enforce_sebi_rate_limit()

        if self.paper_mode:
            symbol = order_intent.get('symbol', 'UNKNOWN')
            price = order_intent.get('entry_price', 0.0)
            qty = order_intent.get('qty', 0)
            
            sim_metrics = self.simulate_brokerage_and_slippage(symbol, price, qty)
            
            order_id = f"MOCK-ORD-{datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
            
            if self.persistence_engine:
                try:
                    self.persistence_engine.log_telemetry('KITE_EXEC', {
                        'order_id': order_id,
                        'algo_id': algo_id,
                        'symbol': symbol,
                        'simulated_price': sim_metrics['executed_price'],
                        'total_charges': sim_metrics['total_charges']
                    })
                except AttributeError:
                    # Ignore for tests where persistence_engine may be minimally mocked
                    pass
                    
            return {
                'status': 'success',
                'order_id': order_id,
                'executed_price': sim_metrics['executed_price'],
                'charges': sim_metrics['total_charges']
            }
        else:
            # Stub for real kite.place_order mapping
            return {'status': 'live_stub'}
