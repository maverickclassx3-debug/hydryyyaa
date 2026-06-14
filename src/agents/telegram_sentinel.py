import os
import time
import requests
import logging
from dotenv import load_dotenv
from src.database.supabase_broker import SupabasePortfolioManager
from src.utils.api_clients import MarketDataClient

load_dotenv()

class TelegramSentinel:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        # In a real environment, you'd store the CHAT_ID in .env or pull it from a webhook 
        # Hardcoding a dummy target for safety / testing if not provided.
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "123456789") 
        self.db = SupabasePortfolioManager()
        self.market = MarketDataClient()

    def send_alert(self, message: str) -> bool:
        if not self.bot_token:
            logging.warning(f"Simulating Telegram Alert (No Token): {message}")
            return False
            
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10.0)
            return response.status_code == 200
        except Exception as e:
            logging.error(f"Telegram API Post Failed: {e}")
            return False

    def check_breaches(self):
        positions = self.db.fetch_active_positions()
        
        for pos in positions:
            symbol = pos.get("symbol")
            stop_loss = pos.get("stop_loss")
            target = pos.get("target")
            row_id = pos.get("id")
            
            tech = self.market.fetch_ticker_data(symbol)
            if not tech:
                continue
                
            current_price = tech.get("current_price", 0.0)
            
            # Simplified long-only execution boundary check
            breached = False
            exit_reason = ""
            
            if current_price <= stop_loss:
                breached = True
                exit_reason = f"breached STOP-LOSS at {current_price:.2f}"
            elif current_price >= target:
                breached = True
                exit_reason = f"hit PROFIT TARGET at {current_price:.2f}"
                
            if breached:
                message = f"🚨 *URGENT EXIT ADVICE*: {symbol} {exit_reason}!"
                alert_success = self.send_alert(message)
                
                # Invert state to prevent spam alerting
                # Even if telegram fails, we close the loop to not hammer the API, 
                # or we could queue it. For now we invert immediately per blueprint.
                self.db.update_position_status(row_id, "CLOSED")

    def run_sentinel_loop(self):
        logging.info("Starting 24/7 Telegram Sentinel Loop...")
        while True:
            try:
                self.check_breaches()
            except Exception as e:
                logging.error(f"Sentinel Loop Exception caught: {e}. Sleeping safely.")
                
            time.sleep(60)

if __name__ == "__main__":
    sentinel = TelegramSentinel()
    sentinel.run_sentinel_loop()
