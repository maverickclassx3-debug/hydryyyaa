import os
import time
import requests
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from src.database.supabase_broker import SupabasePortfolioManager
from src.utils.api_clients import MarketDataClient
from src.agents.sentiment_engine import SentimentEngine

load_dotenv()

class TelegramSentinel:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        # In a real environment, you'd store the CHAT_ID in .env or pull it from a webhook 
        # Hardcoding a dummy target for safety / testing if not provided.
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "123456789") 
        self.db = SupabasePortfolioManager()
        self.market = MarketDataClient()
        self.sentiment_engine = SentimentEngine()

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
            
            # Analyze Sentiment
            sentiment = self.sentiment_engine.analyze_ticker(symbol)
            
            # Log AI Cognitive Process
            self.db.log_ai_journal(
                symbol, 
                "MONITOR", 
                current_price, 
                sentiment['sentiment_score'], 
                f"Signal: {sentiment['signal']}"
            )
            
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
                message = f"🚨 *URGENT EXIT ADVICE*: {symbol} {exit_reason}!\n🧠 Sentiment: {sentiment['signal']} (Score: {sentiment['sentiment_score']})"
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

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        """Handle HEAD requests — required for Render's port scanner to detect service as live."""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Sentinel is ALIVE and monitoring!")

    def log_message(self, format, *args):
        # Suppress default HTTP access log noise from Render health checks
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logging.info(f"Health check server successfully bound to port {port}.")
        server.serve_forever()
    except Exception as e:
        logging.error(f"CRITICAL: Health check server failed to bind on port {port}: {e}")

def run_bot_loop():
    """Isolated runner for the Telegram Sentinel polling logic"""
    try:
        logging.info("Starting isolated Sentinel core agent...")
        sentinel = TelegramSentinel()
        # Explicit execution trigger depending on your framework's loop controller
        if hasattr(sentinel, 'run'):
            sentinel.run()
        elif hasattr(sentinel, 'run_sentinel_loop'):
            sentinel.run_sentinel_loop()
    except Exception as e:
        logging.error(f"Surgical Thread Recovery Alert - Core loop crashed: {e}")

if __name__ == "__main__":
    # Configure logging baseline format to flush streams instantly on Render
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Thread Alpha: Satisfy Render's mandatory PORT binding checks
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()
    logging.info("Thread Alpha: Render Dummy HTTP Server successfully detached.")

    # Thread Beta: Run the main Sentinel automation engine
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
    bot_thread.start()
    logging.info("Thread Beta: Telegram Sentinel loop successfully detached.")

    # Keep the main process thread alive indefinitely so background threads don't terminate
    import time
    while True:
        time.sleep(1)
