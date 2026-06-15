import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client, ClientOptions
import httpx

# Surgical Interceptor for ghost-cached httpx version conflicts
original_httpx_client_init = httpx.Client.__init__

def patched_httpx_client_init(self, *args, **kwargs):
    if 'proxy' in kwargs:
        kwargs.pop('proxy')
    original_httpx_client_init(self, *args, **kwargs)

httpx.Client.__init__ = patched_httpx_client_init

load_dotenv()

class SupabasePortfolioManager:
    def __init__(self):
        raw_url = str(os.getenv("SUPABASE_URL", "")).strip()
        raw_key = str(os.getenv("SUPABASE_KEY", "")).strip()

        # Aggressively strip any residual text or ghost whitespace from Render or .env
        self.url = raw_url.replace("`", "").replace('"', "").replace("'", "")
        self.key = raw_key.replace("`", "").replace('"', "").replace("'", "").replace("anon public ", "").strip()

        if not self.url or not self.key:
            logging.error("CRITICAL: Sanitized Supabase credentials are empty!")
            raise ValueError("Invalid configuration boundaries.")

        logging.info(f"Connecting to Supabase... Key format valid: {self.key.startswith('eyJ')}")
        logging.info("Welding clean API key headers to all threads using ClientOptions...")

        # CRITICAL FIX: Weld the headers to survive thread hopping
        opts = ClientOptions(headers={
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}"
        })

        self.client: Client = create_client(self.url, self.key, options=opts)

    def sync_manual_position(self, data: dict) -> bool:
        """
        Pushes row definitions to the cloud table manual_portfolio.
        """
        if not self.client:
            return False
            
        try:
            response = self.client.table("manual_portfolio").insert(data).execute()
            return len(response.data) > 0
        except Exception as e:
            logging.error(f"Failed to sync position to Supabase: {e}")
            return False

    def fetch_active_positions(self) -> list:
        """
        Queries the database dynamically where status == 'ACTIVE'.
        """
        if not self.client:
            return []
            
        try:
            response = self.client.table("manual_portfolio").select("*").eq("status", "ACTIVE").execute()
            return response.data
        except Exception as e:
            logging.error(f"Failed to fetch active positions from Supabase: {e}")
            return []

    def update_position_status(self, row_id: int, status: str) -> bool:
        """
        Commits instant mutations updating rows safely.
        """
        if not self.client:
            return False
            
        try:
            response = self.client.table("manual_portfolio").update({"status": status}).eq("id", row_id).execute()
            return len(response.data) > 0
        except Exception as e:
            logging.error(f"Failed to update position {row_id} status in Supabase: {e}")
            return False

    def log_ai_journal(self, symbol: str, action: str, price: float, sentiment_score: float, logic: str):
        """Logs the cognitive processing matrix into public.ai_trade_journal"""
        try:
            self.client.table("ai_trade_journal").insert({
                "symbol": symbol,
                "action": action,
                "price": price,
                "sentiment_score": sentiment_score,
                "ai_logic": logic
            }).execute()
        except Exception as e:
            logging.error(f"Failed to write to ai_trade_journal: {e}")
