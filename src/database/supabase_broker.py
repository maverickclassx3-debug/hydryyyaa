import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client
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
        raw_url = os.getenv("SUPABASE_URL", "")
        raw_key = os.getenv("SUPABASE_KEY", "")
        
        # Strict Multi-Pass Sanitization Wrapper
        def sanitize(val: str) -> str:
            if not val:
                return ""
            # Strip accidental prefixes if pasted along with the key
            if "SUPABASE_KEY=" in val:
                val = val.split("SUPABASE_KEY=")[-1]
            if "SUPABASE_URL=" in val:
                val = val.split("SUPABASE_URL=")[-1]
            # Strip backticks, quotes, and whitespace bounds
            return val.replace("`", "").replace("'", "").replace('"', "").strip()

        self.url = sanitize(raw_url)
        self.key = sanitize(raw_key)
        
        if not self.url or not self.key:
            logging.error("CRITICAL: Sanitized Supabase credentials are empty!")
            self.client = None
            # Raising an error may crash sentinel depending on handling, 
            # but user explicitly requested to raise ValueError
            raise ValueError("Invalid configuration boundaries.")
        else:
            # Bypass Supabase JWT validation for sb_publishable_* keys
            import supabase._sync.client
            original_match = supabase._sync.client.re.match
            
            def patched_match(pattern, string, flags=0):
                if string.startswith("sb_publishable_"):
                    return True
                return original_match(pattern, string, flags)
                
            supabase._sync.client.re.match = patched_match
            try:
                self.client: Client = create_client(self.url, self.key)
                
                # CRITICAL FIX: Explicitly enforce key headers across all thread requests
                self.client.options.headers.update({
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}"
                })
                logging.info("Supabase API Request headers forcefully bound to client matrix.")
            finally:
                supabase._sync.client.re.match = original_match

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
